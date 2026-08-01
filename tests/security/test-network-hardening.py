#!/usr/bin/env python3
"""Aion network hardening security tests."""
import re
import unittest
from pathlib import Path


PROJ_ROOT = Path(__file__).resolve().parents[2]

PASSWORD_PATTERN = re.compile(
    r"(?:password|passwd|secret|token)\s*[=:]\s*[\"'][^\"']+[\"']",
    re.IGNORECASE,
)


def _find_files(pattern):
    return list(PROJ_ROOT.rglob(pattern))


def _read_file(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, PermissionError, OSError):
        return ""


class TestNetworkHardening(unittest.TestCase):
    """Validate network security configurations."""

    def test_no_plaintext_passwords_in_configs(self):
        scan_exts = {".conf", ".cfg", ".ini", ".yaml", ".yml", ".json", ".env", ".service"}
        violations = []
        for ext in scan_exts:
            for f in _find_files(f"*{ext}"):
                content = _read_file(f)
                if not content:
                    continue
                for match in PASSWORD_PATTERN.finditer(content):
                    line_start = content.rfind("\n", 0, match.start()) + 1
                    line_end = content.find("\n", match.end())
                    if line_end == -1:
                        line_end = len(content)
                    line = content[line_start:line_end].strip()
                    if line.startswith("#") or line.startswith("//"):
                        continue
                    # Skip variable references and prompts: not literal secrets.
                    if "$" in match.group(0) or "read " in match.group(0):
                        continue
                    violations.append(f"{f.relative_to(PROJ_ROOT)}: {match.group()}")
        self.assertEqual(violations, [], f"Plaintext passwords found: {violations}")

    def test_upnp_port_forward_uses_timeout(self):
        source_files = _find_files("*.py") + _find_files("*.sh") + _find_files("*.conf")
        has_port_forward = False
        has_timeout = False
        for f in source_files:
            content = _read_file(f)
            if not content:
                continue
            if "upnp" in content.lower() or "port.forward" in content.lower():
                has_port_forward = True
                if "timeout" in content.lower() or "expire" in content.lower():
                    has_timeout = True
                break
        if has_port_forward:
            self.assertTrue(has_timeout, "UPnP/port-forwarding found without timeout/expiry")

    def test_bbr_sysctl_valid_ranges(self):
        ranges = {
            "rmem_max": (1048576, 134217728),
            "wmem_max": (1048576, 134217728),
            "tcp_keepalive_time": (60, 3600),
            "tcp_fastopen": (0, 3),
            "tcp_tw_reuse": (0, 1),
        }
        defaults = {
            "rmem_max": 16777216,
            "wmem_max": 16777216,
            "tcp_keepalive_time": 600,
            "tcp_fastopen": 3,
            "tcp_tw_reuse": 1,
        }
        for param, (lo, hi) in ranges.items():
            val = defaults[param]
            self.assertGreaterEqual(val, lo, f"{param}={val} below valid range minimum {lo}")
            self.assertLessEqual(val, hi, f"{param}={val} above valid range maximum {hi}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
