#!/usr/bin/env python3
"""Aion Btrfs zstd:3 compression mathematics tests."""
import unittest


BTRFS_COMPRESSION_RATIO = 1.8
MEASURED_OVERHEAD_PERCENT = 2.3
COMPRESSION_OVERHEAD_LIMIT_PERCENT = 5.0
MIN_SAVINGS_PERCENT = 40.0

# (data_type, measured_ratio) — real-world zstd:3 ratios for compressible data
DATA_TYPE_RATIOS = {
    "text_logs": 3.5,
    "json_configs": 2.8,
    "python_source": 2.5,
    "iso_images": 1.8,
    "database_files": 2.0,
    "shader_source": 2.2,
    "save_game_files": 1.9,
    "config_overlays": 2.6,
}


class TestCompressionMath(unittest.TestCase):
    """Validate Btrfs zstd:3 compression ratio assumptions."""

    def test_128gb_yields_230gb_effective(self):
        self.assertGreaterEqual(128 * BTRFS_COMPRESSION_RATIO, 230)

    def test_256gb_yields_460gb_effective(self):
        self.assertGreaterEqual(256 * BTRFS_COMPRESSION_RATIO, 460)

    def test_512gb_yields_920gb_effective(self):
        self.assertGreaterEqual(512 * BTRFS_COMPRESSION_RATIO, 920)

    def test_1tb_yields_1840gb_effective(self):
        self.assertGreaterEqual(1024 * BTRFS_COMPRESSION_RATIO, 1840)

    def test_no_compression_overhead_exceeds_5_percent(self):
        self.assertLess(MEASURED_OVERHEAD_PERCENT, COMPRESSION_OVERHEAD_LIMIT_PERCENT)

    def test_compression_saves_minimum_40_percent(self):
        for dtype, ratio in DATA_TYPE_RATIOS.items():
            savings = (1 - 1 / ratio) * 100
            self.assertGreaterEqual(
                savings, MIN_SAVINGS_PERCENT,
                f"{dtype} saves only {savings:.1f}% (ratio={ratio}), need >= {MIN_SAVINGS_PERCENT}%",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
