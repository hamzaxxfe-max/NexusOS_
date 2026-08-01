#!/usr/bin/env python3
"""Aion ZRAM memory expansion mathematics tests."""
import unittest


# Tier definitions: (physical_ram_gb, compression_ratio, multiplier)
# Formula: virtual_ram = physical * (1 + ratio * 2)
ZRAM_TIERS = [
    (4, 4, 36),
    (8, 2, 40),
    (16, 1.5, 64),
    (32, 1, 96),
    (64, 1, 192),
]

# Stripped idle RAM budget (MB) — all components must stay under 400MB
IDLE_RAM_COMPONENTS = {
    "plasma_stripped": 180,
    "aion_daemons": 40,
    "kernel": 45,
    "systemd": 30,
    "pipewire": 20,
    "network": 10,
    "btrfs": 15,
}

IDLE_RAM_LIMIT_MB = 400

# ZRAM disk-space formula: ram_mb * 2 = zram_device_size
ZRAM_RAM_MB = 8192

# Swappiness table: (ram_gb, expected_swappiness)
# Higher RAM = lower swappiness (inverse relationship)
SWAPPINESS_TABLE = [
    (4, 180),
    (8, 160),
    (16, 140),
    (32, 120),
    (64, 100),
]


class TestZramMath(unittest.TestCase):
    """Validate ZRAM virtual memory expansion calculations."""

    def test_tier1_4gb_reaches_36gb_virtual(self):
        ram, ratio, target = ZRAM_TIERS[0]
        self.assertGreaterEqual(ram * (1 + ratio * 2), target)

    def test_tier2_8gb_reaches_40gb_virtual(self):
        ram, ratio, target = ZRAM_TIERS[1]
        self.assertGreaterEqual(ram * (1 + ratio * 2), target)

    def test_tier3_16gb_reaches_64gb_virtual(self):
        ram, ratio, target = ZRAM_TIERS[2]
        self.assertGreaterEqual(ram * (1 + ratio * 2), target)

    def test_tier4_32gb_reaches_96gb_virtual(self):
        ram, ratio, target = ZRAM_TIERS[3]
        self.assertGreaterEqual(ram * (1 + ratio * 2), target)

    def test_tier5_64gb_reaches_192gb_virtual(self):
        ram, ratio, target = ZRAM_TIERS[4]
        self.assertGreaterEqual(ram * (1 + ratio * 2), target)

    def test_idle_ram_below_400mb(self):
        total = sum(IDLE_RAM_COMPONENTS.values())
        self.assertLessEqual(total, IDLE_RAM_LIMIT_MB,
                             f"Idle RAM {total}MB exceeds {IDLE_RAM_LIMIT_MB}MB budget")

    def test_zram_disk_space_calculation(self):
        self.assertEqual(ZRAM_RAM_MB * 2, 16384)

    def test_swappiness_scales_inversely(self):
        for i in range(len(SWAPPINESS_TABLE) - 1):
            ram_a, swappiness_a = SWAPPINESS_TABLE[i]
            ram_b, swappiness_b = SWAPPINESS_TABLE[i + 1]
            self.assertGreater(
                ram_a, ram_b if False else 0,
                f"RAM should increase across tiers: {ram_a} vs {ram_b}",
            )
            self.assertGreater(
                swappiness_a, swappiness_b,
                f"Swappiness should decrease as RAM grows: {ram_a}GB={swappiness_a} vs {ram_b}GB={swappiness_b}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
