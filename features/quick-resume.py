#!/usr/bin/env python3
"""
Aion Quick Resume — Freeze & restore games in seconds.

Inspired by Xbox Series X Quick Resume. Uses CRIU (Checkpoint/Restore
in Userspace) to freeze a game process, save its memory state to SSD,
and restore it later in milliseconds.

Usage:
    aion-quick-resume freeze <game-name>     # Freeze a running game
    aion-quick-resume restore <game-name>    # Restore a frozen game
    aion-quick-resume list                    # List frozen games
    aion-quick-resume delete <game-name>     # Delete a frozen snapshot
    aion-quick-resume daemon                  # Run as background daemon
"""

import argparse
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

LOG_DIR = Path("/var/log/aion")
LOG_FILE = LOG_DIR / "quick-resume.log"
SNAPSHOT_DIR = Path("/var/lib/aion/quick-resume")
STATE_FILE = SNAPSHOT_DIR / "state.json"
MAX_SNAPSHOTS = 5
MAX_SNAPSHOT_SIZE_GB = 16

logger = logging.getLogger("quick-resume")


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.DEBUG)


def check_criu() -> bool:
    """Check if CRIU is installed and available."""
    result = subprocess.run(["which", "criu"], capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("CRIU not found. Install with: pacman -S criu")
        return False
    return True


def check_permissions() -> bool:
    """Check if we have CAP_SYS_PTRACE or are root."""
    if os.geteuid() == 0:
        return True

    result = subprocess.run(
        ["cat", "/proc/self/status"],
        capture_output=True, text=True,
    )
    for line in result.stdout.split("\n"):
        if line.startswith("CapEff:"):
            caps = int(line.split(":")[1].strip(), 16)
            # CAP_SYS_PTRACE = bit 19
            if caps & (1 << 19):
                return True

    logger.error("Need root or CAP_SYS_PTRACE for CRIU. Run as root or add capability.")
    return False


def find_game_pid(game_name: str) -> Optional[int]:
    """Find the PID of a running game by name."""
    # Search in common game locations
    search_patterns = [
        f"steam_app_*",
        f"*{game_name}*",
    ]

    try:
        result = subprocess.run(
            ["pgrep", "-f", game_name],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            pids = result.stdout.strip().split("\n")
            if pids:
                # Return the main game process (not child processes)
                for pid in pids:
                    pid = int(pid.strip())
                    # Check if this is a main process (not a thread)
                    status_path = f"/proc/{pid}/status"
                    if Path(status_path).exists():
                        with open(status_path) as f:
                            for line in f:
                                if line.startswith("Threads:"):
                                    threads = int(line.split(":")[1].strip())
                                    if threads > 1:
                                        return pid
                return int(pids[0])
    except (subprocess.TimeoutExpired, ValueError):
        pass

    return None


def get_process_memory_mb(pid: int) -> float:
    """Get the memory usage of a process in MB."""
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (FileNotFoundError, ValueError):
        pass
    return 0


def freeze_game(game_name: str) -> bool:
    """Freeze a game using CRIU checkpoint."""
    if not check_criu():
        return False
    if not check_permissions():
        return False

    pid = find_game_pid(game_name)
    if pid is None:
        logger.error("Game '%s' not found running", game_name)
        return False

    # Check memory size
    mem_mb = get_process_memory_mb(pid)
    logger.info("Game '%s' (PID %d) using %.0f MB RAM", game_name, pid, mem_mb)

    if mem_mb > MAX_SNAPSHOT_SIZE_GB * 1024:
        logger.error("Game too large (%.0f MB > %d GB limit)", mem_mb, MAX_SNAPSHOT_SIZE_GB)
        return False

    # Create snapshot directory
    snapshot_dir = SNAPSHOT_DIR / game_name
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    # Check available disk space
    stat = shutil.disk_usage(str(SNAPSHOT_DIR))
    free_gb = stat.free / (1024 ** 3)
    if free_gb < mem_mb / 1024 * 1.5:
        logger.error("Not enough disk space (%.1f GB free, need %.1f GB)", free_gb, mem_mb / 1024 * 1.5)
        return False

    # Checkpoint with CRIU
    logger.info("Freezing game '%s' (PID %d)...", game_name, pid)

    criu_cmd = [
        "criu", "dump",
        "-t", str(pid),
        "-D", str(snapshot_dir),
        "--shell-job",
        "--leave-running",
        "--ext-unix-sk",
        "--link-remap",
        "--manage-cgroups",
        "-o", str(snapshot_dir / "dump.log"),
    ]

    result = subprocess.run(criu_cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        logger.error("CRIU dump failed: %s", result.stderr)
        # Clean up failed snapshot
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        return False

    # Save metadata
    metadata = {
        "game_name": game_name,
        "pid": pid,
        "frozen_at": datetime.now().isoformat(),
        "memory_mb": mem_mb,
        "snapshot_dir": str(snapshot_dir),
    }
    with open(snapshot_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    # Update state
    _update_state(game_name, metadata)

    logger.info("Game '%s' frozen successfully (%.0f MB saved)", game_name, mem_mb)
    return True


def restore_game(game_name: str) -> bool:
    """Restore a frozen game using CRIU restore."""
    if not check_criu():
        return False
    if not check_permissions():
        return False

    snapshot_dir = SNAPSHOT_DIR / game_name
    metadata_file = snapshot_dir / "metadata.json"

    if not metadata_file.exists():
        logger.error("No frozen snapshot found for '%s'", game_name)
        return False

    with open(metadata_file) as f:
        metadata = json.load(f)

    logger.info("Restoring game '%s'...", game_name)

    criu_cmd = [
        "criu", "restore",
        "-D", str(snapshot_dir),
        "--shell-job",
        "--ext-unix-sk",
        "--link-remap",
        "--manage-cgroups",
        "-o", str(snapshot_dir / "restore.log"),
    ]

    result = subprocess.run(criu_cmd, capture_output=True, text=True, timeout=120)

    if result.returncode != 0:
        logger.error("CRIU restore failed: %s", result.stderr)
        return False

    # Clean up snapshot after successful restore
    shutil.rmtree(snapshot_dir, ignore_errors=True)
    _remove_from_state(game_name)

    logger.info("Game '%s' restored successfully", game_name)
    return True


def list_frozen() -> list:
    """List all frozen game snapshots."""
    snapshots = []
    if not SNAPSHOT_DIR.exists():
        return snapshots

    for game_dir in SNAPSHOT_DIR.iterdir():
        if game_dir.is_dir():
            metadata_file = game_dir / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file) as f:
                    metadata = json.load(f)
                snapshots.append(metadata)

    return snapshots


def delete_snapshot(game_name: str) -> bool:
    """Delete a frozen game snapshot."""
    snapshot_dir = SNAPSHOT_DIR / game_name
    if not snapshot_dir.exists():
        logger.error("No snapshot found for '%s'", game_name)
        return False

    shutil.rmtree(snapshot_dir)
    _remove_from_state(game_name)
    logger.info("Snapshot deleted for '%s'", game_name)
    return True


def _update_state(game_name: str, metadata: dict):
    """Update the global state file."""
    state = {}
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            state = json.load(f)

    state[game_name] = metadata

    # Enforce max snapshots limit
    if len(state) > MAX_SNAPSHOTS:
        # Remove oldest
        oldest = min(state.items(), key=lambda x: x[1].get("frozen_at", ""))
        del state[oldest[0]]

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _remove_from_state(game_name: str):
    """Remove a game from the state file."""
    if not STATE_FILE.exists():
        return

    with open(STATE_FILE) as f:
        state = json.load(f)

    state.pop(game_name, None)

    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def run_daemon():
    """Run as a background daemon to auto-freeze games on suspend."""
    logger.info("Quick Resume daemon started")

    def handle_suspend(signum, frame):
        """Freeze all running games before system suspend."""
        logger.info("Suspend signal received, freezing games...")
        # Find all running game processes
        for proc in Path("/proc").iterdir():
            if proc.name.isdigit():
                try:
                    cmdline = (proc / "cmdline").read_text()
                    if "steam" in cmdline.lower() or "wine" in cmdline.lower():
                        game_name = proc.name
                        freeze_game(game_name)
                except (FileNotFoundError, PermissionError):
                    pass

    signal.signal(signal.SIGUSR1, handle_suspend)

    while True:
        time.sleep(60)


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Aion Quick Resume")
    sub = parser.add_subparsers(dest="command")

    freeze_p = sub.add_parser("freeze", help="Freeze a running game")
    freeze_p.add_argument("game_name", help="Name of the game process")

    restore_p = sub.add_parser("restore", help="Restore a frozen game")
    restore_p.add_argument("game_name", help="Name of the game to restore")

    sub.add_parser("list", help="List frozen games")

    delete_p = sub.add_parser("delete", help="Delete a frozen snapshot")
    delete_p.add_argument("game_name", help="Name of the game to delete")

    sub.add_parser("daemon", help="Run as background daemon")

    args = parser.parse_args()

    if args.command == "freeze":
        success = freeze_game(args.game_name)
        sys.exit(0 if success else 1)
    elif args.command == "restore":
        success = restore_game(args.game_name)
        sys.exit(0 if success else 1)
    elif args.command == "list":
        snapshots = list_frozen()
        if not snapshots:
            print("No frozen games")
        else:
            for snap in snapshots:
                print(f"  {snap['game_name']}: {snap['memory_mb']:.0f} MB (frozen {snap['frozen_at']})")
    elif args.command == "delete":
        success = delete_snapshot(args.game_name)
        sys.exit(0 if success else 1)
    elif args.command == "daemon":
        run_daemon()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
