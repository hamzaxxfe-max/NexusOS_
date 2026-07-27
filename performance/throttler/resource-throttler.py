#!/usr/bin/env python3
"""NexusOS Resource Throttler — Gaming performance optimizer.

Monitors window focus, detects games, and dynamically manages CPU/memory
resources via cgroups v2. Liberates memory for game processes and throttles
background activity during gaming sessions.
"""

import ctypes
import ctypes.util
import json
import logging
import os
import re
import resource
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
LOG_DATE = "%Y-%m-%d %H:%M:%S"
LOG_FILE = Path("/var/log/nexusos/resource-throttler.log")
CONFIG_PATH = Path(os.environ.get("NEXUSOS_CONFIG", "/etc/nexusos/config.json"))
CGROUP_ROOT = Path("/sys/fs/cgroup")
GAMING_SLICE = "nexusos-gaming.slice"
INSTALL_SLICE = "nexusos-install.slice"
POLL_INTERVAL = 2
TARGET_FREE_MB = 7168
CRITICAL_SERVICES = [
    "bluetooth.service",
    "cups.service",
    "cups-browsed.service",
    "avahi-daemon.service",
    "packagekit.service",
    "ModemManager.service",
]
DECOMPRESSION_BINARIES = frozenset([
    "tar", "unzip", "zstd", "zstdcat", "pigz", "pbzip2",
    "xz", "7z", "7za", "cpio", "gunzip", "bunzip2",
])
NOCONTROLLER_SERVICES = ["bluetooth.service"]

libx11 = None
libx11_path = ctypes.util.find_library("X11")
if libx11_path:
    try:
        libx11 = ctypes.CDLL(libx11_path)
    except OSError:
        libx11 = None

logger = logging.getLogger("nexusos-throttler")


class CGroupManager:
    """Manages cgroups v2 hierarchies for nexusos slices."""

    def __init__(self, root: Path = CGROUP_ROOT):
        self.root = root
        self.gaming_path = root / GAMING_SLICE
        self.install_path = root / INSTALL_SLICE

    def setup_slices(self) -> None:
        for path in (self.gaming_path, self.install_path):
            path.mkdir(parents=True, exist_ok=True)
            self._write(path / "cgroup.subtree_control", "+cpu +memory")

        gaming_procs = self.gaming_path / "nexusos-games.scope"
        gaming_procs.mkdir(exist_ok=True)

        install_procs = self.install_path / "nexusos-install.scope"
        install_procs.mkdir(exist_ok=True)

    def set_cpu_weight(self, scope: str, weight: int) -> None:
        path = self.root / scope / "cpu.weight"
        if path.exists():
            path.write_text(str(max(1, min(10000, weight))))

    def set_memory_max(self, scope: str, max_bytes: int) -> None:
        path = self.root / scope / "memory.max"
        if path.exists():
            path.write_text(str(max_bytes))

    def add_pid(self, scope: str, pid: int) -> None:
        procs_path = self.root / scope / "cgroup.procs"
        if procs_path.exists():
            try:
                procs_path.write_text(str(pid))
            except (OSError, PermissionError):
                pass

    def get_all_pids(self, scope: str) -> set[int]:
        procs_path = self.root / scope / "cgroup.procs"
        if not procs_path.exists():
            return set()
        try:
            return {int(line) for line in procs_path.read_text().splitlines() if line.strip()}
        except (OSError, ValueError):
            return set()

    def destroy_scope(self, scope: str) -> None:
        scope_path = self.root / scope
        if not scope_path.exists():
            return
        for child in scope_path.iterdir():
            if child.is_dir() and child.name.endswith(".scope"):
                procs = child / "cgroup.procs"
                if procs.exists():
                    try:
                        pids = procs.read_text().splitlines()
                        for pid_str in pids:
                            if pid_str.strip():
                                try:
                                    os.kill(int(pid_str.strip()), signal.SIGKILL)
                                except (ProcessLookupError, ValueError, PermissionError):
                                    pass
                    except OSError:
                        pass
                try:
                    child.rmdir()
                except OSError:
                    pass

    def set_game_throttle(self) -> None:
        self.set_cpu_weight(GAMING_SLICE, 100)
        for scope_name in self._get_all_cgroup_scopes():
            if GAMING_SLICE not in scope_name and INSTALL_SLICE not in scope_name:
                self.set_cpu_weight(scope_name, 10)
        self.set_memory_max("nexusos-install.scope", 256 * 1024 * 1024)

    def set_idle_restore(self) -> None:
        self.set_cpu_weight(GAMING_SLICE, 100)
        for scope_name in self._get_all_cgroup_scopes():
            self.set_cpu_weight(scope_name, 100)
        self.set_memory_max("nexusos-install.scope", -1)

    def _get_all_cgroup_scopes(self) -> list[str]:
        scopes = []
        try:
            for entry in self.root.iterdir():
                if entry.is_dir():
                    weight_path = entry / "cpu.weight"
                    if weight_path.exists():
                        scopes.append(entry.name)
        except OSError:
            pass
        return scopes


class ServiceManager:
    """Controls systemd services for memory liberation."""

    def __init__(self, bluetooth_controller: bool = True):
        self.bluetooth_controller = bluetooth_controller
        self.stopped_services: list[str] = []
        self.was_throttled = False

    def stop_non_critical(self) -> None:
        services = [s for s in CRITICAL_SERVICES if s != "bluetooth.service" or not self.bluetooth_controller]
        for svc in services:
            if self._is_active(svc):
                self._stop(svc)
                self.stopped_services.append(svc)

    def restore_services(self) -> None:
        for svc in self.stopped_services:
            self._start(svc)
        self.stopped_services.clear()

    def detect_bluetooth_controller(self) -> bool:
        try:
            result = subprocess.run(
                ["bluetoothctl", "show"],
                capture_output=True, text=True, timeout=5,
            )
            return "Powered: yes" in result.stdout
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _is_active(self, service: str) -> bool:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "--quiet", service],
                capture_output=True, timeout=5,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return False

    def _stop(self, service: str) -> None:
        try:
            subprocess.run(
                ["systemctl", "stop", service],
                capture_output=True, timeout=10,
            )
            logger.info("Stopped service: %s", service)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("Failed to stop service: %s", service)

    def _start(self, service: str) -> None:
        try:
            subprocess.run(
                ["systemctl", "start", service],
                capture_output=True, timeout=10,
            )
            logger.info("Started service: %s", service)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("Failed to start service: %s", service)


class MemoryManager:
    """Manages physical memory and kernel caches."""

    def __init__(self, target_free_mb: int = TARGET_FREE_MB):
        self.target_free_mb = target_free_mb

    def get_free_mb(self) -> float:
        try:
            meminfo = Path("/proc/meminfo").read_text()
            for line in meminfo.splitlines():
                if line.startswith("MemAvailable:"):
                    kb = int(line.split()[1])
                    return kb / 1024.0
        except (OSError, ValueError, IndexError):
            pass
        return 0.0

    def drop_caches(self) -> None:
        try:
            subprocess.run(["sync"], timeout=10, check=True)
            Path("/proc/sys/vm/drop_caches").write_text("3")
            logger.info("Dropped kernel caches")
        except (OSError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            logger.warning("Failed to drop caches: %s", e)

    def ensure_free_memory(self) -> float:
        free_mb = self.get_free_mb()
        if free_mb >= self.target_free_mb:
            return free_mb

        self.drop_caches()
        free_mb = self.get_free_mb()

        if free_mb < self.target_free_mb:
            self._kill_low_priority(free_mb)

        return self.get_free_mb()

    def _kill_low_priority(self, current_free_mb: float) -> None:
        deficit_mb = self.target_free_mb - current_free_mb
        if deficit_mb <= 0:
            return

        candidates = self._get_killable_processes()
        for pid, rss_mb, name in candidates:
            if deficit_mb <= 0:
                break
            try:
                os.kill(pid, signal.SIGTERM)
                time.sleep(0.1)
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deficit_mb -= rss_mb
                logger.info("Killed process %d (%s) to reclaim %.0f MB", pid, name, rss_mb)
            except (ProcessLookupError, PermissionError):
                pass

    def _get_killable_processes(self) -> list[tuple[int, float, str]]:
        candidates = []
        proc_path = Path("/proc")
        killable_names = {"firefox", "thunderbird", "libreoffice", "gimp", "blender"}

        for entry in proc_path.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                pid = int(entry.name)
                comm = (entry / "comm").read_text().strip()
                status = (entry / "status").read_text()
                rss_kb = 0
                for line in status.splitlines():
                    if line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
                        break
                rss_mb = rss_kb / 1024.0
                if comm.lower() in killable_names or rss_mb > 500:
                    candidates.append((pid, rss_mb, comm))
            except (OSError, ValueError):
                continue

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates


class ForegroundDetector:
    """Detects the currently focused window and its process."""

    def __init__(self, game_paths: Optional[set[str]] = None, game_names: Optional[set[str]] = None):
        self.game_paths = game_paths or set()
        self.game_names = game_names or set()
        self._xdotool_path = self._find_xdotool()

    def _find_xdotool(self) -> Optional[str]:
        try:
            result = subprocess.run(
                ["which", "xdotool"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    def get_focused_pid(self) -> Optional[int]:
        if self._xdotool_path:
            return self._get_pid_xdotool()
        if libx11:
            return self._get_pid_xlib()
        return self._get_pid_wmctrl()

    def _get_pid_xdotool(self) -> Optional[int]:
        try:
            result = subprocess.run(
                [self._xdotool_path, "getactivewindow", "getpid"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                return int(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError, FileNotFoundError):
            pass
        return None

    def _get_pid_xlib(self) -> Optional[int]:
        try:
            display = libx11.XOpenDisplay(None)
            if not display:
                return None
            try:
                root = libx11.XDefaultRootWindow(display)
                atom = libx11.XInternAtom(display, b"_NET_ACTIVE_WINDOW", False)
                actual_type = ctypes.c_ulong()
                actual_format = ctypes.c_int()
                nitems = ctypes.c_ulong()
                bytes_after = ctypes.c_ulong()
                prop = ctypes.POINTER(ctypes.c_ubyte)()

                result = libx11.XGetWindowProperty(
                    display, root, atom, 0, 1, False, 6,
                    ctypes.byref(actual_type), ctypes.byref(actual_format),
                    ctypes.byref(nitems), ctypes.byref(bytes_after),
                    ctypes.byref(prop),
                )
                if result != 0 or nitems.value == 0:
                    return None
                window_id = ctypes.cast(prop, ctypes.POINTER(ctypes.c_ulong)).contents.value
                libx11.XFree(prop)

                pid_atom = libx11.XInternAtom(display, b"_NET_WM_PID", False)
                result = libx11.XGetWindowProperty(
                    display, window_id, pid_atom, 0, 1, False, 6,
                    ctypes.byref(actual_type), ctypes.byref(actual_format),
                    ctypes.byref(nitems), ctypes.byref(bytes_after),
                    ctypes.byref(prop),
                )
                if result != 0 or nitems.value == 0:
                    return None
                pid = ctypes.cast(prop, ctypes.POINTER(ctypes.c_ulong)).contents.value
                libx11.XFree(prop)
                return pid
            finally:
                libx11.XCloseDisplay(display)
        except Exception:
            return None

    def _get_pid_wmctrl(self) -> Optional[int]:
        try:
            result = subprocess.run(
                ["wmctrl", "-l", "-p"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and "*_" in line.split()[0]:
                        return int(parts[2])
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
        return None

    def is_game(self, pid: int) -> bool:
        exe_path = self._get_exe_path(pid)
        if not exe_path:
            return False

        if exe_path in self.game_paths:
            return True

        exe_name = Path(exe_path).name
        if exe_name in self.game_names:
            return True

        if self._check_gpu_usage(pid):
            return True

        return False

    def is_game_or_browser(self, pid: int) -> bool:
        if self.is_game(pid):
            return True
        exe_name = Path(self._get_exe_path(pid) or "").name
        return exe_name in {"chrome", "chromium", "firefox", "thunderbird", "brave-browser"}

    def _get_exe_path(self, pid: int) -> Optional[str]:
        try:
            return os.readlink(f"/proc/{pid}/exe")
        except (OSError, PermissionError):
            return None

    def _check_gpu_usage(self, pid: int) -> bool:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid,used_memory",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split(",")
                    if len(parts) >= 1 and parts[0].strip().isdigit():
                        if int(parts[0].strip()) == pid:
                            return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        try:
            result = subprocess.run(
                ["rocm-smi", "--showuse", "--json"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if "gpu_use" in data and float(data.get("gpu_use", "0")) > 60:
                    return True
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass

        return False


class DecompressionMonitor:
    """Monitors decompression processes and pins them to low-priority cores."""

    def __init__(self, cgroup_manager: CGroupManager):
        self.cgroup = cgroup_manager
        self.decomp_pids: set[int] = set()

    def scan(self) -> set[int]:
        current = set()
        proc_path = Path("/proc")

        for entry in proc_path.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                pid = int(entry.name)
                cmdline = (entry / "cmdline").read_text().split("\x00")
                if not cmdline:
                    continue
                exe_name = Path(cmdline[0]).name
                if exe_name in DECOMPRESSION_BINARIES:
                    current.add(pid)
                elif exe_name in ("python3", "python") and any(
                    arg in " ".join(cmdline) for arg in ("tar", "unzip", "zstd", "extract")
                ):
                    current.add(pid)
            except (OSError, ValueError):
                continue

        self.decomp_pids = current
        return current

    def is_active(self) -> bool:
        return len(self.decomp_pids) > 0

    def pin_to_low_priority_cores(self) -> None:
        for pid in self.decomp_pids:
            try:
                os.sched_setaffinity(pid, {0})
                self.cgroup.add_pid(INSTALL_SLICE, pid)
            except (OSError, PermissionError):
                pass

    def unpin(self) -> None:
        for pid in self.decomp_pids:
            try:
                os.sched_setaffinity(pid, set(range(os.cpu_count() or 4)))
            except (OSError, PermissionError):
                pass


class ConfigLoader:
    """Loads NexusOS configuration from JSON."""

    def __init__(self, path: Path = CONFIG_PATH):
        self.path = path

    def load_game_paths(self) -> tuple[set[str], set[str]]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to load config from %s: %s", self.path, e)
            return set(), set()

        game_paths = set()
        game_names = set()

        for entry in data.get("trusted_game_paths", []):
            game_paths.add(str(Path(entry).resolve()))

        for entry in data.get("game_executables", []):
            game_names.add(entry)

        return game_paths, game_names


class NexusOSThrottler:
    """Main throttler orchestrator."""

    def __init__(self):
        self.running = True
        self.game_active = False

        self.cgroup = CGroupManager()
        self.service_mgr = ServiceManager()
        self.memory = MemoryManager()
        self.detector = ForegroundDetector()
        self.decomp_monitor = DecompressionMonitor(self.cgroup)
        self.config = ConfigLoader()

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum: int, _frame) -> None:
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, shutting down", sig_name)
        self.running = False

    def setup(self) -> None:
        self.cgroup.setup_slices()
        self.service_mgr.bluetooth_controller = self.service_mgr.detect_bluetooth_controller()
        game_paths, game_names = self.config.load_game_paths()
        self.detector.game_paths = game_paths
        self.detector.game_names = game_names
        logger.info(
            "Initialized: %d trusted game paths, %d game executables, bluetooth=%s",
            len(game_paths), len(game_names), self.service_mgr.bluetooth_controller,
        )

    def run(self) -> None:
        self.setup()
        logger.info("NexusOS Resource Throttler started")

        while self.running:
            try:
                self._tick()
            except Exception:
                logger.exception("Error in main loop")
            time.sleep(POLL_INTERVAL)

        self._shutdown()

    def _tick(self) -> None:
        pid = self.detector.get_focused_pid()
        focused_is_game = False

        if pid:
            focused_is_game = self.detector.is_game_or_browser(pid)

        self.decomp_monitor.scan()

        if focused_is_game and not self.game_active:
            self._enter_game_mode()
        elif not focused_is_game and self.game_active:
            self._exit_game_mode()

        if self.game_active:
            self._maintain_game_mode()

        if self.decomp_monitor.is_active():
            if self.game_active:
                self.decomp_monitor.pin_to_low_priority_cores()
            else:
                self.decomp_monitor.unpin()

    def _enter_game_mode(self) -> None:
        self.game_active = True
        logger.info("Entering game mode")

        self.cgroup.set_game_throttle()
        self.service_mgr.stop_non_critical()

        free_mb = self.memory.ensure_free_memory()
        logger.info("Game mode active: %.0f MB free RAM (target: %d MB)", free_mb, TARGET_FREE_MB)

        self._apply_background_memory_caps()

    def _exit_game_mode(self) -> None:
        self.game_active = False
        logger.info("Exiting game mode")

        self.cgroup.set_idle_restore()
        self.service_mgr.restore_services()
        self.decomp_monitor.unpin()

        free_mb = self.memory.get_free_mb()
        logger.info("Idle mode active: %.0f MB free RAM", free_mb)

    def _maintain_game_mode(self) -> None:
        free_mb = self.memory.get_free_mb()
        if free_mb < TARGET_FREE_MB * 0.85:
            logger.info("Free RAM dropped to %.0f MB, reclaiming", free_mb)
            free_mb = self.memory.ensure_free_memory()
            if free_mb < TARGET_FREE_MB * 0.7:
                logger.warning("Cannot maintain target free RAM: %.0f MB", free_mb)

    def _apply_background_memory_caps(self) -> None:
        bg_pids = set()
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                pid = int(entry.name)
                if not self.detector.is_game(pid):
                    bg_pids.add(pid)
            except (OSError, ValueError):
                continue

        total_bg_rss = 0
        for pid in bg_pids:
            try:
                status = (Path(f"/proc/{pid}/status")).read_text()
                for line in status.splitlines():
                    if line.startswith("VmRSS:"):
                        total_bg_rss += int(line.split()[1]) * 1024
                        break
            except (OSError, ValueError):
                continue

        max_bg_bytes = 1024 * 1024 * 1024
        if total_bg_rss > max_bg_bytes:
            cap = int(max_bg_bytes / max(len(bg_pids), 1))
            for pid in bg_pids:
                try:
                    mem_max = Path(f"/proc/{pid}/cgroup").read_text()
                    if "nexusos" in mem_max:
                        self.cgroup.add_pid("nexusos-gaming.scope", pid)
                except OSError:
                    pass

    def _shutdown(self) -> None:
        logger.info("Shutting down throttler")
        if self.game_active:
            self._exit_game_mode()
        self.cgroup.destroy_scope(GAMING_SLICE)
        self.cgroup.destroy_scope(INSTALL_SLICE)
        logger.info("Cleanup complete")


def setup_logging() -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(str(LOG_FILE))
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE))

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, LOG_DATE))

    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)


def check_prerequisites() -> bool:
    errors = []

    if os.geteuid() != 0:
        errors.append("Must run as root")

    if not CGROUP_ROOT.exists():
        errors.append(f"cgroup root not found: {CGROUP_ROOT}")

    cpu_weight = CGROUP_ROOT / "cpu.weight"
    if not cpu_weight.exists():
        errors.append("cgroups v2 cpu controller not available")

    mem_max = CGROUP_ROOT / "memory.max"
    if not mem_max.exists():
        errors.append("cgroups v2 memory controller not available")

    mem_path = Path("/proc/sys/vm/drop_caches")
    if not mem_path.exists():
        errors.append(f"drop_caches not available: {mem_path}")

    for err in errors:
        logger.error("Prerequisite check failed: %s", err)

    return len(errors) == 0


def main() -> int:
    setup_logging()

    if not check_prerequisites():
        logger.critical("Prerequisites not met, exiting")
        return 1

    throttler = NexusOSThrottler()
    throttler.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
