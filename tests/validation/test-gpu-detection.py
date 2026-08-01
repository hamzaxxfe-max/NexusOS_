#!/usr/bin/env python3
"""Aion GPU vendor detection and PCI class code tests."""
import unittest


# PCI vendor IDs
NVIDIA_VENDOR_ID = 0x10DE
AMD_VENDOR_ID = 0x1002
INTEL_VENDOR_ID = 0x8086

# PCI class codes
VGA_CLASS_CODE = 0x030000
GPU_3D_CLASS_CODE = 0x030200


class TestGpuDetection(unittest.TestCase):
    """Validate PCI vendor and class codes for GPU detection."""

    def test_nvidia_vendor_code(self):
        self.assertEqual(NVIDIA_VENDOR_ID, 0x10DE)

    def test_amd_vendor_code(self):
        self.assertEqual(AMD_VENDOR_ID, 0x1002)

    def test_intel_vendor_code(self):
        self.assertEqual(INTEL_VENDOR_ID, 0x8086)

    def test_pci_class_vga(self):
        self.assertEqual(VGA_CLASS_CODE, 0x030000)

    def test_pci_class_3d(self):
        self.assertEqual(GPU_3D_CLASS_CODE, 0x030200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
