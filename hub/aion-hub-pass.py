#!/usr/bin/env python3
"""Aion Hub — optional user password protection.

Gives the user the choice to lock the system / hub with a secret phrase.
The password is stored as a salted hash (PBKDF2-HMAC-SHA256), never plaintext.
Disabled by default: the user opts in via `aion-hub-pass.py --set`.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import json
import os
import secrets
import sys
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("AION_CONFIG_DIR", "/etc/aion"))
PASSWORD_FILE = CONFIG_DIR / "hub-password.json"
ITERATIONS = 200_000


def derive_key(password: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               salt, iterations)


def set_password(password: str, out_path: Path = PASSWORD_FILE) -> None:
    """Write a salted PBKDF2 hash for the user's secret."""
    salt = secrets.token_bytes(16)
    digest = derive_key(password, salt)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "salt": salt.hex(),
        "hash": digest.hex(),
        "iterations": ITERATIONS,
        "kdf": "pbkdf2-hmac-sha256",
    }, indent=2), encoding="utf-8")
    os.chmod(out_path, 0o600)


def verify(password: str, in_path: Path = PASSWORD_FILE) -> bool:
    """Constant-time comparison of a candidate secret against the stored hash."""
    if not in_path.is_file():
        return True  # no password configured → open access
    try:
        data = json.loads(in_path.read_text(encoding="utf-8"))
        salt = bytes.fromhex(data["salt"])
        expected = bytes.fromhex(data["hash"])
        iterations = int(data.get("iterations", ITERATIONS))
    except (OSError, ValueError, KeyError, TypeError):
        return False
    digest = derive_key(password, salt, iterations)
    return hmac.compare_digest(digest, expected)


def is_enabled(in_path: Path = PASSWORD_FILE) -> bool:
    return in_path.is_file()


def remove_password(in_path: Path = PASSWORD_FILE) -> bool:
    try:
        in_path.unlink()
        return True
    except FileNotFoundError:
        return False


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aion Hub password management")
    sub = parser.add_subparsers(dest="command", required=True)

    set_p = sub.add_parser("set", help="Set (or change) the hub password")
    set_p.add_argument("--password", default=None,
                       help="Secret phrase (interactive if omitted)")
    set_p.add_argument("--config-dir", default=str(CONFIG_DIR))

    clear_p = sub.add_parser("clear", help="Remove the password (open access)")
    clear_p.add_argument("--config-dir", default=str(CONFIG_DIR))

    check_p = sub.add_parser("check", help="Verify a candidate secret")
    check_p.add_argument("password", help="Secret phrase to verify")
    check_p.add_argument("--config-dir", default=str(CONFIG_DIR))

    status_p = sub.add_parser("status", help="Report whether a password is set")
    status_p.add_argument("--config-dir", default=str(CONFIG_DIR))

    args = parser.parse_args(argv)
    config_dir = Path(args.config_dir)
    target = config_dir / "hub-password.json"

    if args.command == "set":
        password = args.password or getpass.getpass(
            "Enter the secret for this system: ")
        confirm = args.password or getpass.getpass("Confirm the secret: ")
        if password != confirm:
            print("Secrets do not match.", file=sys.stderr)
            return 1
        if len(password) < 4:
            print("Secret must be at least 4 characters.", file=sys.stderr)
            return 1
        set_password(password, target)
        print(f"Secret set in {target}")
        return 0

    if args.command == "clear":
        if remove_password(target):
            print("Secret removed — open access restored.")
        else:
            print("No secret was set.", file=sys.stderr)
        return 0

    if args.command == "check":
        ok = verify(args.password, target)
        print("OK" if ok else "DENIED")
        return 0 if ok else 1

    if args.command == "status":
        print("enabled" if is_enabled(target) else "disabled")
        return 0

    parser.error("unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
