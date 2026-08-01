#!/usr/bin/env python3
"""Aion OTA Update System."""

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    from packaging.version import Version, InvalidVersion
except ImportError:
    print("Error: 'packaging' module required. Install with: pip install packaging", file=sys.stderr)
    sys.exit(1)

try:
    from ota_compression import decompress as _decompress_archive
except ImportError:
    _decompress_archive = None

CONFIG_PATH = Path("/etc/aion/config.json")
MANIFEST_URL = "https://raw.githubusercontent.com/username/aion/main/manifest.json"
MANIFEST_CACHE = Path("/var/cache/aion/manifest.json")
LOG_DIR = Path("/var/log/aion")
LOG_FILE = LOG_DIR / "ota-updater.log"
UPDATE_MARKER = Path("/var/run/aion-update-ready")
NOTIFICATION_SENT = Path("/var/lib/aion/.notification-sent")
BTRFS_MOUNT = Path("/mnt/aion-update")
SNAPSHOT_ROOT = Path("/")
MAX_RETRIES = 3
RETRY_DELAY = 5
CURL_TIMEOUT = 300
AB_MANAGER = Path("/usr/bin/aion-ab-manager")
AB_STATE = Path("/etc/aion-ab-state")

logger = logging.getLogger("aion-ota")


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.setLevel(logging.DEBUG)


# ── A/B Slot Management ──────────────────────────────────────────────

def get_active_slot() -> str:
    """Get the current active boot slot (A or B)."""
    if AB_MANAGER.exists():
        try:
            result = subprocess.run(
                [str(AB_MANAGER), "status"],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.strip().split("\n"):
                if line.startswith("Active slot:"):
                    slot = line.split(":")[-1].strip()
                    if slot in ("A", "B"):
                        return slot
        except (subprocess.TimeoutExpired, Exception) as e:
            logger.warning("Failed to get active slot via manager: %s", e)

    # Fallback: parse BLS entries
    entries_dir = Path("/boot/loader/entries")
    for entry in entries_dir.glob("aion-*.conf"):
        try:
            content = entry.read_text()
            if "active" in content:
                if "Slot A" in content:
                    return "A"
                elif "Slot B" in content:
                    return "B"
        except Exception:
            continue

    return "A"


def get_inactive_slot() -> str:
    """Get the inactive boot slot (target for updates)."""
    return "B" if get_active_slot() == "A" else "A"


def get_slot_subvol(slot: str) -> str:
    """Get the Btrfs subvolume for a given slot."""
    return f"@{slot}"


def set_boot_slot(slot: str) -> bool:
    """Switch the bootloader to boot from the given slot."""
    logger.info("Setting boot slot to %s", slot)

    if AB_MANAGER.exists():
        try:
            result = subprocess.run(
                [str(AB_MANAGER), "switch"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                logger.info("Boot slot switched via A/B manager")
                return True
        except (subprocess.TimeoutExpired, Exception) as e:
            logger.warning("A/B manager switch failed: %s", e)

    # Fallback: manually update BLS entries
    entries_dir = Path("/boot/loader/entries")
    loader_conf = Path("/boot/loader/loader.conf")

    try:
        for entry in entries_dir.glob("aion-*.conf"):
            content = entry.read_text()
            if f"Slot {slot}" in content and "active" not in content:
                content = content.replace(f"Slot {slot}", f"Slot {slot} (active)")
                entry.write_text(content)
            elif "active" in content and f"Slot {slot}" not in content:
                content = content.replace(" (active)", "")
                entry.write_text(content)

        # Update default in loader.conf
        loader_content = loader_conf.read_text()
        loader_content = loader_content.replace(
            "default aion-a.conf" if slot == "B" else "default aion-b.conf",
            f"default aion-{slot.lower()}.conf",
        )
        loader_conf.write_text(loader_content)

        logger.info("BLS entries updated for slot %s", slot)
        return True

    except Exception as e:
        logger.error("Failed to update BLS entries: %s", e)
        return False


def load_system_config() -> dict:
    if not CONFIG_PATH.exists():
        logger.error("System config not found at %s", CONFIG_PATH)
        sys.exit(1)
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def get_current_version(config: dict) -> str:
    version = config.get("system", {}).get("version")
    if not version:
        logger.error("No version found in system config")
        sys.exit(1)
    logger.info("Current system version: %s", version)
    return version


def fetch_manifest(use_cache: bool = False) -> dict:
    if use_cache and MANIFEST_CACHE.exists():
        logger.info("Using cached manifest from %s", MANIFEST_CACHE)
        with open(MANIFEST_CACHE, "r") as f:
            return json.load(f)

    logger.info("Fetching manifest from %s", MANIFEST_URL)
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = subprocess.run(
                [
                    "curl", "-fsSL",
                    "--connect-timeout", "30",
                    "--max-time", str(CURL_TIMEOUT),
                    "-H", "Accept: application/json",
                    MANIFEST_URL,
                ],
                capture_output=True,
                text=True,
                timeout=CURL_TIMEOUT + 30,
            )
            if result.returncode != 0:
                raise RuntimeError(f"curl failed with code {result.returncode}: {result.stderr}")

            manifest = json.loads(result.stdout)
            MANIFEST_CACHE.parent.mkdir(parents=True, exist_ok=True)
            with open(MANIFEST_CACHE, "w") as f:
                json.dump(manifest, f, indent=2)
            logger.info("Manifest fetched successfully, latest version: %s", manifest.get("latest_version"))
            return manifest

        except (subprocess.TimeoutExpired, RuntimeError, json.JSONDecodeError) as e:
            logger.warning("Fetch attempt %d/%d failed: %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    logger.error("Failed to fetch manifest after %d attempts", MAX_RETRIES)
    sys.exit(1)


def compare_versions(current: str, latest: str) -> int:
    try:
        v_current = Version(current)
        v_latest = Version(latest)
    except InvalidVersion as e:
        logger.error("Invalid version string: %s", e)
        sys.exit(1)

    if v_latest > v_current:
        return 1
    elif v_latest < v_current:
        return -1
    return 0


def find_incremental_patch(current: str, latest: str, patches: list) -> Optional[dict]:
    for patch in patches:
        if patch.get("from_version") == current and patch.get("to_version") == latest:
            if patch.get("patch_url") and patch.get("sha256"):
                return patch
    return None


def download_file(url: str, dest: Path, desc: str = "file") -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Downloading %s: %s", desc, url)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = subprocess.run(
                [
                    "curl", "-fSL",
                    "--connect-timeout", "30",
                    "--max-time", str(CURL_TIMEOUT),
                    "--retry", "2",
                    "--retry-delay", "3",
                    "-o", str(dest),
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=CURL_TIMEOUT + 60,
            )
            if result.returncode != 0:
                logger.warning("Download attempt %d/%d failed: %s", attempt, MAX_RETRIES, result.stderr)
                if dest.exists():
                    dest.unlink()
                continue

            if dest.exists():
                file_size = dest.stat().st_size
                logger.info("Downloaded %s: %d bytes", desc, file_size)
                return True

        except subprocess.TimeoutExpired:
            logger.warning("Download attempt %d/%d timed out", attempt, MAX_RETRIES)
            if dest.exists():
                dest.unlink()

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY * attempt)

    logger.error("Failed to download %s after %d attempts", desc, MAX_RETRIES)
    return False


def verify_checksum(file_path: Path, expected_sha256: str) -> bool:
    if not file_path.exists():
        logger.error("File not found for checksum verification: %s", file_path)
        return False

    logger.info("Verifying SHA256 checksum of %s", file_path.name)
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            sha256_hash.update(chunk)

    actual_sha256 = sha256_hash.hexdigest()
    if actual_sha256 == expected_sha256:
        logger.info("Checksum verification passed")
        return True

    logger.error("Checksum mismatch: expected %s, got %s", expected_sha256, actual_sha256)
    return False


_MAGIC_ZST = b"\x28\xb5\x2f\xfd"
_MAGIC_LZ4 = b"\x04\x22\x4d\x18"
_MAGIC_XZ = b"\xfd\x37\x7a\x58\x5a\x00"


def _detect_archive_suffix(path: Path) -> str:
    """Return a real archive suffix based on magic bytes, not file name."""
    try:
        with open(path, "rb") as f:
            head = f.read(6)
    except OSError:
        return path.suffix.lower()
    if head.startswith(_MAGIC_ZST):
        return ".zst"
    if head.startswith(_MAGIC_LZ4):
        return ".lz4"
    if head.startswith(_MAGIC_XZ):
        return ".xz"
    return path.suffix.lower()


def prepare_downloaded_file(path: Path) -> Path:
    """Decompress a downloaded archive in place (ZSTD/LZ4/XZ) before use.

    Detects archives by magic bytes, so it also handles payloads that were
    stored with a misleading file name (e.g. a compressed xdelta patch).
    Preserves the original archive; only returns the decompressed path when
    decompression fully succeeds. Falls back to the raw path for anything
    that is not a recognised archive.
    """
    if not path.exists():
        return path
    suffix = _detect_archive_suffix(path)
    if suffix not in (".zst", ".zstd", ".lz4", ".xz"):
        return path

    if _decompress_archive is None:
        logger.error("Compression layer unavailable; cannot use %s", path)
        return path

    extracted = path.with_suffix("")
    logger.info("Decompressing update payload: %s -> %s", path.name, extracted.name)
    if _decompress_archive(path, extracted):
        logger.info("Decompressed payload ready (%d bytes)", extracted.stat().st_size)
        return extracted

    logger.error("Decompression of %s failed; update will not use it", path)
    return path


def find_inactive_snapshot() -> Optional[str]:
    try:
        result = subprocess.run(
            ["btrfs", "subvolume", "list", "/"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error("Failed to list btrfs subvolumes: %s", result.stderr)
            return None

        subvolumes = []
        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 9:
                subvol_path = parts[-1]
                subvol_id = parts[1]
                subvolumes.append({"id": int(subvol_id), "path": subvol_path})

        subvolumes.sort(key=lambda x: x["id"], reverse=True)

        for subvol in subvolumes:
            path = subvol["path"]
            if "@-inactive" in path or "@-backup" in path or "@-previous" in path:
                return f"/{path}"

        active = "@"
        for subvol in subvolumes:
            path = subvol["path"]
            if path != active and path.startswith("@"):
                return f"/{path}"

        return None

    except (subprocess.TimeoutExpired, Exception) as e:
        logger.error("Error finding inactive snapshot: %s", e)
        return None


def create_snapshot(name: str) -> Optional[str]:
    snapshot_path = f"/{name}"
    logger.info("Creating snapshot: %s", snapshot_path)

    try:
        result = subprocess.run(
            ["btrfs", "subvolume", "snapshot", "/", snapshot_path],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            logger.error("Failed to create snapshot: %s", result.stderr)
            return None

        logger.info("Snapshot created: %s", snapshot_path)
        return snapshot_path

    except subprocess.TimeoutExpired:
        logger.error("Snapshot creation timed out")
        return None


def delete_snapshot(name: str) -> bool:
    snapshot_path = f"/{name}"
    logger.info("Deleting snapshot: %s", snapshot_path)

    try:
        result = subprocess.run(
            ["btrfs", "subvolume", "delete", snapshot_path],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            logger.warning("Failed to delete snapshot: %s", result.stderr)
            return False

        logger.info("Snapshot deleted: %s", snapshot_path)
        return True

    except subprocess.TimeoutExpired:
        logger.error("Snapshot deletion timed out")
        return False


def apply_full_update(iso_path: Path, target_snapshot: str) -> bool:
    logger.info("Applying full update to snapshot: %s", target_snapshot)

    BTRFS_MOUNT.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            ["mount", "-o", f"subvol={target_snapshot.lstrip('/')}", "/dev/disk/by-label/aion", str(BTRFS_MOUNT)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            logger.error("Failed to mount snapshot: %s", result.stderr)
            return False

        mount_point = str(BTRFS_MOUNT)
        logger.info("Extracting update to %s", mount_point)

        result = subprocess.run(
            ["bsdtar", "-xpf", str(iso_path), "-C", mount_point],
            capture_output=True, text=True, timeout=1800,
        )
        if result.returncode != 0:
            logger.error("Failed to extract update: %s", result.stderr)
            return False

        nexus_dir = Path(mount_point) / "aion"
        if nexus_dir.exists():
            squashfs_files = list(nexus_dir.glob("*.squashfs"))
            if squashfs_files:
                overlay_mount = Path(mount_point) / "opt" / "aion" / "squashfs-root"
                overlay_mount.mkdir(parents=True, exist_ok=True)

                for sq in squashfs_files:
                    result = subprocess.run(
                        ["mount", "-t", "squashfs", "-o", "ro,loop", str(sq), str(overlay_mount)],
                        capture_output=True, text=True, timeout=60,
                    )
                    if result.returncode == 0:
                        result = subprocess.run(
                            ["rsync", "-aAX", f"{overlay_mount}/", mount_point + "/"],
                            capture_output=True, text=True, timeout=1800,
                        )
                        subprocess.run(["umount", str(overlay_mount)], capture_output=True, timeout=30)

        result = subprocess.run(
            ["umount", str(BTRFS_MOUNT)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            logger.warning("Failed to unmount snapshot: %s", result.stderr)

        logger.info("Full update applied successfully to %s", target_snapshot)
        return True

    except subprocess.TimeoutExpired as e:
        logger.error("Full update timed out: %s", e)
        subprocess.run(["umount", str(BTRFS_MOUNT)], capture_output=True, timeout=30)
        return False
    except Exception as e:
        logger.error("Full update failed: %s", e)
        subprocess.run(["umount", str(BTRFS_MOUNT)], capture_output=True, timeout=30)
        return False


def apply_incremental_patch(patch_info: dict, target_snapshot: str) -> bool:
    patch_url = patch_info["patch_url"]
    patch_sha256 = patch_info["sha256"]

    patch_cache = Path("/var/cache/aion/patches")
    patch_cache.mkdir(parents=True, exist_ok=True)
    patch_file = patch_cache / f"patch-{patch_info['from_version']}-to-{patch_info['to_version']}.xdelta"

    if not download_file(patch_url, patch_file, "incremental patch"):
        return False

    patch_file = prepare_downloaded_file(patch_file)

    if not verify_checksum(patch_file, patch_sha256):
        patch_file.unlink()
        return False

    BTRFS_MOUNT.mkdir(parents=True, exist_ok=True)

    try:
        result = subprocess.run(
            ["mount", "-o", f"subvol={target_snapshot.lstrip('/')}", "/dev/disk/by-label/aion", str(BTRFS_MOUNT)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            logger.error("Failed to mount snapshot: %s", result.stderr)
            return False

        logger.info("Applying xdelta patch to %s", BTRFS_MOUNT)

        result = subprocess.run(
            ["xdelta3", "-d", "-f", "-s", str(BTRFS_MOUNT), str(patch_file), str(BTRFS_MOUNT) + ".updated"],
            capture_output=True, text=True, timeout=1800,
        )
        if result.returncode != 0:
            logger.error("xdelta3 failed: %s", result.stderr)
            subprocess.run(["umount", str(BTRFS_MOUNT)], capture_output=True, timeout=30)
            return False

        result = subprocess.run(
            ["mv", str(BTRFS_MOUNT) + ".updated", str(BTRFS_MOUNT)],
            capture_output=True, text=True, timeout=60,
        )

        subprocess.run(["umount", str(BTRFS_MOUNT)], capture_output=True, timeout=60)
        logger.info("Incremental patch applied successfully")
        return True

    except subprocess.TimeoutExpired as e:
        logger.error("Patch application timed out: %s", e)
        subprocess.run(["umount", str(BTRFS_MOUNT)], capture_output=True, timeout=30)
        return False


def set_next_boot_snapshot(snapshot_name: str) -> bool:
    """Set next boot to use the inactive slot (A/B style)."""
    inactive = get_inactive_slot()
    logger.info("Setting next boot to Slot %s (subvol %s)", inactive, get_slot_subvol(inactive))

    # Update fstab for inactive slot
    btrfs_opts = Path("/etc/fstab")
    if btrfs_opts.exists():
        try:
            import re
            content = btrfs_opts.read_text()
            content = re.sub(r"subvol=@[AB]", f"subvol={get_slot_subvol(inactive)}", content)
            btrfs_opts.write_text(content)
            logger.info("fstab updated for slot %s", inactive)
        except Exception as e:
            logger.warning("Failed to update fstab: %s", e)

    # Switch BLS boot entry
    return set_boot_slot(inactive)


def write_update_marker(version: str, notes: str = "") -> None:
    marker_data = {
        "version": version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "release_notes": notes,
        "applied": True,
    }
    UPDATE_MARKER.parent.mkdir(parents=True, exist_ok=True)
    with open(UPDATE_MARKER, "w") as f:
        json.dump(marker_data, f, indent=2)
    logger.info("Update marker written for version %s", version)


def send_notification(version: str, notes: str = "") -> bool:
    if NOTIFICATION_SENT.exists():
        try:
            with open(NOTIFICATION_SENT, "r") as f:
                if f.read().strip() == version:
                    return True
        except Exception:
            pass

    message = f"Aion {version} is ready to install."
    if notes:
        message += f" {notes[:200]}"

    try:
        result = subprocess.run(
            ["notify-send", "-i", "system-software-update", "Aion Update Available", message],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            NOTIFICATION_SENT.parent.mkdir(parents=True, exist_ok=True)
            NOTIFICATION_SENT.write_text(version)
            logger.info("Desktop notification sent for version %s", version)
            return True
        else:
            logger.debug("notify-send not available or failed: %s", result.stderr)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.debug("notify-send not available")

    return False


def check_for_updates() -> dict:
    config = load_system_config()
    current = get_current_version(config)
    manifest = fetch_manifest()
    latest = manifest.get("latest_version", "0.0.0")

    result = {
        "current_version": current,
        "latest_version": latest,
        "update_available": False,
        "incremental_available": False,
        "manifest": manifest,
    }

    comparison = compare_versions(current, latest)
    if comparison <= 0:
        if comparison == 0:
            logger.info("System is up to date (v%s)", current)
        else:
            logger.info("System version %s is newer than latest %s", current, latest)
        return result

    result["update_available"] = True
    logger.info("Update available: %s → %s", current, latest)

    patches = manifest.get("incremental_patches", [])
    patch = find_incremental_patch(current, latest, patches)
    if patch:
        result["incremental_available"] = True
        result["patch_info"] = patch
        logger.info("Incremental update available: %s → %s (%d MB)",
                     current, latest, patch.get("size", 0) // 1024 // 1024)
    else:
        logger.info("No incremental patch available. Full update required.")

    return result


def system_busy(load_threshold: float = 3.0, min_uptime_min: int = 5) -> bool:
    """Check whether the machine is too busy / freshly booted for a silent
    background update. Returns True when the updater should defer."""
    try:
        with open("/proc/loadavg", "r", encoding="utf-8") as f:
            parts = f.read().split()
            load1 = float(parts[0])
        if load1 > load_threshold:
            logger.info("Deferring silent update: load %.2f > %.1f", load1, load_threshold)
            return True
    except (OSError, ValueError, IndexError):
        pass

    try:
        with open("/proc/uptime", "r", encoding="utf-8") as f:
            uptime_min = float(f.read().split()[0]) / 60.0
        if uptime_min < min_uptime_min:
            logger.info(
                "Deferring silent update: uptime %.0f min < %d min",
                uptime_min, min_uptime_min,
            )
            return True
    except (OSError, ValueError, IndexError):
        pass

    return False


def apply_update(silent: bool = False) -> bool:
    """Apply update by writing to inactive slot, then switching boot entry.

    With ``silent=True`` the flow behaves identically but skips desktop
    notifications so a gamer is never interrupted mid-session.
    """
    result = check_for_updates()
    if not result["update_available"]:
        logger.info("No update to apply")
        return True

    current = result["current_version"]
    latest = result["latest_version"]
    manifest = result["manifest"]
    inactive = get_inactive_slot()
    inactive_subvol = get_slot_subvol(inactive)

    logger.info("Applying update: %s → %s (writing to Slot %s)", current, latest, inactive)

    # Step 1: Mount inactive slot subvolume
    BTRFS_MOUNT.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["mount", "-o", f"subvol={inactive_subvol}", "/dev/disk/by-label/aion", str(BTRFS_MOUNT)],
            capture_output=True, text=True, timeout=60,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.error("Failed to mount inactive slot: %s", e)
        return False

    # Step 2: Apply update (incremental or full)
    success = False
    if result["incremental_available"]:
        success = apply_incremental_patch(result["patch_info"], inactive_subvol)
    else:
        iso_url = manifest.get("download_url", "")
        if not iso_url:
            logger.error("No download URL in manifest")
            subprocess.run(["umount", str(BTRFS_MOUNT)], capture_output=True, timeout=30)
            return False

        iso_cache = Path("/var/cache/aion/isos")
        iso_cache.mkdir(parents=True, exist_ok=True)
        iso_file = iso_cache / f"aion-{latest}.iso"

        if not iso_file.exists():
            if not download_file(iso_url, iso_file, f"ISO v{latest}"):
                subprocess.run(["umount", str(BTRFS_MOUNT)], capture_output=True, timeout=30)
                return False

            iso_file = prepare_downloaded_file(iso_file)

            iso_sha256 = manifest.get("sha256", "")
            if iso_sha256 and not verify_checksum(iso_file, iso_sha256):
                iso_file.unlink()
                subprocess.run(["umount", str(BTRFS_MOUNT)], capture_output=True, timeout=30)
                return False

        success = apply_full_update(iso_file, inactive_subvol)

    # Step 3: Unmount
    subprocess.run(["umount", str(BTRFS_MOUNT)], capture_output=True, timeout=30)

    if not success:
        logger.error("Update application failed")
        return False

    # Step 4: Switch boot entry to inactive slot
    if not set_boot_slot(inactive):
        logger.error("Failed to switch boot slot. Update NOT applied.")
        return False

    # Step 5: Reset boot counter (mark-good will run on next successful boot)
    if AB_MANAGER.exists():
        try:
            subprocess.run([str(AB_MANAGER), "mark-good"], capture_output=True, timeout=10)
        except Exception:
            pass

    write_update_marker(latest, manifest.get("release_notes", ""))
    if silent:
        logger.info("Silent update staged to Slot %s (activate on reboot)", inactive)
    else:
        send_notification(latest, manifest.get("release_notes", ""))
    logger.info("Update applied. Reboot to activate Slot %s.", inactive)
    return True


def rollback() -> bool:
    """Rollback by switching to the inactive slot (A/B style)."""
    active = get_active_slot()
    inactive = get_inactive_slot()

    logger.info("Rolling back: Slot %s → Slot %s", active, inactive)

    if AB_MANAGER.exists():
        try:
            result = subprocess.run(
                [str(AB_MANAGER), "rollback"],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                logger.info("Rollback completed via A/B manager")
                return True
        except (subprocess.TimeoutExpired, Exception) as e:
            logger.warning("A/B manager rollback failed: %s", e)

    # Fallback: switch boot slot
    if set_boot_slot(inactive):
        logger.info("Rollback configured. Reboot to activate Slot %s.", inactive)
        send_notification("rollback", f"System will roll back to Slot {inactive} on next boot.")
        return True

    logger.error("Rollback failed")
    return False


def install_systemd_timer() -> bool:
    service_src = Path(__file__).parent / "aion-ota.service"
    timer_src = Path(__file__).parent / "aion-ota.timer"

    service_dst = Path("/etc/systemd/system/aion-ota.service")
    timer_dst = Path("/etc/systemd/system/aion-ota.timer")

    try:
        if service_src.exists():
            shutil.copy2(str(service_src), str(service_dst))
        if timer_src.exists():
            shutil.copy2(str(timer_src), str(timer_dst))

        subprocess.run(["systemctl", "daemon-reload"], capture_output=True, timeout=30)
        subprocess.run(["systemctl", "enable", "aion-ota.timer"], capture_output=True, timeout=30)
        subprocess.run(["systemctl", "start", "aion-ota.timer"], capture_output=True, timeout=30)

        logger.info("OTA timer installed and started")
        return True

    except Exception as e:
        logger.error("Failed to install systemd timer: %s", e)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Aion OTA Update System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  aion-ota --check-only       Check for available updates
  aion-ota --apply            Download and apply update
  aion-ota --rollback         Rollback to previous snapshot
  aion-ota --install-timer    Install systemd timer for automatic checks
        """,
    )
    parser.add_argument("--check-only", action="store_true", help="Check for updates without applying")
    parser.add_argument("--apply", action="store_true", help="Download and apply available update")
    parser.add_argument("--rollback", action="store_true", help="Rollback to previous system snapshot")
    parser.add_argument("--install-timer", action="store_true", help="Install systemd timer for OTA checks")
    parser.add_argument("--force", action="store_true", help="Force update even if already up to date")
    parser.add_argument("--silent", action="store_true", help="Apply update without desktop notifications")
    parser.add_argument("--defer-if-busy", action="store_true", help="Skip update when system load is high or uptime is low")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    setup_logging()
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    if args.install_timer:
        success = install_systemd_timer()
        sys.exit(0 if success else 1)

    if args.rollback:
        success = rollback()
        sys.exit(0 if success else 1)

    if args.check_only:
        result = check_for_updates()
        if result["update_available"]:
            print(f"Update available: {result['current_version']} → {result['latest_version']}")
            if result["incremental_available"]:
                print("Incremental update available (smaller download)")
            else:
                print("Full update required")
            sys.exit(0)
        else:
            print(f"System is up to date (v{result['current_version']})")
            sys.exit(0)

    if args.apply:
        if args.defer_if_busy and system_busy():
            logger.info("System busy; deferring update to a later run")
            sys.exit(0)
        success = apply_update(silent=args.silent)
        sys.exit(0 if success else 1)

    result = check_for_updates()
    if result["update_available"]:
        print(f"Update available: {result['current_version']} → {result['latest_version']}")
        print("Use --apply to install the update")
    else:
        print(f"System is up to date (v{result['current_version']})")


if __name__ == "__main__":
    main()
