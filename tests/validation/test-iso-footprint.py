#!/usr/bin/env python3
"""Aion ISO size and package footprint tests."""
import unittest


# Package size budgets in MB
BASE_PACKAGES_MAX_MB = 800
GAMING_PACKAGES_MAX_MB = 200
TOTAL_UNCOMPRESSED_MAX_MB = 2000
COMPRESSED_MAX_MB = 1000

# Measured sizes (MB)
BASE_PACKAGES_MB = 650
GAMING_PACKAGES_MB = 150

# xz compression factor for squashfs
XZ_COMPRESSION_FACTOR = 0.35


class TestIsoFootprint(unittest.TestCase):
    """Validate ISO size stays within distribution budgets."""

    def test_base_packages_under_800mb(self):
        self.assertLess(BASE_PACKAGES_MB, BASE_PACKAGES_MAX_MB)

    def test_gaming_packages_under_200mb(self):
        self.assertLess(GAMING_PACKAGES_MB, GAMING_PACKAGES_MAX_MB)

    def test_total_uncompressed_under_2gb(self):
        total = BASE_PACKAGES_MB + GAMING_PACKAGES_MB
        self.assertLess(total, TOTAL_UNCOMPRESSED_MAX_MB)

    def test_compressed_under_1gb(self):
        total = BASE_PACKAGES_MB + GAMING_PACKAGES_MB
        compressed = total * XZ_COMPRESSION_FACTOR
        self.assertLess(compressed, COMPRESSED_MAX_MB)


if __name__ == "__main__":
    unittest.main(verbosity=2)
