#!/usr/bin/env python3
"""Aion Emulation Framework — zero-setup RetroArch PSX bridge tests."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
EMU = ROOT / "games/emulation/aion-emu-framework.py"


def _load():
    spec = importlib.util.spec_from_file_location("aion_emu", EMU)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aion_emu"] = mod
    spec.loader.exec_module(mod)
    return mod


EMU_MODULE = _load()


class TestEmulationFramework(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        EMU_MODULE.CONFIG_DIR = self.base / "config"
        EMU_MODULE.ROM_DIR = self.base / "ROMs"
        EMU_MODULE.BIOS_DIR = self.base / "config/system"
        EMU_MODULE.CORE_DIR = self.base / "config/cores"
        EMU_MODULE.STATE_FILE = self.base / "state.json"
        (self.base / "ROMs/psx").mkdir(parents=True)
        (self.base / "ROMs/psx/Spyro.chd").write_bytes(b"\x00")
        (self.base / "ROMs/psx/notes.txt").write_text("skip me", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_find_roms_filters_extensions(self):
        roms = EMU_MODULE.find_roms()
        self.assertEqual(len(roms), 1)
        self.assertTrue(roms[0].name.endswith(".chd"))

    def test_core_installed_checks_path(self):
        self.assertFalse(EMU_MODULE.core_installed())
        EMU_MODULE.CORE_DIR.mkdir(parents=True)
        (EMU_MODULE.CORE_DIR / "pcsx_rearmed_libretro.so").write_bytes(b"core")
        self.assertTrue(EMU_MODULE.core_installed())

    def test_bios_detection(self):
        self.assertIsNone(EMU_MODULE.bios_path())
        (EMU_MODULE.BIOS_DIR).mkdir(parents=True)
        (EMU_MODULE.BIOS_DIR / "scph1001.bin").write_bytes(b"bios")
        self.assertTrue(EMU_MODULE.bios_path().endswith("scph1001.bin"))

    def test_status_reports_rom_count(self):
        s = EMU_MODULE.status()
        self.assertEqual(s["romCount"], 1)
        self.assertEqual(s["coreName"], "pcsx_rearmed")

    def test_ensure_dirs_creates_skeleton(self):
        EMU_MODULE.ensure_dirs()
        for d in (EMU_MODULE.CONFIG_DIR, EMU_MODULE.ROM_DIR,
                  EMU_MODULE.BIOS_DIR, EMU_MODULE.CORE_DIR):
            self.assertTrue(d.is_dir())

    def test_write_default_config_idempotent(self):
        first = EMU_MODULE.write_default_config()
        second = EMU_MODULE.write_default_config()
        self.assertTrue(first)
        self.assertFalse(second, "existing config must not be overwritten")
        cfg = (EMU_MODULE.CONFIG_DIR / "retroarch.cfg").read_text(encoding="utf-8")
        self.assertIn("system_directory", cfg)
        self.assertIn("libretro_directory", cfg)

    def test_run_setup_without_retroarch(self):
        with patch.object(EMU_MODULE, "retroarch_bin", return_value=None), \
             patch.object(EMU_MODULE, "setup_core", return_value=True):
            state = EMU_MODULE.run_setup()
            self.assertFalse(state["retroarch"])
            self.assertTrue(state["core"])

    def test_launch_requires_retroarch(self):
        with patch.object(EMU_MODULE, "retroarch_bin", return_value=None):
            result = EMU_MODULE.launch_rom("Spyro")
            self.assertFalse(result["ok"])
            self.assertIn("RetroArch", result["error"])

    def test_launch_requires_core(self):
        (EMU_MODULE.BIOS_DIR).mkdir(parents=True)
        (EMU_MODULE.BIOS_DIR / "scph1001.bin").write_bytes(b"bios")
        with patch.object(EMU_MODULE, "retroarch_bin", return_value="/usr/bin/retroarch"), \
             patch.object(EMU_MODULE, "core_installed", return_value=False):
            result = EMU_MODULE.launch_rom("Spyro")
            self.assertFalse(result["ok"])
            self.assertIn("core", result["error"].lower())

    def test_launch_requires_bios(self):
        (EMU_MODULE.CORE_DIR).mkdir(parents=True)
        (EMU_MODULE.CORE_DIR / "pcsx_rearmed_libretro.so").write_bytes(b"core")
        with patch.object(EMU_MODULE, "retroarch_bin", return_value="/usr/bin/retroarch"):
            result = EMU_MODULE.launch_rom("Spyro")
            self.assertFalse(result["ok"])
            self.assertIn("BIOS", result["error"])

    def test_launch_builds_correct_command(self):
        (EMU_MODULE.CORE_DIR).mkdir(parents=True)
        (EMU_MODULE.CORE_DIR / "pcsx_rearmed_libretro.so").write_bytes(b"core")
        (EMU_MODULE.BIOS_DIR).mkdir(parents=True)
        (EMU_MODULE.BIOS_DIR / "scph1001.bin").write_bytes(b"bios")
        with patch.object(EMU_MODULE, "retroarch_bin", return_value="/usr/bin/retroarch"), \
             patch.object(EMU_MODULE, "_run", return_value=0) as m:
            result = EMU_MODULE.launch_rom("Spyro")
            self.assertTrue(result["ok"])
            cmd = m.call_args[0][0]
            self.assertIn("-L", cmd)
            self.assertTrue(any("pcsx_rearmed_libretro.so" in part for part in cmd))
            self.assertTrue(any("Spyro.chd" in part for part in cmd))

    def test_launch_unknown_rom_returns_error(self):
        with patch.object(EMU_MODULE, "retroarch_bin", return_value="/usr/bin/retroarch"), \
             patch.object(EMU_MODULE, "core_installed", return_value=True), \
             patch.object(EMU_MODULE, "bios_path", return_value="/bios"):
            result = EMU_MODULE.launch_rom("does-not-exist")
            self.assertFalse(result["ok"])
            self.assertIn("not found", result["error"])

    def test_cli_status_and_list(self):
        with patch.object(EMU_MODULE, "retroarch_bin", return_value="/usr/bin/retroarch"):
            code = EMU_MODULE.main(["status"])
            self.assertEqual(code, 0)
        code = EMU_MODULE.main(["list"])
        self.assertEqual(code, 0)
        self.assertEqual(EMU_MODULE.main(["cores-dir"]), 0)

    def test_cli_launch_failure_exits_nonzero(self):
        with patch.object(EMU_MODULE, "retroarch_bin", return_value=None):
            code = EMU_MODULE.main(["launch", "Spyro"])
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
