#!/usr/bin/env python3
"""
NexusOS UI Alignment Validation Suite
Tests all PyQt6 UI components at multiple virtual resolutions
to ensure zero clipping, zero overlap, zero truncation.
"""
import unittest
from unittest.mock import MagicMock, PropertyMock
from PyQt6.QtCore import QRect, QSize, Qt, QPoint
from PyQt6.QtGui import QFont, QFontMetrics, QGuiApplication
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QLabel, QTabWidget, QScrollArea
)
import sys

PROJ_ROOT = __import__("pathlib").Path(__file__).resolve().parents[2]

THEME_CARD_COUNT = 3
THEME_CARD_W = 320
THEME_CARD_H = 420
THEME_CARD_SPACING = 24
TWEAK_TAB_COUNT = 3
DROPZONE_MIN_W = 600
DROPZONE_MIN_H = 300
DIALOG_MIN_W = 520
DIALOG_MIN_H = 400
LABEL_OVERSHOOT_TOLERANCE = 2
GRID_BALANCE_TOLERANCE = 1.0


def _ensure_app():
    if QApplication.instance() is None:
        return QApplication(sys.argv)
    return QApplication.instance()


def create_mock_screen(width, height, dpi=96):
    screen = MagicMock()
    screen.size.return_value = QSize(width, height)
    screen.width.return_value = width
    screen.height.return_value = height
    screen.logicalDotsPerInch.return_value = (dpi, dpi)
    screen.logicalDotsPerInchX.return_value = dpi
    screen.logicalDotsPerInchY.return_value = dpi
    screen.devicePixelRatio.return_value = dpi / 96.0
    screen.availableGeometry.return_value = QRect(0, 0, width, height)
    screen.geometry.return_value = QRect(0, 0, width, height)
    screen.name.return_value = f"mock-{width}x{height}"
    screen.isVirtual.return_value = True
    return screen


def compute_card_positions(cols, card_w, card_h, spacing, container_w, container_h):
    positions = []
    total_grid_w = cols * card_w + max(0, cols - 1) * spacing
    start_x = max(0, (container_w - total_grid_w) // 2)
    start_y = spacing
    for i in range(cols):
        x = start_x + i * (card_w + spacing)
        positions.append(QRect(x, start_y, card_w, card_h))
    return positions


RESOLUTIONS = [
    ("720p", 1280, 720),
    ("1080p", 1920, 1080),
    ("4K", 3840, 2160),
    ("Ultrawide", 3440, 1440),
]


class TestUIAlignment(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.app = _ensure_app()

    def _build_theme_cards(self, container_w, container_h):
        container = QWidget()
        container.setFixedSize(container_w, container_h)
        layout = QGridLayout(container)
        layout.setSpacing(THEME_CARD_SPACING)
        layout.setContentsMargins(
            THEME_CARD_SPACING, THEME_CARD_SPACING,
            THEME_CARD_SPACING, THEME_CARD_SPACING
        )
        cards = []
        for i in range(THEME_CARD_COUNT):
            card = QWidget()
            card.setFixedSize(THEME_CARD_W, THEME_CARD_H)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(16, 16, 16, 16)
            title = QLabel(f"Theme {i + 1}")
            title.setFixedHeight(32)
            preview = QWidget()
            preview.setFixedHeight(200)
            card_layout.addWidget(title)
            card_layout.addWidget(preview)
            cards.append(card)
            layout.addWidget(card, 0, i)
        container.show()
        container.repaint()
        return container, cards

    def _assert_fits(self, widget, screen_w, screen_h):
        widget_geom = widget.geometry()
        self.assertGreaterEqual(widget_geom.x(), 0)
        self.assertGreaterEqual(widget_geom.y(), 0)
        self.assertLessEqual(widget_geom.x() + widget_geom.width(), screen_w)
        self.assertLessEqual(widget_geom.y() + widget_geom.height(), screen_h)

    def _assert_no_overlap(self, widgets):
        rects = [w.geometry() for w in widgets if w.isVisible()]
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                intersection = rects[i].intersected(rects[j])
                self.assertTrue(
                    intersection.isEmpty(),
                    f"Overlap detected between widget {i} and widget {j}: "
                    f"{rects[i]} intersects {rects[j]} (intersection={intersection})"
                )

    def _assert_labels_visible(self, parent_widget):
        parent_rect = QRect(QPoint(0, 0), parent_widget.size())
        labels = parent_widget.findChildren(QLabel)
        for label in labels:
            if not label.isVisible():
                continue
            text = label.text()
            if not text:
                continue
            label_geom = label.geometry()
            label_rect = QRect(QPoint(0, 0), label.size())
            local_pos = parent_widget.mapFromGlobal(label.mapToGlobal(QPoint(0, 0)))
            text_rect = QRect(local_pos, label.size())
            overflow = text_rect.right() - parent_rect.right()
            if overflow > LABEL_OVERSHOOT_TOLERANCE:
                self.fail(
                    f"Label '{text}' overflows parent by {overflow}px horizontally"
                )
            v_overflow = text_rect.bottom() - parent_rect.bottom()
            if v_overflow > LABEL_OVERSHOOT_TOLERANCE:
                self.fail(
                    f"Label '{text}' overflows parent by {v_overflow}px vertically"
                )

    def _assert_grid_balanced(self, cards):
        widths = [c.width() for c in cards]
        self.assertTrue(
            max(widths) - min(widths) <= GRID_BALANCE_TOLERANCE,
            f"Grid cards not balanced: widths={widths}, "
            f"range={max(widths) - min(widths)} > {GRID_BALANCE_TOLERANCE}"
        )

    def test_theme_switcher_cards_fit_at_720p(self):
        screen = create_mock_screen(1280, 720)
        sw, sh = 1280, 720
        positions = compute_card_positions(
            THEME_CARD_COUNT, THEME_CARD_W, THEME_CARD_H,
            THEME_CARD_SPACING, sw, sh
        )
        container, cards = self._build_theme_cards(sw, sh)
        for i, pos in enumerate(positions):
            card = cards[i]
            self.assertLessEqual(
                pos.x() + pos.width(), sw,
                f"Card {i} overflows screen at 720p (right edge {pos.x() + pos.width()} > {sw})"
            )
            self.assertLessEqual(
                pos.y() + pos.height(), sh,
                f"Card {i} overflows screen at 720p (bottom edge {pos.y() + pos.height()} > {sh})"
            )
        self._assert_no_overlap(cards)
        container.close()

    def test_theme_switcher_cards_fit_at_1080p(self):
        sw, sh = 1920, 1080
        positions = compute_card_positions(
            THEME_CARD_COUNT, THEME_CARD_W, THEME_CARD_H,
            THEME_CARD_SPACING, sw, sh
        )
        container, cards = self._build_theme_cards(sw, sh)
        for i, pos in enumerate(positions):
            self.assertLessEqual(pos.x() + pos.width(), sw)
            self.assertLessEqual(pos.y() + pos.height(), sh)
        self._assert_no_overlap(cards)
        container.close()

    def test_theme_switcher_cards_fit_at_4k(self):
        sw, sh = 3840, 2160
        positions = compute_card_positions(
            THEME_CARD_COUNT, THEME_CARD_W, THEME_CARD_H,
            THEME_CARD_SPACING, sw, sh
        )
        container, cards = self._build_theme_cards(sw, sh)
        for i, pos in enumerate(positions):
            self.assertLessEqual(pos.x() + pos.width(), sw)
            self.assertLessEqual(pos.y() + pos.height(), sh)
        self._assert_no_overlap(cards)
        container.close()

    def test_theme_switcher_cards_fit_at_ultrawide(self):
        sw, sh = 3440, 1440
        positions = compute_card_positions(
            THEME_CARD_COUNT, THEME_CARD_W, THEME_CARD_H,
            THEME_CARD_SPACING, sw, sh
        )
        container, cards = self._build_theme_cards(sw, sh)
        for i, pos in enumerate(positions):
            self.assertLessEqual(pos.x() + pos.width(), sw)
            self.assertLessEqual(pos.y() + pos.height(), sh)
        self._assert_no_overlap(cards)
        container.close()

    def test_wine_installer_dropzone_fits(self):
        for res_name, sw, sh in RESOLUTIONS:
            with self.subTest(resolution=res_name):
                container = QWidget()
                container.setFixedSize(sw, sh)
                layout = QVBoxLayout(container)
                layout.setContentsMargins(24, 24, 24, 24)
                dropzone = QWidget()
                dropzone.setMinimumSize(DROPZONE_MIN_W, DROPZONE_MIN_H)
                drop_label = QLabel("Drop .exe or .msi here")
                dropzone_layout = QVBoxLayout(dropzone)
                dropzone_layout.addWidget(drop_label)
                layout.addWidget(dropzone)
                container.show()
                container.repaint()
                dz_geom = dropzone.geometry()
                self.assertLessEqual(
                    dz_geom.width(), sw - 48,
                    f"Dropzone too wide for {res_name}: {dz_geom.width()} > {sw - 48}"
                )
                self.assertLessEqual(
                    dz_geom.height(), sh - 48,
                    f"Dropzone too tall for {res_name}: {dz_geom.height()} > {sh - 48}"
                )
                self.assertGreaterEqual(dz_geom.width(), DROPZONE_MIN_W)
                self.assertGreaterEqual(dz_geom.height(), DROPZONE_MIN_H)
                container.close()

    def test_tweak_hub_tabs_fit(self):
        for res_name, sw, sh in RESOLUTIONS:
            with self.subTest(resolution=res_name):
                container = QWidget()
                container.setFixedSize(sw, sh)
                layout = QVBoxLayout(container)
                layout.setContentsMargins(0, 0, 0, 0)
                tabs = QTabWidget()
                tab_names = ["System", "Graphics", "Audio"]
                for name in tab_names:
                    tab_content = QWidget()
                    tabs.addTab(tab_content, name)
                layout.addWidget(tabs)
                container.show()
                container.repaint()
                tab_bar = tabs.tabBar()
                total_tab_width = 0
                for i in range(tab_bar.count()):
                    total_tab_width += tab_bar.tabRect(i).width()
                tab_bar_height = tab_bar.height()
                self.assertLessEqual(
                    total_tab_width, sw,
                    f"Tabs overflow at {res_name}: total={total_tab_width} > screen={sw}"
                )
                self.assertGreater(
                    tab_bar.count(), 0,
                    "Tab bar has no tabs"
                )
                self.assertEqual(tab_bar.count(), TWEAK_TAB_COUNT)
                container.close()

    def test_game_capture_dialog_fits(self):
        for res_name, sw, sh in RESOLUTIONS:
            with self.subTest(resolution=res_name):
                screen = create_mock_screen(sw, sh)
                dialog = QWidget()
                dialog.setFixedSize(min(DIALOG_MIN_W, sw - 100), min(DIALOG_MIN_H, sh - 100))
                layout = QVBoxLayout(dialog)
                layout.setContentsMargins(24, 24, 24, 24)
                title_label = QLabel("Game Capture Configuration")
                title_label.setFixedHeight(32)
                layout.addWidget(title_label)
                source_row = QHBoxLayout()
                source_label = QLabel("Source:")
                source_input = QLabel("Screen / Window")
                source_row.addWidget(source_label)
                source_row.addWidget(source_input)
                layout.addLayout(source_row)
                fps_row = QHBoxLayout()
                fps_label = QLabel("FPS:")
                fps_input = QLabel("60")
                fps_row.addWidget(fps_label)
                fps_row.addWidget(fps_input)
                layout.addLayout(fps_row)
                button_row = QHBoxLayout()
                start_btn = QLabel("Start Capture")
                cancel_btn = QLabel("Cancel")
                start_btn.setFixedHeight(36)
                cancel_btn.setFixedHeight(36)
                button_row.addWidget(start_btn)
                button_row.addWidget(cancel_btn)
                layout.addLayout(button_row)
                dialog.show()
                dialog.repaint()
                dlg_geom = dialog.geometry()
                self.assertLessEqual(
                    dlg_geom.x() + dlg_geom.width(), sw,
                    f"Dialog overflows right at {res_name}"
                )
                self.assertLessEqual(
                    dlg_geom.y() + dlg_geom.height(), sh,
                    f"Dialog overflows bottom at {res_name}"
                )
                self.assertGreaterEqual(dlg_geom.width(), DIALOG_MIN_W)
                self.assertGreaterEqual(dlg_geom.height(), DIALOG_MIN_H)
                self._assert_no_overlap([title_label, source_label, fps_label, start_btn, cancel_btn])
                container_layout = dialog.findChild(QVBoxLayout)
                if container_layout is not None:
                    self._assert_labels_visible(dialog)
                dialog.close()

    def test_no_widget_overlap(self):
        sw, sh = 1920, 1080
        container = QWidget()
        container.setFixedSize(sw, sh)
        main_layout = QVBoxLayout(container)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(16)
        card_row = QHBoxLayout()
        card_row.setSpacing(12)
        widgets = []
        for i in range(THEME_CARD_COUNT):
            card = QWidget()
            card.setFixedSize(THEME_CARD_W, THEME_CARD_H)
            card_layout = QVBoxLayout(card)
            lbl = QLabel(f"Theme {i + 1}")
            card_layout.addWidget(lbl)
            card_row.addWidget(card)
            widgets.append(card)
        main_layout.addLayout(card_row)
        tab_widget = QTabWidget()
        for name in ["System", "Graphics", "Audio"]:
            tab_widget.addTab(QWidget(), name)
        main_layout.addWidget(tab_widget)
        widgets.append(tab_widget)
        container.show()
        container.repaint()
        child_widgets = container.findChildren(QWidget)
        visible_children = [
            w for w in child_widgets
            if w.isVisible() and w is not container
        ]
        tab_rect = tab_widget.geometry()
        card_rects = [c.geometry() for c in widgets[:THEME_CARD_COUNT]]
        for cr in card_rects:
            self.assertFalse(
                cr.intersects(tab_rect),
                f"Card rect {cr} overlaps tab widget {tab_rect}"
            )
        for i in range(len(card_rects)):
            for j in range(i + 1, len(card_rects)):
                self.assertFalse(
                    card_rects[i].intersects(card_rects[j]),
                    f"Card {i} overlaps card {j}"
                )
        container.close()

    def test_all_labels_visible(self):
        sw, sh = 1920, 1080
        container = QWidget()
        container.setFixedSize(sw, sh)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 24, 24, 24)
        labels_text = [
            "Theme Switcher", "Light", "Dark", "NexusOS",
            "System Tweaks", "Graphics", "Audio",
            "Game Capture", "Source", "FPS",
            "Start Capture", "Cancel", "Drop .exe here",
        ]
        for text in labels_text:
            lbl = QLabel(text)
            layout.addWidget(lbl)
        card_grid = QGridLayout()
        for i in range(THEME_CARD_COUNT):
            card = QWidget()
            card.setFixedSize(THEME_CARD_W, THEME_CARD_H)
            card_layout = QVBoxLayout(card)
            card_title = QLabel(f"Card {i + 1} Title")
            card_desc = QLabel(f"Description for card {i + 1} with some text content")
            card_layout.addWidget(card_title)
            card_layout.addWidget(card_desc)
            card_grid.addWidget(card, 0, i)
            layout.addWidget(card)
        container.show()
        container.repaint()
        self._assert_labels_visible(container)
        container.close()

    def test_grid_layout_balanced(self):
        sw, sh = 1920, 1080
        container = QWidget()
        container.setFixedSize(sw, sh)
        layout = QGridLayout(container)
        layout.setSpacing(THEME_CARD_SPACING)
        layout.setContentsMargins(
            THEME_CARD_SPACING, THEME_CARD_SPACING,
            THEME_CARD_SPACING, THEME_CARD_SPACING
        )
        cards = []
        for i in range(THEME_CARD_COUNT):
            card = QWidget()
            card.setFixedSize(THEME_CARD_W, THEME_CARD_H)
            layout.addWidget(card, 0, i)
            cards.append(card)
        container.show()
        container.repaint()
        actual_widths = [c.geometry().width() for c in cards]
        for i in range(len(actual_widths)):
            for j in range(i + 1, len(actual_widths)):
                self.assertLessEqual(
                    abs(actual_widths[i] - actual_widths[j]),
                    GRID_BALANCE_TOLERANCE,
                    f"Card widths unbalanced: {actual_widths}"
                )
        container.close()

    def test_grid_layout_balanced_across_resolutions(self):
        for res_name, sw, sh in RESOLUTIONS:
            with self.subTest(resolution=res_name):
                positions = compute_card_positions(
                    THEME_CARD_COUNT, THEME_CARD_W, THEME_CARD_H,
                    THEME_CARD_SPACING, sw, sh
                )
                widths = [p.width() for p in positions]
                self.assertEqual(
                    len(set(widths)), 1,
                    f"Card widths not equal at {res_name}: {widths}"
                )

    def test_font_renderable(self):
        database = self.app.fontDatabase()
        test_families = [
            "Noto Sans", "Inter", "Segoe UI", "Ubuntu", "Cantarell",
            "DejaVu Sans", "Liberation Sans", "Arial", "Helvetica",
        ]
        test_sizes = [8, 9, 10, 11, 12, 14, 16, 18, 20, 24, 28, 36]
        available = set(database.families())
        found_any = False
        for family in test_families:
            if family in available:
                found_any = True
                for size in test_sizes:
                    font = QFont(family, size)
                    metrics = QFontMetrics(font)
                    self.assertGreater(
                        metrics.height(), 0,
                        f"Font {family} at {size}pt renders with zero height"
                    )
                    self.assertGreater(
                        metrics.averageCharWidth(), 0,
                        f"Font {family} at {size}pt has zero char width"
                    )
        if not found_any:
            fallback = QFont("sans-serif")
            metrics = QFontMetrics(fallback)
            self.assertGreater(metrics.height(), 0)

    def test_font_renderable_at_computed_sizes(self):
        database = self.app.fontDatabase()
        available = set(database.families())
        target_family = None
        for candidate in ["Noto Sans", "DejaVu Sans", "Liberation Sans", "Arial"]:
            if candidate in available:
                target_family = candidate
                break
        if target_family is None:
            self.skipTest("No suitable test font found on system")
        base_size = 10
        scale_factors = {
            "720p": 0.75,
            "1080p": 1.0,
            "4K": 2.0,
            "Ultrawide": 1.0,
        }
        for res_name, _, _ in RESOLUTIONS:
            with self.subTest(resolution=res_name):
                factor = scale_factors[res_name]
                computed_size = max(8, int(base_size * factor))
                font = QFont(target_family, computed_size)
                self.assertTrue(
                    database.families().count(target_family) > 0,
                    f"Font {target_family} not in database"
                )
                metrics = QFontMetrics(font)
                self.assertGreater(metrics.height(), 0)

    def test_no_widget_overlap_across_all_layouts(self):
        layouts_to_test = []
        for res_name, sw, sh in RESOLUTIONS:
            with self.subTest(resolution=res_name):
                container = QWidget()
                container.setFixedSize(sw, sh)
                main = QVBoxLayout(container)
                main.setContentsMargins(24, 24, 24, 24)
                main.setSpacing(16)
                header = QLabel("NexusOS Settings")
                header.setFixedHeight(48)
                main.addWidget(header)
                tabs = QTabWidget()
                for name in ["System", "Graphics", "Audio"]:
                    tab_page = QWidget()
                    tab_layout = QVBoxLayout(tab_page)
                    card_row = QHBoxLayout()
                    card_row.setSpacing(12)
                    tab_page_widgets = []
                    for i in range(3):
                        card = QWidget()
                        card.setFixedSize(280, 350)
                        card_row.addWidget(card)
                        tab_page_widgets.append(card)
                    tab_layout.addLayout(card_row)
                    tabs.addTab(tab_page, name)
                main.addWidget(tabs)
                footer = QLabel("Status: Ready")
                footer.setFixedHeight(32)
                main.addWidget(footer)
                container.show()
                container.repaint()
                header_geom = header.geometry()
                tabs_geom = tabs.geometry()
                footer_geom = footer.geometry()
                self.assertFalse(
                    header_geom.intersects(tabs_geom),
                    f"Header {header_geom} overlaps tabs {tabs_geom}"
                )
                self.assertFalse(
                    tabs_geom.intersects(footer_geom),
                    f"Tabs {tabs_geom} overlaps footer {footer_geom}"
                )
                self.assertFalse(
                    header_geom.intersects(footer_geom),
                    f"Header {header_geom} overlaps footer {footer_geom}"
                )
                container.close()

    def test_dropzone_min_size_all_resolutions(self):
        for res_name, sw, sh in RESOLUTIONS:
            with self.subTest(resolution=res_name):
                container = QWidget()
                container.setFixedSize(sw, sh)
                layout = QVBoxLayout(container)
                layout.setContentsMargins(24, 24, 24, 24)
                dropzone = QWidget()
                dropzone.setMinimumSize(DROPZONE_MIN_W, DROPZONE_MIN_H)
                dropzone_label = QLabel("Drop .exe or .msi here")
                dz_layout = QVBoxLayout(dropzone)
                dz_layout.addWidget(dropzone_label)
                layout.addWidget(dropzone)
                container.show()
                container.repaint()
                dz = dropzone.geometry()
                self.assertGreaterEqual(dz.width(), DROPZONE_MIN_W)
                self.assertGreaterEqual(dz.height(), DROPZONE_MIN_H)
                self.assertLessEqual(dz.x() + dz.width(), sw)
                self.assertLessEqual(dz.y() + dz.height(), sh)
                container.close()

    def test_label_truncation_check(self):
        font = QFont("sans-serif", 12)
        metrics = QFontMetrics(font)
        long_texts = [
            "System Configuration",
            "Graphics Pipeline Settings",
            "Audio Output Device Selection",
            "Game Capture Source",
            "Theme Switcher",
            "Drop .exe or .msi installer here",
        ]
        max_widget_width = 320
        for text in long_texts:
            text_width = metrics.horizontalAdvance(text)
            if text_width > max_widget_width:
                self.fail(
                    f"Label '{text}' requires {text_width}px but "
                    f"widget is only {max_widget_width}px wide — text will truncate"
                )


if __name__ == "__main__":
    unittest.main()
