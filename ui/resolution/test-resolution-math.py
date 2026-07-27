#!/usr/bin/env python3
import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from resolution_math import (
    NexusDisplayEngine, get_display_engine,
    BG_PRIMARY, BG_SECONDARY, ACCENT, ACCENT_HOVER,
    TEXT_PRIMARY, TEXT_SECONDARY, DANGER, SUCCESS,
    ASPECT_RATIOS, RESOLUTION_TIERS,
)


class MockScreen:
    def __init__(self, width, height, dpi=96.0):
        self._geometry = type('G', (), {'width': lambda: width, 'height': lambda: height})()
        self._width = width
        self._height = height
        self._dpi = dpi

    def geometry(self):
        return self

    def width(self):
        return self._width

    def height(self):
        return self._height

    def logicalDotsPerInch(self):
        return self._dpi

    def physicalDotsPerInch(self):
        return self._dpi

    def devicePixelRatio(self):
        return 1.0


class TestScaleFactorCalculation(unittest.TestCase):
    def test_96_dpi_is_1x(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.dpi = 96.0
        engine.scale_factor = 96.0 / 96.0
        self.assertAlmostEqual(engine.scale_factor, 1.0)

    def test_192_dpi_is_2x(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.dpi = 192.0
        engine.scale_factor = 192.0 / 96.0
        self.assertAlmostEqual(engine.scale_factor, 2.0)

    def test_144_dpi_is_1_5x(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.dpi = 144.0
        engine.scale_factor = 144.0 / 96.0
        self.assertAlmostEqual(engine.scale_factor, 1.5)


class TestPointSizeMinimum(unittest.TestCase):
    def setUp(self):
        self.engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        self.engine.scale_factor = 1.0

    def test_pt_never_below_7(self):
        self.engine.scale_factor = 0.5
        self.assertEqual(self.engine.pt(8), 7)

    def test_pt_normal_size(self):
        self.engine.scale_factor = 1.0
        self.assertEqual(self.engine.pt(10), 10)

    def test_pt_large_scale(self):
        self.engine.scale_factor = 2.0
        self.assertEqual(self.engine.pt(10), 20)


class TestPixelSizeMinimum(unittest.TestCase):
    def setUp(self):
        self.engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        self.engine.scale_factor = 1.0

    def test_px_never_below_1(self):
        self.engine.scale_factor = 0.1
        self.assertEqual(self.engine.px(1), 1)

    def test_px_normal(self):
        self.engine.scale_factor = 1.0
        self.assertEqual(self.engine.px(20), 20)

    def test_px_large_scale(self):
        self.engine.scale_factor = 2.0
        self.assertEqual(self.engine.px(20), 40)


class TestCardDimensionsFractional(unittest.TestCase):
    def test_card_width_fraction(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.raw_width = 1920
        self.assertAlmostEqual(engine.card_width() / engine.raw_width, 0.28, places=2)

    def test_card_height_fraction(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.raw_height = 1080
        self.assertAlmostEqual(engine.card_height() / engine.raw_height, 0.55, places=2)

    def test_card_width_720p(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.raw_width = 1280
        self.assertEqual(engine.card_width(), int(1280 * 0.28))

    def test_card_width_4k(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.raw_width = 3840
        self.assertEqual(engine.card_width(), int(3840 * 0.28))


class TestGridColumnsPerTier(unittest.TestCase):
    def _make_engine(self, width):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.raw_width = width
        engine.raw_height = int(width * 9 / 16)
        if width <= 1280:
            engine.tier = "low"
        elif width <= 1920:
            engine.tier = "medium"
        elif width <= 2560:
            engine.tier = "high"
        else:
            engine.tier = "ultra"
        return engine

    def test_low_tier_2_columns(self):
        self.assertEqual(self._make_engine(1280).grid_columns(), 2)

    def test_medium_tier_3_columns(self):
        self.assertEqual(self._make_engine(1920).grid_columns(), 3)

    def test_high_tier_4_columns(self):
        self.assertEqual(self._make_engine(2560).grid_columns(), 4)

    def test_ultra_tier_5_columns(self):
        self.assertEqual(self._make_engine(3840).grid_columns(), 5)


class TestValidateLayout(unittest.TestCase):
    def test_widget_inside_container_is_valid(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)

        class Rect:
            def __init__(self, l, t, r, b):
                self._l, self._t, self._r, self._b = l, t, r, b
            def right(self): return self._r
            def bottom(self): return self._b
            def left(self): return self._l
            def top(self): return self._t

        widget = Rect(10, 10, 100, 100)
        container = Rect(0, 0, 200, 200)
        valid, overflow, suggestion = engine.validate_layout(widget, container)
        self.assertTrue(valid)
        self.assertEqual(overflow, 0)

    def test_widget_outside_container_is_invalid(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)

        class Rect:
            def __init__(self, l, t, r, b):
                self._l, self._t, self._r, self._b = l, t, r, b
            def right(self): return self._r
            def bottom(self): return self._b
            def left(self): return self._l
            def top(self): return self._t

        widget = Rect(0, 0, 300, 300)
        container = Rect(0, 0, 200, 200)
        valid, overflow, suggestion = engine.validate_layout(widget, container)
        self.assertFalse(valid)
        self.assertGreater(overflow, 0)
        self.assertIsNotNone(suggestion)


class TestComputeSafeGrid(unittest.TestCase):
    def test_all_items_fit(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.raw_width = 1920
        engine.raw_height = 1080
        engine.scale_factor = 1.0
        engine.aspect_type = "standard"
        cols, rows, w, h = engine.compute_safe_grid(9, 100, 100)
        self.assertGreaterEqual(cols, 1)
        self.assertGreaterEqual(rows, 1)
        self.assertGreaterEqual(cols * rows, 9)

    def test_single_item(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.raw_width = 1920
        engine.raw_height = 1080
        engine.scale_factor = 1.0
        engine.aspect_type = "standard"
        cols, rows, w, h = engine.compute_safe_grid(1, 100, 100)
        self.assertEqual(cols, 1)
        self.assertEqual(rows, 1)


class TestUltrawideCentering(unittest.TestCase):
    def test_ultrawide_has_offset(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.raw_width = 3440
        engine.raw_height = 1440
        engine.aspect_type = "ultrawide"
        self.assertGreater(engine.content_offset_x(), 0)

    def test_standard_has_no_offset(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.raw_width = 1920
        engine.raw_height = 1080
        engine.aspect_type = "standard"
        engine.scale_factor = 1.0
        self.assertEqual(engine.content_offset_x(), engine.px(16))


class TestAspectRatioDetection(unittest.TestCase):
    def test_16_9_detected(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.raw_width = 1920
        engine.raw_height = 1080
        from math import gcd
        g = gcd(1920, 1080)
        engine.aspect_w = 1920 // g
        engine.aspect_h = 1080 // g
        engine.aspect_key = (engine.aspect_w, engine.aspect_h)
        engine.aspect_type = ASPECT_RATIOS.get(engine.aspect_key, "standard")
        self.assertEqual(engine.aspect_type, "standard")

    def test_21_9_detected(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.raw_width = 2520
        engine.raw_height = 1080
        from math import gcd
        g = gcd(2520, 1080)
        engine.aspect_w = 2520 // g
        engine.aspect_h = 1080 // g
        engine.aspect_key = (engine.aspect_w, engine.aspect_h)
        engine.aspect_type = ASPECT_RATIOS.get(engine.aspect_key, "standard")
        self.assertEqual(engine.aspect_type, "ultrawide")


class TestColorConstants(unittest.TestCase):
    import re

    def _is_hex_color(self, val):
        return bool(self.re.match(r'^#[0-9A-Fa-f]{6}$', val))

    def test_bg_primary_valid(self):
        self.assertTrue(self._is_hex_color(BG_PRIMARY))

    def test_bg_secondary_valid(self):
        self.assertTrue(self._is_hex_color(BG_SECONDARY))

    def test_accent_valid(self):
        self.assertTrue(self._is_hex_color(ACCENT))

    def test_accent_hover_valid(self):
        self.assertTrue(self._is_hex_color(ACCENT_HOVER))

    def test_text_primary_valid(self):
        self.assertTrue(self._is_hex_color(TEXT_PRIMARY))

    def test_text_secondary_valid(self):
        self.assertTrue(self._is_hex_color(TEXT_SECONDARY))

    def test_danger_valid(self):
        self.assertTrue(self._is_hex_color(DANGER))

    def test_success_valid(self):
        self.assertTrue(self._is_hex_color(SUCCESS))


class TestMarginsAndSpacing(unittest.TestCase):
    def test_margins_return_tuple(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.scale_factor = 1.0
        m = engine.margins()
        self.assertEqual(len(m), 4)

    def test_spacing_positive(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.scale_factor = 1.0
        self.assertGreater(engine.spacing(), 0)

    def test_card_margins_return_tuple(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.scale_factor = 1.0
        m = engine.card_margins()
        self.assertEqual(len(m), 4)


class TestDebugInfo(unittest.TestCase):
    def test_debug_info_contains_key_fields(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.raw_width = 1920
        engine.raw_height = 1080
        engine.dpi = 96.0
        engine.physical_dpi = 96.0
        engine.device_pixel_ratio = 1.0
        engine.scale_factor = 1.0
        engine.tier = "medium"
        engine.aspect_w = 16
        engine.aspect_h = 9
        engine.aspect_type = "standard"

        info = engine.debug_info()
        self.assertIn("Resolution", info)
        self.assertIn("DPI", info)
        self.assertIn("Scale Factor", info)
        self.assertIn("Tier", info)
        self.assertIn("Grid Columns", info)


class TestStylesheet(unittest.TestCase):
    def test_stylesheet_contains_selectors(self):
        engine = NexusDisplayEngine.__new__(NexusDisplayEngine)
        engine.scale_factor = 1.0
        engine.raw_width = 1920
        engine.raw_height = 1080

        ss = engine.get_stylesheet()
        self.assertIn("QWidget", ss)
        self.assertIn("QPushButton", ss)
        self.assertIn("QLabel", ss)
        self.assertIn("QTabBar", ss)
        self.assertIn("QProgressBar", ss)
        self.assertIn("QSlider", ss)
        self.assertIn("QScrollBar", ss)
        self.assertIn(BG_PRIMARY, ss)
        self.assertIn(ACCENT, ss)


if __name__ == "__main__":
    unittest.main(verbosity=2)
