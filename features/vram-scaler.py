#!/usr/bin/env python3
"""
Aion Dynamic VRAM Scaler — Auto-adjust VRAM/GTT allocation for APUs.

For devices with shared memory (AMD Ryzen APUs, Intel Iris Xe),
this daemon monitors game VRAM usage and dynamically adjusts the
GTT (Graphics Translation Table) allocation to prevent stuttering.

Usage:
    aion-vram-scaler [--threshold 80] [--interval 5]
    aion-vram-scaler daemon
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

LOG_DIR = Path("/var/log/aion")
LOG_FILE = LOG_DIR / "vram-scaler.log"
STATE_FILE = Path("/var/lib/aion/vram-state.json")

logger = logging.getLogger("vram-scaler")

# AMD APU VRAM allocation paths
AMD_GTT_PATH = "/sys/class/drm/card0/device/gtt_size"
AMD_VRAM_PATH = "/sys/class/drm/card0/device/mem_info_vram_total"
AMD_VRAM_USED_PATH = "/sys/class/drm/card0/device/mem_info_vram_used"
# NOTE: the kernel reports GTT usage at mem_info_gtt_used (gtt_used does
# not exist on amdgpu).
AMD_GTT_USED_PATH = "/sys/class/drm/card0/device/mem_info_gtt_used"

# Intel Iris Xe paths
INTEL_GTT_PATH = "/sys/class/drm/card0/gt_cur_freq_mhz"

# NVIDIA VRAM paths (for reference, not actively scaled)
NVIDIA_VRAM_PATH = "/proc/driver/nvidia/gpus/0000:01:00.0/vram"

# VRAM allocation tiers (for AMD APUs)
# Format: (total_ram_gb, default_gtt_mb, max_gtt_mb)
VRAM_TIERS = [
    (4, 512, 1024),
    (8, 1024, 2048),
    (16, 2048, 4096),
    (32, 4096, 8192),
]


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.DEBUG)


def get_total_ram_gb() -> int:
    """Get total system RAM in GB."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb // 1024 // 1024
    except (FileNotFoundError, ValueError):
        pass
    return 8


def detect_gpu_type() -> str:
    """Detect GPU type (amd, intel, nvidia, unknown)."""
    try:
        result = subprocess.run(
            ["lspci"], capture_output=True, text=True, timeout=5,
        )
        output = result.stdout.lower()
        if "amd" in output and ("vga" in output or "display" in output):
            return "amd"
        elif "intel" in output and ("vga" in output or "display" in output):
            return "intel"
        elif "nvidia" in output:
            return "nvidia"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return "unknown"


def read_sysfs(path: str) -> Optional[int]:
    """Read a sysfs value as integer."""
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError, PermissionError):
        return None


def get_vram_usage_percent() -> float:
    """Get current VRAM usage percentage."""
    vram_total = read_sysfs(AMD_VRAM_PATH)
    vram_used = read_sysfs(AMD_VRAM_USED_PATH)

    if vram_total and vram_used and vram_total > 0:
        return (vram_used / vram_total) * 100

    return 0.0


def get_gtt_usage_percent() -> float:
    """Get current GTT usage percentage."""
    gtt_size = read_sysfs(AMD_GTT_PATH)
    gtt_used = read_sysfs(AMD_GTT_USED_PATH)

    if gtt_size and gtt_used and gtt_size > 0:
        return (gtt_used / gtt_size) * 100

    return 0.0


def get_gtt_size_mb() -> int:
    """Get current GTT allocation in MB."""
    gtt_size = read_sysfs(AMD_GTT_PATH)
    if gtt_size:
        return gtt_size // 1024 // 1024
    return 0


def set_gtt_size_mb(size_mb: int) -> bool:
    """Set GTT allocation size (requires root).

    Returns True only if the size was actually applied at runtime via
    sysfs. The modprobe option file is a boot-time hint and never counts
    as a successful runtime change.
    """
    gtt_path = Path(AMD_GTT_PATH)
    if not gtt_path.exists():
        logger.debug("GTT path not found: %s", AMD_GTT_PATH)
        return False

    logger.info("Attempting to set GTT size to %d MB", size_mb)

    # Method 1: Boot-time module parameter (only written once — not on
    # every loop iteration).
    modprobe_conf = Path("/etc/modprobe.d/aion-gtt.conf")
    if not modprobe_conf.exists():
        modprobe_conf.parent.mkdir(parents=True, exist_ok=True)
        modprobe_conf.write_text(f"options amdgpu gttsize={size_mb}\n")
        logger.info("Wrote boot-time GTT option: gttsize=%d", size_mb)

    # Method 2: Runtime adjustment via sysfs. If the write succeeds this
    # is a real, applied change; otherwise it did NOT happen.
    for card in Path("/sys/class/drm").glob("card*"):
        gtt_size_file = card / "device" / "gtt_size"
        if gtt_size_file.exists():
            try:
                gtt_size_file.write_text(str(size_mb * 1024 * 1024))
                logger.info("GTT size set to %d MB via sysfs", size_mb)
                return True
            except PermissionError:
                logger.debug("No permission to write GTT via sysfs")

    logger.warning("GTT could not be changed at runtime (read-only sysfs)")
    return False


def get_recommended_gtt() -> int:
    """Get recommended GTT size based on system RAM."""
    total_ram = get_total_ram_gb()

    for ram_gb, default_gtt, max_gtt in VRAM_TIERS:
        if total_ram <= ram_gb:
            return default_gtt

    # For systems with >32GB RAM, scale up to the largest tier.
    return 8192


def scale_vram(threshold: float = 80.0, interval: int = 5):
    """Main VRAM scaling loop."""
    gpu_type = detect_gpu_type()
    total_ram = get_total_ram_gb()

    logger.info("VRAM Scaler started")
    logger.info("GPU: %s, RAM: %d GB", gpu_type, total_ram)

    if gpu_type != "amd":
        logger.info("Dynamic VRAM scaling only supported on AMD APUs")
        logger.info("GPU type: %s — exiting (no scaling)", gpu_type)
        return

    recommended = get_recommended_gtt()
    current = get_gtt_size_mb()

    if current == 0:
        logger.info("Setting initial GTT to %d MB", recommended)
        set_gtt_size_mb(recommended)

    while True:
        vram_pct = get_vram_usage_percent()
        gtt_pct = get_gtt_usage_percent()
        gtt_current = get_gtt_size_mb()

        logger.debug("VRAM: %.1f%%, GTT: %.1f%% (%d MB)", vram_pct, gtt_pct, gtt_current)

        # Scale up if usage exceeds threshold
        if gtt_pct > threshold and gtt_current < recommended * 2:
            new_size = min(gtt_current + 256, recommended * 2)
            logger.info("GTT usage %.1f%% > %.1f%% threshold. Scaling: %d → %d MB",
                       gtt_pct, threshold, gtt_current, new_size)
            set_gtt_size_mb(new_size)

        # Scale down if usage drops well below threshold
        elif gtt_pct < threshold * 0.3 and gtt_current > recommended:
            new_size = max(gtt_current - 256, recommended)
            if new_size != gtt_current:
                logger.info("GTT usage %.1f%% < %.1f%%. Scaling: %d → %d MB",
                           gtt_pct, threshold * 0.3, gtt_current, new_size)
                set_gtt_size_mb(new_size)

        # Save state
        state = {
            "gpu_type": gpu_type,
            "total_ram_gb": total_ram,
            "gtt_size_mb": gtt_current,
            "vram_usage_pct": round(vram_pct, 1),
            "gtt_usage_pct": round(gtt_pct, 1),
            "last_check": time.time(),
        }
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)

        time.sleep(interval)


def show_status():
    """Show current VRAM status."""
    gpu_type = detect_gpu_type()
    total_ram = get_total_ram_gb()
    vram_pct = get_vram_usage_percent()
    gtt_pct = get_gtt_usage_percent()
    gtt_current = get_gtt_size_mb()
    recommended = get_recommended_gtt()

    print(f"GPU:        {gpu_type.upper()}")
    print(f"RAM:        {total_ram} GB")
    print(f"GTT:        {gtt_current} MB (recommended: {recommended} MB)")
    print(f"VRAM Usage: {vram_pct:.1f}%")
    print(f"GTT Usage:  {gtt_pct:.1f}%")


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Aion Dynamic VRAM Scaler")
    parser.add_argument("--threshold", type=float, default=80.0,
                       help="Usage threshold to trigger scaling (default: 80%%)")
    parser.add_argument("--interval", type=int, default=5,
                       help="Check interval in seconds (default: 5)")
    parser.add_argument("--daemon", action="store_true",
                       help="Run as background daemon")
    parser.add_argument("--status", action="store_true",
                       help="Show current VRAM status")

    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.daemon:
        scale_vram(args.threshold, args.interval)
    else:
        show_status()


if __name__ == "__main__":
    main()
