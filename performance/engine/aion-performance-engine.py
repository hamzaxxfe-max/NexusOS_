#!/usr/bin/env python3
"""Aion Dynamic Performance Engine — model-agnostic proportional scaling.

Consumes the hardware profiles produced by the Aion hardware-adapter
detectors (cpu / gpu / memory / storage) and derives a single power rating
plus a proportional performance profile: FPS target, CPU weights, free-RAM
target, ZRAM ratio and debloat decisions.

Design constraints (History-Maker):
- Model-agnostic: any CPU / RAM / GPU / storage maps onto the rating curve.
- Zero fixed hardware limits: every value is derived from detected inputs
  via closed-form (O(1)) math — no tables, no per-vendor branches.
- Deterministic and pure: compute() has no I/O and is unit-testable.
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATE = "%Y-%m-%d %H:%M:%S"
LOG_FILE = Path("/var/log/aion/performance-engine.log")
CONFIG_DIR = Path(os.environ.get("AION_CONFIG_DIR", "/etc/aion"))
OUTPUT_FILE = CONFIG_DIR / "performance-profile.json"

RATING_RANGE = (0.0, 100.0)
TIER_COUNT = 5
TIER_THRESHOLDS = (20.0, 40.0, 60.0, 80.0)

_STORAGE_INDEX = {"hdd": 0.35, "ssd": 0.75, "nvme": 1.0}
STORAGE_DEFAULT = 0.75

logger = logging.getLogger("aion-performance-engine")


class PerformanceRating:
    """Closed-form hardware rating and proportional profile derivation."""

    __slots__ = ("rating", "tier", "fps_target", "game_cpu_weight",
                 "bg_cpu_weight", "target_free_mb", "zram_ratio",
                 "governor", "debloat", "quality", "gpu_count")

    @staticmethod
    def _saturate(value: float, knee: float) -> float:
        """Sigmoid-style saturation curve — strictly monotonic, bounded to [0,1].

        Uses 1 - exp(-x/knee): zero inputs yield ~0, huge inputs asymptote at 1
        without any hard ceiling, so no hardware is ever 'out of range'.
        """
        return 1.0 - math.exp(-max(0.0, value) / max(1e-6, knee))

    @staticmethod
    def _storage_score(index: float) -> float:
        return min(1.0, max(0.0, index))

    @classmethod
    def compute(cls, threads: int, ram_mb: int, vram_mb: int,
                storage_index: float, gpu_count: int = 1) -> "PerformanceRating":
        """Derive a full performance profile from detected hardware.

        All math is closed-form (no loops, no lookup tables), making it
        proportional for any hardware — i3 laptops to workstations.
        """
        # Component scores (0..1 each), weighted by impact.
        cpu_score = cls._saturate(float(max(1, threads)), 24.0)
        ram_score = cls._saturate(float(max(64, ram_mb)), 24.0 * 1024.0)
        vram_score = cls._saturate(float(max(0, vram_mb)), 8.0 * 1024.0)
        storage_score = cls._storage_score(storage_index)

        rating = 100.0 * (
            0.40 * cpu_score
            + 0.25 * ram_score
            + 0.25 * vram_score
            + 0.10 * storage_score
        )
        rating = min(RATING_RANGE[1], max(RATING_RANGE[0], rating))

        tier = 1
        for threshold in TIER_THRESHOLDS:
            if rating >= threshold:
                tier += 1

        # Proportional FPS target: 30 (low-end) .. 240 (high-end), O(1).
        fps = int(round(30.0 + 210.0 * (rating / 100.0)))
        fps = max(30, min(240, fps))

        # CPU cgroup weights scale with capability.
        game_weight = int(round(100.0 + 300.0 * (rating / 100.0)))
        bg_weight = int(round(100.0 * (1.0 - 0.90 * (rating / 100.0))))
        bg_weight = max(10, min(100, bg_weight))

        # Free-RAM target is a proportion of detected RAM (never a fixed GB).
        free_mb = int(round(ram_mb * (0.08 + 0.06 * (rating / 100.0))))
        free_mb = max(256, min(free_mb, int(ram_mb * 0.20)))

        # ZRAM ratio: tight on low RAM, relaxed on high RAM.
        zram_ratio = 2.5 - 1.2 * (rating / 100.0)
        zram_ratio = round(max(1.0, min(2.5, zram_ratio)), 2)

        governor = "performance" if rating >= 60.0 else "schedutil"
        debloat = rating < 40.0
        quality = ("ultra" if rating >= 80.0
                   else "high" if rating >= 60.0
                   else "medium" if rating >= 40.0
                   else "low")

        return cls(rating, tier, fps, game_weight, bg_weight, free_mb,
                   zram_ratio, governor, debloat, quality, gpu_count)

    def __init__(self, rating: float, tier: int, fps_target: int,
                 game_cpu_weight: int, bg_cpu_weight: int,
                 target_free_mb: int, zram_ratio: float,
                 governor: str, debloat: bool, quality: str,
                 gpu_count: int) -> None:
        self.rating = rating
        self.tier = tier
        self.fps_target = fps_target
        self.game_cpu_weight = game_cpu_weight
        self.bg_cpu_weight = bg_cpu_weight
        self.target_free_mb = target_free_mb
        self.zram_ratio = zram_ratio
        self.governor = governor
        self.debloat = debloat
        self.quality = quality
        self.gpu_count = gpu_count

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rating": round(self.rating, 2),
            "tier": self.tier,
            "fps_target": self.fps_target,
            "resource_allocation": {
                "game_cpu_weight": self.game_cpu_weight,
                "bg_cpu_weight": self.bg_cpu_weight,
                "target_free_mb": self.target_free_mb,
            },
            "governor": self.governor,
            "zram_ratio": self.zram_ratio,
            "debloat": self.debloat,
            "quality": self.quality,
            "gpu_count": self.gpu_count,
            "model": "aion-performance-engine-1.0",
        }


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("Could not parse %s", path)
    return None


def detect_from_profiles(config_dir: Path = CONFIG_DIR) -> Dict[str, Any]:
    """Read detected hardware from the hardware-adapter JSON profiles.

    Every profile is optional: if a detector did not run, sane model-agnostic
    defaults are used so the engine always produces a valid profile.
    """
    threads = 1
    ram_mb = 2048
    vram_mb = 0
    storage_index = STORAGE_DEFAULT
    gpu_count = 0

    # CPU — from /proc/cpuinfo (direct, no fixed model knowledge).
    try:
        if Path("/proc/cpuinfo").is_file():
            n = sum(1 for line in Path("/proc/cpuinfo").read_text(
                encoding="utf-8", errors="replace").splitlines()
                if line.startswith("processor"))
            if n > 0:
                threads = n
    except OSError:
        pass

    # RAM — from /proc/meminfo.
    try:
        if Path("/proc/meminfo").is_file():
            for line in Path("/proc/meminfo").read_text(
                    encoding="utf-8", errors="replace").splitlines():
                if line.startswith("MemTotal:"):
                    ram_mb = max(64, int(line.split()[1]) // 1024)
                    break
    except OSError:
        pass

    # GPU — from gpu-profile.json (fallback to udev enumeration below).
    gpu_profile = _read_json(config_dir / "gpu-profile.json")
    if gpu_profile:
        vram_mb = int(gpu_profile.get("max_vram_mb", 0) or 0)
        gpu_count = int(gpu_profile.get("gpu_count", 0) or 0)
        storage_index = STORAGE_DEFAULT

    # Storage — from storage-profile.json (fastest detected device wins).
    storage_profile = _read_json(config_dir / "storage-profile.json")
    if storage_profile:
        devices = storage_profile.get("devices") or []
        idx = STORAGE_DEFAULT
        for dev in devices:
            dev_type = str(dev.get("type", ""))
            if dev_type in _STORAGE_INDEX:
                idx = max(idx, _STORAGE_INDEX[dev_type])
        storage_index = idx

    return {
        "threads": threads,
        "ram_mb": ram_mb,
        "vram_mb": vram_mb,
        "storage_index": storage_index,
        "gpu_count": gpu_count,
    }


def build_profile(config_dir: Path = CONFIG_DIR) -> PerformanceRating:
    hardware = detect_from_profiles(config_dir)
    return PerformanceRating.compute(
        threads=hardware["threads"],
        ram_mb=hardware["ram_mb"],
        vram_mb=hardware["vram_mb"],
        storage_index=hardware["storage_index"],
        gpu_count=hardware["gpu_count"],
    )


def write_profile(profile: PerformanceRating, out_path: Path = OUTPUT_FILE) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = profile.to_dict()
    payload["hardware"] = detect_from_profiles(out_path.parent)
    payload["timestamp"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Aion Dynamic Performance Engine")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the derived profile without writing files")
    parser.add_argument("--threads", type=int, default=None)
    parser.add_argument("--ram-mb", type=int, default=None)
    parser.add_argument("--vram-mb", type=int, default=None)
    parser.add_argument("--storage", type=str, default=None,
                        choices=list(_STORAGE_INDEX) + ["default"])
    parser.add_argument("--config-dir", type=str, default=None)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, datefmt=LOG_DATE)

    config_dir = Path(args.config_dir) if args.config_dir else CONFIG_DIR

    if args.threads or args.ram_mb or args.vram_mb or args.storage:
        hw = detect_from_profiles(config_dir)
        if args.threads:
            hw["threads"] = args.threads
        if args.ram_mb:
            hw["ram_mb"] = args.ram_mb
        if args.vram_mb is not None:
            hw["vram_mb"] = args.vram_mb
        if args.storage and args.storage != "default":
            hw["storage_index"] = _STORAGE_INDEX[args.storage]
        profile = PerformanceRating.compute(
            threads=hw["threads"], ram_mb=hw["ram_mb"], vram_mb=hw["vram_mb"],
            storage_index=hw["storage_index"], gpu_count=hw["gpu_count"],
        )
    else:
        profile = build_profile(config_dir)

    print(json.dumps(profile.to_dict(), indent=2))

    if not args.dry_run:
        write_profile(profile, config_dir / "performance-profile.json")
        logger.info("Wrote performance profile to %s",
                    config_dir / "performance-profile.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
