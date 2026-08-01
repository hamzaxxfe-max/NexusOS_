#!/usr/bin/env python3
"""Aion Dynamic Performance Engine — model-agnostic proportional scaling tests."""
import importlib.util
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ENGINE = ROOT / "performance" / "engine" / "aion-performance-engine.py"


def _load_engine():
    spec = importlib.util.spec_from_file_location("aion_perf_engine", ENGINE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ENGINE_MODULE = _load_engine()
Compute = ENGINE_MODULE.PerformanceRating


class TestModelAgnostic(unittest.TestCase):
    """Any CPU / RAM / GPU maps onto the rating curve."""

    def test_i3_laptop_valid_profile(self):
        r = Compute.compute(threads=4, ram_mb=8192, vram_mb=1024,
                            storage_index=0.75, gpu_count=1)
        self.assertGreaterEqual(r.rating, 0.0)
        self.assertLessEqual(r.rating, 100.0)
        self.assertGreaterEqual(r.fps_target, 30)
        self.assertLessEqual(r.fps_target, 240)
        self.assertIn(r.tier, range(1, 6))

    def test_workstation_valid_profile(self):
        r = Compute.compute(threads=64, ram_mb=262144, vram_mb=24576,
                            storage_index=1.0, gpu_count=1)
        self.assertGreaterEqual(r.rating, 0.0)
        self.assertLessEqual(r.rating, 100.0)
        self.assertGreaterEqual(r.fps_target, 30)
        self.assertLessEqual(r.fps_target, 240)
        self.assertIn(r.tier, range(1, 6))

    def test_gpu_less_powerful_than_workstation(self):
        low = Compute.compute(4, 8192, 1024, 0.35, 1)
        high = Compute.compute(64, 262144, 24576, 1.0, 2)
        self.assertLess(low.rating, high.rating)

    def test_igpu_only_rating(self):
        r = Compute.compute(threads=8, ram_mb=16384, vram_mb=0,
                            storage_index=0.75, gpu_count=0)
        self.assertGreaterEqual(r.rating, 0.0)
        self.assertEqual(r.gpu_count, 0)


class TestProportional(unittest.TestCase):
    """Higher capability strictly implies better-or-equal performance."""

    def test_rating_monotonic_in_ram(self):
        a = Compute.compute(8, 4096, 8192, 0.75, 1)
        b = Compute.compute(8, 65536, 8192, 0.75, 1)
        self.assertLess(a.rating, b.rating)

    def test_rating_monotonic_in_vram(self):
        a = Compute.compute(8, 16384, 2048, 0.75, 1)
        b = Compute.compute(8, 16384, 12288, 0.75, 1)
        self.assertLess(a.rating, b.rating)

    def test_fps_scales_with_rating(self):
        low = Compute.compute(2, 2048, 512, 0.35, 1)
        high = Compute.compute(32, 131072, 24576, 1.0, 1)
        self.assertLess(low.fps_target, high.fps_target)

    def test_game_weight_scales_with_rating(self):
        low = Compute.compute(2, 2048, 512, 0.35, 1)
        high = Compute.compute(32, 131072, 24576, 1.0, 1)
        self.assertLess(low.game_cpu_weight, high.game_cpu_weight)
        self.assertGreater(low.bg_cpu_weight, high.bg_cpu_weight)

    def test_free_ram_is_proportional_not_fixed(self):
        small = Compute.compute(4, 4096, 1024, 0.75, 1)
        self.assertLess(small.target_free_mb, 4096)
        big = Compute.compute(16, 65536, 8192, 1.0, 1)
        self.assertLess(big.target_free_mb, 65536)


class TestNoFixedLimits(unittest.TestCase):
    """No pre-hardcoded hardware thresholds may reject valid hardware."""

    def test_rating_bounds_for_extremes(self):
        for threads in (1, 2, 8, 64, 256):
            for ram in (512, 4096, 65536, 1048576):
                for vram in (0, 512, 8192, 131072):
                    r = Compute.compute(threads, ram, vram, 0.75, 1)
                    self.assertGreaterEqual(r.rating, 0.0)
                    self.assertLessEqual(r.rating, 100.0)

    def test_fps_clamped_to_sane_range(self):
        r = Compute.compute(256, 1048576, 131072, 1.0, 4)
        self.assertLessEqual(r.fps_target, 240)
        self.assertGreaterEqual(r.fps_target, 30)

    def test_zram_ratio_in_range(self):
        for t in (1, 4, 8, 64, 256):
            r = Compute.compute(t, 8192, 4096, 0.75, 1)
            self.assertGreaterEqual(r.zram_ratio, 1.0)
            self.assertLessEqual(r.zram_ratio, 2.5)

    def test_debloat_only_on_low_end(self):
        low = Compute.compute(2, 2048, 0, 0.35, 0)
        high = Compute.compute(32, 131072, 24576, 1.0, 1)
        self.assertTrue(low.debloat)
        self.assertFalse(high.debloat)


class TestClosedForm(unittest.TestCase):
    """Computations are O(1) — must not iterate over hardware resources."""

    def test_compute_is_deterministic(self):
        a = Compute.compute(8, 16384, 8192, 0.75, 1).to_dict()
        b = Compute.compute(8, 16384, 8192, 0.75, 1).to_dict()
        self.assertEqual(a, b)

    def test_compute_is_fast(self):
        start = time.perf_counter()
        for _ in range(1000):
            Compute.compute(8, 16384, 8192, 0.75, 1)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 1.0, "compute must be closed-form (O(1))")

    def test_storage_score_bounded(self):
        for idx in (-1.0, 0.0, 0.5, 1.0, 5.0):
            r = Compute.compute(4, 8192, 4096, idx, 1)
            self.assertGreaterEqual(r.rating, 0.0)
            self.assertLessEqual(r.rating, 100.0)


class TestEngineIntegration(unittest.TestCase):
    """The engine module loads and produces a serializable profile."""

    def test_module_imports(self):
        self.assertIsNotNone(ENGINE_MODULE)

    def test_tier_mapping(self):
        r = Compute.compute(2, 2048, 0, 0.35, 0)
        self.assertLessEqual(r.tier, 5)
        self.assertGreaterEqual(r.tier, 1)

    def test_profile_dict_has_required_keys(self):
        r = Compute.compute(8, 16384, 8192, 0.75, 1)
        d = r.to_dict()
        for key in ("rating", "tier", "fps_target", "resource_allocation",
                    "governor", "zram_ratio", "debloat", "quality"):
            self.assertIn(key, d)

    def test_no_gui_imports(self):
        src = ENGINE.read_text(encoding="utf-8")
        for forbidden in ("PyQt", "import tkinter", "Gtk", "opencv", "cv2"):
            self.assertNotIn(forbidden, src)

    def test_profiles_are_optional(self):
        """detect_from_profiles must not require the JSON files to exist."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            hw = ENGINE_MODULE.detect_from_profiles(Path(tmp))
            for key in ("threads", "ram_mb", "vram_mb", "storage_index",
                        "gpu_count"):
                self.assertIn(key, hw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
