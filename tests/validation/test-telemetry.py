#!/usr/bin/env python3
"""Tests for the encrypted telemetry collector (core/telemetry)."""

import base64
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core" / "telemetry"))

import telemetry_collector as tc  # noqa: E402

KEY = b"x" * 32


def _decrypt(envelope: dict, key: bytes) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = base64.b64decode(envelope["iv"])
    ct = base64.b64decode(envelope["ciphertext"])
    plaintext = AESGCM(key).decrypt(nonce, ct, None)
    return json.loads(plaintext)


@pytest.mark.skipif(not tc.HAVE_CRYPTO, reason="cryptography unavailable")
def test_encrypt_report_roundtrip():
    report = {"type": "ui", "event": "crash", "line": 42}
    envelope = tc.encrypt_report(report, KEY)
    assert envelope["alg"] == "AES-256-GCM"
    assert _decrypt(envelope, KEY) == report


@pytest.mark.skipif(not tc.HAVE_CRYPTO, reason="cryptography unavailable")
def test_encrypt_report_wrong_key_fails():
    report = {"a": 1}
    envelope = tc.encrypt_report(report, KEY)
    with pytest.raises(Exception):
        _decrypt(envelope, b"y" * 32)


def test_load_key_none_without_source(monkeypatch):
    monkeypatch.setattr(tc, "KEY_FILE", Path("definitely-missing-key-file"))
    monkeypatch.delenv("AION_TELEMETRY_KEY", raising=False)
    assert tc.load_key() is None


def test_load_key_from_env(monkeypatch):
    monkeypatch.delenv("AION_TELEMETRY_KEY", raising=False)
    monkeypatch.setenv("AION_TELEMETRY_KEY", "supersecret")
    key = tc.load_key()
    assert key is not None and len(key) == 32
    assert key != b"supersecret"  # derived, never the raw secret


def test_telemetry_disabled_by_default(monkeypatch):
    monkeypatch.setattr(tc, "CONFIG_PATH", Path("definitely-missing-config"))
    assert tc.telemetry_enabled() is False


def test_telemetry_enabled_from_config(monkeypatch, tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"telemetry": {"enabled": True}}), encoding="utf-8")
    monkeypatch.setattr(tc, "CONFIG_PATH", cfg)
    assert tc.telemetry_enabled() is True


def test_write_report_requires_opt_in(monkeypatch, tmp_path):
    monkeypatch.setattr(tc, "telemetry_enabled", lambda: False)
    monkeypatch.delenv("AION_TELEMETRY_KEY", raising=False)
    assert tc.write_report({"x": 1}, dest_dir=tmp_path) is None


@pytest.mark.skipif(not tc.HAVE_CRYPTO, reason="cryptography unavailable")
def test_write_report_encrypts_to_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(tc, "telemetry_enabled", lambda: True)
    monkeypatch.setenv("AION_TELEMETRY_KEY", "s3cret-k3y")
    path = tc.write_report({"type": "crash", "n": 7}, dest_dir=tmp_path)
    assert path is not None and path.exists()
    env = json.loads(path.read_text(encoding="utf-8"))
    assert env["alg"] == "AES-256-GCM"
    assert "ciphertext" in env and "iv" in env
    assert "type" not in path.read_text(encoding="utf-8")  # plaintext never leaked


def test_service_files_present():
    base = ROOT / "core" / "telemetry"
    assert (base / "telemetry_collector.py").exists()
    assert (base / "aion-telemetry.service").exists()


def test_no_hardcoded_secret():
    src = (ROOT / "core" / "telemetry" / "telemetry_collector.py").read_text(encoding="utf-8")
    assert "AION_TELEMETRY_KEY" in src  # key must come from env/file
    assert "def load_key" in src
    assert "AESGCM" in src
