#!/usr/bin/env python3
"""
Aion Resolution and DPI Scaling Engine.
All UI dimensions are derived from this module to ensure pixel-perfect
layout across ALL screen sizes, aspect ratios, and DPI settings.
"""

import os
import sys
from math import gcd

BG_PRIMARY = "#121212"
BG_SECONDARY = "#1A2238"
ACCENT = "#00D2FF"
ACCENT_HOVER = "#33DBFF"
TEXT_PRIMARY = "#FFFFFF"
TEXT_SECONDARY = "#B0B0B0"
DANGER = "#FF4444"
SUCCESS = "#44FF88"

ASPECT_RATIOS = {
    (16, 9): "standard",
    (16, 10): "widescreen",
    (3, 2): "tall",
    (4, 3): "classic",
    (21, 9): "ultrawide",
    (7, 3): "ultrawide",
    (32, 9): "super_ultrawide",
    (32, 10): "super_ultrawide",
    (16, 5): "super_ultrawide",
}

RESOLUTION_TIERS = {
    "low": (1280, 720),
    "medium": (1920, 1080),
    "high": (2560, 1440),
    "ultra": (3840, 2160),
}


class NexusDisplayEngine:
    """Central display resolution and DPI scaling engine."""

    def __init__(self, screen=None):
        try:
            from PyQt6.QtWidgets import QApplication
            if screen is None:
                screen = QApplication.primaryScreen()
            geo = screen.geometry()
            self.raw_width = geo.width()
            self.raw_height = geo.height()
            self.dpi = screen.logicalDotsPerInch()
            self.physical_dpi = screen.physicalDotsPerInch()
            self.device_pixel_ratio = screen.devicePixelRatio()
        except Exception:
            self.raw_width = 1920
            self.raw_height = 1080
            self.dpi = 96.0
            self.physical_dpi = 96.0
            self.device_pixel_ratio = 1.0

        self.scale_factor = self.dpi / 96.0

        if self.raw_width <= 1280:
            self.tier = "low"
        elif self.raw_width <= 1920:
            self.tier = "medium"
        elif self.raw_width <= 2560:
            self.tier = "high"
        else:
            self.tier = "ultra"

        g = gcd(self.raw_width, self.raw_height)
        self.aspect_w = self.raw_width // g
        self.aspect_h = self.raw_height // g
        self.aspect_key = (self.aspect_w, self.aspect_h)
        self.aspect_type = ASPECT_RATIOS.get(self.aspect_key, "standard")

    def px(self, base_px):
        return max(1, int(base_px * self.scale_factor))

    def pt(self, base_pt):
        return max(7, int(base_pt * self.scale_factor))

    def radius(self, base):
        return max(2, int(base * self.scale_factor))

    def icon(self, base):
        return max(16, int(base * self.scale_factor))

    def card_width(self, fraction=0.28):
        return int(self.raw_width * fraction)

    def card_height(self, fraction=0.55):
        return int(self.raw_height * fraction)

    def panel_width(self, fraction=0.35):
        return int(self.raw_width * fraction)

    def panel_height(self, fraction=0.90):
        return int(self.raw_height * fraction)

    def dialog_width(self, fraction=0.40):
        return int(self.raw_width * fraction)

    def dialog_height(self, fraction=0.60):
        return int(self.raw_height * fraction)

    def sidebar_width(self):
        return int(self.raw_width * 0.08)

    def grid_columns(self):
        cols = {"low": 2, "medium": 3, "high": 4, "ultra": 5}
        return cols.get(self.tier, 3)

    def grid_spacing(self):
        return self.px(16)

    def content_max_width(self):
        if self.aspect_type in ("ultrawide", "super_ultrawide"):
            return int(self.raw_width * 0.6)
        return int(self.raw_width * 0.92)

    def content_offset_x(self):
        if self.aspect_type in ("ultrawide", "super_ultrawide"):
            return int((self.raw_width - self.content_max_width()) / 2)
        return self.px(16)

    def margins(self):
        m = self.px(20)
        return (m, m, m, m)

    def card_margins(self):
        m = self.px(16)
        return (m, m, m, m)

    def spacing(self):
        return self.px(12)

    def validate_layout(self, widget_rect, container_rect, name="widget"):
        overflow_right = widget_rect.right() - container_rect.right()
        overflow_bottom = widget_rect.bottom() - container_rect.bottom()
        overflow_left = container_rect.left() - widget_rect.left()
        overflow_top = container_rect.top() - widget_rect.top()

        max_overflow = max(overflow_right, overflow_bottom, overflow_left, overflow_top)

        if max_overflow <= 0:
            return (True, 0, None)

        suggestion = f"Reduce {name} size by {max_overflow}px or increase container"
        return (False, max_overflow, suggestion)

    def compute_safe_grid(self, items_count, item_min_w, item_min_h, spacing=None):
        if spacing is None:
            spacing = self.grid_spacing()

        avail_w = self.content_max_width() - (2 * self.px(20))
        avail_h = int(self.raw_height * 0.75)

        cols = min(items_count, (avail_w + spacing) // (item_min_w + spacing))
        cols = max(1, cols)
        rows = (items_count + cols - 1) // cols

        actual_w = (avail_w - (cols - 1) * spacing) // cols
        actual_h = max(item_min_h, (avail_h - (rows - 1) * spacing) // rows)
        actual_w = max(item_min_w, actual_w)

        return (cols, rows, actual_w, actual_h)

    def get_font(self, family="Noto Sans", size_pt=10, weight="normal"):
        try:
            from PyQt6.QtGui import QFont
            font = QFont(family)
            font.setPointSize(self.pt(size_pt))
            weight_map = {
                "thin": QFont.Weight.Thin,
                "light": QFont.Weight.Light,
                "normal": QFont.Weight.Normal,
                "medium": QFont.Weight.Medium,
                "bold": QFont.Weight.Bold,
                "black": QFont.Weight.Black,
            }
            font.setWeight(weight_map.get(weight, QFont.Weight.Normal))
            return font
        except Exception:
            return None

    def get_stylesheet(self):
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
            QPushButton:pressed {{
                background-color: #0099CC;
            }}
            QLabel {{
                color: {TEXT_PRIMARY};
                background: transparent;
            }}
            QLabel#secondary {{
                color: {TEXT_SECONDARY};
            }}
            QSlider::groove:horizontal {{
                background: {BG_SECONDARY};
                height: {self.px(4)}px;
                border-radius: {self.radius(2)}px;
            }}
            QSlider::handle:horizontal {{
                background: {ACCENT};
                width: {self.px(16)}px;
                height: {self.px(16)}px;
                margin: {self.px(-6)}px 0;
                border-radius: {self.radius(8)}px;
            }}
            QTabBar::tab {{
                background: {BG_SECONDARY};
                color: {TEXT_SECONDARY};
                padding: {self.px(10)}px {self.px(20)}px;
                font-size: {self.pt(10)}pt;
                border: none;
                border-bottom: {self.px(2)}px solid transparent;
            }}
            QTabBar::tab:selected {{
                color: {ACCENT};
                border-bottom: {self.px(2)}px solid {ACCENT};
            }}
            QProgressBar {{
                background: {BG_SECONDARY};
                border: none;
                border-radius: {self.radius(4)}px;
                height: {self.px(8)}px;
                text-align: center;
                color: transparent;
            }}
            QProgressBar::chunk {{
                background: {ACCENT};
                border-radius: {self.radius(4)}px;
            }}
            QScrollArea {{
                border: none;
                background: transparent;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: {self.px(8)}px;
                margin: 0;
            }}
            QScrollBar::handle:vertical {{
                background: #444444;
                border-radius: {self.radius(4)}px;
                min-height: {self.px(20)}px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
        """

    def debug_info(self):
        return (
            f"Resolution: {self.raw_width}x{self.raw_height}\n"
            f"DPI: {self.dpi:.1f} (physical: {self.physical_dpi:.1f})\n"
            f"Scale Factor: {self.scale_factor:.2f}x\n"
            f"Device Pixel Ratio: {self.device_pixel_ratio}\n"
            f"Aspect Ratio: {self.aspect_w}:{self.aspect_h} ({self.aspect_type})\n"
            f"Tier: {self.tier}\n"
            f"Grid Columns: {self.grid_columns()}\n"
            f"Content Max Width: {self.content_max_width()}px\n"
        )


_instance = None


def get_display_engine():
    global _instance
    if _instance is None:
        _instance = NexusDisplayEngine()
    return _instance
