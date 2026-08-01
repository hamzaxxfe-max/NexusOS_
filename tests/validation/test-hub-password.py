#!/usr/bin/env python3
"""Aion Hub password — PBKDF2 storage, constant-time verify, enable/disable."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PASS_MOD = ROOT / "hub" / "aion-hub-pass.py"


def _load_pass_module():
    spec = importlib.util.spec_from_file_location("aion_hub_pass", PASS_MOD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["aion_hub_pass"] = mod
    spec.loader.exec_module(mod)
    return mod


PASS_MODULE = _load_pass_module()


class TestPasswordStorage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pw_file = Path(self.tmp.name) / "hub-password.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_then_verify_correct(self):
        PASS_MODULE.set_password("my-secret", self.pw_file)
        self.assertTrue(PASS_MODULE.verify("my-secret", self.pw_file))

    def test_verify_wrong_secret_fails(self):
        PASS_MODULE.set_password("my-secret", self.pw_file)
        self.assertFalse(PASS_MODULE.verify("wrong", self.pw_file))

    def test_verify_constant_time_no_early_return(self):
        PASS_MODULE.set_password("secret", self.pw_file)
        self.assertFalse(PASS_MODULE.verify("secrex", self.pw_file))
        self.assertFalse(PASS_MODULE.verify("S" * 100, self.pw_file))

    def test_storage_is_never_plaintext(self):
        PASS_MODULE.set_password("top-secret", self.pw_file)
        content = self.pw_file.read_text(encoding="utf-8")
        self.assertNotIn("top-secret", content)
        data = json.loads(content)
        self.assertNotEqual(data["hash"], "top-secret")
        self.assertIn("salt", data)
        self.assertIn("kdf", data)
        self.assertEqual(data["kdf"], "pbkdf2-hmac-sha256")

    def test_salt_is_random_per_write(self):
        PASS_MODULE.set_password("abc", self.pw_file)
        salt1 = json.loads(self.pw_file.read_text())["salt"]
        PASS_MODULE.set_password("abc", self.pw_file)
        salt2 = json.loads(self.pw_file.read_text())["salt"]
        self.assertNotEqual(salt1, salt2, "salt must rotate on every write")

    def test_disabled_when_no_file(self):
        self.assertFalse(PASS_MODULE.is_enabled(self.pw_file))
        self.assertTrue(PASS_MODULE.verify("anything", self.pw_file))

    def test_enabled_after_set(self):
        PASS_MODULE.set_password("abc", self.pw_file)
        self.assertTrue(PASS_MODULE.is_enabled(self.pw_file))

    def test_remove_password(self):
        PASS_MODULE.set_password("abc", self.pw_file)
        self.assertTrue(PASS_MODULE.remove_password(self.pw_file))
        self.assertFalse(PASS_MODULE.is_enabled(self.pw_file))
        self.assertTrue(PASS_MODULE.verify("x", self.pw_file))

    def test_pbkdf2_deterministic(self):
        salt = b"\x00" * 16
        a = PASS_MODULE.derive_key("pw", salt, 1000)
        b = PASS_MODULE.derive_key("pw", salt, 1000)
        self.assertEqual(a, b)
        self.assertNotEqual(PASS_MODULE.derive_key("pw", b"\x01" * 16, 1000), a)


class TestPasswordCLI(unittest.TestCase):
    def test_check_command_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            import os
            pw = Path(tmp) / "hub-password.json"
            PASS_MODULE.set_password("pass123", pw)
            code = PASS_MODULE.main(["check", "pass123", "--config-dir", tmp])
            self.assertEqual(code, 0)

    def test_check_command_wrong(self):
        with tempfile.TemporaryDirectory() as tmp:
            PASS_MODULE.set_password("pass123", Path(tmp) / "hub-password.json")
            code = PASS_MODULE.main(["check", "nope", "--config-dir", tmp])
            self.assertEqual(code, 1)

    def test_status_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = PASS_MODULE.main(["status", "--config-dir", tmp])
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
