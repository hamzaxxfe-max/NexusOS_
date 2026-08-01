#!/usr/bin/env python3
import json
import glob
import os
import time
import signal
import logging
import logging.handlers
from pathlib import Path

STATS_PATH = Path("/tmp/aion-gpu-stats.json")
LOG_DIR = Path("/var/log/aion")
LOG_PATH = LOG_DIR / "gpu-monitor.log"
POLL_INTERVAL = 2
HISTORY_LENGTH = 60

logger = logging.getLogger("gpu-monitor")


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        str(LOG_PATH), maxBytes=2 * 1024 * 1024, backupCount=3
    )
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(console)


def find_intel_gpu():
    cards = sorted(glob.glob("/sys/class/drm/card*"))
    for card in cards:
        vendor_path = os.path.join(card, "device", "vendor")
        try:
            with open(vendor_path, "r") as f:
                vendor = f.read().strip()
            if vendor == "0x8086":
                return card
        except (FileNotFoundError, PermissionError):
            continue
    return None


def read_sysfs(path):
    """Read a sysfs attribute without following symlinks (O_NOFOLLOW).

    A malicious user could swap a sysfs file for a symlink pointing at a
    sensitive file (e.g. /etc/shadow) — reading it would leak contents into
    the stats JSON. O_NOFOLLOW refuses such paths.
    """
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return ""
    try:
        with os.fdopen(fd, "r") as f:
            return f.read().strip()
    except OSError:
        return ""


def read_gpu_stats(gpu_path):
    stats = {
        "timestamp": time.time(),
        "gpu_path": gpu_path,
        "available": True,
    }
    fields = {
        "cur_freq_mhz": "gt_cur_freq_mhz",
        "actual_freq_mhz": "gt_actual_freq_mhz",
        "max_freq_mhz": "gt_max_freq_mhz",
        "min_freq_mhz": "gt_min_freq_mhz",
        "boost_freq_mhz": "gt_boost_freq_mhz",
        "rp0_freq_mhz": "gt_RP0_freq_mhz",
        "rp1_freq_mhz": "gt_RP1_freq_mhz",
        "rpn_freq_mhz": "gt_RPn_freq_mhz",
        "rc6_residency": "gt_rc6_residency",
        "forcewake": "gt_forcewake",
        "busy": "gt_busy",
        "frame_time": "gt_frame_time_us",
        "power_up": "gt_power",
        "thermal_limit": "gt_power_limit",
    }
    for key, filename in fields.items():
        val = read_sysfs(os.path.join(gpu_path, "device", filename))
        if not val:
            val = read_sysfs(os.path.join(gpu_path, filename))
        if val:
            try:
                stats[key] = int(val)
            except ValueError:
                stats[key] = val
        else:
            stats[key] = None

    for attr_file in glob.glob(os.path.join(gpu_path, "device", "hwmon", "hwmon*", "power*")):
        attr_name = os.path.basename(attr_file)
        val = read_sysfs(attr_file)
        if val:
            try:
                stats["hwmon_" + attr_name] = int(val)
            except ValueError:
                pass

    stats["gpu_temp"] = None
    for temp_file in glob.glob(os.path.join(gpu_path, "device", "hwmon", "hwmon*", "temp*_input")):
        val = read_sysfs(temp_file)
        if val:
            try:
                stats["gpu_temp"] = int(val) / 1000.0
            except ValueError:
                pass
            break

    return stats


def write_stats(stats):
    tmp_path = STATS_PATH.with_suffix(".tmp")
    try:
        with open(tmp_path, "w") as f:
            json.dump(stats, f, indent=None)
        tmp_path.replace(STATS_PATH)
    except OSError as e:
        logger.error("Failed to write stats: %s", e)


def main():
    setup_logging()
    logger.info("GPU monitor daemon starting")

    gpu_path = find_intel_gpu()
    if not gpu_path:
        logger.error("No Intel GPU found. Daemon exiting.")
        return

    logger.info("Monitoring Intel GPU at %s", gpu_path)

    running = True

    def handle_signal(signum, frame):
        nonlocal running
        if signum == signal.SIGTERM:
            logger.info("Received SIGTERM, shutting down")
            running = False
        elif signum == signal.SIGINT:
            logger.info("Received SIGINT, shutting down")
            running = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    history = []

    while running:
        try:
            stats = read_gpu_stats(gpu_path)
            history.append(stats)
            if len(history) > HISTORY_LENGTH:
                history = history[-HISTORY_LENGTH:]
            stats["history"] = history[-HISTORY_LENGTH:]
            stats["driver_version"] = (
                read_sysfs(os.path.join(gpu_path, "device", "driver", "module", "version"))
                or read_sysfs(os.path.join(gpu_path, "driver", "module", "version"))
                or "unknown"
            )
            write_stats(stats)

            cur = stats.get("cur_freq_mhz", "?")
            actual = stats.get("actual_freq_mhz", "?")
            temp = stats.get("gpu_temp")
            rc6 = stats.get("rc6_residency", "?")
            temp_str = "%.1fC" % temp if temp else "N/A"
            logger.debug(
                "cur=%sMHz actual=%sMHz temp=%s rc6=%s",
                cur, actual, temp_str, rc6
            )
        except Exception as e:
            logger.error("Poll error: %s", e)

        for _ in range(POLL_INTERVAL * 10):
            if not running:
                break
            time.sleep(0.1)

    logger.info("GPU monitor daemon stopped")
    try:
        STATS_PATH.unlink(missing_ok=True)
    except OSError:
        pass


if __name__ == "__main__":
    main()
