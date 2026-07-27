#!/usr/bin/env python3
import sys
import os
import re
import fcntl
import json
import subprocess
import logging
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar, QTextEdit, QFileDialog,
    QFrame, QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QPainter, QDragEnterEvent, QDropEvent

LOG_DIR = "/var/log/nexusos"
LOG_FILE = os.path.join(LOG_DIR, "wine-installer.log")
LOCK_FILE = "/tmp/nexusos-wine-installer.lock"
GAMES_DIR = "/opt/nexusos/games"
WINE_PREFIX_BASE = os.path.expanduser("~/.local/share/nexusos/wine-prefixes")
DESKTOP_DIR = os.path.expanduser("~/.local/share/applications")
ICON_DIR = os.path.expanduser("~/.local/share/nexusos/icons")
GAME_GRID = os.path.expanduser("~/.local/share/nexusos/game-grid.json")

BG_PRIMARY = "#121212"
BG_SECONDARY = "#1A2238"
ACCENT = "#00D2FF"
ACCENT_HOVER = "#33DBFF"
TEXT_PRIMARY = "#FFFFFF"
TEXT_SECONDARY = "#B0B0B0"
DANGER = "#FF4444"
SUCCESS = "#44FF88"


def ensure_dirs():
    for d in [LOG_DIR, WINE_PREFIX_BASE, DESKTOP_DIR, ICON_DIR, GAMES_DIR]:
        os.makedirs(d, exist_ok=True)


def log(msg, level="info"):
    ensure_dirs()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] [{level.upper()}] {msg}\n"
    with open(LOG_FILE, "a") as f:
        f.write(entry)


class ScaleManager:
    def __init__(self, screen=None):
        if screen is None:
            screen = QApplication.primaryScreen()
        self.screen = screen
        geo = screen.geometry()
        self.raw_w = geo.width()
        self.raw_h = geo.height()
        self.dpi = screen.logicalDotsPerInch()
        self.sf = self.dpi / 96.0

    def px(self, b):
        return max(1, int(b * self.sf))

    def pt(self, b):
        return max(7, int(b * self.sf))

    def radius(self, b):
        return max(2, int(b * self.sf))

    def font(self, family="Noto Sans", size=10, bold=False):
        f = QFont(family)
        f.setPointSize(self.pt(size))
        f.setBold(bold)
        return f

    def stylesheet(self):
        return f"""
            QWidget {{
                background-color: {BG_PRIMARY};
                color: {TEXT_PRIMARY};
                font-family: "Noto Sans", sans-serif;
                font-size: {self.pt(10)}pt;
            }}
            QPushButton#install-btn {{
                background-color: {ACCENT};
                color: #000000;
                border: none;
                border-radius: {self.radius(8)}px;
                padding: {self.px(12)}px {self.px(24)}px;
                font-size: {self.pt(12)}pt;
                font-weight: bold;
            }}
            QPushButton#install-btn:hover {{
                background-color: {ACCENT_HOVER};
            }}
            QPushButton#install-btn:disabled {{
                background-color: #333333;
                color: #666666;
            }}
            QProgressBar {{
                background: {BG_SECONDARY};
                border: none;
                border-radius: {self.radius(4)}px;
                height: {self.px(10)}px;
                text-align: center;
                color: transparent;
            }}
            QProgressBar::chunk {{
                background: {ACCENT};
                border-radius: {self.radius(4)}px;
            }}
            QTextEdit {{
                background-color: #0D0D15;
                color: {TEXT_SECONDARY};
                border: 1px solid #333333;
                border-radius: {self.radius(6)}px;
                padding: {self.px(8)}px;
                font-family: "Noto Sans Mono", monospace;
                font-size: {self.pt(8)}pt;
            }}
        """


class InstallWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str)

    def __init__(self, exe_path, game_name):
        super().__init__()
        self.exe_path = exe_path
        self.game_name = game_name

    def run(self):
        safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', self.game_name)
        prefix = os.path.join(WINE_PREFIX_BASE, safe_name)

        try:
            self.progress.emit(5, "Validating .exe file...")
            if not self.exe_path.lower().endswith('.exe'):
                self.finished.emit(False, "Not a valid .exe file")
                return

            file_size = os.path.getsize(self.exe_path)
            if file_size < 1024 * 1024:
                self.finished.emit(False, "File too small (< 1MB), likely not a real installer")
                return

            self.progress.emit(10, "Checking Wine installation...")
            wine_check = subprocess.run(
                ["which", "wine"], capture_output=True, text=True
            )
            if wine_check.returncode != 0:
                self.finished.emit(False, "Wine not installed. Run: sudo pacman -S wine-ge")
                return

            self.progress.emit(20, f"Creating Wine prefix: {prefix}...")
            os.makedirs(prefix, exist_ok=True)
            env = os.environ.copy()
            env['WINEPREFIX'] = prefix
            env['WINEDEBUG'] = '-all'
            env['WINEARCH'] = 'win64'
            env['DXVK_ASYNC'] = '1'
            env['STAGING_SHARED_MEMORY'] = '1'

            boot_result = subprocess.run(
                ["wine", "wineboot", "--init"],
                env=env, capture_output=True, text=True, timeout=120
            )
            if boot_result.returncode != 0:
                log(f"Wineboot failed: {boot_result.stderr}", "error")
                self.finished.emit(False, f"Wineboot failed: {boot_result.stderr[:200]}")
                return

            self.progress.emit(40, "Installing dependencies (vcrun2019, DXVK, Vulkan)...")
            deps = ["vcrun2019", "d3dx9", "dxvk"]
            for i, dep in enumerate(deps):
                self.progress.emit(40 + (i * 10), f"Installing {dep}...")
                wt_result = subprocess.run(
                    ["winetricks", "-q", dep],
                    env=env, capture_output=True, text=True, timeout=300
                )
                if wt_result.returncode != 0:
                    log(f"winetricks {dep} warning: {wt_result.stderr[:200]}", "warn")

            self.progress.emit(70, "Optimizing prefix for gaming...")
            self._optimize_prefix(prefix, env)

            self.progress.emit(80, "Generating desktop entry...")
            self._create_desktop_entry(safe_name, prefix, env)

            self.progress.emit(90, "Applying SELinux context...")
            try:
                subprocess.run(
                    ["chcon", "-t", "nexusos_game_t", self.exe_path],
                    capture_output=True, timeout=10
                )
            except FileNotFoundError:
                log("chcon not available, skipping SELinux context", "warn")

            self.progress.emit(95, "Updating game grid...")
            self._update_game_grid(safe_name, self.game_name, self.exe_path)

            self.progress.emit(100, "Installation complete!")
            log(f"Successfully installed: {self.game_name} -> {prefix}")
            self.finished.emit(True, f"Game installed successfully!\nPrefix: {prefix}")

        except subprocess.TimeoutExpired:
            log("Installation timed out", "error")
            self.finished.emit(False, "Installation timed out after 5 minutes")
        except Exception as e:
            log(f"Installation error: {str(e)}", "error")
            self.finished.emit(False, f"Error: {str(e)}")

    def _optimize_prefix(self, prefix, env):
        registry_commands = [
            ['reg', 'add', 'HKCU\\Software\\Wine\\DllOverrides', '/v', 'dxgi', '/t', 'REG_SZ', '/d', 'dxgi', '/f'],
            ['reg', 'add', 'HKCU\\Software\\Wine\\DllOverrides', '/v', 'd3d11', '/t', 'REG_SZ', '/d', 'd3d11', '/f'],
            ['reg', 'add', 'HKCU\\System\\Wine\\Wine Config', '/v', 'MouseWarpOverride', '/t', 'REG_SZ', '/d', 'disable', '/f'],
            ['reg', 'add', 'HKCU\\Software\\Wine\\Wine Config\\DllOverrides', '/v', 'winevulkan', '/t', 'REG_SZ', '/d', 'native', '/f'],
        ]
        for cmd in registry_commands:
            subprocess.run(cmd, env=env, capture_output=True, timeout=10)

        env_file = os.path.join(prefix, "nexusos.env")
        env_content = {
            "DXVK_ASYNC": "1",
            "STAGING_SHARED_MEMORY": "1",
            "MANGOHUD_CONFIG": "fps_limit=60,no_display",
            "WINEFSYNC": "1",
            "WINEESYNC": "1",
            "WINEDEBUG": "-all",
            "WINE_DISABLE_GL_STRING_CACHE": "1",
            "vblank_mode": "0",
            "__GL_THREADED_OPTIMIZATIONS": "1",
        }
        with open(env_file, "w") as f:
            for k, v in env_content.items():
                f.write(f"{k}={v}\n")

        log(f"Prefix optimized: {prefix}")

    def _create_desktop_entry(self, safe_name, prefix, env):
        icon_path = os.path.join(ICON_DIR, f"{safe_name}.png")
        if not os.path.exists(icon_path):
            default_icon = os.path.join(os.path.dirname(__file__), "..", "..",
                                         "ui", "icons", "default-gamepad.svg")
            if os.path.exists(default_icon):
                try:
                    subprocess.run(
                        ["convert", default_icon, "-resize", "512x512", icon_path],
                        capture_output=True, timeout=10
                    )
                except FileNotFoundError:
                    pass

        env_file = os.path.join(prefix, "nexusos.env")
        exec_line = (
            f"env WINEPREFIX={prefix} WINEDEBUG=-all DXVK_ASYNC=1 "
            f"WINEFSYNC=1 WINEESYNC=1 "
            f"bash -c 'for line in $(cat {env_file}); do export $line; done; "
            f"wine \"{self.exe_path}\"'"
        )

        desktop_content = (
            f"[Desktop Entry]\n"
            f"Name={self.game_name}\n"
            f"Comment=NexusOS Wine Game\n"
            f"Exec={exec_line}\n"
            f"Type=Application\n"
            f"Categories=Game;\n"
            f"Icon={icon_path}\n"
            f"Terminal=false\n"
            f"StartupNotify=true\n"
        )

        desktop_path = os.path.join(DESKTOP_DIR, f"nexusos-wine-{safe_name}.desktop")
        with open(desktop_path, "w") as f:
            f.write(desktop_content)
        os.chmod(desktop_path, 0o755)
        log(f"Desktop entry created: {desktop_path}")

    def _update_game_grid(self, safe_name, game_name, exe_path):
        grid = []
        if os.path.exists(GAME_GRID):
            try:
                with open(GAME_GRID) as f:
                    grid = json.load(f)
            except (json.JSONDecodeError, IOError):
                grid = []

        entry = {
            "name": game_name,
            "icon_path": os.path.join(ICON_DIR, f"{safe_name}.png"),
            "exec_path": exe_path,
            "platform": "wine",
            "prefix": os.path.join(WINE_PREFIX_BASE, safe_name),
            "last_played": None,
            "installed": datetime.now().isoformat(),
        }
        grid.append(entry)
        with open(GAME_GRID, "w") as f:
            json.dump(grid, f, indent=2)


class DropZone(QFrame):
    file_dropped = pyqtSignal(str)

    def __init__(self, scale, parent=None):
        super().__init__(parent)
        self.scale = scale
        self._hovering = False
        self.setAcceptDrops(True)
        self.setMinimumHeight(scale.px(300))
        self.setStyleSheet(
            f"QFrame {{ border: 2px dashed {TEXT_SECONDARY}; "
            f"border-radius: {scale.radius(12)}px; background: {BG_SECONDARY}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        icon = QLabel("\U0001F4E5")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFont(scale.font(size=48))
        layout.addWidget(icon)

        title = QLabel("Drop .exe Here")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setFont(scale.font(size=16, bold=True))
        layout.addWidget(title)

        sub = QLabel("Windows game installers (.exe)\nWine-GE / Proton optimized")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setFont(scale.font(size=9))
        sub.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(sub)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith('.exe'):
                    event.acceptProposedAction()
                    self._hovering = True
                    self.setStyleSheet(
                        f"QFrame {{ border: 3px solid {ACCENT}; "
                        f"border-radius: {self.scale.radius(12)}px; "
                        f"background: #1A2240; }}"
                    )
                    return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._hovering = False
        self.setStyleSheet(
            f"QFrame {{ border: 2px dashed {TEXT_SECONDARY}; "
            f"border-radius: {self.scale.radius(12)}px; background: {BG_SECONDARY}; }}"
        )

    def dropEvent(self, event: QDropEvent):
        self._hovering = False
        self.setStyleSheet(
            f"QFrame {{ border: 2px solid {SUCCESS}; "
            f"border-radius: {self.scale.radius(12)}px; background: {BG_SECONDARY}; }}"
        )
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith('.exe'):
                self.file_dropped.emit(path)
                return


class WineInstallerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.scale = ScaleManager()
        self.setWindowTitle("NexusOS Wine Game Installer")
        self.setMinimumSize(self.scale.px(700), self.scale.px(550))
        self.resize(
            min(self.scale.raw_w - 300, self.scale.px(800)),
            min(self.scale.raw_h - 200, self.scale.px(600))
        )
        self.setStyleSheet(self.scale.stylesheet())

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(*[self.scale.px(24)] * 4)
        layout.setSpacing(self.scale.px(16))

        title = QLabel("\U0001F3AE Wine Game Installer")
        title.setFont(self.scale.font(size=16, bold=True))
        layout.addWidget(title)

        subtitle = QLabel("Drag and drop a Windows .exe installer to set up automatically")
        subtitle.setFont(self.scale.font(size=9))
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(subtitle)

        self.drop_zone = DropZone(self.scale)
        self.drop_zone.file_dropped.connect(self._on_file_dropped)
        layout.addWidget(self.drop_zone, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel("")
        self.status_label.setFont(self.scale.font(size=9))
        self.status_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        self.status_label.hide()
        layout.addWidget(self.status_label)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(self.scale.px(150))
        self.log_view.hide()
        layout.addWidget(self.log_view)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.install_btn = QPushButton("Browse for .exe...")
        self.install_btn.setObjectName("install-btn")
        self.install_btn.setFixedWidth(self.scale.px(220))
        self.install_btn.clicked.connect(self._browse_exe)
        btn_row.addWidget(self.install_btn)
        layout.addLayout(btn_row)

        self._worker = None

    def _browse_exe(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Windows Executable", "",
            "Windows Executables (*.exe);;All Files (*)"
        )
        if path:
            self._on_file_dropped(path)

    def _on_file_dropped(self, exe_path):
        game_name = os.path.splitext(os.path.basename(exe_path))[0]
        game_name = re.sub(r'[_-]', ' ', game_name).strip().title()

        self.drop_zone.hide()
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.status_label.show()
        self.status_label.setText(f"Installing: {game_name}")
        self.log_view.show()
        self.log_view.clear()
        self.install_btn.setEnabled(False)
        self.install_btn.setText("Installing...")

        self._worker = InstallWorker(exe_path, game_name)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _on_progress(self, value, message):
        self.progress_bar.setValue(value)
        self.status_label.setText(message)
        self.log_view.append(f"[{value}%] {message}")

    def _on_finished(self, success, message):
        self.install_btn.setEnabled(True)
        self.install_btn.setText("Browse for .exe...")
        if success:
            self.status_label.setText(f"\u2705 {message}")
            self.status_label.setStyleSheet(f"color: {SUCCESS};")
            self.log_view.append(f"\n{'='*40}\n{message}")
        else:
            self.status_label.setText(f"\u274C {message}")
            self.status_label.setStyleSheet(f"color: {DANGER};")
            self.log_view.append(f"\nERROR: {message}")
            self.drop_zone.show()
            self.drop_zone.setStyleSheet(
                f"QFrame {{ border: 2px dashed {TEXT_SECONDARY}; "
                f"border-radius: {self.scale.radius(12)}px; background: {BG_SECONDARY}; }}"
            )


def main():
    ensure_dirs()
    app = QApplication(sys.argv)
    try:
        lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        sys.exit(0)
    window = WineInstallerWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
