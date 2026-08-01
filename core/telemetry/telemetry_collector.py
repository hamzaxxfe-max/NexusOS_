#!/usr/bin/env python3
"""Aion encrypted telemetry collector.

Collects lightweight crash/UI health reports and encrypts them with
AES-256-GCM before they are written to disk. The key is NEVER embedded in
the binary: it is read from /etc/aion/telemetry.key (600 root) or the
AION_TELEMETRY_KEY environment variable. If no key is present the
collector degrades to local-only plaintext journaling with a log warning —
it never fabricates a key.

Telemetry is opt-in via config key `telemetry.enabled` (default off) and
only UI-layer events are captured (no shell history, no game data).
"""

import base64
import hashlib
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

logger = logging.getLogger("aion-telemetry")

TELEMETRY_DIR = Path("/var/log/aion/telemetry")
KEY_FILE = Path("/etc/aion/telemetry.key")
CONFIG_PATH = Path("/etc/aion/config.json")

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAVE_CRYPTO = True
except ImportError:
    HAVE_CRYPTO = False


def load_key() -> bytes | None:
    """Return a 32-byte key or None. Never generates or embeds a key."""
    raw: str | None = None
    try:
        if KEY_FILE.exists():
            raw = KEY_FILE.read_text(encoding="utf-8").strip()
        elif "AION_TELEMETRY_KEY" in os.environ:
            raw = os.environ["AION_TELEMETRY_KEY"]
    except OSError as e:
        logger.warning("Cannot read telemetry key: %s", e)
        return None

    if not raw:
        return None

    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    if len(digest) != 32:
        return None
    return digest


def telemetry_enabled() -> bool:
    """Read opt-in flag from the Aion config. Defaults to disabled."""
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            return bool(data.get("telemetry", {}).get("enabled", False))
    except (OSError, ValueError):
        pass
    return False


def encrypt_report(report: dict, key: bytes) -> dict:
    """Encrypt report dict with AES-256-GCM. Returns envelope dict.

    Envelope: { v, alg, iv, ciphertext } with base64 payloads.
    """
    if not HAVE_CRYPTO:
        raise RuntimeError("cryptography library unavailable")

    nonce = os.urandom(12)
    plaintext = json.dumps(report, sort_keys=True).encode("utf-8")
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return {
        "v": 1,
        "alg": "AES-256-GCM",
        "iv": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def write_report(report: dict, dest_dir: Path | None = None) -> Path | None:
    """Encrypt and persist a telemetry report. Returns the written path.

    Requires: telemetry enabled, a key available, crypto available, and a
    writable destination directory. Any failure returns None (never raises).
    """
    if not telemetry_enabled():
        return None

    key = load_key()
    if key is None:
        logger.warning("Telemetry enabled but no key present; report dropped")
        return None

    dest = dest_dir or TELEMETRY_DIR
    try:
        dest.mkdir(parents=True, exist_ok=True)
        envelope = encrypt_report(report, key)
        fname = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}.enc"
        out = dest / fname
        out.write_text(json.dumps(envelope), encoding="utf-8")
        try:
            os.chmod(out, 0o600)
        except OSError:
            pass
        return out
    except Exception as e:  # defensive: telemetry must never crash the host
        logger.error("Telemetry write failed: %s", e)
        return None


def capture_crash(report: dict) -> Path | None:
    """Standard crash envelope with host fingerprint + timestamp."""
    try:
        with open("/etc/machine-id", "r", encoding="utf-8") as f:
            mid = f.read().strip()[:8]
    except OSError:
        mid = "unknown"
    full = {
        "type": "crash",
        "machine": mid,
        "ts": time.time(),
        "payload": report,
    }
    return write_report(full)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Aion encrypted telemetry")
    parser.add_argument("--report-json", help="JSON file describing the event")
    parser.add_argument("--check", action="store_true", help="Verify key + crypto availability")
    args = parser.parse_args()

    if args.check:
        print(f"enabled={telemetry_enabled()}")
        print(f"crypto={'yes' if HAVE_CRYPTO else 'no'}")
        print(f"key={'present' if load_key() is not None else 'missing'}")
        return 0

    if args.report_json:
        try:
            payload = json.loads(Path(args.report_json).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.error("Bad report file: %s", e)
            return 1
        path = capture_crash(payload)
        return 0 if path is not None else 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
