#!/usr/bin/env python3
"""
Aion Cloud Sync — Sync non-Steam game saves to cloud storage.

Syncs Wine prefixes, Lutris saves, and RetroArch save files to
Google Drive, OneDrive, or Nextcloud using Rclone.

Usage:
    aion-cloud-sync setup          # Configure cloud provider
    aion-cloud-sync sync           # Sync saves to cloud
    aion-cloud-sync restore        # Restore saves from cloud
    aion-cloud-sync list           # List syncable games
    aion-cloud-sync daemon         # Run auto-sync daemon
"""

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

LOG_DIR = Path("/var/log/aion")
LOG_FILE = LOG_DIR / "cloud-sync.log"
CONFIG_FILE = Path("/etc/aion/cloud-sync.json")
STATE_FILE = Path("/var/lib/aion/cloud-sync-state.json")
# Saves live under the gaming user's home (never /root).
GAME_HOME = Path(os.environ.get("AION_GAME_HOME", "/home/aion"))
RCLONE_CONFIG = GAME_HOME / ".config/rclone/rclone.conf"

logger = logging.getLogger("cloud-sync")

# Default game save locations — anchored to GAME_HOME so the daemon
# syncs the player's real saves regardless of which user runs it.
SYNC_PATHS = {
    "wine-prefixes": {
        "path": str(GAME_HOME / ".wine/drive_c/users"),
        "pattern": "**/Documents/My Games/**",
        "description": "Wine game saves (My Games folder)",
    },
    "lutris-saves": {
        "path": str(GAME_HOME / "Games"),
        "pattern": "**/prefix/drive_c/users/*/Documents/**",
        "description": "Lutris game saves",
    },
    "retroarch-saves": {
        "path": str(GAME_HOME / ".config/retroarch/saves"),
        "pattern": "**/*",
        "description": "RetroArch save files",
    },
    "retroarch-states": {
        "path": str(GAME_HOME / ".config/retroarch/states"),
        "pattern": "**/*",
        "description": "RetroArch save states",
    },
    "steam-compat": {
        "path": str(GAME_HOME / ".local/share/Steam/steamapps/compatdata"),
        "pattern": "**/pfx/drive_c/users/*/Documents/**",
        "description": "Steam Proton saves",
    },
    "cemu-saves": {
        "path": str(GAME_HOME / ".local/share/Cemu/mlc01/usr/save"),
        "pattern": "**/*",
        "description": "Cemu (Wii U) saves",
    },
    "dolphin-saves": {
        "path": str(GAME_HOME / ".local/share/dolphin-emu/GC"),
        "pattern": "**/*",
        "description": "Dolphin (GameCube/Wii) saves",
    },
    "yuzu-saves": {
        "path": str(GAME_HOME / ".local/share/yuzu/sdmc"),
        "pattern": "**/*",
        "description": "Yuzu (Switch) saves",
    },
}


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    logger.setLevel(logging.DEBUG)


def check_rclone() -> bool:
    """Check if Rclone is installed."""
    result = subprocess.run(["which", "rclone"], capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("Rclone not found. Install with: pacman -S rclone")
        return False
    return True


def load_config() -> dict:
    """Load cloud sync configuration."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {}


def save_config(config: dict):
    """Save cloud sync configuration."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def setup_cloud_provider():
    """Interactive setup for cloud provider."""
    print("Aion Cloud Sync Setup")
    print("=" * 40)
    print()
    print("Select cloud provider:")
    print("  1. Google Drive")
    print("  2. OneDrive")
    print("  3. Nextcloud")
    print("  4. Custom Rclone remote")
    print()

    choice = input("Enter choice (1-4): ").strip()

    provider_map = {
        "1": "gdrive",
        "2": "onedrive",
        "3": "nextcloud",
        "4": "custom",
    }

    provider = provider_map.get(choice)
    if not provider:
        print("Invalid choice")
        return False

    # Run rclone config for the selected provider
    print(f"\nConfiguring {provider}...")
    print("Follow the Rclone wizard to authenticate.")
    print()

    remote_name = f"aion-{provider}"
    result = subprocess.run(
        ["rclone", "--config", str(RCLONE_CONFIG), "config", "create", remote_name, provider],
        capture_output=False,
    )

    if result.returncode != 0:
        # Try interactive config
        subprocess.run(["rclone", "--config", str(RCLONE_CONFIG), "config", "create", remote_name, provider])

    # Create a REAL crypt remote wrapping the provider. Encryption must
    # be an actual rclone crypt remote, not a runtime flag.
    crypt_remote = f"{remote_name}-crypt"
    crypt_result = subprocess.run(
        [
            "rclone", "--config", str(RCLONE_CONFIG), "config", "create", crypt_remote, "crypt",
            "remote", f"{remote_name}:Aion-Saves",
            "filename_encryption", "standard",
            "directory_name_encryption", "true",
        ],
        capture_output=True, text=True,
    )
    if crypt_result.returncode != 0:
        print(f"WARNING: could not create crypt remote: {crypt_result.stderr.strip()}")

    # Save config — point at the crypt remote so all synced saves are
    # encrypted at rest on the provider.
    config = {
        "provider": provider,
        "remote_name": remote_name,
        "crypt_remote": crypt_remote,
        "remote_path": f"{crypt_remote}:",
        "sync_paths": list(SYNC_PATHS.keys()),
        "auto_sync": True,
        "sync_interval_hours": 6,
        "encrypt": True,
    }

    save_config(config)
    print(f"\nCloud provider configured: {remote_name} (encrypted via {crypt_remote})")
    print("Run 'aion-cloud-sync sync' to sync your saves.")
    return True


def get_file_hash(path: Path) -> str:
    """Get SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            sha256.update(chunk)
    return sha256.hexdigest()


def sync_to_cloud(config: dict) -> bool:
    """Sync game saves to cloud storage."""
    if not check_rclone():
        return False

    remote_path = config.get("remote_path", "")
    if not remote_path:
        logger.error("No remote path configured. Run 'aion-cloud-sync setup' first.")
        return False

    logger.info("Starting cloud sync to %s", remote_path)
    synced = 0
    errors = 0

    for save_key, save_info in SYNC_PATHS.items():
        if save_key not in config.get("sync_paths", []):
            continue

        src_path = Path(os.path.expanduser(save_info["path"]))
        if not src_path.exists():
            logger.debug("Skipping %s (not found)", save_key)
            continue

        logger.info("Syncing %s: %s", save_info["description"], src_path)

        cmd = [
            "rclone", "--config", str(RCLONE_CONFIG), "sync",
            str(src_path),
            f"{remote_path}/{save_key}",
            "--progress",
            "--transfers", "4",
            "--checkers", "8",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)

        if result.returncode == 0:
            synced += 1
            logger.info("Synced: %s", save_key)
        else:
            errors += 1
            logger.error("Failed to sync %s: %s", save_key, result.stderr)

    logger.info("Sync complete: %d synced, %d errors", synced, errors)
    return errors == 0


def restore_from_cloud(config: dict) -> bool:
    """Restore game saves from cloud storage."""
    if not check_rclone():
        return False

    remote_path = config.get("remote_path", "")
    if not remote_path:
        logger.error("No remote path configured.")
        return False

    logger.info("Starting cloud restore from %s", remote_path)
    restored = 0
    errors = 0

    for save_key, save_info in SYNC_PATHS.items():
        if save_key not in config.get("sync_paths", []):
            continue

        src_path = Path(os.path.expanduser(save_info["path"]))
        src_path.mkdir(parents=True, exist_ok=True)

        logger.info("Restoring %s: %s", save_info["description"], src_path)

        # NOTE: use `copy`, never `sync` — sync would DELETE local saves
        # that don't exist on the remote (data loss on restore).
        cmd = [
            "rclone", "--config", str(RCLONE_CONFIG), "copy",
            f"{remote_path}/{save_key}",
            str(src_path),
            "--progress",
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)

        if result.returncode == 0:
            restored += 1
            logger.info("Restored: %s", save_key)
        else:
            errors += 1
            logger.error("Failed to restore %s: %s", save_key, result.stderr)

    logger.info("Restore complete: %d restored, %d errors", restored, errors)
    return errors == 0


def list_syncable():
    """List all syncable game save locations."""
    print("Syncable game saves:")
    print("=" * 60)

    for save_key, save_info in SYNC_PATHS.items():
        src_path = Path(os.path.expanduser(save_info["path"]))
        exists = src_path.exists()
        status = "✓" if exists else "✗"

        # Count files if exists
        file_count = 0
        total_size = 0
        if exists:
            for f in src_path.rglob("*"):
                if f.is_file():
                    file_count += 1
                    total_size += f.stat().st_size

        size_str = f"{total_size / 1024 / 1024:.1f} MB" if total_size > 0 else "empty"

        print(f"  {status} {save_key}")
        print(f"    {save_info['description']}")
        print(f"    Path: {src_path}")
        print(f"    Files: {file_count}, Size: {size_str}")
        print()


def run_daemon(config: dict):
    """Run auto-sync daemon."""
    interval_hours = config.get("sync_interval_hours", 6)
    interval_seconds = interval_hours * 3600

    logger.info("Cloud sync daemon started (interval: %d hours)", interval_hours)

    while True:
        try:
            sync_to_cloud(config)
        except Exception as e:
            logger.error("Sync failed: %s", e)

        time.sleep(interval_seconds)


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Aion Cloud Sync")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("setup", help="Configure cloud provider")
    sub.add_parser("sync", help="Sync saves to cloud")
    sub.add_parser("restore", help="Restore saves from cloud")
    sub.add_parser("list", help="List syncable games")
    sub.add_parser("daemon", help="Run auto-sync daemon")

    args = parser.parse_args()

    if args.command == "setup":
        setup_cloud_provider()
    elif args.command == "sync":
        config = load_config()
        sync_to_cloud(config)
    elif args.command == "restore":
        config = load_config()
        restore_from_cloud(config)
    elif args.command == "list":
        list_syncable()
    elif args.command == "daemon":
        config = load_config()
        run_daemon(config)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
