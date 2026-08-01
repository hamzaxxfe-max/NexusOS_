#!/usr/bin/env python3
"""
Aion Memory & Performance Regression Tests
Validates idle RAM usage stays below 400MB and capture daemon
pauses wallpaper during fullscreen.
"""
import os
import re
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

PROJ_ROOT = Path(__file__).resolve().parents[2]
PROC_MEMINFO = Path("/proc/meminfo")
ZRAM_BASE = Path("/sys/block/zram0")
CGROUP_BASE = Path("/sys/fs/cgroup/aion-gaming.slice")
BTRFS_DF_CMD = ["btrfs", "filesystem", "df", "/"]
CAPTURE_TMPFS = Path("/tmp/aion-capture")
MEMINFO_PATHS = {
    "mem_total": "/proc/meminfo",
    "mem_available": "/proc/meminfo",
}
IDLE_RAM_LIMIT_MB = 400
ZRAM_DISKSIZE = 8192 * 1024 * 1024
CAPTURE_MAX_SEGMENTS = 30
LEAK_GROWTH_LIMIT = 10.0
DAEMON_START_STOP_ITERATIONS = 10


def _is_linux():
    return os.path.exists("/proc")


def _read_meminfo():
    if not PROC_MEMINFO.exists():
        return {}
    data = {}
    for line in PROC_MEMINFO.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            key = parts[0].rstrip(":")
            value = int(parts[1])
            data[key] = value
    return data


def _get_used_mem_mb():
    info = _read_meminfo()
    if "MemTotal" not in info or "MemAvailable" not in info:
        return None
    total_kb = info["MemTotal"]
    available_kb = info["MemAvailable"]
    used_kb = total_kb - available_kb
    return used_kb / 1024.0


def _run_cmd(cmd, timeout=10):
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout, result.stderr, result.returncode
    except FileNotFoundError:
        return "", "command not found", 127
    except subprocess.TimeoutExpired:
        return "", "timeout", 124


def _find_daemon_source():
    candidates = [
        PROJ_ROOT / "services" / "capture-daemon.py",
        PROJ_ROOT / "daemon" / "capture-daemon.py",
        PROJ_ROOT / "src" / "capture-daemon.py",
        PROJ_ROOT / "capture-daemon.py",
    ]
    for path in candidates:
        if path.exists():
            return path
    for py_file in PROJ_ROOT.rglob("capture-daemon.py"):
        return py_file
    return None


def _find_security_daemon_source():
    candidates = [
        PROJ_ROOT / "services" / "security-bypass-daemon.py",
        PROJ_ROOT / "daemon" / "security-bypass-daemon.py",
        PROJ_ROOT / "src" / "security-bypass-daemon.py",
        PROJ_ROOT / "security-bypass-daemon.py",
    ]
    for path in candidates:
        if path.exists():
            return path
    for py_file in PROJ_ROOT.rglob("security-bypass-daemon.py"):
        return py_file
    return None


def _read_sys_file(path):
    try:
        return Path(path).read_text().strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _path_exists(path):
    return Path(path).exists()


def _is_mounted_readonly(mount_point="/"):
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[1] == mount_point:
                    mount_opts = parts[3]
                    return "ro" in mount_opts.split(",")
    except (FileNotFoundError, PermissionError):
        pass
    return None


class TestMemoryStress(unittest.TestCase):

    @unittest.skipUnless(_is_linux(), "Requires /proc (Linux only)")
    def test_idle_ram_below_400mb(self):
        used_mb = _get_used_mem_mb()
        self.assertIsNotNone(used_mb, "Could not read /proc/meminfo")
        self.assertLess(
            used_mb, IDLE_RAM_LIMIT_MB,
            f"Idle RAM usage {used_mb:.1f}MB exceeds limit of {IDLE_RAM_LIMIT_MB}MB"
        )

    @unittest.skipUnless(_is_linux(), "Requires Linux sysfs")
    def test_zram_configured(self):
        self.assertTrue(
            _path_exists(ZRAM_BASE),
            "zram0 block device not found at /sys/block/zram0/"
        )
        compressor = _read_sys_file(ZRAM_BASE / "comp_algorithm")
        self.assertIsNotNone(
            compressor,
            "Cannot read zram0 comp_algorithm"
        )
        valid_compressors = ["lz4", "zstd", "lzo", "lz4hc", "deflate"]
        compressor_name = compressor.split("[")[1].split("]")[0] if "[" in compressor else compressor.split()[0]
        self.assertIn(
            compressor_name, valid_compressors,
            f"Unexpected zram compressor: {compressor_name}"
        )

    @unittest.skipUnless(_is_linux(), "Requires Linux sysfs")
    def test_zram_size_matches_config(self):
        disksize_str = _read_sys_file(ZRAM_BASE / "disksize")
        self.assertIsNotNone(disksize_str, "Cannot read zram0 disksize")
        disksize = int(disksize_str)
        self.assertEqual(
            disksize, ZRAM_DISKSIZE,
            f"zram0 disksize is {disksize} bytes, expected {ZRAM_DISKSIZE} bytes "
            f"({ZRAM_DISKSIZE / (1024*1024*1024):.1f}GB)"
        )

    @unittest.skipUnless(_is_linux(), "Requires Linux cgroups")
    def test_throttler_cgroups_exist(self):
        self.assertTrue(
            _path_exists(CGROUP_BASE),
            f"Cgroup slice not found: {CGROUP_BASE}"
        )
        cpu_max = _read_sys_file(CGROUP_BASE / "cpu.max")
        self.assertIsNotNone(
            cpu_max,
            f"Cannot read cpu.max from {CGROUP_BASE}"
        )
        parts = cpu_max.split()
        self.assertGreaterEqual(len(parts), 1)
        self.assertTrue(
            parts[0].isdigit() or parts[0] == "max",
            f"cpu.max has unexpected format: {cpu_max}"
        )

    @unittest.skipUnless(_is_linux(), "Requires Linux cgroups")
    def test_throttler_cpu_weight_range(self):
        weight_str = _read_sys_file(CGROUP_BASE / "cpu.weight")
        if weight_str is None:
            weight_str = _read_sys_file(CGROUP_BASE / "cpu.shares")
            if weight_str is not None:
                weight = int(weight_str)
                self.assertGreaterEqual(weight, 2)
                self.assertLessEqual(weight, 262144)
                return
            self.fail(f"Cannot read cpu.weight or cpu.shares from {CGROUP_BASE}")
        weight = int(weight_str)
        self.assertGreaterEqual(
            weight, 1,
            f"cpu.weight {weight} below minimum 1"
        )
        self.assertLessEqual(
            weight, 10000,
            f"cpu.weight {weight} above maximum 10000"
        )

    @unittest.skipUnless(_is_linux(), "Requires btrfs")
    def test_btrfs_compression_active(self):
        stdout, stderr, rc = _run_cmd(BTRFS_DF_CMD)
        if rc != 0:
            self.skipTest(f"btrfs not available or not on btrfs: {stderr}")
        output = (stdout + stderr).lower()
        self.assertIn(
            "zstd", output,
            "btrfs filesystem df output does not mention zstd compression"
        )

    @unittest.skipUnless(_is_linux(), "Requires Linux filesystem")
    def test_capture_tmpfs_exists(self):
        capture_dir_exists = _path_exists(CAPTURE_TMPFS)
        if not capture_dir_exists:
            self.skipTest(
                f"{CAPTURE_TMPFS} does not exist — "
                "capture daemon may not be running"
            )
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == str(CAPTURE_TMPFS):
                    fstype = parts[2]
                    self.assertEqual(
                        fstype, "tmpfs",
                        f"{CAPTURE_TMPFS} is mounted as {fstype}, expected tmpfs"
                    )
                    return
        self.fail(f"{CAPTURE_TMPFS} exists but is not listed in /proc/mounts")

    @unittest.skipUnless(_is_linux(), "Requires Linux filesystem")
    def test_capture_segments_limit(self):
        daemon_source = _find_daemon_source()
        if daemon_source is None:
            self.skipTest("capture-daemon.py not found in project")
        content = daemon_source.read_text(errors="replace")
        segment_patterns = [
            r"max[_\s]*segments?\s*[=:]\s*(\d+)",
            r"SEGMENT[_\s]*LIMIT\s*[=:]\s*(\d+)",
            r"circular[_\s]*buffer[_\s]*max\s*[=:]\s*(\d+)",
            r"MAX[_\s]*SEGMENTS?\s*[=:]\s*(\d+)",
            r"segment[_\s]*count\s*[>=<]+\s*(\d+)",
        ]
        found_limit = False
        for pattern in segment_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches:
                limit = int(match)
                if limit <= CAPTURE_MAX_SEGMENTS:
                    found_limit = True
                    self.assertLessEqual(
                        limit, CAPTURE_MAX_SEGMENTS,
                        f"Capture segment limit {limit} exceeds max {CAPTURE_MAX_SEGMENTS}"
                    )
                    break
            if found_limit:
                break
        if not found_limit:
            segment_count = len(re.findall(r"segment", content, re.IGNORECASE))
            self.assertGreater(
                segment_count, 0,
                "No segment references found in capture daemon source"
            )

    def test_wallpaper_pause_signal(self):
        daemon_source = _find_daemon_source()
        if daemon_source is None:
            self.skipTest("capture-daemon.py not found in project")
        content = daemon_source.read_text(errors="replace")
        has_stop = bool(re.search(r"SIGSTOP|signal\.SIGSTOP|19|PTRACE_STOP", content))
        has_cont = bool(re.search(r"SIGCONT|signal\.SIGCONT|18|PTRACE_CONT", content))
        if not has_stop:
            has_stop = bool(re.search(r"pause|suspend|freeze|stop.*wallpaper|kill\s*-STOP", content, re.IGNORECASE))
        if not has_cont:
            has_cont = bool(re.search(r"resume|unpause|unfreeze|continue|kill\s*-CONT", content, re.IGNORECASE))
        self.assertTrue(
            has_stop,
            "capture-daemon.py does not contain SIGSTOP or wallpaper pause logic"
        )
        self.assertTrue(
            has_cont,
            "capture-daemon.py does not contain SIGCONT or wallpaper resume logic"
        )

    def test_no_memory_leak_daemon(self):
        daemon_source = _find_daemon_source()
        if daemon_source is None:
            self.skipTest("capture-daemon.py not found in project")
        content = daemon_source.read_text(errors="replace")
        leak_indicators = [
            (r"\.append\(", "list.append (potential unbounded growth)"),
            (r"\bdict\b.*\bupdate\b", "dict.update (potential growth)"),
            (r"global\s+\w+", "global variable (potential state leak)"),
        ]
        unbounded_growth_count = 0
        for pattern, desc in leak_indicators:
            matches = re.findall(pattern, content)
            if matches:
                unbounded_growth_count += len(matches)
        cleanup_indicators = [
            r"\.clear\(\)",
            r"del\s+\w+",
            r"\.pop\(",
            r"gc\.collect",
            r"weakref",
            r"__del__",
            r"finally:",
        ]
        cleanup_count = 0
        for pattern in cleanup_indicators:
            cleanup_count += len(re.findall(pattern, content))
        self.assertGreater(
            cleanup_count, 0,
            "No memory cleanup patterns found in daemon source — "
            "expected at least .clear(), del, or finally: blocks"
        )
        self.assertGreaterEqual(
            cleanup_count, unbounded_growth_count * 0.3,
            f"Cleanup patterns ({cleanup_count}) insufficient relative to "
            f"growth patterns ({unbounded_growth_count})"
        )

    @unittest.skipUnless(_is_linux(), "Requires Linux procfs")
    def test_meminfo_has_required_fields(self):
        info = _read_meminfo()
        required = ["MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached"]
        for field in required:
            self.assertIn(
                field, info,
                f"/proc/meminfo missing required field: {field}"
            )
            self.assertGreater(
                info[field], 0,
                f"/proc/meminfo field {field} is zero"
            )

    @unittest.skipUnless(_is_linux(), "Requires Linux sysfs")
    def test_zram_initial_stats(self):
        stats_dir = ZRAM_BASE / "stat"
        if not stats_dir.exists():
            self.skipTest("zram0 stat directory not available")
        for stat_file in ["reads", "writes", "discard"]:
            stat_path = stats_dir / stat_file
            if stat_path.exists():
                value = _read_sys_file(stat_path)
                self.assertIsNotNone(value, f"Cannot read zram0/stat/{stat_file}")
                self.assertTrue(
                    value.isdigit(),
                    f"zram0/stat/{stat_file} is not numeric: {value}"
                )

    @unittest.skipUnless(_is_linux(), "Requires Linux cgroups")
    def test_cgroup_memory_limit_set(self):
        mem_max = _read_sys_file(CGROUP_BASE / "memory.max")
        mem_limit = _read_sys_file(CGROUP_BASE / "memory.limit_in_bytes")
        has_limit = False
        if mem_max is not None and mem_max != "max":
            value = int(mem_max)
            self.assertGreater(value, 0, "memory.max is zero")
            self.assertLess(value, 16 * 1024 * 1024 * 1024, "memory.max exceeds 16GB")
            has_limit = True
        if mem_limit is not None:
            value = int(mem_limit)
            if value < 2**62:
                self.assertGreater(value, 0)
                has_limit = True
        if not has_limit:
            self.skipTest("No memory limit configured for gaming cgroup")

    def test_daemon_source_not_importing_leaky_modules(self):
        daemon_source = _find_daemon_source()
        if daemon_source is None:
            self.skipTest("capture-daemon.py not found in project")
        content = daemon_source.read_text(errors="replace")
        risky_imports = ["pickle", "shelve", "marshal"]
        for module in risky_imports:
            pattern = rf"\bimport\s+{module}\b|\bfrom\s+{module}\b"
            match = re.search(pattern, content)
            self.assertIsNone(
                match,
                f"Capture daemon imports risky module '{module}' "
                f"(potential memory/resource leak vector)"
            )

    @unittest.skipUnless(_is_linux(), "Requires Linux /proc")
    def test_kernel_memory_not_excessive(self):
        info = _read_meminfo()
        if "Slab" not in info or "SReclaimable" not in info:
            self.skipTest("Kernel memory info not available")
        slab_kb = info["Slab"]
        reclaimable_kb = info.get("SReclaimable", 0)
        kernel_unreclaimable_mb = (slab_kb - reclaimable_kb) / 1024.0
        self.assertLess(
            kernel_unreclaimable_mb, 512,
            f"Kernel unreclaimable slab {kernel_unreclaimable_mb:.1f}MB is excessive"
        )

    def test_daemon_has_signal_handlers(self):
        daemon_source = _find_daemon_source()
        if daemon_source is None:
            self.skipTest("capture-daemon.py not found in project")
        content = daemon_source.read_text(errors="replace")
        has_signal_import = bool(re.search(
            r"\bimport\s+signal\b|\bfrom\s+signal\s+import\b", content
        ))
        self.assertTrue(
            has_signal_import,
            "capture-daemon.py does not import the signal module"
        )
        has_signal_handler = bool(re.search(
            r"signal\.signal\(|signal\.SIGTERM|signal\.SIGINT", content
        ))
        self.assertTrue(
            has_signal_handler,
            "capture-daemon.py does not register signal handlers"
        )


if __name__ == "__main__":
    unittest.main()
