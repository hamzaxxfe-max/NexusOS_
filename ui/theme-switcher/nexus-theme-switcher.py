#!/usr/bin/env python3
import sys
import os
import fcntl
import configparser
import subprocess
import logging
from datetime import datetime
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QPushButton, QTabBar, QStackedWidget,
    QFrame, QSystemTrayIcon, QMenu, QSizePolicy, QScrollArea
)
from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QRect, QPoint, QSize,
    QTimer, pyqtSignal, QParallelAnimationGroup, QSequentialAnimationGroup
)
from PyQt6.QtGui import (
    QFont, QColor, QPainter, QPainterPath, QPen, QBrush,
    QLinearGradient, QRadialGradient, QIcon, QPixmap, QScreen
)

LOG_DIR = "/var/log/nexusos"
LOG_FILE = os.path.join(LOG_DIR, "theme-switch.log")
LOCK_FILE = "/tmp/nexusos-theme.lock"
CONFIG_DIR = os.path.expanduser("~/.config")
PLASMARC = os.path.join(CONFIG_DIR, "plasmarc")
KWINRC = os.path.join(CONFIG_DIR, "kwinrc")
KDEGLOBALS = os.path.join(CONFIG_DIR, "kdeglobals")
PANEL_APPLETSRC = os.path.join(CONFIG_DIR, "plasma-org.kde.plasma.desktop-appletsrc")

BG_PRIMARY = "#121212"
BG_SECONDARY = "#1A2238"
ACCENT = "#00D2FF"
ACCENT_HOVER = "#33DBFF"
TEXT_PRIMARY = "#FFFFFF"
TEXT_SECONDARY = "#B0B0B0"
DANGER = "#FF4444"
SUCCESS = "#44FF88"

PREVIEW_MACOS = (
    "┌──────────────────────────────────────┐\n"
    "│ [Apple]  File  Edit  View  Help       │  ← Top Panel\n"
    "├──────────────────────────────────────┤\n"
    "│                                      │\n"
    "│                                      │\n"
    "│          Desktop Workspace           │\n"
    "│                                      │\n"
    "│                                      │\n"
    "│          ┌──┐ ┌──┐ ┌──┐             │\n"
    "│          │📁│ │🎮│ │⚙│              │  ← Floating Dock\n"
    "│          └──┘ └──┘ └──┘             │\n"
    "└──────────────────────────────────────┘"
)
PREVIEW_WINDOWS = (
    "┌──────────────────────────────────────┐\n"
    "│                                      │\n"
    "│          Desktop Workspace           │\n"
    "│                                      │\n"
    "│                                      │\n"
    "│                                      │\n"
    "├──────────────────────────────────────┤\n"
    "│ [⊞] 📁 🎮 🌐 📋     🔊 🕐 12:00   │  ← Bottom Taskbar\n"
    "└──────────────────────────────────────┘"
)
PREVIEW_CONSOLE = (
    "┌──────────────────────────────────────┐\n"
    "│                                      │\n"
    "│   ╔══════════════════════════════╗   │\n"
    "│   ║    N E X U S  G A M E S     ║   │\n"
    "│   ╠══════╦══════╦══════╦════════╣   │\n"
    "│   ║  🎮  ║  🎮  ║  🎮  ║   🎮   ║   │\n"
    "│   ║ Halo ║ Cyber║ Doom ║ Zelda  ║   │\n"
    "│   ╠══════╬══════╬══════╬════════╣   │\n"
    "│   ║  🎮  ║  🎮  ║  🎮  ║   🎮   ║   │\n"
    "│   ║ Game ║ Game ║ Game ║  Game  ║   │\n"
    "│   ╚══════╩══════╩══════╩════════╝   │\n"
    "│      ◄  A Select  B Back  ►         │\n"
    "└──────────────────────────────────────┘"
)


def ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


def log_theme_change(style_name, status="success"):
    ensure_log_dir()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] Theme changed to: {style_name} — {status}\n"
    with open(LOG_FILE, "a") as f:
        f.write(entry)


def write_plasma_config(filepath, section, key, value):
    config = configparser.ConfigParser()
    if os.path.exists(filepath):
        config.read(filepath)
    if not config.has_section(section):
        config.add_section(section)
    config.set(section, key, value)
    with open(filepath, "w") as f:
        config.write(f)


def write_plasma_applets(content):
    os.makedirs(os.path.dirname(PANEL_APPLETSRC), exist_ok=True)
    with open(PANEL_APPLETSRC, "w") as f:
        f.write(content)


def restart_plasmashell():
    try:
        subprocess.run(["kquitapp5", "plasmashell"], timeout=10,
                        capture_output=True)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    try:
        subprocess.Popen(["kstart5", "plasmashell"])
    except FileNotFoundError:
        pass


class ScaleManager:
    def __init__(self, screen=None):
        if screen is None:
            screen = QApplication.primaryScreen()
        self.screen = screen
        geo = screen.geometry()
        self.raw_w = geo.width()
        self.raw_h = geo.height()
        self.dpi = screen.logicalDotsPerInch()
        self.scale_factor = self.dpi / 96.0

    def px(self, base):
        return max(1, int(base * self.scale_factor))

    def pt(self, base):
        return max(7, int(base * self.scale_factor))

    def radius(self, base):
        return max(2, int(base * self.scale_factor))

    def card_w(self):
        return int(self.raw_w * 0.28)

    def card_h(self):
        return int(self.raw_h * 0.55)

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
                font-family: "Noto Sans", "Segoe UI", sans-serif;
                font-size: {self.pt(10)}pt;
            }}
            QFrame#panel {{
                background-color: {BG_SECONDARY};
                border-radius: {self.radius(8)}px;
            }}
            QPushButton {{
                background-color: {ACCENT};
                color: #000000;
                border: none;
                border-radius: {self.radius(6)}px;
                padding: {self.px(8)}px {self.px(16)}px;
                font-size: {self.pt(10)}pt;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {ACCENT_HOVER};
            }}
            QLabel {{
                background: transparent;
                color: {TEXT_PRIMARY};
            }}
            QTabBar::tab {{
                background: {BG_SECONDARY};
                color: {TEXT_SECONDARY};
                padding: {self.px(10)}px {self.px(20)}px;
                border: none;
                border-bottom: {self.px(2)}px solid transparent;
            }}
            QTabBar::tab:selected {{
                color: {ACCENT};
                border-bottom: {self.px(2)}px solid {ACCENT};
            }}
        """


class ThemeCard(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, title, icon_text, description, preview_text,
                 style_key, scale, parent=None):
        super().__init__(parent)
        self.title = title
        self.icon_text = icon_text
        self.description = description
        self.preview_text = preview_text
        self.style_key = style_key
        self.scale = scale
        self._selected = False
        self._hover = False
        self._glow_radius = 0.0

        self.setFixedSize(scale.card_w(), scale.card_h())
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            scale.px(20), scale.px(16), scale.px(20), scale.px(16)
        )
        layout.setSpacing(scale.px(8))

        icon_label = QLabel(icon_text)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFont(scale.font(size=28))
        icon_label.setStyleSheet(f"color: {ACCENT}; font-size: {scale.pt(28)}pt;")
        layout.addWidget(icon_label)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setFont(scale.font(size=14, bold=True))
        layout.addWidget(title_label)

        desc_label = QLabel(description)
        desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        desc_label.setFont(scale.font(size=9))
        desc_label.setStyleSheet(f"color: {TEXT_SECONDARY};")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        preview_label = QLabel(preview_text)
        preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_label.setFont(QFont("Noto Sans Mono", scale.pt(7)))
        preview_label.setStyleSheet(
            f"color: {TEXT_SECONDARY}; background-color: #0D0D15; "
            f"border-radius: {self.scale.radius(6)}px; "
            f"padding: {self.scale.px(8)}px;"
        )
        layout.addWidget(preview_label, 1)

        self.setStyleSheet(self._build_style())

    def _build_style(self):
        border = f"3px solid {ACCENT}" if self._selected else "1px solid #333333"
        glow = f"0 0 {int(self._glow_radius)}px {ACCENT}" if self._glow_radius > 0 else "none"
        return (
            f"ThemeCard {{ background-color: {BG_SECONDARY}; "
            f"border-radius: {self.scale.radius(12)}px; "
            f"border: {border}; box-shadow: {glow}; }}"
        )

    def enterEvent(self, event):
        self._hover = True
        self._glow_radius = 8.0
        self.setStyleSheet(self._build_style())

    def leaveEvent(self, event):
        self._hover = False
        self._glow_radius = 0.0
        self.setStyleSheet(self._build_style())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.style_key)


class GlowBackground(QWidget):
    def __init__(self, scale, parent=None):
        super().__init__(parent)
        self.scale = scale
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._animate)
        self._timer.start(50)

    def _animate(self):
        self._phase += 0.05
        if self._phase > 6.28:
            self._phase = 0.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(BG_PRIMARY))

        w = self.width()
        h = self.height()
        cx = w // 2
        cy = h // 2
        import math
        radius = int(min(w, h) * 0.3 + math.sin(self._phase) * 20)

        gradient = QRadialGradient(cx, cy, radius)
        gradient.setColorAt(0.0, QColor(0, 210, 255, 25))
        gradient.setColorAt(0.5, QColor(0, 210, 255, 8))
        gradient.setColorAt(1.0, QColor(0, 210, 255, 0))
        painter.setBrush(QBrush(gradient))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPoint(cx, cy), radius, radius)

        gradient2 = QRadialGradient(int(w * 0.7), int(h * 0.3), radius // 2)
        gradient2.setColorAt(0.0, QColor(123, 47, 190, 15))
        gradient2.setColorAt(1.0, QColor(123, 47, 190, 0))
        painter.setBrush(QBrush(gradient2))
        painter.drawEllipse(QPoint(int(w * 0.7), int(h * 0.3)), radius // 2, radius // 2)
        painter.end()


class ThemesTab(QWidget):
    def __init__(self, scale, parent=None):
        super().__init__(parent)
        self.scale = scale
        self.cards = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*[scale.px(24)] * 4)
        layout.setSpacing(scale.px(16))

        header = QLabel("Choose Your Desktop Style")
        header.setFont(scale.font(size=16, bold=True))
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        subtitle = QLabel("Select a visual theme. Changes apply instantly without reboot.")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setFont(scale.font(size=9))
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(subtitle)

        grid = QGridLayout()
        grid.setSpacing(scale.px(20))
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)

        themes = [
            ("macOS Style", "\U0001F34E",
             "Floating panel, global menu,\nMagic Lamp animations",
             PREVIEW_MACOS, "macos"),
            ("Windows Style", "\U0001F5FA\uFE0F",
             "Traditional taskbar, Start menu,\nSystem tray integration",
             PREVIEW_WINDOWS, "windows"),
            ("Console Style", "\U0001F3AE",
             "Full-screen gamepad UI,\nPegasus / Steam Big Picture",
             PREVIEW_CONSOLE, "console"),
        ]

        self._selected_style = "windows"

        for col, (title, icon, desc, preview, key) in enumerate(themes):
            card = ThemeCard(title, icon, desc, preview, key, scale)
            card.clicked.connect(self._on_card_clicked)
            if key == "windows":
                card._selected = True
                card._glow_radius = 8.0
                card.setStyleSheet(card._build_style())
            grid.addWidget(card, 0, col)
            self.cards[key] = card

        layout.addLayout(grid, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        reset_btn = QPushButton("Reset to Default")
        reset_btn.setFont(scale.font(size=9))
        reset_btn.setFixedWidth(scale.px(200))
        reset_btn.setStyleSheet(
            f"background-color: transparent; border: 1px solid {TEXT_SECONDARY}; "
            f"color: {TEXT_SECONDARY}; border-radius: {scale.radius(6)}px; "
            f"padding: {scale.px(6)}px {scale.px(12)}px;"
        )
        reset_btn.clicked.connect(lambda: self._on_card_clicked("windows"))
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

    def _on_card_clicked(self, style_key):
        for key, card in self.cards.items():
            card._selected = (key == style_key)
            card._glow_radius = 8.0 if card._selected else 0.0
            card.setStyleSheet(card._build_style())

        apply_map = {
            "macos": apply_macos_style,
            "windows": apply_windows_style,
            "console": apply_console_style,
        }
        if style_key in apply_map:
            apply_map[style_key]()
            self._selected_style = style_key


class AboutTab(QWidget):
    def __init__(self, scale, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*[scale.px(40)] * 4)
        layout.setSpacing(scale.px(16))

        title = QLabel("NexusOS Settings")
        title.setFont(scale.font(size=18, bold=True))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        version = QLabel("Version 1.0.0 — Build 2026.07.27")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        version.setFont(scale.font(size=10))
        version.setStyleSheet(f"color: {TEXT_SECONDARY};")
        layout.addWidget(version)

        layout.addSpacing(scale.px(20))

        info_lines = [
            "Immutable Gaming OS for Low-Spec Hardware",
            "Built on Arch Linux with Btrfs Snapshots",
            "Linux-Zen Low-Latency Kernel",
            "",
            "Copyright 2026 NexusOS Technologies",
            "Proprietary Software — All Rights Reserved",
        ]
        for line in info_lines:
            lbl = QLabel(line)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFont(scale.font(size=9))
            if "Copyright" in line or "Proprietary" in line:
                lbl.setStyleSheet(f"color: {TEXT_SECONDARY};")
            layout.addWidget(lbl)

        layout.addStretch()


class ThemeSwitcher(QMainWindow):
    def __init__(self):
        super().__init__()
        self.scale = ScaleManager()
        self.setWindowTitle("NexusOS Settings")
        self.setMinimumSize(self.scale.px(800), self.scale.px(500))
        self.resize(
            min(self.scale.raw_w - 200, self.scale.px(960)),
            min(self.scale.raw_h - 150, self.scale.px(640))
        )
        self.setStyleSheet(self.scale.stylesheet())
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        title_bar = QWidget()
        title_bar.setFixedHeight(self.scale.px(36))
        title_bar.setStyleSheet(
            f"background-color: {BG_SECONDARY}; border-bottom: 1px solid #333333;"
        )
        tb_layout = QHBoxLayout(title_bar)
        tb_layout.setContentsMargins(self.scale.px(12), 0, self.scale.px(12), 0)

        title_text = QLabel("NexusOS Settings")
        title_text.setFont(self.scale.font(size=9, bold=True))
        tb_layout.addWidget(title_text)
        tb_layout.addStretch()

        close_btn = QPushButton("\u2715")
        close_btn.setFixedSize(self.scale.px(28), self.scale.px(28))
        close_btn.setStyleSheet(
            f"background: transparent; color: {TEXT_SECONDARY}; border: none; "
            f"font-size: {self.scale.pt(10)}pt;"
        )
        close_btn.clicked.connect(self.close)
        tb_layout.addWidget(close_btn)

        main_layout.addWidget(title_bar)

        self._drag_pos = None
        title_bar.mousePressEvent = self._title_mouse_press
        title_bar.mouseMoveEvent = self._title_mouse_move

        tabs = QTabBar()
        tabs.addTab("Themes")
        tabs.addTab("About")
        tabs.setFont(self.scale.font(size=10))
        tabs.setStyleSheet(
            f"QTabBar {{ background: {BG_SECONDARY}; border-bottom: 1px solid #333333; }}"
        )
        main_layout.addWidget(tabs)

        self.stack = QStackedWidget()
        self.stack.addWidget(ThemesTab(self.scale))
        self.stack.addWidget(AboutTab(self.scale))
        main_layout.addWidget(self.stack, 1)

        tabs.currentChanged.connect(self.stack.setCurrentIndex)

    def _title_mouse_press(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def _title_mouse_move(self, event):
        if self._drag_pos and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)


def apply_macos_style():
    write_plasma_config(PLASMARC, "General", "ShellPackage", "org.kde.plasma.desktop")
    panel_cfg = (
        "[PlasmaViews][Panel 2]\n"
        "panelVisibility=0\n"
        "panelHeight=28\n"
        "panelPosition=Top\n"
        "floating=true\n\n"
        "[PlasmaViews][Panel 2][Defaults]\n"
        "length=100\n"
        "alignment=Center\n"
    )
    write_plasma_appletsrc(panel_cfg)
    write_plasma_config(KWINRC, "Windows", "TitlebarAlignment", "Left")
    write_plasma_config(KWINRC, "Compositing", "AnimationSpeed", "3")
    write_plasma_config(KWINRC, "Effect-magiclamp", "Enabled", "true")
    log_theme_change("macOS Style")
    restart_plasmashell()


def apply_windows_style():
    panel_cfg = (
        "[PlasmaViews][Panel 2]\n"
        "panelVisibility=0\n"
        "panelHeight=40\n"
        "panelPosition=Bottom\n"
        "floating=false\n\n"
        "[PlasmaViews][Panel 2][Defaults]\n"
        "length=100\n"
        "alignment=Left\n"
    )
    write_plasma_appletsrc(panel_cfg)
    write_plasma_config(KWINRC, "Windows", "TitlebarAlignment", "Right")
    write_plasma_config(KWINRC, "Compositing", "AnimationSpeed", "2")
    write_plasma_config(KWINRC, "Effect-magiclamp", "Enabled", "false")
    log_theme_change("Windows Style")
    restart_plasmashell()


def apply_console_style():
    panel_cfg = (
        "[PlasmaViews][Panel 2]\n"
        "panelVisibility=2\n"
        "panelHeight=0\n"
    )
    write_plasma_appletsrc(panel_cfg)
    write_plasma_config(KWINRC, "Windows", "BorderlessMaximizedWindows", "true")
    log_theme_change("Console Style")

    pegasus_check = subprocess.run(
        ["which", "pegasus-fe"], capture_output=True
    )
    if pegasus_check.returncode == 0:
        subprocess.Popen(["pegasus-fe", "--fullscreen"])
    else:
        bp_check = subprocess.run(
            ["which", "plasma-bigpicture"], capture_output=True
        )
        if bp_check.returncode == 0:
            subprocess.Popen(["plasma-bigpicture"])
        else:
            subprocess.Popen(["kstart5", "plasmashell"])


def main():
    ensure_log_dir()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("NexusOS Settings")

    try:
        lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError):
        sys.exit(0)

    window = ThemeSwitcher()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
