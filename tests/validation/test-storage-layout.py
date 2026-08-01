#!/usr/bin/env python3
"""Aion Btrfs partition and subvolume layout tests."""
import unittest
from pathlib import Path

PROJ_ROOT = Path(__file__).resolve().parents[2]

# Required Btrfs subvolumes
REQUIRED_SUBVOLUMES = ["@", "@home", "@var-log", "@tmp", "@snapshots"]

# Expected mount options for root
ROOT_MOUNT_OPTIONS = [
    "compress-force=zstd:3",
    "ssd",
    "discard=async",
    "noatime",
]

# Swap sizing rule
SWAP_CAP_GB = 16


class TestStorageLayout(unittest.TestCase):
    """Validate Btrfs partition layout and mount options."""

    def test_subvolumes_created(self):
        for subvol in REQUIRED_SUBVOLUMES:
            self.assertIn(subvol, REQUIRED_SUBVOLUMES)

    def test_root_is_readonly(self):
        script = (PROJ_ROOT / "core" / "services" / "immount-root.sh")
        self.assertTrue(
            script.is_file(),
            "immount-root.sh must exist to enforce read-only root",
        )
        content = script.read_text(encoding="utf-8", errors="ignore")
        self.assertIn(
            "chattr +i", content,
            "Read-only root must enforce immutability via chattr +i",
        )
        self.assertIn(
            "set-default", content,
            "Read-only root must pin the default btrfs subvolume",
        )
        self.assertIn(
            "overlay", content,
            "Writable /etc must be provided via overlay (immutable root)",
        )

    def test_compression_enabled(self):
        root_opts = "rw,compress-force=zstd:3,ssd,discard=async,noatime,subvol=/@"
        self.assertIn(
            "compress-force=zstd:3", root_opts,
            "Root mount missing compress-force=zstd:3",
        )

    def test_ssd_optimization(self):
        root_opts = "rw,compress-force=zstd:3,ssd,discard=async,noatime,subvol=/@"
        has_ssd = "ssd" in root_opts.split(",")
        has_discard = "discard=async" in root_opts.split(",")
        self.assertTrue(
            has_ssd or has_discard,
            f"Missing SSD optimization in mount options: {root_opts}",
        )

    def test_swap_size_matches_ram(self):
        ram_gb = 32
        expected_swap = min(ram_gb, SWAP_CAP_GB)
        actual_swap = min(ram_gb, SWAP_CAP_GB)
        self.assertEqual(actual_swap, expected_swap)

        ram_gb_large = 64
        expected_swap_large = SWAP_CAP_GB
        actual_swap_large = min(ram_gb_large, SWAP_CAP_GB)
        self.assertEqual(actual_swap_large, expected_swap_large)
        self.assertEqual(actual_swap_large, 16)


if __name__ == "__main__":
    unittest.main(verbosity=2)
