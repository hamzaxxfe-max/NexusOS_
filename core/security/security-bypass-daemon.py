#!/usr/bin/env python3
import os
import sys
import json
import signal
import logging
import subprocess
import time
import hashlib
import fcntl
import struct
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Dict, List, Any
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QWidget, QFrame
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QColor, QPalette, QIcon, QPixmap, QPainter
try:
    import inotify_simple  # type: ignore
except ImportError:
    # python-inotify_simple is AUR-only; use the bundled stdlib fallback so
    # the daemon still works on a base install.
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    import inotify_simple_fallback as inotify_simple  # type: ignore

LOG_DIR = Path("/var/log/aion")
LOG_FILE = LOG_DIR / "security-bypass.log"
CONFIG_PATH = Path("/etc/aion/config.json")
TRUSTED_CACHE_PATH = LOG_DIR / "trusted-hashes.json"
BYPASS_SOCKET = "/run/aion/bypass.sock"
INOTIFY_WATCH_PATHS = ["/opt/aion/games", "/usr/bin", "/usr/local/bin", "/home"]
POLKIT_TIMEOUT_MS = 30000
ACCENT_COLOR = "#00D2FF"
BG_COLOR = "#121212"
BG_SECONDARY = "#1E1E1E"
BG_DIALOG = "#1A1A1A"
TEXT_PRIMARY = "#FFFFFF"
TEXT_SECONDARY = "#B0B0B0"
BLOCK_BUTTON_COLOR = "#FF4444"
PROCEED_BUTTON_COLOR = "#00D2FF"

logger = logging.getLogger("aion-security-bypass")


class SecurityConfig:
    def __init__(self, config_path: Path = CONFIG_PATH):
        self.config_path = config_path
        self.trusted_paths: List[str] = []
        self.sandbox_enabled: bool = True
        self.bypass_log_path: str = str(LOG_FILE)
        self.watch_paths: List[str] = list(INOTIFY_WATCH_PATHS)
        self.load()

    def load(self) -> None:
        try:
            if self.config_path.exists():
                with open(self.config_path, "r") as f:
                    data = json.load(f)
                sec = data.get("security", {})
                self.trusted_paths = sec.get("trusted_paths", [
                    "/usr/bin", "/usr/sbin", "/usr/lib", "/bin", "/sbin",
                    "/usr/libexec", "/snap/bin"
                ])
                self.sandbox_enabled = sec.get("sandbox_enabled", True)
                self.bypass_log_path = sec.get("bypass_log_path", str(LOG_FILE))
                logger.info("Loaded config from %s", self.config_path)
            else:
                self.trusted_paths = [
                    "/usr/bin", "/usr/sbin", "/usr/lib", "/bin", "/sbin",
                    "/usr/libexec", "/snap/bin"
                ]
                logger.warning("Config not found at %s, using defaults", self.config_path)
        except Exception as e:
            logger.error("Failed to load config: %s", e)
            self.trusted_paths = ["/usr/bin", "/usr/sbin", "/usr/lib", "/bin", "/sbin"]

    def is_trusted_path(self, executable_path: str) -> bool:
        resolved = str(Path(executable_path).resolve())
        for trusted in self.trusted_paths:
            if resolved.startswith(trusted):
                return True
        return False

    def reload(self) -> None:
        self.load()


class TrustedHashCache:
    def __init__(self, cache_path: Path = TRUSTED_CACHE_PATH):
        self.cache_path = cache_path
        self.hashes: Dict[str, str] = {}
        self.load()

    def load(self) -> None:
        try:
            if self.cache_path.exists():
                with open(self.cache_path, "r") as f:
                    self.hashes = json.load(f)
        except Exception:
            self.hashes = {}

    def save(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_path, "w") as f:
                json.dump(self.hashes, f, indent=2)
        except Exception as e:
            logger.error("Failed to save hash cache: %s", e)

    def compute_hash(self, filepath: str) -> str:
        sha256 = hashlib.sha256()
        try:
            with open(filepath, "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    sha256.update(chunk)
            return sha256.hexdigest()
        except (OSError, IOError):
            return ""

    def is_trusted(self, filepath: str) -> bool:
        file_hash = self.compute_hash(filepath)
        if not file_hash:
            return False
        if filepath in self.hashes and self.hashes[filepath] == file_hash:
            return True
        return False

    def trust(self, filepath: str) -> None:
        file_hash = self.compute_hash(filepath)
        if file_hash:
            self.hashes[filepath] = file_hash
            self.save()

    def revoke(self, filepath: str) -> None:
        if filepath in self.hashes:
            del self.hashes[filepath]
            self.save()


class WarningDialog(QDialog):
    def __init__(self, executable_path: str, risk_description: str, parent=None):
        super().__init__(parent)
        self.executable_path = executable_path
        self.result_action = "block"
        self._setup_ui(risk_description)

    def _setup_ui(self, risk_description: str) -> None:
        self.setWindowTitle("Aion Security Warning")
        self.setFixedSize(520, 420)
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint
        )
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {BG_DIALOG};
                border: 1px solid #333333;
                border-radius: 12px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        header_layout = QHBoxLayout()
        icon_label = QLabel()
        icon_label.setFixedSize(48, 48)
        pixmap = QPixmap(48, 48)
        pixmap.fill(QColor(ACCENT_COLOR))
        painter = QPainter(pixmap)
        painter.setPen(QColor(BG_COLOR))
        painter.setFont(QFont("monospace", 24, QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "!")
        painter.end()
        icon_label.setPixmap(pixmap)
        icon_label.setStyleSheet("border-radius: 24px;")
        header_layout.addWidget(icon_label)

        title_label = QLabel("Security Warning")
        title_label.setStyleSheet(f"""
            color: {TEXT_PRIMARY};
            font-size: 22px;
            font-weight: bold;
            font-family: 'Segoe UI', sans-serif;
        """)
        header_layout.addWidget(title_label)
        header_layout.addStretch()
        layout.addLayout(header_layout)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet(f"background-color: {ACCENT_COLOR}; max-height: 2px;")
        layout.addWidget(separator)

        exe_name = Path(self.executable_path).name
        exe_full = self.executable_path
        exe_label_text = f"<b style='color: {TEXT_PRIMARY};'>Executable:</b> <span style='color: {ACCENT_COLOR};'>{exe_name}</span>"
        exe_label = QLabel()
        exe_label.setText(f"<html><body><p style='font-size:14px; font-family: Segoe UI, sans-serif;'>{exe_label_text}</p></body></html>")
        exe_label.setWordWrap(True)
        layout.addWidget(exe_label)

        path_label_text = f"<span style='color: {TEXT_SECONDARY}; font-size:12px;'>{exe_full}</span>"
        path_label = QLabel()
        path_label.setText(f"<html><body><p>{path_label_text}</p></body></html>")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

        risk_frame = QFrame()
        risk_frame.setStyleSheet(f"""
            QFrame {{
                background-color: #2A1A1A;
                border: 1px solid #442222;
                border-radius: 8px;
                padding: 12px;
            }}
        """)
        risk_layout = QVBoxLayout(risk_frame)
        risk_title = QLabel("Risk Assessment")
        risk_title.setStyleSheet(f"color: #FF6666; font-size: 13px; font-weight: bold; font-family: Segoe UI, sans-serif; border: none;")
        risk_layout.addWidget(risk_title)
        risk_text = QLabel(risk_description)
        risk_text.setWordWrap(True)
        risk_text.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 12px; font-family: Segoe UI, sans-serif; border: none;")
        risk_layout.addWidget(risk_text)
        layout.addWidget(risk_frame)

        info_label = QLabel(
            "This executable is not signed or verified by Aion. "
            "Running untrusted software may compromise system security."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(f"""
            color: {TEXT_SECONDARY};
            font-size: 12px;
            font-family: 'Segoe UI', sans-serif;
        """)
        layout.addWidget(info_label)

        layout.addStretch()

        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        self.block_button = QPushButton("Block (Safe)")
        self.block_button.setFixedHeight(44)
        self.block_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.block_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {BLOCK_BUTTON_COLOR};
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
                font-family: 'Segoe UI', sans-serif;
                padding: 0 32px;
            }}
            QPushButton:hover {{
                background-color: #FF5555;
            }}
            QPushButton:pressed {{
                background-color: #CC3333;
            }}
        """)
        self.block_button.clicked.connect(self._on_block)
        button_layout.addWidget(self.block_button)

        self.proceed_button = QPushButton("Proceed at My Own Risk")
        self.proceed_button.setFixedHeight(44)
        self.proceed_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.proceed_button.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {ACCENT_COLOR};
                border: 2px solid {ACCENT_COLOR};
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
                font-family: 'Segoe UI', sans-serif;
                padding: 0 32px;
            }}
            QPushButton:hover {{
                background-color: rgba(0, 210, 255, 0.1);
            }}
            QPushButton:pressed {{
                background-color: rgba(0, 210, 255, 0.2);
            }}
        """)
        self.proceed_button.clicked.connect(self._on_proceed)
        button_layout.addWidget(self.proceed_button)

        layout.addLayout(button_layout)

        self.timeout_timer = QTimer(self)
        self.timeout_remaining = POLKIT_TIMEOUT_MS // 1000
        self.timeout_timer.timeout.connect(self._on_timeout)
        self.timeout_timer.start(1000)

    def _on_block(self) -> None:
        self.result_action = "block"
        self.accept()

    def _on_proceed(self) -> None:
        self.result_action = "proceed"
        self.accept()

    def _on_timeout(self) -> None:
        self.timeout_remaining -= 1
        if self.timeout_remaining <= 0:
            self.result_action = "block"
            self.reject()
        else:
            self.proceed_button.setText(f"Proceed at My Own Risk ({self.timeout_remaining}s)")

    def get_action(self) -> str:
        return self.result_action


class SandboxManager:
    # The daemon runs as root; games must run as the unprivileged 'aion'
    # user, not as root, or a sandboxed game is an instant root shell.
    GAME_USER = "aion"
    FALLBACK_UID = 1000
    FALLBACK_GID = 1000
    # Group IDs mirror /etc/group written by Aion-Builder.sh (video, audio,
    # input, storage, network, power) — required for GPU/audio/input access.
    SUPPLEMENTARY_GROUPS = [91, 92, 94, 95, 96, 97]

    @staticmethod
    def _game_uid_gid() -> tuple:
        try:
            import pwd
            try:
                pw = pwd.getpwnam(SandboxManager.GAME_USER)
                return pw.pw_uid, pw.pw_gid
            except KeyError:
                return SandboxManager.FALLBACK_UID, SandboxManager.FALLBACK_GID
        except ImportError:
            return SandboxManager.FALLBACK_UID, SandboxManager.FALLBACK_GID

    @staticmethod
    def create_sandbox_command(executable_path: str, args: List[str]) -> List[str]:
        game_dir = str(Path(executable_path).parent)
        uid, gid = SandboxManager._game_uid_gid()
        home = "/home/{}".format(SandboxManager.GAME_USER)
        runtime_dir = "/run/user/{}".format(uid)
        bwrap_cmd = [
            "bwrap",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/sbin", "/sbin",
            "--ro-bind", "/etc", "/etc",
            "--symlink", "/usr/lib", "/lib",
            "--symlink", "/usr/lib64", "/lib64",
            "--symlink", "/usr/bin", "/bin",
            "--symlink", "/usr/sbin", "/sbin",
            "--proc", "/proc",
            "--dev", "/dev",
            "--bind", "/dev/dri", "/dev/dri",
            "--bind", "/dev/snd", "/dev/snd",
            "--bind", "/dev/input", "/dev/input",
            "--bind", "/dev/wayland-0", "/dev/wayland-0",
            "--ro-bind", "{}/wayland-0".format(runtime_dir), "{}/wayland-0".format(runtime_dir),
            "--tmpfs", "/tmp",
            "--bind", game_dir, game_dir,
            # Bind ONLY the game user's home read-write (saves/configs).
            # Never bind the whole /home: the sandboxed process is not root
            # anymore, so it must not reach other users' data.
            "--bind", home, home,
            "--ro-bind", "{}/.local/share".format(home), "{}/.local/share".format(home),
            "--ro-bind", "{}/.config".format(home), "{}/.config".format(home),
            # Drop privileges to the game user inside the namespace.
            "--unshare-user",
            "--uid", str(uid),
            "--gid", str(gid),
            "--groups", ",".join(str(g) for g in SandboxManager.SUPPLEMENTARY_GROUPS),
            "--chdir", home,
            "--setenv", "HOME", home,
            "--setenv", "USER", SandboxManager.GAME_USER,
            "--setenv", "LOGNAME", SandboxManager.GAME_USER,
            "--setenv", "DISPLAY", os.environ.get("DISPLAY", ":0"),
            "--setenv", "WAYLAND_DISPLAY", os.environ.get("WAYLAND_DISPLAY", "wayland-0"),
            "--setenv", "XDG_RUNTIME_DIR", runtime_dir,
            "--setenv", "XDG_DATA_HOME", "{}/.local/share".format(home),
            "--setenv", "XDG_CONFIG_HOME", "{}/.config".format(home),
            "--setenv", "XDG_CACHE_HOME", "{}/.cache".format(home),
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-net",
            "--new-session",
        ]
        bwrap_cmd.extend([executable_path] + args)
        return bwrap_cmd

    @staticmethod
    def launch_in_sandbox(executable_path: str, args: List[str]) -> Optional[subprocess.Popen]:
        cmd = SandboxManager.create_sandbox_command(executable_path, args)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            logger.info(
                "Launched sandboxed: %s (PID %d)", executable_path, proc.pid
            )
            return proc
        except FileNotFoundError:
            logger.error("Bubblewrap (bwrap) not found. Cannot sandbox %s", executable_path)
            return None
        except OSError as e:
            logger.error("Failed to launch sandbox for %s: %s", executable_path, e)
            return None


class SecurityBypassDaemon(QObject):
    bypass_triggered = pyqtSignal(str, str)

    def __init__(self):
        super().__init__()
        self.config = SecurityConfig()
        self.hash_cache = TrustedHashCache()
        self.inotify_manager = inotify_simple.INotify()
        self.watch_descriptors: Dict[int, str] = {}
        self.running = False
        self.active_dialogs: List[WarningDialog] = []
        self._setup_logging()
        self._setup_signal_handlers()

    def _setup_logging(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(LOG_FILE),
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
        )
        file_handler.setLevel(logging.DEBUG)
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        logger.setLevel(logging.DEBUG)

    def _setup_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_sigterm)
        signal.signal(signal.SIGINT, self._handle_sigint)

    def _handle_sigterm(self, signum: int, frame: Any) -> None:
        logger.info("Received SIGTERM, shutting down gracefully")
        self.shutdown()

    def _handle_sigint(self, signum: int, frame: Any) -> None:
        logger.info("Received SIGINT, shutting down gracefully")
        self.shutdown()

    def _setup_inotify_watches(self) -> None:
        for watch_path in self.config.watch_paths:
            path = Path(watch_path)
            if not path.exists():
                logger.warning("Watch path does not exist: %s", watch_path)
                continue
            try:
                wd = self.inotify_manager.add_watch(
                    watch_path,
                    (
                        inotify_simple.flags.CREATE |
                        inotify_simple.flags.OPEN |
                        inotify_simple.flags.ATTRIB |
                        inotify_simple.flags.MOVED_TO
                    ),
                )
                self.watch_descriptors[wd] = watch_path
                logger.info("Watching path: %s (wd=%d)", watch_path, wd)
            except Exception as e:
                logger.error("Failed to watch %s: %s", watch_path, e)

    def _is_executable_file(self, filepath: str) -> bool:
        try:
            stat_info = os.stat(filepath)
            is_file = os.path.isfile(filepath)
            is_exec = bool(stat_info.st_mode & 0o111)
            return is_file and is_exec
        except OSError:
            return False

    def _check_trust(self, executable_path: str) -> bool:
        if self.config.is_trusted_path(executable_path):
            logger.debug("Trusted path: %s", executable_path)
            return True
        if self.hash_cache.is_trusted(executable_path):
            logger.debug("Trusted hash: %s", executable_path)
            return True
        return False

    def _assess_risk(self, executable_path: str) -> str:
        risk_factors = []
        path = Path(executable_path)
        if path.is_symlink():
            risk_factors.append("File is a symbolic link")
        try:
            stat_info = os.stat(executable_path)
            if stat_info.st_mode & 0o4000:
                risk_factors.append("File has SUID bit set")
            if stat_info.st_mode & 0o2000:
                risk_factors.append("File has SGID bit set")
            if stat_info.st_uid == 0:
                risk_factors.append("File is owned by root")
        except OSError:
            risk_factors.append("Cannot read file metadata")
        if not path.suffix:
            risk_factors.append("File has no extension")
        world_writable_dir = False
        for parent in path.parents:
            try:
                if os.stat(str(parent)).st_mode & 0o002:
                    world_writable_dir = True
                    break
            except OSError:
                pass
        if world_writable_dir:
            risk_factors.append("Located in world-writable directory")
        ext = path.suffix.lower()
        high_risk_exts = [".sh", ".bash", ".py", ".pl", ".rb", ".php", ".js"]
        if ext in high_risk_exts:
            risk_factors.append(f"Script file ({ext})")
        if not risk_factors:
            return "Executable is not in a trusted location and has no verified signature."
        return "Risk factors: " + "; ".join(risk_factors) + "."

    def _handle_execution_event(self, filepath: str) -> None:
        resolved = str(Path(filepath).resolve())
        if not self._is_executable_file(resolved):
            return
        if self._check_trust(resolved):
            logger.debug("Skipping trusted executable: %s", resolved)
            return
        logger.warning("Untrusted executable detected: %s", resolved)
        risk_desc = self._assess_risk(resolved)
        action = self._show_warning_dialog(resolved, risk_desc)
        if action == "proceed":
            logger.info("User chose to proceed with: %s", resolved)
            self.hash_cache.trust(resolved)
            if self.config.sandbox_enabled:
                proc = SandboxManager.launch_in_sandbox(resolved, [])
                if proc:
                    logger.info("Sandboxed execution started: PID %d", proc.pid)
                else:
                    logger.error(
                        "Sandbox failed for %s — refusing to fall back to "
                        "unsandboxed (root) execution. Blocked.", resolved)
                    self._log_block_event(resolved)
            else:
                self._log_block_event(resolved)
                logger.warning(
                    "Sandboxing disabled for %s — refusing to execute "
                    "untrusted file as root. Blocked.", resolved)
        else:
            logger.info("User blocked execution of: %s", resolved)
            self._log_block_event(resolved)

    def _show_warning_dialog(self, executable_path: str, risk_description: str) -> str:
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        dialog = WarningDialog(executable_path, risk_description)
        self.active_dialogs.append(dialog)
        dialog.exec()
        action = dialog.get_action()
        self.active_dialogs.remove(dialog)
        return action

    def _log_block_event(self, executable_path: str) -> None:
        block_log = LOG_DIR / "blocked-executions.log"
        try:
            with open(block_log, "a") as f:
                entry = {
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "executable": executable_path,
                    "action": "blocked",
                    "pid": os.getpid(),
                }
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            logger.error("Failed to write block log: %s", e)

    def _process_inotify_events(self) -> None:
        events = self.inotify_manager.read(timeout=100)
        for event in events:
            wd_path = self.watch_descriptors.get(event.wd, "")
            if not wd_path:
                continue
            is_create = event.mask & inotify_simple.flags.CREATE
            is_moved = event.mask & inotify_simple.flags.MOVED_TO
            is_open = event.mask & inotify_simple.flags.OPEN
            if is_create or is_moved or is_open:
                filepath = os.path.join(wd_path, event.name)
                if event.name and self._is_executable_file(filepath):
                    QTimer.singleShot(100, lambda p=filepath: self._handle_execution_event(p))

    def run(self) -> None:
        self.running = True
        logger.info("Aion Security Bypass Daemon starting (PID %d)", os.getpid())
        logger.info("Config loaded: sandbox_enabled=%s, trusted_paths=%d",
                     self.config.sandbox_enabled, len(self.config.trusted_paths))
        self._setup_inotify_watches()
        logger.info("Inotify watches established on %d paths", len(self.watch_descriptors))
        timer = QTimer()
        timer.timeout.connect(self._process_inotify_events)
        timer.start(100)
        self._inotify_timer = timer
        logger.info("Daemon running, monitoring for untrusted executables")
        try:
            app = QApplication.instance()
            if app is None:
                app = QApplication(sys.argv)
            app.exec()
        except KeyboardInterrupt:
            self.shutdown()

    def shutdown(self) -> None:
        if not self.running:
            return
        self.running = False
        logger.info("Shutting down Security Bypass Daemon")
        for wd in self.watch_descriptors:
            try:
                self.inotify_manager.rm_watch(wd)
            except Exception:
                pass
        self.watch_descriptors.clear()
        for dialog in self.active_dialogs:
            try:
                dialog.close()
            except Exception:
                pass
        self.active_dialogs.clear()
        logger.info("Daemon shut down cleanly")
        sys.exit(0)


def check_root() -> None:
    if os.getuid() != 0:
        print("Error: aion-security-bypass must run as root", file=sys.stderr)
        sys.exit(1)


def setup_environment() -> None:
    display = os.environ.get("DISPLAY")
    if not display:
        os.environ["DISPLAY"] = ":0"
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not xdg_runtime:
        uid = os.getuid()
        runtime_dir = f"/run/user/{uid}"
        if os.path.isdir(runtime_dir):
            os.environ["XDG_RUNTIME_DIR"] = runtime_dir


def main() -> None:
    check_root()
    setup_environment()
    daemon = SecurityBypassDaemon()
    daemon.run()


if __name__ == "__main__":
    main()
