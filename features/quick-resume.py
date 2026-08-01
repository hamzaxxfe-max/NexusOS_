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
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

LOG_DIR = Path("/var/log/aion")
LOG_FILE = LOG_DIR / "quick-resume.log"
SNAPSHOT_DIR = Path("/var/lib/aion/quick-resume")
STATE_FILE = SNAPSHOT_DIR / "state.json"
MAX_SNAPSHOTS = 5
MAX_SNAPSHOT_SIZE_GB = 16

# Only safe characters — blocks path traversal ("freeze ../foo").
GAME_NAME_RE = re.compile(r"^[a-zA-Z0-9 _-]+$")

logger = logging.getLogger("quick-resume")


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.DEBUG)


def validate_game_name(game_name: str) -> bool:
    """Reject names that could escape the snapshot dir."""
    if not game_name or not GAME_NAME_RE.match(game_name):
        logger.error("Invalid game name (only letters, digits, space, _ and - allowed): %r", game_name)
        return False
    return True


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
    if not validate_game_name(game_name):
        return False
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

    # Checkpoint with CRIU. NOTE: no --leave-running — a real freeze must
    # stop the process so the snapshot is consistent and memory is freed.
    logger.info("Freezing game '%s' (PID %d)...", game_name, pid)

    criu_cmd = [
        "criu", "dump",
        "-t", str(pid),
        "-D", str(snapshot_dir),
        "--shell-job",
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
    if not validate_game_name(game_name):
        return False
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
    if not validate_game_name(game_name):
        return False
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

    # Enforce max snapshots limit — evict oldest snapshot AND delete its files.
    if len(state) > MAX_SNAPSHOTS:
        oldest_name, _oldest_meta = min(state.items(), key=lambda x: x[1].get("frozen_at", ""))
        del state[oldest_name]
        shutil.rmtree(SNAPSHOT_DIR / oldest_name, ignore_errors=True)

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


def _guess_game_name(pid: int) -> Optional[str]:
    """Derive a human-safe game name from a game process cmdline."""
    try:
        cmdline = (Path(f"/proc/{pid}/cmdline").read_text(errors="replace")).replace("\x00", " ").strip()
    except (FileNotFoundError, PermissionError):
        return None
    if not cmdline:
        try:
            return (Path(f"/proc/{pid}/comm").read_text(errors="replace")).strip()
        except (FileNotFoundError, PermissionError):
            return None

    lower = cmdline.lower()
    # Common game engines keep the game name in argv; use the last
    # non-option argument that looks like an executable/game.
    parts = [p for p in cmdline.split() if p and not p.startswith(("-", "/"))]
    if any(k in lower for k in ("steam", "wine", "proton")):
        for p in reversed(parts):
            if p.lower().endswith((".exe", ".sh", ".bin")) or ("game" in p.lower() or "dota" in p.lower() or "cs2" in p.lower()):
                name = Path(p).stem
                if validate_name_only(name):
                    return name
    name = Path(cmdline.split()[0]).stem if cmdline.split() else None
    return name if name and validate_name_only(name) else None


def validate_name_only(name: str) -> bool:
    return bool(name) and bool(GAME_NAME_RE.match(name))


def _suspend_games():
    """Freeze all running game processes before system suspend."""
    logger.info("PrepareForSleep signal received, freezing games...")
    frozen = 0
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        pid = int(proc.name)
        try:
            cmdline = (proc / "cmdline").read_text(errors="replace").lower()
        except (FileNotFoundError, PermissionError):
            continue
        if any(k in cmdline for k in ("steam", "wine", "proton")):
            game_name = _guess_game_name(pid)
            if not game_name:
                logger.warning("Could not derive game name for PID %d, skipping", pid)
                continue
            if list_frozen() and game_name in {s["game_name"] for s in list_frozen()}:
                logger.info("Game '%s' already frozen, skipping", game_name)
                continue
            if freeze_game(game_name):
                frozen += 1
    logger.info("Froze %d game(s) before suspend", frozen)


def _wait_for_systemd_sleep():
    """Listen on the logind PrepareForSleep signal using dbus-monitor."""
    cmd = [
        "dbus-monitor", "--system",
        "type='signal',interface='org.freedesktop.login1.Manager',member='PrepareForSleep'",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    except FileNotFoundError:
        logger.warning("dbus-monitor not available; suspend auto-freeze disabled")
        return
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if "boolean true" in line:
                _suspend_games()
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()


def run_daemon():
    """Freeze games on system suspend (via logind PrepareForSleep)."""
    logger.info("Quick Resume daemon started (watching logind PrepareForSleep)")
    _wait_for_systemd_sleep()


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Aion Quick Resume")
    sub = parser.add_subparsers(dest="command")

    freeze_p = sub.add_parser("freeze", help="Freeze a running game")
    freeze_p.add_argument("game_name", nargs="?", help="Name of the game process")
    freeze_p.add_argument("--game", dest="game_name_opt", help="Name of the game process")

    restore_p = sub.add_parser("restore", help="Restore a frozen game")
    restore_p.add_argument("game_name", nargs="?", help="Name of the game to restore")
    restore_p.add_argument("--game", dest="game_name_opt", help="Name of the game to restore")

    sub.add_parser("list", help="List frozen games")

    delete_p = sub.add_parser("delete", help="Delete a frozen snapshot")
    delete_p.add_argument("game_name", nargs="?", help="Name of the game to delete")
    delete_p.add_argument("--game", dest="game_name_opt", help="Name of the game to delete")

    sub.add_parser("daemon", help="Run as background daemon")

    args = parser.parse_args()

    if args.command == "freeze":
        success = freeze_game(args.game_name or args.game_name_opt)
        sys.exit(0 if success else 1)
    elif args.command == "restore":
        success = restore_game(args.game_name or args.game_name_opt)
        sys.exit(0 if success else 1)
    elif args.command == "list":
        snapshots = list_frozen()
        if not snapshots:
            print("No frozen games")
        else:
            for snap in snapshots:
                print(f"  {snap['game_name']}: {snap['memory_mb']:.0f} MB (frozen {snap['frozen_at']})")
    elif args.command == "delete":
        success = delete_snapshot(args.game_name or args.game_name_opt)
        sys.exit(0 if success else 1)
    elif args.command == "daemon":
        run_daemon()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
