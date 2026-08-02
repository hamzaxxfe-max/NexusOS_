#!/usr/bin/env python3
import sys
import os
import json
import fcntl
import glob
import struct
import socket
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve, QTimer, QRect, QSize,
    QParallelAnimationGroup, QSequentialAnimationGroup, pyqtProperty,
    QThread, pyqtSignal, QPoint, QSettings
)
from PyQt6.QtGui import (
    QPainter, QColor, QLinearGradient, QFont, QFontDatabase,
    QPen, QBrush, QPainterPath, QPolygon, QPalette
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QSlider, QComboBox,
    QCheckBox, QGridLayout, QFrame, QScrollArea
)

APP_NAME = "Aion Setup"
CONFIG_DIR = Path("/etc/aion")
CONFIG_FILE = CONFIG_DIR / "config.json"
MARKER_FILE = CONFIG_DIR / ".oobe-complete"
LOCK_FILE = Path("/tmp/aion-oobe.lock")

BG_PRIMARY = "#121212"
BG_PANEL = "#1A2238"
ACCENT = "#00D2FF"
ACCENT_DIM = "#007A8C"
TEXT_PRIMARY = "#FFFFFF"
TEXT_SECONDARY = "#B0BEC5"
TEXT_MUTED = "#607D8B"
DANGER = "#FF5252"
SUCCESS = "#69F0AE"
PANEL_BORDER = "#263248"

LANGUAGES = [
    ("EN", "English", "English"),
    ("ES", "Español", "Spanish"),
    ("FR", "Français", "French"),
    ("DE", "Deutsch", "German"),
    ("JA", "日本語", "Japanese"),
    ("KO", "한국어", "Korean"),
    ("AR", "العربية", "Arabic"),
    ("PT", "Português", "Portuguese"),
    ("RU", "Русский", "Russian"),
    ("ZH", "中文", "Chinese"),
]

STEP_TITLES = [
    "Welcome",
    "Language",
    "Controller",
    "Display",
    "Security",
    "Ready",
]

STR_WELCOME_TITLE = "Aion"
STR_WELCOME_SUBTITLE = "Your system is ready. Let's get started."
STR_WELCOME_PRESS = "Press Next to begin setup."
STR_LANG_TITLE = "Select Language"
STR_LANG_SUBTITLE = "Choose your preferred language."
STR_CTRL_TITLE = "Controller Setup"
STR_CTRL_SUBTITLE = "Configure your game controller."
STR_CTRL_NONE = "No controller detected."
STR_CTRL_DETECTED = "Controller detected:"
STR_CTRL_DEVICE = "Device"
STR_CTRL_SENSITIVITY = "Sensitivity"
STR_CTRL_TEST = "Test Input"
STR_DISPLAY_TITLE = "Display Settings"
STR_DISPLAY_SUBTITLE = "Configure your display preferences."
STR_DISPLAY_RESOLUTION = "Resolution"
STR_DISPLAY_REFRESH = "Refresh Rate"
STR_DISPLAY_VSYNC = "V-Sync"
STR_DISPLAY_MODE = "Graphics Mode"
STR_DISPLAY_PERF = "Performance"
STR_DISPLAY_QUALITY = "Quality"
STR_SEC_TITLE = "Security & Privacy"
STR_SEC_SUBTITLE = "Review these settings before launch."
STR_SEC_BYPASS = "Allow bypassing system protections"
STR_SEC_OTA = "Enable automatic OTA updates"
STR_SEC_DATA = "Share anonymous usage data"
STR_SUMMARY_TITLE = "Setup Complete"
STR_SUMMARY_SUBTITLE = "Review your configuration."
STR_SUMMARY_LANG = "Language"
STR_SUMMARY_RES = "Resolution"
STR_SUMMARY_VSYNC = "V-Sync"
STR_SUMMARY_MODE = "Graphics Mode"
STR_SUMMARY_OTA = "OTA Updates"
STR_SUMMARY_DATA = "Data Collection"
STR_BTN_NEXT = "Next"
STR_BTN_BACK = "Back"
STR_BTN_LAUNCH = "Launch Aion"
STR_BTN_DONE = "Finish"

LOCK_FD = None


class ControllerDetector(QThread):
    detected = pyqtSignal(str, str)
    lost = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._running = True
        self._device = ""

    def run(self):
        while self._running:
            devices = glob.glob("/dev/input/event*")
            found = ""
            for dev in devices:
                try:
                    with open(dev, "rb") as f:
                        cap = fcntl.ioctl(f.fileno(), 0x4502, b"\x00" * 256)
                        ev_types = struct.unpack_from("<8H", cap, 28)
                        if ev_types[0] & 0x20:
                            found = dev
                            break
                except (OSError, IOError):
                    continue
            if found and not self._device:
                self._device = found
                name = self._read_name(found)
                self.detected.emit(found, name)
            elif not found and self._device:
                self._device = ""
                self.lost.emit()
            self.msleep(500)

    def _read_name(self, path):
        try:
            with open(path, "rb") as f:
                name_raw = fcntl.ioctl(f.fileno(), 0x4506, b"\x00" * 256)
                end = name_raw.find(b"\x00")
                if end > 0:
                    return name_raw[:end].decode("utf-8", errors="replace")
        except (OSError, IOError):
            pass
        return os.path.basename(path)

    def stop(self):
        self._running = False
        self.wait(2000)


class GlowBackground(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._glow_x = 0.0
        self._glow_y = 0.0
        self._glow_radius = 300.0
        self._glow_opacity = 0.3
        self._pulse_phase = 0.0

    @pyqtProperty(float)
    def glowX(self):
        return self._glow_x

    @glowX.setter
    def glowX(self, val):
        self._glow_x = val
        self.update()

    @pyqtProperty(float)
    def glowY(self):
        return self._glow_y

    @glowY.setter
    def glowY(self, val):
        self._glow_y = val
        self.update()

    @pyqtProperty(float)
    def glowRadius(self):
        return self._glow_radius

    @glowRadius.setter
    def glowRadius(self, val):
        self._glow_radius = val
        self.update()

    @pyqtProperty(float)
    def glowOpacity(self):
        return self._glow_opacity

    @glowOpacity.setter
    def glowOpacity(self, val):
        self._glow_opacity = val
        self.update()

    def set_pulse(self, phase):
        self._pulse_phase = phase
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        bg = QColor(BG_PRIMARY)
        painter.fillRect(0, 0, w, h, bg)

        accent_rgb = QColor(ACCENT)
        pulse = 0.15 + 0.1 * (0.5 + 0.5 * (self._pulse_phase % 6.283185 - 3.141593) / 3.141593)
        accent_rgb.setAlphaF(pulse)
        grad = QLinearGradient(0, 0, w, h)
        grad.setColorAt(0.0, QColor(ACCENT_DIM))
        grad.setColorAt(0.5, QColor(BG_PANEL))
        grad.setColorAt(1.0, QColor(ACCENT_DIM))
        painter.fillRect(0, 0, w, h, grad)

        glow_color = QColor(ACCENT)
        glow_color.setAlphaF(self._glow_opacity)
        path = QPainterPath()
        path.addEllipse(
            int(self._glow_x - self._glow_radius),
            int(self._glow_y - self._glow_radius),
            int(self._glow_radius * 2),
            int(self._glow_radius * 2),
        )
        for i in range(5):
            scale = 1.0 + i * 0.3
            c = QColor(glow_color)
            c.setAlphaF(glow_color.alphaF() / (1 + i * 0.8))
            p = QPainterPath()
            r = self._glow_radius * scale
            p.addEllipse(
                int(self._glow_x - r),
                int(self._glow_y - r),
                int(r * 2),
                int(r * 2),
            )
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(c))
            painter.drawPath(p)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(ACCENT)))
        painter.setOpacity(0.03)
        for y_off in range(0, h, 4):
            painter.setOpacity(0.01 + 0.005 * (y_off % 8 == 0))
            painter.drawLine(0, y_off, w, y_off)
        painter.setOpacity(1.0)

        painter.end()


class GlowText(QLabel):
    def __init__(self, text, size=72, color=TEXT_PRIMARY, parent=None):
        super().__init__(text, parent)
        self._size = size
        self._color = QColor(color)
        self._glow_strength = 0.0
        font = QFont("Segoe UI", size, QFont.Weight.Bold)
        self.setFont(font)
        self.setStyleSheet("background: transparent;")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    @pyqtProperty(float)
    def glowStrength(self):
        return self._glow_strength

    @glowStrength.setter
    def glowStrength(self, val):
        self._glow_strength = val
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        text = self.text()
        font = self.font()
        painter.setFont(font)
        fm = painter.fontMetrics()
        rect = self.rect()
        x = (rect.width() - fm.horizontalAdvance(text)) // 2
        y = (rect.height() + fm.ascent() - fm.descent()) // 2

        if self._glow_strength > 0:
            for i in range(8, 0, -1):
                glow_c = QColor(self._color)
                glow_c.setAlphaF(self._glow_strength * 0.15 / i)
                painter.setPen(QPen(glow_c, i * 2))
                painter.drawText(x, y, text)

        painter.setPen(QPen(self._color))
        painter.drawText(x, y, text)
        painter.end()


class PanelCard(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("panelCard")
        self.setStyleSheet(f"""
            QFrame#panelCard {{
                background-color: {BG_PANEL};
                border: 1px solid {PANEL_BORDER};
                border-radius: 16px;
            }}
        """)


class CyberButton(QPushButton):
    def __init__(self, text, primary=True, parent=None):
        super().__init__(text, parent)
        self._primary = primary
        self._hover = False
        self._pressed = False
        self.setMinimumHeight(52)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_style()

    def _update_style(self):
        if self._primary:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: {ACCENT};
                    color: #000000;
                    border: none;
                    border-radius: 12px;
                    font-size: 16px;
                    font-weight: 600;
                    padding: 12px 40px;
                }}
                QPushButton:hover {{
                    background-color: #33DDFF;
                }}
                QPushButton:pressed {{
                    background-color: #00A8CC;
                }}
                QPushButton:disabled {{
                    background-color: {ACCENT_DIM};
                    color: #555555;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background-color: transparent;
                    color: {TEXT_SECONDARY};
                    border: 1px solid {PANEL_BORDER};
                    border-radius: 12px;
                    font-size: 16px;
                    font-weight: 500;
                    padding: 12px 40px;
                }}
                QPushButton:hover {{
                    border-color: {ACCENT};
                    color: {ACCENT};
                }}
                QPushButton:pressed {{
                    background-color: rgba(0, 210, 255, 0.1);
                }}
            """)


class StepWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._opacity = 1.0

    @pyqtProperty(float)
    def opacity(self):
        return self._opacity

    @opacity.setter
    def opacity(self, val):
        self._opacity = val
        self.setWindowOpacity(val)


class WelcomeStep(StepWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.title = GlowText(STR_WELCOME_TITLE, 72, ACCENT)
        self.title.setMinimumHeight(100)

        subtitle = QLabel(STR_WELCOME_SUBTITLE)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 20px; background: transparent;")

        hint = QLabel(STR_WELCOME_PRESS)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 14px; background: transparent; margin-top: 30px;")

        layout.addStretch(2)
        layout.addWidget(self.title)
        layout.addSpacing(20)
        layout.addWidget(subtitle)
        layout.addStretch(2)
        layout.addWidget(hint)
        layout.addStretch(1)


class LanguageStep(StepWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel(STR_LANG_TITLE)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 28px; font-weight: bold; background: transparent;")

        subtitle = QLabel(STR_LANG_SUBTITLE)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 16px; background: transparent; margin-bottom: 20px;")

        self.selected_lang = "EN"
        self.buttons = []

        grid = QGridLayout()
        grid.setSpacing(12)

        for idx, (code, native, english) in enumerate(LANGUAGES):
            row, col = divmod(idx, 5)
            btn = QPushButton(f"{native}\n{english}")
            btn.setMinimumHeight(80)
            btn.setMinimumWidth(140)
            btn.setCheckable(True)
            btn.setChecked(code == "EN")
            btn.setProperty("lang_code", code)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {BG_PANEL};
                    color: {TEXT_PRIMARY};
                    border: 2px solid {PANEL_BORDER};
                    border-radius: 12px;
                    font-size: 14px;
                    padding: 10px;
                }}
                QPushButton:hover {{
                    border-color: {ACCENT_DIM};
                }}
                QPushButton:checked {{
                    border-color: {ACCENT};
                    background-color: rgba(0, 210, 255, 0.1);
                }}
            """)
            btn.clicked.connect(lambda checked, b=btn: self._on_select(b))
            grid.addWidget(btn, row, col)
            self.buttons.append(btn)

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addSpacing(8)
        layout.addWidget(subtitle)
        layout.addSpacing(10)
        card = PanelCard()
        card_layout = QVBoxLayout(card)
        card_layout.addLayout(grid)
        layout.addWidget(card)
        layout.addStretch(2)

    def _on_select(self, btn):
        for b in self.buttons:
            b.setChecked(False)
        btn.setChecked(True)
        self.selected_lang = btn.property("lang_code")


class ControllerStep(StepWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel(STR_CTRL_TITLE)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 28px; font-weight: bold; background: transparent;")

        subtitle = QLabel(STR_CTRL_SUBTITLE)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 16px; background: transparent; margin-bottom: 20px;")

        card = PanelCard()
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(20)

        self.status_label = QLabel(STR_CTRL_NONE)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 16px; background: transparent;")
        card_layout.addWidget(self.status_label)

        self.device_label = QLabel("")
        self.device_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.device_label.setStyleSheet(f"color: {ACCENT}; font-size: 14px; background: transparent;")
        card_layout.addWidget(self.device_label)

        sens_label = QLabel(STR_CTRL_SENSITIVITY)
        sens_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 14px; background: transparent;")
        card_layout.addWidget(sens_label)

        self.sensitivity_slider = QSlider(Qt.Orientation.Horizontal)
        self.sensitivity_slider.setRange(10, 200)
        self.sensitivity_slider.setValue(100)
        self.sensitivity_slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background: {PANEL_BORDER};
                height: 6px;
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {ACCENT};
                width: 18px;
                height: 18px;
                margin: -6px 0;
                border-radius: 9px;
            }}
            QSlider::sub-page:horizontal {{
                background: {ACCENT_DIM};
                border-radius: 3px;
            }}
        """)
        self.sensitivity_value = QLabel("100%")
        self.sensitivity_value.setStyleSheet(f"color: {ACCENT}; font-size: 14px; background: transparent;")
        self.sensitivity_slider.valueChanged.connect(
            lambda v: self.sensitivity_value.setText(f"{v}%")
        )

        slider_row = QHBoxLayout()
        slider_row.addWidget(self.sensitivity_slider, 1)
        slider_row.addSpacing(12)
        slider_row.addWidget(self.sensitivity_value)
        card_layout.addLayout(slider_row)

        self.test_btn = QPushButton(STR_CTRL_TEST)
        self.test_btn.setMinimumHeight(44)
        self.test_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {BG_PRIMARY};
                color: {ACCENT};
                border: 1px solid {ACCENT_DIM};
                border-radius: 8px;
                font-size: 14px;
                padding: 8px 24px;
            }}
            QPushButton:hover {{
                background-color: rgba(0, 210, 255, 0.1);
            }}
        """)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.test_btn)
        btn_row.addStretch()
        card_layout.addLayout(btn_row)

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addSpacing(8)
        layout.addWidget(subtitle)
        layout.addWidget(card)
        layout.addStretch(2)

        self.controller_path = ""
        self.controller_name = ""


class DisplayStep(StepWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel(STR_DISPLAY_TITLE)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 28px; font-weight: bold; background: transparent;")

        subtitle = QLabel(STR_DISPLAY_SUBTITLE)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 16px; background: transparent; margin-bottom: 20px;")

        card = PanelCard()
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(20)

        def make_combo_row(label_text, items, default_idx=0):
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFixedWidth(130)
            lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 14px; background: transparent;")
            combo = QComboBox()
            combo.addItems(items)
            combo.setCurrentIndex(default_idx)
            combo.setMinimumHeight(44)
            combo.setStyleSheet(f"""
                QComboBox {{
                    background-color: {BG_PRIMARY};
                    color: {TEXT_PRIMARY};
                    border: 1px solid {PANEL_BORDER};
                    border-radius: 8px;
                    padding: 8px 16px;
                    font-size: 14px;
                }}
                QComboBox:hover {{
                    border-color: {ACCENT_DIM};
                }}
                QComboBox::drop-down {{
                    border: none;
                    width: 30px;
                }}
                QComboBox QAbstractItemView {{
                    background-color: {BG_PANEL};
                    color: {TEXT_PRIMARY};
                    border: 1px solid {PANEL_BORDER};
                    selection-background-color: {ACCENT_DIM};
                }}
            """)
            row.addWidget(lbl)
            row.addWidget(combo, 1)
            return row, combo

        resolutions = ["3840x2160", "2560x1440", "1920x1080", "1600x900", "1280x720"]
        row_res, self.resolution_combo = make_combo_row(STR_DISPLAY_RESOLUTION, resolutions, 1)
        card_layout.addLayout(row_res)

        refresh_rates = ["240 Hz", "165 Hz", "144 Hz", "120 Hz", "60 Hz"]
        row_ref, self.refresh_combo = make_combo_row(STR_DISPLAY_REFRESH, refresh_rates, 2)
        card_layout.addLayout(row_ref)

        self.vsync_check = QCheckBox(STR_DISPLAY_VSYNC)
        self.vsync_check.setChecked(True)
        self.vsync_check.setStyleSheet(f"""
            QCheckBox {{
                color: {TEXT_SECONDARY};
                font-size: 14px;
                background: transparent;
                spacing: 10px;
            }}
            QCheckBox::indicator {{
                width: 22px;
                height: 22px;
                border: 2px solid {PANEL_BORDER};
                border-radius: 6px;
                background: {BG_PRIMARY};
            }}
            QCheckBox::indicator:hover {{
                border-color: {ACCENT_DIM};
            }}
            QCheckBox::indicator:checked {{
                background: {ACCENT};
                border-color: {ACCENT};
            }}
        """)
        card_layout.addWidget(self.vsync_check)

        mode_label = QLabel(STR_DISPLAY_MODE)
        mode_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 14px; background: transparent;")
        card_layout.addWidget(mode_label)

        mode_row = QHBoxLayout()
        self.perf_btn = QPushButton(STR_DISPLAY_PERF)
        self.quality_btn = QPushButton(STR_DISPLAY_QUALITY)
        self.graphics_mode = "performance"

        for btn, mode, is_first in [(self.perf_btn, "performance", True), (self.quality_btn, "quality", False)]:
            btn.setCheckable(True)
            btn.setChecked(is_first)
            btn.setMinimumHeight(44)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {BG_PRIMARY};
                    color: {TEXT_SECONDARY};
                    border: 2px solid {PANEL_BORDER};
                    border-radius: 8px;
                    font-size: 14px;
                    font-weight: 500;
                    padding: 8px 24px;
                }}
                QPushButton:hover {{
                    border-color: {ACCENT_DIM};
                }}
                QPushButton:checked {{
                    border-color: {ACCENT};
                    color: {ACCENT};
                    background-color: rgba(0, 210, 255, 0.1);
                }}
            """)
            btn.clicked.connect(lambda checked, m=mode: self._set_mode(m))
            mode_row.addWidget(btn)

        card_layout.addLayout(mode_row)

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addSpacing(8)
        layout.addWidget(subtitle)
        layout.addWidget(card)
        layout.addStretch(2)

    def _set_mode(self, mode):
        self.graphics_mode = mode
        self.perf_btn.setChecked(mode == "performance")
        self.quality_btn.setChecked(mode == "quality")


class SecurityStep(StepWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel(STR_SEC_TITLE)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 28px; font-weight: bold; background: transparent;")

        subtitle = QLabel(STR_SEC_SUBTITLE)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 16px; background: transparent; margin-bottom: 20px;")

        card = PanelCard()
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(24)

        def make_toggle(label_text, default=False):
            row = QHBoxLayout()
            row.setContentsMargins(8, 8, 8, 8)
            lbl = QLabel(label_text)
            lbl.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 15px; background: transparent;")
            lbl.setWordWrap(True)
            row.addWidget(lbl, 1)

            toggle = QCheckBox()
            toggle.setChecked(default)
            toggle.setFixedSize(52, 28)
            toggle.setStyleSheet(f"""
                QCheckBox {{
                    background: transparent;
                }}
                QCheckBox::indicator {{
                    width: 52px;
                    height: 28px;
                    border-radius: 14px;
                    background: {PANEL_BORDER};
                    border: none;
                }}
                QCheckBox::indicator:checked {{
                    background: {ACCENT};
                }}
            """)
            row.addWidget(toggle)
            return row, toggle

        row_bypass, self.bypass_toggle = make_toggle(STR_SEC_BYPASS, False)
        row_ota, self.ota_toggle = make_toggle(STR_SEC_OTA, True)
        row_data, self.data_toggle = make_toggle(STR_SEC_DATA, False)

        card_layout.addLayout(row_bypass)
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet(f"background-color: {PANEL_BORDER}; max-height: 1px;")
        card_layout.addWidget(sep1)
        card_layout.addLayout(row_ota)
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"background-color: {PANEL_BORDER}; max-height: 1px;")
        card_layout.addWidget(sep2)
        card_layout.addLayout(row_data)

        layout.addStretch(1)
        layout.addWidget(title)
        layout.addSpacing(8)
        layout.addWidget(subtitle)
        layout.addWidget(card)
        layout.addStretch(2)


class SummaryStep(StepWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.title = GlowText(STR_SUMMARY_TITLE, 42, ACCENT)
        self.title.setMinimumHeight(60)

        subtitle = QLabel(STR_SUMMARY_SUBTITLE)
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 16px; background: transparent; margin-bottom: 20px;")

        card = PanelCard()
        card_layout = QVBoxLayout(card)
        card_layout.setSpacing(16)
        self.summary_rows = {}
        self.summary_values = {}

        for key, label in [
            ("lang", STR_SUMMARY_LANG),
            ("res", STR_SUMMARY_RES),
            ("vsync", STR_SUMMARY_VSYNC),
            ("mode", STR_SUMMARY_MODE),
            ("ota", STR_SUMMARY_OTA),
            ("data", STR_SUMMARY_DATA),
        ]:
            row = QHBoxLayout()
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 14px; background: transparent;")
            val = QLabel("—")
            val.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 14px; font-weight: 600; background: transparent;")
            val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(lbl)
            row.addStretch()
            row.addWidget(val)
            card_layout.addLayout(row)
            self.summary_values[key] = val

            if key != "data":
                sep = QFrame()
                sep.setFrameShape(QFrame.Shape.HLine)
                sep.setStyleSheet(f"background-color: {PANEL_BORDER}; max-height: 1px;")
                card_layout.addWidget(sep)

        layout.addStretch(1)
        layout.addWidget(self.title)
        layout.addSpacing(8)
        layout.addWidget(subtitle)
        layout.addWidget(card)
        layout.addStretch(2)

    def update_summary(self, data):
        self.summary_values["lang"].setText(data.get("language", "—"))
        self.summary_values["res"].setText(data.get("resolution", "—"))
        self.summary_values["vsync"].setText("Enabled" if data.get("vsync", True) else "Disabled")
        mode_map = {"performance": "Performance", "quality": "Quality"}
        self.summary_values["mode"].setText(mode_map.get(data.get("mode", ""), "—"))
        self.summary_values["ota"].setText("Enabled" if data.get("ota", True) else "Disabled")
        self.summary_values["data"].setText("Enabled" if data.get("data", False) else "Disabled")


class OOBEWizard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMinimumSize(1200, 800)
        self.resize(1200, 800)

        self.current_step = 0
        self.total_steps = 6

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.background = GlowBackground(self)
        self.background.setGeometry(0, 0, 1200, 800)
        self.background.lower()

        self.content_area = QWidget()
        self.content_area.setStyleSheet("background: transparent;")
        content_layout = QVBoxLayout(self.content_area)
        content_layout.setContentsMargins(80, 40, 80, 20)

        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: transparent;")

        self.welcome_step = WelcomeStep()
        self.language_step = LanguageStep()
        self.controller_step = ControllerStep()
        self.display_step = DisplayStep()
        self.security_step = SecurityStep()
        self.summary_step = SummaryStep()

        self.stack.addWidget(self.welcome_step)
        self.stack.addWidget(self.language_step)
        self.stack.addWidget(self.controller_step)
        self.stack.addWidget(self.display_step)
        self.stack.addWidget(self.security_step)
        self.stack.addWidget(self.summary_step)

        content_layout.addWidget(self.stack, 1)
        main_layout.addWidget(self.content_area, 1)

        self.bottom_bar = QWidget()
        self.bottom_bar.setStyleSheet("background: transparent;")
        bottom_layout = QHBoxLayout(self.bottom_bar)
        bottom_layout.setContentsMargins(80, 0, 80, 30)

        self.back_btn = CyberButton(STR_BTN_BACK, primary=False)
        self.back_btn.setFixedWidth(140)
        self.back_btn.clicked.connect(self._go_back)
        self.back_btn.setVisible(False)
        bottom_layout.addWidget(self.back_btn)

        self.dots_layout = QHBoxLayout()
        self.dots_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dot_labels = []
        for i in range(self.total_steps):
            dot = QLabel()
            dot.setFixedSize(12, 12)
            dot.setStyleSheet(f"""
                background-color: {' ' + ACCENT + ' ' if i == 0 else ' ' + PANEL_BORDER + ' '};
                border-radius: 6px;
            """)
            self.dots_layout.addWidget(dot)
            self.dot_labels.append(dot)
        bottom_layout.addLayout(self.dots_layout)

        self.next_btn = CyberButton(STR_BTN_NEXT, primary=True)
        self.next_btn.setFixedWidth(200)
        self.next_btn.clicked.connect(self._go_next)
        bottom_layout.addWidget(self.next_btn)

        main_layout.addWidget(self.bottom_bar)

        self.controller_thread = None
        self._start_controller_detection()
        self._start_background_animation()

    def _start_controller_detection(self):
        self.controller_thread = ControllerDetector()
        self.controller_thread.detected.connect(self._on_controller_detected)
        self.controller_thread.lost.connect(self._on_controller_lost)
        self.controller_thread.start()

    def _on_controller_detected(self, path, name):
        self.controller_step.controller_path = path
        self.controller_step.controller_name = name
        self.controller_step.status_label.setText(f"{STR_CTRL_DETECTED}")
        self.controller_step.status_label.setStyleSheet(f"color: {SUCCESS}; font-size: 16px; background: transparent;")
        self.controller_step.device_label.setText(f"{STR_CTRL_DEVICE}: {name} ({path})")

    def _on_controller_lost(self):
        self.controller_step.controller_path = ""
        self.controller_step.controller_name = ""
        self.controller_step.status_label.setText(STR_CTRL_NONE)
        self.controller_step.status_label.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 16px; background: transparent;")
        self.controller_step.device_label.setText("")

    def _start_background_animation(self):
        self._anim_timer = QTimer(self)
        self._anim_phase = 0.0
        self._anim_timer.timeout.connect(self._animate_background)
        self._anim_timer.start(16)

    def _animate_background(self):
        self._anim_phase += 0.05
        w, h = self.width(), self.height()
        cx = w / 2 + 150 * (0.5 + 0.5 * (self._anim_phase % 12.566) / 6.283 - 0.5)
        cy = h / 2 + 100 * (0.5 + 0.5 * ((self._anim_phase * 0.7) % 12.566) / 6.283 - 0.5)
        self.background._glow_x = cx
        self.background._glow_y = cy
        self.background._glow_opacity = 0.12 + 0.05 * (0.5 + 0.5 * (self._anim_phase % 12.566 - 6.283) / 6.283)
        self.background._glow_radius = 250 + 50 * (0.5 + 0.5 * ((self._anim_phase * 0.5) % 12.566 - 6.283) / 6.283)
        self.background.set_pulse(self._anim_phase)
        self.background.update()

        if self.current_step == 0:
            self.welcome_step.title.glowStrength = 0.3 + 0.2 * (0.5 + 0.5 * (self._anim_phase % 6.283 - 3.1416) / 3.1416)

    def _spring_transition(self, from_idx, to_idx):
        """Spring-damper driven transition (2nd-order underdamped).

        Falls back to the cubic-eased transition on any failure so the
        existing behaviour is never lost.
        """
        current_widget = self.stack.widget(from_idx)
        next_widget = self.stack.widget(to_idx)

        direction = 1 if to_idx > from_idx else -1
        w = self.stack.width()
        h = self.stack.height()

        next_widget.setGeometry(direction * w, 0, w, h)
        next_widget.show()
        next_widget.raise_()

        try:
            from ui.motion.motion_engine import spring_interpolate
        except ImportError:
            self._slide_transition(from_idx, to_idx)
            return

        duration = 0.35
        steps = max(10, int(duration * 60))
        try:
            curve = spring_interpolate(
                target=1.0, x0=0.0, duration=duration, steps=steps + 1,
                wn=12.0, zeta=0.7,
            )
            curve = [max(0.0, min(1.0, v)) for v in curve]
        except Exception:
            self._slide_transition(from_idx, to_idx)
            return

        state = {"i": 0}

        def tick():
            i = state["i"]
            if i >= len(curve):
                self.stack.setCurrentIndex(to_idx)
                current_widget.hide()
                current_widget.setGeometry(0, 0, w, h)
                next_widget.setGeometry(0, 0, w, h)
                timer.stop()
                timer.deleteLater()
                return
            f = curve[i]
            current_widget.setGeometry(-direction * int(w * f), 0, w, h)
            next_widget.setGeometry(direction * int(w * (1.0 - f)), 0, w, h)
            state["i"] = i + 1

        timer = QTimer(self)
        timer.timeout.connect(tick)
        timer.start(max(4, int(duration * 1000.0 / steps)))
        self._spring_timer = timer

    def _slide_transition(self, from_idx, to_idx):
        current_widget = self.stack.widget(from_idx)
        next_widget = self.stack.widget(to_idx)

        direction = 1 if to_idx > from_idx else -1
        w = self.stack.width()

        next_widget.setGeometry(direction * w, 0, w, self.stack.height())
        next_widget.show()
        next_widget.raise_()

        anim_group = QParallelAnimationGroup(self)

        slide_out = QPropertyAnimation(current_widget, b"geometry")
        slide_out.setDuration(350)
        slide_out.setEasingCurve(QEasingCurve.Type.InOutCubic)
        slide_out.setStartValue(QRect(0, 0, w, self.stack.height()))
        slide_out.setEndValue(QRect(-direction * w, 0, w, self.stack.height()))

        slide_in = QPropertyAnimation(next_widget, b"geometry")
        slide_in.setDuration(350)
        slide_in.setEasingCurve(QEasingCurve.Type.InOutCubic)
        slide_in.setStartValue(QRect(direction * w, 0, w, self.stack.height()))
        slide_in.setEndValue(QRect(0, 0, w, self.stack.height()))

        anim_group.addAnimation(slide_out)
        anim_group.addAnimation(slide_in)

        def on_finished():
            self.stack.setCurrentIndex(to_idx)
            current_widget.hide()
            current_widget.setGeometry(0, 0, w, self.stack.height())
            next_widget.setGeometry(0, 0, w, self.stack.height())
            anim_group.deleteLater()

        anim_group.finished.connect(on_finished)
        anim_group.start()
        self._current_anim = anim_group

    def _update_dots(self):
        for i, dot in enumerate(self.dot_labels):
            if i == self.current_step:
                dot.setStyleSheet(f"background-color: {ACCENT}; border-radius: 6px;")
            elif i < self.current_step:
                dot.setStyleSheet(f"background-color: {ACCENT_DIM}; border-radius: 6px;")
            else:
                dot.setStyleSheet(f"background-color: {PANEL_BORDER}; border-radius: 6px;")

    def _update_buttons(self):
        self.back_btn.setVisible(self.current_step > 0)
        if self.current_step == self.total_steps - 1:
            self.next_btn.setText(STR_BTN_LAUNCH)
            self.next_btn.setFixedWidth(260)
        else:
            self.next_btn.setText(STR_BTN_NEXT)
            self.next_btn.setFixedWidth(200)

    def _go_next(self):
        if self.current_step >= self.total_steps - 1:
            self._launch_system()
            return

        old_step = self.current_step
        self.current_step += 1

        if self.current_step == self.total_steps - 1:
            self._populate_summary()

        self._spring_transition(old_step, self.current_step)
        self._update_dots()
        self._update_buttons()

    def _go_back(self):
        if self.current_step <= 0:
            return

        old_step = self.current_step
        self.current_step -= 1
        self._spring_transition(old_step, self.current_step)
        self._update_dots()
        self._update_buttons()

    def _populate_summary(self):
        lang_code = self.language_step.selected_lang
        lang_name = next((n for c, n, _ in LANGUAGES if c == lang_code), lang_code)
        resolution = self.display_step.resolution_combo.currentText()
        vsync = self.display_step.vsync_check.isChecked()
        mode = self.display_step.graphics_mode
        ota = self.security_step.ota_toggle.isChecked()
        data = self.security_step.data_toggle.isChecked()

        self.summary_step.update_summary({
            "language": f"{lang_name} ({lang_code})",
            "resolution": resolution,
            "vsync": vsync,
            "mode": mode,
            "ota": ota,
            "data": data,
        })

    def _collect_config(self):
        lang_code = self.language_step.selected_lang
        resolution = self.display_step.resolution_combo.currentText()
        refresh = self.display_step.refresh_combo.currentText().replace(" Hz", "")
        vsync = self.display_step.vsync_check.isChecked()
        mode = self.display_step.graphics_mode
        sensitivity = self.controller_step.sensitivity_slider.value()
        bypass = self.security_step.bypass_toggle.isChecked()
        ota = self.security_step.ota_toggle.isChecked()
        data = self.security_step.data_toggle.isChecked()

        config = {
            "oobe_version": 1,
            "language": lang_code,
            "display": {
                "resolution": resolution,
                "refresh_rate": int(refresh),
                "vsync": vsync,
                "graphics_mode": mode,
            },
            "controller": {
                "sensitivity": sensitivity,
                "device": self.controller_step.controller_path or None,
            },
            "security": {
                "allow_bypass": bypass,
                "ota_updates": ota,
                "data_collection": data,
            },
        }
        return config

    def _launch_system(self):
        config = self._collect_config()

        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            pass

        if not self._write_root(CONFIG_FILE, json.dumps(config, indent=2)):
            print("Warning: Could not write config.", file=sys.stderr)
        if not self._touch_root(MARKER_FILE):
            print("Warning: Could not write completion marker.", file=sys.stderr)

        self._cleanup()
        QApplication.quit()

    def _write_root(self, path: Path, content: str) -> bool:
        try:
            with open(path, "w") as f:
                f.write(content)
            return True
        except (PermissionError, OSError):
            pass
        try:
            import subprocess
            proc = subprocess.run(
                ["sudo", "tee", str(path)],
                input=content, capture_output=True, text=True, timeout=30,
            )
            return proc.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return False

    def _touch_root(self, path: Path) -> bool:
        try:
            path.touch(exist_ok=True)
            return True
        except (PermissionError, OSError):
            pass
        try:
            import subprocess
            proc = subprocess.run(
                ["sudo", "touch", str(path)],
                capture_output=True, text=True, timeout=30,
            )
            return proc.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return False

    def _cleanup(self):
        if self.controller_thread:
            self.controller_thread.stop()
        try:
            LOCK_FD.close()
            LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w, h = self.width(), self.height()
        self.background.setGeometry(0, 0, w, h)
        try:
            from ui.motion.motion_engine import golden_split
            main_w, _ = golden_split(float(w))
            side = max(20, int((float(w) - main_w) / 2))
            self.content_area.layout().setContentsMargins(side, 40, side, 20)
        except Exception:
            pass

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._cleanup()
            QApplication.quit()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._go_next()
        elif event.key() == Qt.Key.Key_Backspace:
            self._go_back()
        super().keyPressEvent(event)

    def closeEvent(self, event):
        self._cleanup()
        super().closeEvent(event)


def acquire_lock():
    global LOCK_FD
    try:
        LOCK_FD = open(LOCK_FILE, "w")
        fcntl.flock(LOCK_FD.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        LOCK_FD.write(str(os.getpid()))
        LOCK_FD.flush()
        return True
    except (IOError, OSError):
        return False


def main():
    if MARKER_FILE.exists():
        print("OOBE already completed.", file=sys.stderr)
        sys.exit(0)

    if not acquire_lock():
        print("Another OOBE instance is running.", file=sys.stderr)
        sys.exit(1)

    app = QApplication(sys.argv)
    app.setApplicationName("Aion OOBE")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG_PRIMARY))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Base, QColor(BG_PRIMARY))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(BG_PANEL))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Button, QColor(BG_PANEL))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#000000"))
    app.setPalette(palette)

    font = QFont("Segoe UI", 12)
    app.setFont(font)

    wizard = OOBEWizard()
    wizard.showFullScreen()

    ret = app.exec()
    sys.exit(ret)


if __name__ == "__main__":
    main()
