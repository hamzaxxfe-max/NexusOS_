#!/usr/bin/env python3
"""Aion Live Wallpaper Engine — mpv-based daemon with VAAPI and focus-pause."""

import fcntl
import json
import logging
import logging.handlers
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QMenu,
    QSlider,
    QSystemTrayIcon,
)

APP_NAME = "Aion Live Wallpaper Engine"
APP_VERSION = "1.0.0"
CONFIG_PATH = Path("/etc/aion/live-wallpaper.json")
LOCK_FILE = Path("/tmp/aion-live-wallpaper.lock")
MPV_SOCKET = Path("/tmp/aion-mpv-socket")
LOG_DIR = Path("/var/log/aion")
LOG_PATH = LOG_DIR / "live-wallpaper.log"
PID_FILE = Path("/tmp/aion-live-wallpaper.pid")

DEFAULT_CONFIG = {
    "enabled": True,
    "wallpaper_dir": "/usr/share/aion/live-wallpapers",
    "user_wallpaper_dir": "~/Videos/Aion-Wallpapers",
    "current_wallpaper": "",
    "playback_mode": "single",
    "cycle_interval_sec": 300,
    "crossfade_duration_sec": 2,
    "volume": 50,
    "mute": False,
    "pause_on_game": True,
    "focus_poll_interval_sec": 2,
    "hw_decoding": "auto",
    "vo_backend": "auto",
    "max_fps": 30,
    "idle_dim_sec": 60,
    "idle_dim_alpha": 0.5,
    "tray_icon": True,
    "randomize": False,
    "loop": True,
}

GAME_PATTERNS = [
    "wine", "steam", "proton", "lutris", "gamescope", "mangohud",
    "gamemode", "dxvk", "vkd3d", "heroic", "epicgames", "gog",
    "starfield", "cyberpunk", "elden", "baldurs", "hogwarts",
    "native", "heroiclauncher", "itch",
]

SUPPORTED_VIDEO_EXT = {".mp4", ".webm", ".mkv", ".avi", ".mov", ".flv", ".ogv"}
SUPPORTED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"


def setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        str(LOG_PATH), maxBytes=5 * 1024 * 1024, backupCount=3
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%Y-%m-%d %H:%M:%S"))
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(LOG_FORMAT, datefmt="%H:%M:%S"))
    logger = logging.getLogger("live-wallpaper")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.addHandler(console)
    return logger


log = setup_logging()


def load_config():
    config = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                user_cfg = json.load(f)
            config.update(user_cfg)
        except (json.JSONDecodeError, OSError) as e:
            log.warning("Failed to load config: %s, using defaults", e)
    return config


def save_config(config):
    content = json.dumps(config, indent=2)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CONFIG_PATH, "w") as f:
            f.write(content)
        return True
    except (PermissionError, OSError) as e:
        log.warning("Failed to save config: %s", e)
    try:
        proc = subprocess.run(
            ["sudo", "tee", str(CONFIG_PATH)],
            input=content, capture_output=True, text=True, timeout=30,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
        log.warning("Failed to elevate config write: %s", e)
        return False


def expand_path(p):
    return str(Path(p).expanduser())


def run_cmd(cmd, timeout=10):
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return r.stdout.strip(), r.returncode
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "", 1


def detect_session_type():
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "x11"


class MpvController:
    def __init__(self):
        self._process = None
        self._connected = False
        self._current_file = ""
        self._volume = 50
        self._mute = False
        self._paused = False

    def _send_ipc(self, command):
        if not self._connected or not MPV_SOCKET.exists():
            return None
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(3)
            sock.connect(str(MPV_SOCKET))
            payload = json.dumps({"command": command}) + "\n"
            sock.sendall(payload.encode())
            data = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if b"\n" in data:
                    break
            sock.close()
            for line in data.decode().strip().split("\n"):
                try:
                    resp = json.loads(line)
                    if "data" in resp:
                        return resp["data"]
                except json.JSONDecodeError:
                    continue
            return None
        except (socket.error, OSError) as e:
            log.debug("IPC send failed: %s", e)
            return None

    def start(self, filepath, config):
        if self._process and self._process.poll() is None:
            self.stop()
        session = detect_session_type()
        hw_decoding = config.get("hw_decoding", "auto")
        vo_backend = config.get("vo_backend", "auto")
        max_fps = config.get("max_fps", 30)
        mpv_args = [
            "mpv",
            "--fullscreen",
            "--no-terminal",
            f"--volume={config.get('volume', 50)}",
            f"--input-ipc-server={MPV_SOCKET}",
            f"--vo={self._resolve_vo(vo_backend, session)}",
            f"--hwdec={self._resolve_hwdec(hw_decoding)}",
            "--keep-open=yes",
            "--loop-file=inf",
            "--no-audio-display",
            "--pause=no",
            "--wid=0",
            f"--video-sync=display-resync",
            f"--interpolation-threshold=0.01",
        ]
        ext = Path(filepath).suffix.lower()
        if ext in SUPPORTED_IMAGE_EXT:
            mpv_args.append("--loop-file=inf")
            mpv_args.append("--image-display-duration=inf")
        else:
            mpv_args.append("--loop-file=inf")
        if max_fps > 0:
            mpv_args.append(f"--fps={max_fps}")
        if config.get("mute", False):
            mpv_args.append("--mute=yes")
        mpv_args.append(filepath)
        try:
            self._process = subprocess.Popen(
                mpv_args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
            self._current_file = filepath
            self._volume = config.get("volume", 50)
            self._mute = config.get("mute", False)
            time.sleep(0.5)
            self._connected = MPV_SOCKET.exists()
            log.info("mpv started: %s (PID %d)", filepath, self._process.pid)
            return True
        except OSError as e:
            log.error("Failed to start mpv: %s", e)
            return False

    def _resolve_vo(self, vo_backend, session):
        if vo_backend != "auto":
            return vo_backend
        if session == "wayland":
            return "gpu"
        return "gpu"

    def _resolve_hwdec(self, hw_decoding):
        if hw_decoding == "auto":
            if Path("/dev/dri/renderD128").exists():
                return "vaapi"
            return "auto-safe"
        return hw_decoding

    def stop(self):
        if self._process:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=3)
            except OSError as e:
                log.warning("Error stopping mpv: %s", e)
            self._process = None
            self._connected = False
            self._current_file = ""
            log.info("mpv stopped")

    def pause(self):
        if self._paused or not self._connected:
            return
        self._send_ipc(["set_property", "pause", True])
        self._paused = True
        log.info("mpv paused")

    def resume(self):
        if not self._paused or not self._connected:
            return
        self._send_ipc(["set_property", "pause", False])
        self._paused = False
        log.info("mpv resumed")

    def set_volume(self, vol):
        self._volume = max(0, min(100, vol))
        if self._connected:
            self._send_ipc(["set_property", "volume", self._volume])

    def set_mute(self, mute):
        self._mute = mute
        if self._connected:
            self._send_ipc(["set_property", "mute", self._mute])

    def is_alive(self):
        if self._process is None:
            return False
        return self._process.poll() is None

    @property
    def current_file(self):
        return self._current_file

    @property
    def paused(self):
        return self._paused

    @property
    def volume(self):
        return self._volume

    @property
    def muted(self):
        return self._mute


class FocusMonitor(QThread):
    focus_changed = pyqtSignal(bool)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._poll_interval = config.get("focus_poll_interval_sec", 2)
        self._active = config.get("pause_on_game", True)
        self._game_detected = False
        self._running = True

    def run(self):
        while self._running:
            if self._active:
                is_game = self._detect_game()
                if is_game and not self._game_detected:
                    self._game_detected = True
                    self.focus_changed.emit(True)
                elif not is_game and self._game_detected:
                    self._game_detected = False
                    self.focus_changed.emit(False)
            time.sleep(self._poll_interval)

    def _detect_game(self):
        title = self._get_active_window()
        if not title:
            return False
        title_lower = title.lower()
        for pattern in GAME_PATTERNS:
            if pattern in title_lower:
                return True
        return False

    def _get_active_window(self):
        out, rc = run_cmd("xdotool getactivewindow getwindowname 2>/dev/null", timeout=5)
        if rc != 0 or not out:
            return ""
        return out.strip()

    def stop(self):
        self._running = False
        self.wait(3000)

    @property
    def is_game_active(self):
        return self._game_detected

    @property
    def active(self):
        return self._active

    @active.setter
    def active(self, value):
        self._active = value


class WallpaperPlaylist:
    def __init__(self, config):
        self._config = config
        self._files = []
        self._index = 0
        self._randomize = config.get("randomize", False)
        self._scan()

    def _scan(self):
        self._files = []
        dirs = [
            expand_path(self._config.get("wallpaper_dir", "")),
            expand_path(self._config.get("user_wallpaper_dir", "")),
        ]
        for d in dirs:
            p = Path(d)
            if not p.exists():
                continue
            for f in p.iterdir():
                if f.is_file() and f.suffix.lower() in SUPPORTED_VIDEO_EXT | SUPPORTED_IMAGE_EXT:
                    self._files.append(str(f))
        self._files.sort()
        if self._randomize and self._files:
            import random
            random.shuffle(self._files)
        log.info("Found %d wallpapers", len(self._files))

    def current(self):
        if not self._files:
            return ""
        return self._files[self._index % len(self._files)]

    def next(self):
        if not self._files:
            return ""
        self._index = (self._index + 1) % len(self._files)
        return self._files[self._index]

    def previous(self):
        if not self._files:
            return ""
        self._index = (self._index - 1) % len(self._files)
        return self._files[self._index]

    def set_by_path(self, path):
        for i, f in enumerate(self._files):
            if f == path or f.endswith(path):
                self._index = i
                return True
        return False

    def count(self):
        return len(self._files)

    def files(self):
        return list(self._files)

    def rescan(self):
        self._scan()


class LiveWallpaperDaemon:
    def __init__(self):
        self._config = load_config()
        self._mpv = MpvController()
        self._focus_monitor = FocusMonitor(self._config)
        self._playlist = WallpaperPlaylist(self._config)
        self._cycle_timer = None
        self._tray = None
        self._app = None
        self._lock_fd = None

    def _acquire_lock(self):
        try:
            self._lock_fd = open(str(LOCK_FILE), "w")
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_fd.write(str(os.getpid()))
            self._lock_fd.flush()
            return True
        except (IOError, OSError):
            log.error("Another instance is running (lock file: %s)", LOCK_FILE)
            return False

    def _release_lock(self):
        if self._lock_fd:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                self._lock_fd.close()
                LOCK_FILE.unlink(missing_ok=True)
            except OSError:
                pass

    def _write_pid(self):
        PID_FILE.write_text(str(os.getpid()))

    def _remove_pid(self):
        PID_FILE.unlink(missing_ok=True)

    def _setup_signals(self):
        signal.signal(signal.SIGHUP, self._handle_reload)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        signal.signal(signal.SIGUSR1, self._handle_next)

    def _handle_reload(self, signum, frame):
        log.info("Received SIGHUP — reloading config")
        self._config = load_config()
        self._playlist = WallpaperPlaylist(self._config)
        self._focus_monitor.active = self._config.get("pause_on_game", True)

    def _handle_shutdown(self, signum, frame):
        log.info("Received SIGTERM — shutting down")
        self.shutdown()

    def _handle_next(self, signum, frame):
        log.info("Received SIGUSR1 — next wallpaper")
        self._next_wallpaper()

    def _start_wallpaper(self, filepath=None):
        if not filepath:
            filepath = self._config.get("current_wallpaper", "")
        if not filepath or not Path(filepath).exists():
            filepath = self._playlist.current()
        if not filepath or not Path(filepath).exists():
            log.warning("No valid wallpaper found")
            return
        self._mpv.start(filepath, self._config)
        self._config["current_wallpaper"] = filepath
        save_config(self._config)

    def _next_wallpaper(self):
        next_file = self._playlist.next()
        if next_file:
            self._start_wallpaper(next_file)

    def _previous_wallpaper(self):
        prev_file = self._playlist.previous()
        if prev_file:
            self._start_wallpaper(prev_file)

    def _on_focus_changed(self, game_active):
        if game_active:
            log.info("Game detected — pausing wallpaper")
            self._mpv.pause()
        else:
            log.info("Game lost focus — resuming wallpaper")
            self._mpv.resume()

    def _setup_cycle_timer(self, app):
        interval = self._config.get("cycle_interval_sec", 300)
        if interval <= 0:
            return
        mode = self._config.get("playback_mode", "single")
        if mode in ("single", "slideshow"):
            self._cycle_timer = QTimer()
            self._cycle_timer.timeout.connect(self._next_wallpaper)
            self._cycle_timer.start(interval * 1000)

    def _setup_tray(self, app):
        if not self._config.get("tray_icon", True):
            return
        self._tray = QSystemTrayIcon(self._tray_icon(), app)
        menu = QMenu()
        play_pause = QAction("Pause", app)
        play_pause.triggered.connect(self._toggle_pause)
        menu.addAction(play_pause)
        next_action = QAction("Next Wallpaper", app)
        next_action.triggered.connect(self._next_wallpaper)
        menu.addAction(next_action)
        prev_action = QAction("Previous Wallpaper", app)
        prev_action.triggered.connect(self._previous_wallpaper)
        menu.addAction(prev_action)
        menu.addSeparator()
        mute_action = QAction("Mute", app)
        mute_action.setCheckable(True)
        mute_action.setChecked(self._config.get("mute", False))
        mute_action.triggered.connect(self._toggle_mute)
        menu.addAction(mute_action)
        menu.addSeparator()
        rescan_action = QAction("Rescan Wallpapers", app)
        rescan_action.triggered.connect(self._rescan)
        menu.addAction(rescan_action)
        quit_action = QAction("Quit", app)
        quit_action.triggered.connect(self.shutdown)
        menu.addAction(quit_action)
        self._tray.setContextMenu(menu)
        self._tray.setToolTip(APP_NAME)
        self._tray.show()

    @staticmethod
    def _tray_icon() -> QIcon:
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor("#00D2FF"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(4, 4, 56, 56, 12, 12)
        painter.setPen(QPen(QColor("#000000"), 3))
        painter.setFont(QFont("Arial", 28, QFont.Weight.Bold))
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "W")
        painter.end()
        return QIcon(pixmap)

    def _toggle_pause(self):
        if self._mpv.paused:
            self._mpv.resume()
        else:
            self._mpv.pause()

    def _toggle_mute(self):
        self._config["mute"] = not self._config.get("mute", False)
        self._mpv.set_mute(self._config["mute"])
        save_config(self._config)

    def _rescan(self):
        self._playlist.rescan()
        log.info("Playlist rescanned: %d wallpapers", self._playlist.count())

    def run(self):
        if not self._acquire_lock():
            return 1
        self._write_pid()
        self._setup_signals()
        log.info("%s v%s starting", APP_NAME, APP_VERSION)
        log.info("Config: %s", CONFIG_PATH)
        log.info("Found %d wallpapers", self._playlist.count())
        self._focus_monitor.focus_changed.connect(self._on_focus_changed)
        self._focus_monitor.start()
        app = QApplication.instance() or QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)
        app.setApplicationName(APP_NAME)
        self._setup_tray(app)
        self._setup_cycle_timer(app)
        self._start_wallpaper()
        log.info("Daemon running — press Ctrl+C or SIGTERM to stop")
        exit_code = app.exec()
        self.shutdown()
        return exit_code

    def shutdown(self):
        log.info("Shutting down")
        self._focus_monitor.stop()
        self._mpv.stop()
        self._release_lock()
        self._remove_pid()
        log.info("Shutdown complete")


def main():
    daemon = LiveWallpaperDaemon()
    sys.exit(daemon.run())


if __name__ == "__main__":
    main()
