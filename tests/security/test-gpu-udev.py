#!/usr/bin/env python3
"""Aion GPU udev rules security tests."""
import re
import unittest
from pathlib import Path


PROJ_ROOT = Path(__file__).resolve().parents[2]


def _find_files(pattern):
    return list(PROJ_ROOT.rglob(pattern))


def _read_file(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, OSError):
        return ""


class TestGpuUdev(unittest.TestCase):
    """Validate udev rules for GPU power management and security."""

    def test_nvidia_rtd3_rules_present(self):
        udev_files = _find_files("*.rules") + _find_files("*.udev")
        if not udev_files:
            udev_files = _find_files("udev*") + _find_files("90-*")
        found_rtd3 = False
        for f in udev_files:
            content = _read_file(f)
            if "RTD3" in content or "rtd3" in content.lower():
                found_rtd3 = True
                break
            if "nvidia" in content.lower() and ("power" in content.lower() or "pm" in content.lower()):
                found_rtd3 = True
                break
        if not udev_files:
            self.skipTest("No udev rules files found in project")
        self.assertTrue(found_rtd3, "No NVIDIA RTD3/power management udev rules found")

    def test_thunderbolt_autorize(self):
        udev_files = _find_files("*.rules") + _find_files("*.udev")
        if not udev_files:
            self.skipTest("No udev rules files found in project")
        found_autorize = False
        for f in udev_files:
            content = _read_file(f)
            if "thunderbolt" in content.lower() and "authorize" in content.lower():
                found_autorize = True
                break
            if "0x4" in content and "thunderbolt" in content.lower():
                found_autorize = True
                break
        self.assertTrue(found_autorize, "No thunderbolt auto-authorize udev rule found")

    def test_no_nouveau_with_proprietary(self):
        config_files = (
            _find_files("*.conf") + _find_files("*.rules")
            + _find_files("modprobe*") + _find_files("blacklist*")
            + _find_files("gpu-autodetect*")
        )
        found_nouveau_blacklist = False
        for f in config_files:
            content = _read_file(f)
            if "nouveau" in content.lower() and "blacklist" in content.lower():
                found_nouveau_blacklist = True
                break
            if "blacklist" in content.lower() and "nouveau" in content.lower():
                found_nouveau_blacklist = True
                break
            if "blacklist nouveau" in content.lower():
                found_nouveau_blacklist = True
                break
        if not config_files:
            self.skipTest("No config files found to check nouveau blacklist")
        self.assertTrue(found_nouveau_blacklist,
                        "No nouveau blacklist rule found for proprietary NVIDIA driver")


if __name__ == "__main__":
    unittest.main(verbosity=2)
