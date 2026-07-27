#!/usr/bin/env python3
"""NexusOS Theme Switcher — Console Mode side-car overlay."""

import configparser
import glob
import json
import logging
import os
import signal
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QSettings,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QSystemTrayIcon,
    QTabBar,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_NAME = "NexusOS Theme Switcher"
APP_VERSION = "2.1.0"
APP_ID = "org.nexusui.theme-switcher"
LOCK_FILE = Path(tempfile.gettempdir()) / "nexus-theme-switcher.lock"
PID_FILE = Path("/tmp/nexusos-console.pid")
FRONTEND_LOG = Path(tempfile.gettempdir()) / "nexusos-frontend.log"

NEXUS_DARK_BG = "#121212"
NEXUS_PANEL = "#1A2238"
NEXUS_ACCENT = "#00D2FF"
NEXUS_ACCENT_HOVER = "#33DBFF"
NEXUS_TEXT = "#E0E0E0"
NEXUS_TEXT_DIM = "#8A8A8A"
NEXUS_SUCCESS = "#00E676"
NEXUS_WARNING = "#FFD740"
NEXUS_ERROR = "#FF5252"
NEXUS_BORDER = "#2A2A2A"
NEXUS_CARD_BG = "#1E1E1E"
NEXUS_INPUT_BG = "#252525"

CONFIG_DIR = Path.home() / ".config"
PLASMA_APPLETSRC = CONFIG_DIR / "plasma-org.kde.plasma.desktop-appletsrc"
PLASMARC = CONFIG_DIR / "plasmarc"
KWINRC = CONFIG_DIR / "kwinrc"
KDEGLOBALS = CONFIG_DIR / "kdeglobals"
PLASMA_CONF = CONFIG_DIR / "plasmarc"

FRONTENDS = {
    "pegasus": {"label": "Pegasus Frontend", "cmd": ["pegasus-fe", "--fullscreen"], "binary": "pegasus-fe"},
    "steam": {"label": "Steam Big Picture", "cmd": ["steam", "-bigpicture"], "binary": "steam"},
    "emulationstation": {"label": "EmulationStation", "cmd": ["emulationstation"], "binary": "emulationstation"},
}

CONTROLLER_GLOB = "/dev/input/event*"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("nexus-theme-switcher")

# ---------------------------------------------------------------------------
# INI / config helpers
# ---------------------------------------------------------------------------


def ensure_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()


def read_ini(path: Path) -> configparser.RawConfigParser:
    cfg = configparser.RawConfigParser()
    ensure_file(path)
    cfg.read(str(path), encoding="utf-8")
    return cfg


def write_ini(path: Path, section: str, key: str, value: str) -> None:
    cfg = read_ini(path)
    if not cfg.has_section(section):
        cfg.add_section(section)
    cfg.set(section, key, value)
    ensure_file(path)
    with open(path, "w", encoding="utf-8") as fh:
        cfg.write(fh)


def remove_ini_section(path: Path, section: str) -> None:
    cfg = read_ini(path)
    if cfg.has_section(section):
        cfg.remove_section(section)
        ensure_file(path)
        with open(path, "w", encoding="utf-8") as fh:
            cfg.write(fh)


def set_kwin_effect(name: str, enabled: bool) -> None:
    section = f"Effect-{name}"
    write_ini(KWINRC, section, "Enabled", "true" if enabled else "false")


def set_titlebar_alignment(side: str) -> None:
    write_ini(KWINRC, "Windows", "TitlebarAlignment", side)


def restart_plasmashell() -> None:
    log.info("Restarting plasmashell …")
    try:
        subprocess.run(["kquitapp5", "plasmashell"], timeout=10, capture_output=True)
    except Exception:
        pass
    time.sleep(1.5)
    subprocess.Popen(["kstart5", "plasmashell"], start_new_session=True)
    log.info("plasmashell restarted")


def run_cmd(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            cmd, timeout=timeout, capture_output=True, text=True
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", f"binary not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", "timeout"
    except Exception as exc:
        return -3, "", str(exc)


def binary_available(name: str) -> bool:
    rc, _, _ = run_cmd(["which", name], timeout=5)
    return rc == 0


# ---------------------------------------------------------------------------
# Plasma panel helpers
# ---------------------------------------------------------------------------


def write_panel_config(
    position: str = "bottom",
    height: int = 40,
    floating: bool = False,
    visible: bool = True,
    auto_hide: bool = False,
) -> None:
    ensure_file(PLASMA_APPLETSRC)

    vis_val = "0" if visible else "2"
    float_val = "true" if floating else "false"
    auto_hide_val = "true" if auto_hide else "false"

    content = f"""[PlasmaViews][Panel 2]
panelVisibility={vis_val}
panelHeight={height}
panelPosition={position.capitalize()}
floating={float_val}
autoHide={auto_hide_val}

[PlasmaViews][Panel 2][Defaults]
length=100
alignment=Center
"""
    with open(PLASMA_APPLETSRC, "w", encoding="utf-8") as fh:
        fh.write(content)


def write_global_menu_applet() -> None:
    ensure_file(PLASMA_APPLETSRC)
    existing = ""
    if PLASMA_APPLETSRC.exists():
        existing = PLASMA_APPLETSRC.read_text(encoding="utf-8")

    applet_block = """
[PlasmaViews][Panel 2][Applets][99]
plugin=org.kde.plasma.appmenu

[PlasmaViews][Panel 2][Applets][99][Configuration]
PreloadWeight=70
"""
    if "org.kde.plasma.appmenu" not in existing:
        with open(PLASMA_APPLETSRC, "a", encoding="utf-8") as fh:
            fh.write(applet_block)


def hide_panel() -> None:
    write_panel_config(visible=False)
    log.info("Panel hidden")


def restore_panel() -> None:
    write_panel_config(position="bottom", height=40, floating=False, visible=True)
    log.info("Panel restored")


# ---------------------------------------------------------------------------
# Style applications
# ---------------------------------------------------------------------------


def apply_macos_style() -> str:
    log.info("Applying macOS Style …")
    messages: list[str] = []

    write_panel_config(position="top", height=28, floating=True, visible=True)
    messages.append("Panel: top, floating, 28px")

    write_global_menu_applet()
    messages.append("Global menu applet added")

    set_kwin_effect("magiclamp", True)
    write_ini(KWINRC, "Compositing", "AnimationSpeed", "3")
    messages.append("Magic lamp enabled, animation speed 3")

    set_titlebar_alignment("Left")
    messages.append("Titlebar buttons: left")

    write_ini(KWINRC, "Windows", "BorderlessMaximizeWindows", "false")
    write_ini(KDEGLOBALS, "KWin", "BorderlessMaximizeWindows", "false")

    write_ini(PLASMARC, "Theme", "name", "NexusOS-Dark")
    messages.append("Theme set to NexusOS-Dark")

    restart_plasmashell()
    messages.append("plasmashell restarted")
    return "; ".join(messages)


def apply_windows_style() -> str:
    log.info("Applying Windows Style …")
    messages: list[str] = []

    write_panel_config(position="bottom", height=40, floating=False, visible=True)
    messages.append("Panel: bottom, 40px, standard")

    set_kwin_effect("magiclamp", False)
    messages.append("Magic lamp disabled")

    set_titlebar_alignment("Right")
    messages.append("Titlebar buttons: right")

    write_ini(KWINRC, "Windows", "BorderlessMaximizeWindows", "true")

    write_ini(PLASMARC, "Theme", "name", "Breeze")
    messages.append("Theme restored to Breeze")

    restart_plasmashell()
    messages.append("plasmashell restarted")
    return "; ".join(messages)


def apply_console_style() -> str:
    log.info("Applying Console Style …")
    messages: list[str] = []

    hide_panel()
    messages.append("Panel hidden")

    write_ini(KWINRC, "Windows", "BorderlessMaximizeWindows", "true")
    write_ini(KWINRC, "Windows", "TitlebarDecoration", "none")
    messages.append("Window decorations disabled")

    found_frontend = None
    for key, info in FRONTENDS.items():
        if binary_available(info["binary"]):
            found_frontend = key
            break

    if found_frontend:
        messages.append(f"Detected frontend: {FRONTENDS[found_frontend]['label']}")
    else:
        messages.append("No frontend binary found — install pegasus-fe, steam, or emulationstation")

    return "; ".join(messages)


# ---------------------------------------------------------------------------
# Console mode launch / exit
# ---------------------------------------------------------------------------


def launch_console_mode(frontend: str = "pegasus") -> subprocess.Popen | None:
    info = FRONTENDS.get(frontend)
    if info is None:
        log.error("Unknown frontend: %s", frontend)
        return None

    if not binary_available(info["binary"]):
        log.error("Frontend binary not found: %s", info["binary"])
        return None

    hide_panel()

    try:
        proc = subprocess.Popen(
            info["cmd"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        log.error("Failed to launch %s: %s", frontend, exc)
        restore_panel()
        return None

    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    log.info("Launched %s (PID %d)", frontend, proc.pid)
    return proc


def exit_console_mode() -> bool:
    killed = False

    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            killed = True
            log.info("Killed frontend PID %d", pid)
        except (ProcessLookupError, ValueError, PermissionError) as exc:
            log.warning("Could not kill frontend: %s", exc)
        finally:
            PID_FILE.unlink(missing_ok=True)

    restore_panel()
    restart_plasmashell()
    log.info("Console mode exited")
    return killed


def frontend_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, ValueError):
        PID_FILE.unlink(missing_ok=True)
        return False


def get_active_frontend_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return pid
    except (ProcessLookupError, ValueError):
        PID_FILE.unlink(missing_ok=True)
        return None


# ---------------------------------------------------------------------------
# Gamepad / controller detection
# ---------------------------------------------------------------------------

EV_KEY = 0x01
BTN_GAMEPAD = 0x130
BTN_TRIGGER = 0x120

INPUT_EVENT_FORMAT = "llHHI"
INPUT_EVENT_SIZE = struct.calcsize(INPUT_EVENT_FORMAT)


def detect_controllers() -> list[dict[str, str]]:
    controllers: list[dict[str, str]] = []
    for ev_path in sorted(glob.glob(CONTROLLER_GLOB)):
        name = _read_input_name(ev_path)
        if name and any(
            kw in name.lower()
            for kw in ("gamepad", "controller", "joystick", "xbox", "ps", "dualshock", "dualsense", "switch")
        ):
            controllers.append({"path": ev_path, "name": name})
    return controllers


def _read_input_name(ev_path: str) -> str | None:
    name_file = ev_path.replace("/dev/input/event", "/dev/input/id/")
    try:
        with open(f"/sys/class/input/{os.path.basename(ev_path)}/device/name", "r") as fh:
            return fh.read().strip()
    except Exception:
        return None


def read_gamepad_event(path: str, timeout_ms: int = 200) -> dict | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except (OSError, PermissionError):
        return None

    import selectors
    sel = selectors.DefaultSelector()
    sel.register(fd, selectors.EVENT_READ)
    events = sel.select(timeout=timeout_ms / 1000.0)
    result = None
    if events:
        try:
            data = os.read(fd, INPUT_EVENT_SIZE)
            if len(data) == INPUT_EVENT_SIZE:
                _, _, etype, code, value = struct.unpack(INPUT_EVENT_FORMAT, data)
                result = {"type": etype, "code": code, "value": value}
        except OSError:
            pass
    sel.unregister(fd)
    os.close(fd)
    return result


# ---------------------------------------------------------------------------
# Single-instance lock
# ---------------------------------------------------------------------------


class InstanceLock:
    def __init__(self) -> None:
        self._fd = None

    def acquire(self) -> bool:
        if LOCK_FILE.exists():
            try:
                old_pid = int(LOCK_FILE.read_text(encoding="utf-8").strip())
                os.kill(old_pid, 0)
                return False
            except (ProcessLookupError, ValueError):
                LOCK_FILE.unlink(missing_ok=True)

        LOCK_FILE.write_text(str(os.getpid()), encoding="utf-8")
        return True

    def release(self) -> None:
        LOCK_FILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# QSS Theme
# ---------------------------------------------------------------------------


def build_qss() -> str:
    return f"""
    * {{
        font-family: "Segoe UI", "SF Pro Display", "Noto Sans", sans-serif;
    }}
    QMainWindow {{
        background-color: {NEXUS_DARK_BG};
    }}
    QWidget#central {{
        background-color: {NEXUS_DARK_BG};
    }}

    /* Tab bar */
    QTabBar {{
        background: transparent;
    }}
    QTabBar::tab {{
        background: {NEXUS_PANEL};
        color: {NEXUS_TEXT_DIM};
        padding: 10pt 20pt;
        margin-right: 2pt;
        border: none;
        border-bottom: 2pt solid transparent;
        font-size: 10pt;
        font-weight: 600;
    }}
    QTabBar::tab:selected {{
        color: {NEXUS_ACCENT};
        border-bottom: 2pt solid {NEXUS_ACCENT};
    }}
    QTabBar::tab:hover {{
        color: {NEXUS_TEXT};
    }}

    /* Buttons */
    QPushButton {{
        background-color: {NEXUS_PANEL};
        color: {NEXUS_TEXT};
        border: 1pt solid {NEXUS_BORDER};
        border-radius: 6pt;
        padding: 8pt 16pt;
        font-size: 9pt;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background-color: #243050;
        border-color: {NEXUS_ACCENT};
    }}
    QPushButton:pressed {{
        background-color: #1A2238;
    }}
    QPushButton#accent {{
        background-color: {NEXUS_ACCENT};
        color: {NEXUS_DARK_BG};
        border: none;
    }}
    QPushButton#accent:hover {{
        background-color: {NEXUS_ACCENT_HOVER};
    }}
    QPushButton#danger {{
        background-color: transparent;
        color: {NEXUS_ERROR};
        border: 1pt solid {NEXUS_ERROR};
    }}
    QPushButton#danger:hover {{
        background-color: #3D1515;
    }}

    /* Labels */
    QLabel {{
        color: {NEXUS_TEXT};
    }}
    QLabel#heading {{
        font-size: 16pt;
        font-weight: 700;
        color: {NEXUS_ACCENT};
    }}
    QLabel#subheading {{
        font-size: 10pt;
        color: {NEXUS_TEXT_DIM};
    }}
    QLabel#version {{
        font-size: 8pt;
        color: {NEXUS_TEXT_DIM};
    }}

    /* Cards */
    QFrame#card {{
        background-color: {NEXUS_CARD_BG};
        border: 1pt solid {NEXUS_BORDER};
        border-radius: 10pt;
    }}
    QFrame#card:hover {{
        border-color: {NEXUS_ACCENT};
    }}

    /* Group boxes */
    QGroupBox {{
        background-color: {NEXUS_CARD_BG};
        border: 1pt solid {NEXUS_BORDER};
        border-radius: 8pt;
        margin-top: 14pt;
        padding-top: 20pt;
        font-size: 9pt;
        font-weight: 600;
        color: {NEXUS_TEXT};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12pt;
        padding: 0 6pt;
        color: {NEXUS_ACCENT};
    }}

    /* Inputs */
    QComboBox, QLineEdit, QSpinBox {{
        background-color: {NEXUS_INPUT_BG};
        color: {NEXUS_TEXT};
        border: 1pt solid {NEXUS_BORDER};
        border-radius: 4pt;
        padding: 6pt 10pt;
        font-size: 9pt;
    }}
    QComboBox:hover, QLineEdit:hover {{
        border-color: {NEXUS_ACCENT};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24pt;
    }}
    QComboBox QAbstractItemView {{
        background-color: {NEXUS_INPUT_BG};
        color: {NEXUS_TEXT};
        border: 1pt solid {NEXUS_BORDER};
        selection-background-color: {NEXUS_PANEL};
        selection-color: {NEXUS_ACCENT};
    }}

    /* Slider */
    QSlider::groove:horizontal {{
        height: 4pt;
        background: {NEXUS_BORDER};
        border-radius: 2pt;
    }}
    QSlider::handle:horizontal {{
        background: {NEXUS_ACCENT};
        width: 14pt;
        height: 14pt;
        margin: -5pt 0;
        border-radius: 7pt;
    }}
    QSlider::sub-page:horizontal {{
        background: {NEXUS_ACCENT};
        border-radius: 2pt;
    }}

    /* Scroll area */
    QScrollArea {{
        border: none;
        background: transparent;
    }}
    QScrollBar:vertical {{
        background: transparent;
        width: 6pt;
    }}
    QScrollBar::handle:vertical {{
        background: {NEXUS_BORDER};
        border-radius: 3pt;
        min-height: 30pt;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {NEXUS_ACCENT};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}

    /* Check box */
    QCheckBox {{
        color: {NEXUS_TEXT};
        font-size: 9pt;
        spacing: 8pt;
    }}
    QCheckBox::indicator {{
        width: 18pt;
        height: 18pt;
        border-radius: 4pt;
        border: 1pt solid {NEXUS_BORDER};
        background: {NEXUS_INPUT_BG};
    }}
    QCheckBox::indicator:checked {{
        background: {NEXUS_ACCENT};
        border-color: {NEXUS_ACCENT};
    }}

    /* Text edit / plain text edit */
    QTextEdit, QPlainTextEdit {{
        background-color: {NEXUS_INPUT_BG};
        color: {NEXUS_TEXT};
        border: 1pt solid {NEXUS_BORDER};
        border-radius: 6pt;
        padding: 8pt;
        font-family: "Cascadia Code", "Fira Code", "Consolas", monospace;
        font-size: 8.5pt;
    }}

    /* Progress bar */
    QProgressBar {{
        background-color: {NEXUS_INPUT_BG};
        border: none;
        border-radius: 3pt;
        height: 6pt;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        background: {NEXUS_ACCENT};
        border-radius: 3pt;
    }}

    /* System tray */
    QSystemTrayIcon {{
        background: transparent;
    }}

    /* Menu */
    QMenu {{
        background-color: {NEXUS_PANEL};
        color: {NEXUS_TEXT};
        border: 1pt solid {NEXUS_BORDER};
        border-radius: 6pt;
        padding: 4pt;
    }}
    QMenu::item {{
        padding: 6pt 24pt 6pt 12pt;
        border-radius: 4pt;
    }}
    QMenu::item:selected {{
        background-color: {NEXUS_ACCENT};
        color: {NEXUS_DARK_BG};
    }}
"""


# ---------------------------------------------------------------------------
# Custom widgets
# ---------------------------------------------------------------------------


class TitleBar(QFrame):
    """Custom frameless title bar with drag support."""

    close_clicked = pyqtSignal()
    minimize_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(38)
        self.setStyleSheet(f"""
            QFrame#titleBar {{
                background: {NEXUS_PANEL};
                border-bottom: 1pt solid {NEXUS_BORDER};
            }}
        """)

        self._drag_pos = None
        self.setMouseTracking(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 8, 0)
        layout.setSpacing(0)

        self._title = QLabel(APP_NAME)
        self._title.setStyleSheet(f"color: {NEXUS_ACCENT}; font-size: 9pt; font-weight: 700; background: transparent; border: none;")
        layout.addWidget(self._title)
        layout.addStretch()

        btn_style = f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 4pt;
                padding: 4pt 8pt;
                font-size: 11pt;
                font-weight: 700;
                color: {NEXUS_TEXT_DIM};
            }}
            QPushButton:hover {{
                background: #2A3A5A;
                color: {NEXUS_TEXT};
            }}
        """

        self._btn_min = QPushButton("\u2014")
        self._btn_min.setFixedSize(28, 24)
        self._btn_min.setStyleSheet(btn_style)
        self._btn_min.clicked.connect(self.minimize_clicked.emit)
        layout.addWidget(self._btn_min)

        self._btn_max = QPushButton("\u25A1")
        self._btn_max.setFixedSize(28, 24)
        self._btn_max.setStyleSheet(btn_style)
        self._btn_max.clicked.connect(self._toggle_max)
        layout.addWidget(self._btn_max)

        self._btn_close = QPushButton("\u2715")
        self._btn_close.setFixedSize(28, 24)
        self._btn_close.setStyleSheet(f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 4pt;
                padding: 4pt 8pt;
                font-size: 11pt;
                font-weight: 700;
                color: {NEXUS_TEXT_DIM};
            }}
            QPushButton:hover {{
                background: {NEXUS_ERROR};
                color: white;
            }}
        """)
        self._btn_close.clicked.connect(self.close_clicked.emit)
        layout.addWidget(self._btn_close)

    def _toggle_max(self) -> None:
        win = self.window()
        if win.isMaximized():
            win.showNormal()
        else:
            win.showMaximized()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            win = self.window()
            if win.isMaximized():
                win.showNormal()
                frac = event.position().x() / self.width()
                new_x = int(event.globalPosition().x() - self.width() * frac)
                win.move(new_x, int(event.globalPosition().y() - self.height() // 2))
            self._drag_pos = event.globalPosition().toPoint() - win.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            win = self.window()
            win.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        self._toggle_max()
        super().mouseDoubleClickEvent(event)


class ThemePreviewWidget(QWidget):
    """Small painted preview of a theme layout."""

    def __init__(self, style: str = "windows", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._style = style
        self.setMinimumSize(160, 100)
        self.setMaximumSize(180, 110)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        bg = QColor(NEXUS_INPUT_BG)
        painter.setBrush(QBrush(bg))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(0, 0, w, h, 6, 6)

        if self._style == "macos":
            self._paint_macos(painter, w, h)
        elif self._style == "console":
            self._paint_console(painter, w, h)
        else:
            self._paint_windows(painter, w, h)
        painter.end()

    def _paint_macos(self, p: QPainter, w: int, h: int) -> None:
        p.setBrush(QBrush(QColor(NEXUS_PANEL)))
        p.drawRoundedRect(10, 4, w - 20, 18, 4, 4)

        p.setBrush(QBrush(QColor(NEXUS_ACCENT)))
        p.setOpacity(0.6)
        p.drawRoundedRect(14, 7, 40, 12, 2, 2)
        p.setOpacity(1.0)

        p.setBrush(QBrush(QColor(NEXUS_BORDER)))
        dock_h = 22
        dock_y = h - dock_h - 6
        p.drawRoundedRect(w // 2 - 35, dock_y, 70, dock_h, 8, 8)

        colors = [QColor("#FF5F57"), QColor("#FEBC2E"), QColor("#28C840")]
        for i, c in enumerate(colors):
            p.setBrush(QBrush(c))
            p.drawEllipse(w // 2 - 28 + i * 20, dock_y + 5, 10, 10)

    def _paint_windows(self, p: QPainter, w: int, h: int) -> None:
        bar_h = 22
        p.setBrush(QBrush(QColor(NEXUS_PANEL)))
        p.drawRoundedRect(4, h - bar_h - 4, w - 8, bar_h, 3, 3)

        p.setBrush(QBrush(QColor(NEXUS_ACCENT)))
        p.setOpacity(0.7)
        p.drawRoundedRect(8, h - bar_h - 1, 16, bar_h - 6, 2, 2)
        p.setOpacity(0.4)
        for i in range(1, 5):
            p.drawRoundedRect(28 + i * 14, h - bar_h, 10, bar_h - 8, 2, 2)
        p.setOpacity(1.0)

        p.setBrush(QBrush(QColor(NEXUS_BORDER)))
        p.setOpacity(0.3)
        p.drawRoundedRect(8, 8, w - 16, h - bar_h - 16, 4, 4)
        p.setOpacity(1.0)

    def _paint_console(self, p: QPainter, w: int, h: int) -> None:
        cols, rows = 4, 2
        gap = 4
        cw = (w - 16 - gap * (cols - 1)) // cols
        ch = (h - 12 - gap * (rows - 1)) // rows
        accent_colors = [
            QColor(NEXUS_ACCENT), QColor("#FF5252"),
            QColor("#00E676"), QColor("#FFD740"),
            QColor("#E040FB"), QColor("#448AFF"),
            QColor("#FF6E40"), QColor("#69F0AE"),
        ]
        for r in range(rows):
            for c in range(cols):
                x = 8 + c * (cw + gap)
                y = 6 + r * (ch + gap)
                color = accent_colors[(r * cols + c) % len(accent_colors)]
                p.setBrush(QBrush(color))
                p.setOpacity(0.25)
                p.drawRoundedRect(x, y, cw, ch, 4, 4)
                p.setOpacity(1.0)


class ThemeCard(QFrame):
    """A clickable theme card with preview and apply button."""

    apply_clicked = pyqtSignal(str)

    def __init__(
        self,
        style_key: str,
        title: str,
        description: str,
        preview_style: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._key = style_key
        self.setObjectName("card")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(260)
        self.setMaximumHeight(320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 80))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        preview = ThemePreviewWidget(preview_style, self)
        layout.addWidget(preview, alignment=Qt.AlignmentFlag.AlignCenter)

        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"font-size: 11pt; font-weight: 700; color: {NEXUS_TEXT}; background: transparent; border: none;")
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title_lbl)

        desc_lbl = QLabel(description)
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(f"font-size: 8pt; color: {NEXUS_TEXT_DIM}; background: transparent; border: none;")
        desc_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(desc_lbl)

        layout.addStretch()

        self._btn = QPushButton(f"Apply {title}")
        self._btn.setObjectName("accent")
        self._btn.setFixedHeight(32)
        self._btn.clicked.connect(lambda: self.apply_clicked.emit(self._key))
        layout.addWidget(self._btn)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.apply_clicked.emit(self._key)
        super().mousePressEvent(event)


class StatusIndicator(QWidget):
    """Dot + label status indicator."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self.setFixedSize(12, 12)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.update()

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(NEXUS_SUCCESS) if self._active else QColor(NEXUS_ERROR)
        p.setBrush(QBrush(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(1, 1, 10, 10)
        p.end()


# ---------------------------------------------------------------------------
# Tab 1: Themes
# ---------------------------------------------------------------------------


class ThemesTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        heading = QLabel("Theme Presets")
        heading.setObjectName("heading")
        root.addWidget(heading)

        sub = QLabel("Select a desktop layout. Changes take effect after restarting Plasma.")
        sub.setObjectName("subheading")
        sub.setWordWrap(True)
        root.addWidget(sub)

        grid = QGridLayout()
        grid.setSpacing(14)

        cards_data = [
            ("macos", "macOS Style", "Floating top panel, dock, magic lamp effect, left titlebar buttons", "macos"),
            ("windows", "Windows Style", "Bottom taskbar, standard Breeze theme, right titlebar buttons", "windows"),
            ("console", "Console Mode", "Hidden panel, no decorations, fullscreen gaming frontend", "console"),
        ]

        for col, (key, title, desc, preview) in enumerate(cards_data):
            card = ThemeCard(key, title, desc, preview, self)
            card.apply_clicked.connect(self._on_apply)
            grid.addWidget(card, 0, col)

        root.addLayout(grid)
        root.addStretch()

        self._status = QLabel("")
        self._status.setObjectName("subheading")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

    def _on_apply(self, style_key: str) -> None:
        dispatch = {
            "macos": apply_macos_style,
            "windows": apply_windows_style,
            "console": apply_console_style,
        }
        fn = dispatch.get(style_key)
        if fn is None:
            return

        result = fn()
        self._status.setText(f"Applied: {result}")
        self._status.setStyleSheet(f"color: {NEXUS_SUCCESS}; font-size: 8pt;")


# ---------------------------------------------------------------------------
# Tab 2: Console Mode
# ---------------------------------------------------------------------------


class ConsoleTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._frontend_proc: subprocess.Popen | None = None
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_status)
        self._poll_timer.start(2000)
        self._build_ui()
        self._refresh_controllers()
        self._poll_status()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        heading = QLabel("Console Mode")
        heading.setObjectName("heading")
        root.addWidget(heading)

        sub = QLabel("Manage fullscreen gaming mode with controller support.")
        sub.setObjectName("subheading")
        sub.setWordWrap(True)
        root.addWidget(sub)

        # Status row
        status_row = QHBoxLayout()
        status_row.setSpacing(10)

        self._status_dot = StatusIndicator(self)
        status_row.addWidget(self._status_dot)

        self._status_label = QLabel("Desktop Mode")
        self._status_label.setStyleSheet(f"font-size: 10pt; font-weight: 600; color: {NEXUS_TEXT};")
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        root.addLayout(status_row)

        # Gaming mode toggle
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(10)

        self._toggle_cb = QCheckBox("Gaming Mode Enabled")
        self._toggle_cb.setStyleSheet(f"font-size: 10pt; font-weight: 600;")
        self._toggle_cb.toggled.connect(self._on_toggle)
        toggle_row.addWidget(self._toggle_cb)
        toggle_row.addStretch()
        root.addLayout(toggle_row)

        # Frontend selection group
        fe_group = QGroupBox("Gaming Frontend")
        fe_layout = QVBoxLayout(fe_group)
        fe_layout.setSpacing(8)

        fe_row = QHBoxLayout()
        fe_row.setSpacing(8)

        fe_lbl = QLabel("Select frontend:")
        fe_lbl.setStyleSheet(f"font-size: 9pt; color: {NEXUS_TEXT_DIM};")
        fe_row.addWidget(fe_lbl)

        self._fe_combo = QComboBox()
        self._fe_combo.setFixedWidth(220)
        for key, info in FRONTENDS.items():
            avail = binary_available(info["binary"])
            label = f"{info['label']}{'  ✓' if avail else '  ✗'}"
            self._fe_combo.addItem(label, key)
            if not avail:
                idx = self._fe_combo.count() - 1
                self._fe_combo.model().item(idx).setEnabled(False)
        fe_row.addWidget(self._fe_combo)
        fe_row.addStretch()
        fe_layout.addLayout(fe_row)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self._btn_launch = QPushButton("Launch Gaming Mode Now")
        self._btn_launch.setObjectName("accent")
        self._btn_launch.setFixedWidth(200)
        self._btn_launch.clicked.connect(self._on_launch)
        btn_row.addWidget(self._btn_launch)

        self._btn_return = QPushButton("Return to Desktop")
        self._btn_return.setObjectName("danger")
        self._btn_return.setFixedWidth(160)
        self._btn_return.clicked.connect(self._on_return)
        btn_row.addWidget(self._btn_return)

        btn_row.addStretch()
        fe_layout.addLayout(btn_row)

        root.addWidget(fe_group)

        # Controller section
        ctrl_group = QGroupBox("Controller Detection")
        ctrl_layout = QVBoxLayout(ctrl_group)
        ctrl_layout.setSpacing(8)

        ctrl_top = QHBoxLayout()
        ctrl_top.setSpacing(8)

        self._ctrl_status = QLabel("Scanning …")
        self._ctrl_status.setStyleSheet(f"font-size: 9pt; color: {NEXUS_TEXT_DIM};")
        ctrl_top.addWidget(self._ctrl_status)

        btn_refresh = QPushButton("Refresh")
        btn_refresh.setFixedWidth(80)
        btn_refresh.clicked.connect(self._refresh_controllers)
        ctrl_top.addWidget(btn_refresh)
        ctrl_top.addStretch()
        ctrl_layout.addLayout(ctrl_top)

        self._ctrl_list = QLabel("No controllers detected")
        self._ctrl_list.setWordWrap(True)
        self._ctrl_list.setStyleSheet(f"font-size: 9pt; color: {NEXUS_TEXT}; padding: 4pt;")
        ctrl_layout.addWidget(self._ctrl_list)

        # Sensitivity slider
        sens_row = QHBoxLayout()
        sens_row.setSpacing(10)
        sens_lbl = QLabel("Stick Sensitivity:")
        sens_lbl.setStyleSheet(f"font-size: 9pt; color: {NEXUS_TEXT_DIM};")
        sens_row.addWidget(sens_lbl)

        self._sens_slider = QSlider(Qt.Orientation.Horizontal)
        self._sens_slider.setRange(1, 20)
        self._sens_slider.setValue(10)
        self._sens_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._sens_slider.setTickInterval(1)
        sens_row.addWidget(self._sens_slider, stretch=1)

        self._sens_val = QLabel("1.0x")
        self._sens_val.setFixedWidth(40)
        self._sens_val.setStyleSheet(f"font-size: 9pt; color: {NEXUS_ACCENT};")
        sens_row.addWidget(self._sens_val)

        self._sens_slider.valueChanged.connect(
            lambda v: self._sens_val.setText(f"{v / 10:.1f}x")
        )
        ctrl_layout.addLayout(sens_row)

        # Test buttons area
        test_row = QHBoxLayout()
        test_row.setSpacing(6)
        test_lbl = QLabel("Button Test:")
        test_lbl.setStyleSheet(f"font-size: 9pt; color: {NEXUS_TEXT_DIM};")
        test_row.addWidget(test_lbl)

        self._test_result = QLabel("—")
        self._test_result.setStyleSheet(
            f"font-size: 9pt; color: {NEXUS_WARNING}; min-width: 100pt;"
        )
        test_row.addWidget(self._test_result)

        btn_test = QPushButton("Read Input")
        btn_test.setFixedWidth(100)
        btn_test.clicked.connect(self._on_test_input)
        test_row.addWidget(btn_test)
        test_row.addStretch()
        ctrl_layout.addLayout(test_row)

        root.addWidget(ctrl_group)
        root.addStretch()

        # Log area
        log_group = QGroupBox("Activity Log")
        log_layout = QVBoxLayout(log_group)
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumHeight(120)
        self._log_view.setPlaceholderText("Activity will appear here …")
        log_layout.addWidget(self._log_view)
        root.addWidget(log_group)

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._log_view.appendPlainText(f"[{ts}] {msg}")

    def _on_toggle(self, checked: bool) -> None:
        if checked:
            self._launch_gaming_mode()
        else:
            self._return_to_desktop()

    def _on_launch(self) -> None:
        self._launch_gaming_mode()
        self._toggle_cb.setChecked(True)

    def _on_return(self) -> None:
        self._return_to_desktop()
        self._toggle_cb.setChecked(False)

    def _launch_gaming_mode(self) -> None:
        fe_key = self._fe_combo.currentData()
        if fe_key is None:
            self._log("No frontend selected")
            return

        info = FRONTENDS.get(fe_key)
        if info is None:
            return

        if not binary_available(info["binary"]):
            self._log(f"Frontend not found: {info['binary']}")
            QMessageBox.warning(
                self,
                "Frontend Not Found",
                f"{info['label']} ({info['binary']}) is not installed or not in PATH.",
            )
            self._toggle_cb.setChecked(False)
            return

        proc = launch_console_mode(fe_key)
        if proc is not None:
            self._frontend_proc = proc
            self._log(f"Launched {info['label']} (PID {proc.pid})")
            self._status_label.setText(f"Gaming Mode — {info['label']}")
            self._status_label.setStyleSheet(f"font-size: 10pt; font-weight: 600; color: {NEXUS_SUCCESS};")
        else:
            self._log(f"Failed to launch {info['label']}")
            self._toggle_cb.setChecked(False)

    def _return_to_desktop(self) -> None:
        killed = exit_console_mode()
        self._frontend_proc = None
        self._status_label.setText("Desktop Mode")
        self._status_label.setStyleSheet(f"font-size: 10pt; font-weight: 600; color: {NEXUS_TEXT};")
        if killed:
            self._log("Returned to desktop mode")
        else:
            self._log("Desktop mode restored (no frontend was running)")

    def _poll_status(self) -> None:
        running = frontend_running()
        self._status_dot.set_active(running)
        if running:
            self._status_label.setText("Gaming Mode Active")
            self._status_label.setStyleSheet(f"font-size: 10pt; font-weight: 600; color: {NEXUS_SUCCESS};")
            if not self._toggle_cb.isChecked():
                self._toggle_cb.blockSignals(True)
                self._toggle_cb.setChecked(True)
                self._toggle_cb.blockSignals(False)
        else:
            if self._toggle_cb.isChecked():
                self._toggle_cb.blockSignals(True)
                self._toggle_cb.setChecked(False)
                self._toggle_cb.blockSignals(False)

    def _refresh_controllers(self) -> None:
        controllers = detect_controllers()
        if controllers:
            names = [f"{c['name']}  ({c['path']})" for c in controllers]
            self._ctrl_list.setText("\n".join(names))
            self._ctrl_status.setText(f"{len(controllers)} controller(s) detected")
            self._ctrl_status.setStyleSheet(f"font-size: 9pt; color: {NEXUS_SUCCESS};")
            self._log(f"Detected {len(controllers)} controller(s)")
        else:
            self._ctrl_list.setText("No controllers detected — connect a gamepad and click Refresh")
            self._ctrl_status.setText("No controllers")
            self._ctrl_status.setStyleSheet(f"font-size: 9pt; color: {NEXUS_TEXT_DIM};")

    def _on_test_input(self) -> None:
        controllers = detect_controllers()
        if not controllers:
            self._test_result.setText("No pad")
            self._test_result.setStyleSheet(f"font-size: 9pt; color: {NEXUS_ERROR};")
            return

        path = controllers[0]["path"]
        self._test_result.setText("Waiting …")
        self._test_result.setStyleSheet(f"font-size: 9pt; color: {NEXUS_WARNING};")
        QApplication.processEvents()

        evt = read_gamepad_event(path, timeout_ms=500)
        if evt is not None:
            btn_names = {
                BTN_GAMEPAD: "A/Cross",
                BTN_TRIGGER: "RT",
                0x131: "B/Circle",
                0x132: "X/Square",
                0x133: "Y/Triangle",
                0x134: "LB",
                0x135: "RB",
                0x136: "Back/Select",
                0x137: "Start",
                0x138: "Guide/Home",
                0x139: "LS Click",
                0x13A: "RS Click",
                0x13B: "DPad Up",
                0x13C: "DPad Down",
                0x13D: "DPad Left",
                0x13E: "DPad Right",
            }
            name = btn_names.get(evt["code"], f"Code 0x{evt['code']:X}")
            state = "Pressed" if evt["value"] else "Released"
            self._test_result.setText(f"{name} {state}")
            self._test_result.setStyleSheet(f"font-size: 9pt; color: {NEXUS_SUCCESS};")
            self._log(f"Input: {name} {state}")
        else:
            self._test_result.setText("No input")
            self._test_result.setStyleSheet(f"font-size: 9pt; color: {NEXUS_TEXT_DIM};")


# ---------------------------------------------------------------------------
# Tab 3: About
# ---------------------------------------------------------------------------


class AboutTab(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        heading = QLabel("About NexusOS Theme Switcher")
        heading.setObjectName("heading")
        root.addWidget(heading)

        # Version card
        card = QFrame()
        card.setObjectName("card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(8)

        version_lbl = QLabel(f"Version {APP_VERSION}")
        version_lbl.setStyleSheet(f"font-size: 14pt; font-weight: 700; color: {NEXUS_ACCENT};")
        card_layout.addWidget(version_lbl)

        build_info = QLabel(f"Build: {time.strftime('%Y-%m-%d %H:%M')}  |  PID: {os.getpid()}")
        build_info.setStyleSheet(f"font-size: 8pt; color: {NEXUS_TEXT_DIM};")
        card_layout.addWidget(build_info)

        root.addWidget(card)

        # Description
        desc = QLabel(
            "NexusOS Theme Switcher provides one-click desktop layout presets "
            "for KDE Plasma, including a full-screen Console Mode with gamepad "
            "support for gaming frontends like Pegasus, Steam Big Picture, and "
            "EmulationStation."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 9pt; color: {NEXUS_TEXT}; line-height: 1.4;")
        root.addWidget(desc)

        # Features
        features = QGroupBox("Features")
        feat_layout = QVBoxLayout(features)
        feat_items = [
            "macOS-style floating panel with global menu and magic lamp effect",
            "Windows-style bottom taskbar with standard Breeze theme",
            "Console Mode — hidden panel, fullscreen gaming frontend",
            "Automatic gamepad detection via /dev/input/event*",
            "System tray integration — runs in background",
            "Single-instance lock — prevents duplicate launches",
        ]
        for item in feat_items:
            lbl = QLabel(f"  •  {item}")
            lbl.setStyleSheet(f"font-size: 8.5pt; color: {NEXUS_TEXT}; padding: 2pt 0;")
            feat_layout.addWidget(lbl)
        root.addWidget(features)

        # Dependencies
        deps = QGroupBox("Dependencies")
        dep_layout = QVBoxLayout(deps)
        dep_items = [
            ("PyQt6", "GUI framework"),
            ("KDE Plasma 5.x / 6.x", "Desktop environment"),
            ("Qt 6.x", "Window manager (KWin)"),
            ("Pegasus FE / Steam / ES", "Optional — gaming frontends"),
            ("Python 3.11+", "Runtime"),
        ]
        for name, role in dep_items:
            row = QHBoxLayout()
            row.setSpacing(8)
            n = QLabel(name)
            n.setStyleSheet(f"font-size: 9pt; font-weight: 600; color: {NEXUS_TEXT};")
            n.setFixedWidth(160)
            row.addWidget(n)
            r = QLabel(role)
            r.setStyleSheet(f"font-size: 9pt; color: {NEXUS_TEXT_DIM};")
            row.addWidget(r)
            row.addStretch()
            dep_layout.addLayout(row)
        root.addWidget(deps)

        # Credits
        credits_card = QFrame()
        credits_card.setObjectName("card")
        cc_layout = QVBoxLayout(credits_card)
        cc_layout.setContentsMargins(16, 12, 16, 12)
        cc_layout.setSpacing(4)
        cc_label = QLabel("NexusOS Project  •  Licensed under GPL-3.0  •  Built with PyQt6")
        cc_label.setStyleSheet(f"font-size: 8pt; color: {NEXUS_TEXT_DIM};")
        cc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cc_layout.addWidget(cc_label)
        root.addWidget(credits_card)

        root.addStretch()

        # Debug info
        debug_group = QGroupBox("System Info")
        dbg_layout = QVBoxLayout(debug_group)
        info_lines = [
            f"User: {os.environ.get('USER', os.environ.get('USERNAME', 'unknown'))}",
            f"Home: {str(Path.home())}",
            f"Config: {str(CONFIG_DIR)}",
            f"Plasma appletsrc: {PLASMA_APPLETSRC} ({'exists' if PLASMA_APPLETSRC.exists() else 'missing'})",
            f"KWin config: {KWINRC} ({'exists' if KWINRC.exists() else 'missing'})",
            f"Console PID file: {PID_FILE} ({'exists' if PID_FILE.exists() else 'missing'})",
            f"Frontend running: {frontend_running()}",
        ]
        for line in info_lines:
            lbl = QLabel(line)
            lbl.setStyleSheet(f"font-size: 8pt; font-family: 'Cascadia Code', monospace; color: {NEXUS_TEXT_DIM}; padding: 1pt 0;")
            dbg_layout.addWidget(lbl)
        root.addWidget(debug_group)


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------


class NexusMainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(820, 560)
        self.resize(960, 640)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Title bar
        self._title_bar = TitleBar(self)
        self._title_bar.close_clicked.connect(self._on_close)
        self._title_bar.minimize_clicked.connect(self._on_minimize)
        main_layout.addWidget(self._title_bar)

        # Separator
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {NEXUS_BORDER};")
        main_layout.addWidget(sep)

        # Tab content area (custom tab bar)
        self._tab_stack: list[QWidget] = []
        self._active_tab = 0

        # Tab bar
        tab_container = QWidget()
        tab_container.setStyleSheet(f"background: {NEXUS_DARK_BG};")
        tab_layout = QHBoxLayout(tab_container)
        tab_layout.setContentsMargins(20, 0, 20, 0)
        tab_layout.setSpacing(0)

        self._tab_buttons: list[QPushButton] = []
        tab_names = ["Themes", "Console", "About"]
        for i, name in enumerate(tab_names):
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setChecked(i == 0)
            btn.setFixedHeight(36)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    border: none;
                    border-bottom: 2pt solid transparent;
                    color: {NEXUS_TEXT_DIM};
                    font-size: 10pt;
                    font-weight: 600;
                    padding: 0 16pt;
                }}
                QPushButton:checked {{
                    color: {NEXUS_ACCENT};
                    border-bottom: 2pt solid {NEXUS_ACCENT};
                }}
                QPushButton:hover {{
                    color: {NEXUS_TEXT};
                }}
            """)
            btn.clicked.connect(lambda _, idx=i: self._switch_tab(idx))
            tab_layout.addWidget(btn)
            self._tab_buttons.append(btn)

        tab_layout.addStretch()
        main_layout.addWidget(tab_container)

        # Tab pages
        self._themes_tab = ThemesTab(self)
        self._console_tab = ConsoleTab(self)
        self._about_tab = AboutTab(self)

        self._tab_stack = [self._themes_tab, self._console_tab, self._about_tab]

        for w in self._tab_stack:
            w.setVisible(False)
        self._tab_stack[0].setVisible(True)

        # Content area inside a scroll area
        self._content_area = QWidget()
        self._content_area.setStyleSheet(f"background: {NEXUS_DARK_BG};")
        self._content_layout = QVBoxLayout(self._content_area)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        main_layout.addWidget(self._content_area, stretch=1)

        for w in self._tab_stack:
            self._content_layout.addWidget(w)

    def _switch_tab(self, idx: int) -> None:
        if idx == self._active_tab:
            return
        self._active_tab = idx
        for i, btn in enumerate(self._tab_buttons):
            btn.setChecked(i == idx)
        for i, w in enumerate(self._tab_stack):
            w.setVisible(i == idx)

    def _on_close(self) -> None:
        self.hide()
        log.info("Window hidden (system tray)")

    def _on_minimize(self) -> None:
        self.showMinimized()

    def closeEvent(self, event) -> None:
        if frontend_running():
            reply = QMessageBox.question(
                self,
                "Console Mode Active",
                "A gaming frontend is still running. Return to desktop before exiting?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                exit_console_mode()
        event.accept()


# ---------------------------------------------------------------------------
# System Tray
# ---------------------------------------------------------------------------


class SystemTray(QSystemTrayIcon):
    def __init__(self, main_window: NexusMainWindow, parent: QApplication | None = None) -> None:
        super().__init__(parent)
        self._main = main_window

        pixmap = QPixmap(32, 32)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QBrush(QColor(NEXUS_ACCENT)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(2, 2, 28, 28, 6, 6)
        painter.setPen(QPen(QColor(NEXUS_DARK_BG), 2))
        font = QFont("Arial", 14, QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "N")
        painter.end()

        self.setIcon(QIcon(pixmap))
        self.setToolTip(APP_NAME)

        menu = QMenu()
        menu.setStyleSheet(f"""
            QMenu {{
                background: {NEXUS_PANEL};
                color: {NEXUS_TEXT};
                border: 1pt solid {NEXUS_BORDER};
                border-radius: 6pt;
                padding: 4pt;
            }}
            QMenu::item {{
                padding: 6pt 24pt 6pt 12pt;
                border-radius: 4pt;
            }}
            QMenu::item:selected {{
                background: {NEXUS_ACCENT};
                color: {NEXUS_DARK_BG};
            }}
        """)

        show_action = QAction("Show Window", self)
        show_action.triggered.connect(self._show_window)
        menu.addAction(show_action)

        menu.addSeparator()

        theme_action = QAction("Switch to Themes", self)
        theme_action.triggered.connect(lambda: self._switch_tab_and_show(0))
        menu.addAction(theme_action)

        console_action = QAction("Switch to Console", self)
        console_action.triggered.connect(lambda: self._switch_tab_and_show(1))
        menu.addAction(console_action)

        menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self._exit_app)
        menu.addAction(exit_action)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def _show_window(self) -> None:
        self._main.show()
        self._main.raise_()
        self._main.activateWindow()

    def _switch_tab_and_show(self, idx: int) -> None:
        self._main._switch_tab(idx)
        self._show_window()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._show_window()

    def _exit_app(self) -> None:
        if frontend_running():
            exit_console_mode()
        QApplication.instance().quit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def setup_signal_handlers(app: QApplication, main_win: NexusMainWindow, tray: SystemTray, lock: InstanceLock) -> None:
    def _handle_signal(signum, frame):
        log.info("Signal %d received, shutting down …", signum)
        if frontend_running():
            exit_console_mode()
        lock.release()
        app.quit()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)


def main() -> int:
    lock = InstanceLock()
    if not lock.acquire():
        another = LOCK_FILE.read_text(encoding="utf-8").strip() if LOCK_FILE.exists() else "?"
        print(f"{APP_NAME} is already running (PID {another}). Exiting.", file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)

    app.setStyleSheet(build_qss())

    main_win = NexusMainWindow()
    tray = SystemTray(main_win, app)
    tray.show()

    setup_signal_handlers(app, main_win, tray, lock)

    main_win.show()

    log.info("%s v%s started (PID %d)", APP_NAME, APP_VERSION, os.getpid())

    exit_code = app.exec()

    if frontend_running():
        exit_console_mode()

    lock.release()
    log.info("Exiting with code %d", exit_code)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
