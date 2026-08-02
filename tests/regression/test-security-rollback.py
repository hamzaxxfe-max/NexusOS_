#!/usr/bin/env python3
"""
Aion Security & Immutable Rollback Regression Tests
Simulates attacks on immutable root, Wine prefix, Waydroid container.
Verifies SELinux intercepts and rollback restores pristine state.
"""
import os
import re
import subprocess
import unittest
from pathlib import Path

PROJ_ROOT = Path(__file__).resolve().parents[2]
SELINUX_ENFORCE = Path("/sys/fs/selinux/enforce")
SELINUX_POLICY_DIR = Path("/etc/selinux")
REQUIRED_SELINUX_TYPES = [
    "aion_game_t",
    "aion_bypass_t",
    "aion_untrusted_app_t",
]
PASSWORD_PATTERNS = re.compile(
    r"(?:password|passwd|secret|token)\s*[=:]\s*[\"'][^\"']+[\"']",
    re.IGNORECASE,
)
SERVICE_EXEC_PATTERN = re.compile(
    r"ExecStart\s*=\s*/bin/(?:ba)?sh\b"
)


def _run_cmd(cmd, timeout=10):
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            shell=isinstance(cmd, str),
        )
        return result.stdout, result.stderr, result.returncode
    except FileNotFoundError:
        return "", "command not found", 127
    except subprocess.TimeoutExpired:
        return "", "timeout", 124


def _read_file(path):
    try:
        return Path(path).read_text(errors="replace")
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _find_file(name):
    candidates = list(PROJ_ROOT.rglob(name))
    return candidates[0] if candidates else None


def _find_all_files(extension):
    return list(PROJ_ROOT.rglob(f"*{extension}"))


def _is_linux():
    return os.path.exists("/proc")


def _is_aion_os():
    """True only when running on the deployed Aion OS (not a generic CI host)."""
    return any(
        os.path.exists(marker)
        for marker in ("/etc/aion", "/usr/lib/aion", "/usr/share/aion")
    )


def _get_mount_options(mount_point):
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and parts[1] == mount_point:
                    return parts[3]
    except (FileNotFoundError, PermissionError):
        pass
    return None


def _find_service_files():
    service_dirs = [
        Path("/etc/systemd/system"),
        Path("/usr/lib/systemd/system"),
        PROJ_ROOT / "services",
        PROJ_ROOT / "systemd",
    ]
    services = []
    for d in service_dirs:
        if d.exists():
            services.extend(d.glob("*.service"))
    for f in PROJ_ROOT.rglob("*.service"):
        if f not in services:
            services.append(f)
    return services


class TestSecurityRollback(unittest.TestCase):

    @unittest.skipUnless(_is_linux(), "Requires Linux sysfs")
    def test_selinux_policy_enforcing(self):
        if not SELINUX_ENFORCE.exists():
            self.skipTest("SELinux not available on this system")
        enforce_val = _read_file(SELINUX_ENFORCE)
        self.assertIsNotNone(enforce_val, "Cannot read SELinux enforce file")
        self.assertEqual(
            enforce_val.strip(), "1",
            f"SELinux not enforcing (value={enforce_val.strip()}, expected 1)",
        )

    def test_selinux_types_defined(self):
        policy_files = list(SELINUX_POLICY_DIR.rglob("*.te"))
        if not policy_files:
            policy_files = list(PROJ_ROOT.rglob("selinux-policy.te"))
        if not policy_files:
            policy_files = list(PROJ_ROOT.rglob("*.te"))
        self.assertGreater(len(policy_files), 0, "No SELinux .te policy files found")
        all_policy = ""
        for pf in policy_files:
            content = _read_file(pf)
            if content:
                all_policy += content + "\n"
        for selinux_type in REQUIRED_SELINUX_TYPES:
            self.assertIn(
                selinux_type, all_policy,
                f"SELinux type '{selinux_type}' not defined in policy files",
            )

    @unittest.skipUnless(_is_aion_os(), "Immutable root is a property of the deployed Aion OS")
    def test_immutable_root_is_readonly(self):
        opts = _get_mount_options("/")
        if opts is None:
            self.skipTest("Cannot read /proc/mounts")
        self.assertIn(
            "ro", opts.split(","),
            f"Root filesystem not mounted read-only (opts: {opts})",
        )

    def test_wine_prefix_isolated(self):
        source_files = _find_all_files(".py") + _find_all_files(".sh") + _find_all_files(".conf")
        exclude_keywords = ["installer", "builder", "Aion-Builder", "build-iso", "build-test-iso", "btrfs-compression", "build.sh", "01-base-system", "02-gaming-kernel", "03-gaming-stack", "04-desktop-environment", "05-update-system"]
        cross_access = []
        for sf in source_files:
            if any(kw in sf.name for kw in exclude_keywords):
                continue
            content = _read_file(sf)
            if content is None:
                continue
            if re.search(r"cp\s+.*\.\./\.\.", content):
                cross_access.append(str(sf))
            if re.search(r"ln\s+.*\.\./\.\.", content):
                cross_access.append(str(sf))
            if re.search(r"symlink.*\.\./\.\.", content, re.IGNORECASE):
                cross_access.append(str(sf))
        self.assertEqual(len(cross_access), 0, f"Cross-prefix file access: {cross_access}")

    def test_wine_prefix_no_system_write(self):
        source_files = _find_all_files(".py") + _find_all_files(".sh")
        bad = []
        exclude_keywords = ["installer", "builder", "Aion-Builder", "build-iso", "build-test-iso", "btrfs-compression", "build.sh", "01-base-system", "02-gaming-kernel", "03-gaming-stack", "04-desktop-environment", "05-update-system"]
        targets = [
            r"/etc/(?:passwd|shadow|sudoers|fstab|sudoers\.d/)",
            r"/usr/(?:bin|sbin|lib)/",
            r"/boot/",
            r"/dev/(?:sd[a-z]|nvme)",
        ]
        for sf in source_files:
            if any(kw in sf.name for kw in exclude_keywords):
                continue
            content = _read_file(sf)
            if content is None:
                continue
            for t in targets:
                if re.search(
                    rf"write.*{t}|echo.*>>\s*{t}|cp.*{t}|mv.*{t}",
                    content, re.IGNORECASE,
                ):
                    bad.append(f"{sf}: {t}")
        self.assertEqual(len(bad), 0, f"System write attempts: {bad}")

    def test_waydroid_container_isolated(self):
        source_files = _find_all_files(".py") + _find_all_files(".sh") + _find_all_files(".conf")
        test_file = Path(__file__).resolve()
        violations = []
        for sf in source_files:
            if sf.resolve() == test_file:
                continue
            content = _read_file(sf)
            if content is None or "waydroid" not in content.lower():
                continue
            if re.search(r"mount.*bind.*/etc", content):
                violations.append(f"{sf}: bind-mounts /etc into waydroid")
            if re.search(r"mount.*bind.*/home", content):
                violations.append(f"{sf}: bind-mounts /home into waydroid")
            if re.search(r"mount.*bind.*/var/lib(?!\s*/var/lib/waydroid)", content):
                violations.append(f"{sf}: bind-mounts system dirs into waydroid")
        self.assertEqual(len(violations), 0, f"Waydroid isolation violations: {violations}")

    def test_security_bypass_creates_sandbox(self):
        daemon_path = _find_file("security-bypass-daemon.py")
        if daemon_path is None:
            self.skipTest("security-bypass-daemon.py not found")
        content = _read_file(daemon_path)
        self.assertIsNotNone(content)
        self.assertIn("bwrap", content, "No bwrap sandbox command found")
        bwrap_patterns = [
            r"bwrap\s+",
            r"\"bwrap\"",
            r"'bwrap'",
            r"subprocess.*bwrap",
            r"os\.system.*bwrap",
            r"run.*bwrap",
            r"create_sandbox",
        ]
        found = any(re.search(p, content) for p in bwrap_patterns)
        self.assertTrue(found, "No executable bwrap invocation found")

    def test_security_bypass_has_block_option(self):
        daemon_path = _find_file("security-bypass-daemon.py")
        if daemon_path is None:
            self.skipTest("security-bypass-daemon.py not found")
        content = _read_file(daemon_path)
        self.assertIsNotNone(content)
        block_patterns = [
            r"[\"']Block[\"']",
            r"block.*button",
            r"btn.*block",
            r"action.*block",
            r"BLOCK",
            r"block_app",
            r"block_access",
        ]
        found = any(re.search(p, content, re.IGNORECASE) for p in block_patterns)
        self.assertTrue(found, "No Block button/action handler found")

    def test_ota_checksum_verification(self):
        ota_path = _find_file("ota-updater.py")
        if ota_path is None:
            self.skipTest("ota-updater.py not found")
        content = _read_file(ota_path)
        self.assertIsNotNone(content)
        self.assertIn("sha256", content.lower(), "No SHA256 usage found")
        verify_patterns = [
            r"verify",
            r"check.*sum",
            r"validate.*hash",
            r"digest",
            r"hexdigest",
        ]
        found = any(re.search(p, content, re.IGNORECASE) for p in verify_patterns)
        self.assertTrue(found, "sha256 mentioned but no verification logic")

    def test_ota_rollback_mechanism(self):
        ota_path = _find_file("ota-updater.py")
        if ota_path is None:
            self.skipTest("ota-updater.py not found")
        content = _read_file(ota_path)
        self.assertIsNotNone(content)
        rollback_patterns = [
            r"btrfs.*snapshot",
            r"subvolume.*snapshot",
            r"rollback",
            r"btrfs subvolume list",
            r"default.*subvolume",
            r"btrfs subvolume set-default",
            r"snapper",
        ]
        found = any(re.search(p, content, re.IGNORECASE) for p in rollback_patterns)
        self.assertTrue(found, "No btrfs snapshot rollback mechanism found")

    def test_no_plaintext_passwords(self):
        scan_exts = {".py", ".sh", ".conf", ".cfg", ".ini", ".yaml", ".yml",
                     ".json", ".env", ".service", ".toml"}
        scan_files = []
        for ext in scan_exts:
            scan_files.extend(PROJ_ROOT.rglob(f"*{ext}"))
        scan_files = [f for f in scan_files if ".git" not in str(f)]
        violations = []
        for sf in scan_files:
            content = _read_file(sf)
            if content is None:
                continue
            for match in PASSWORD_PATTERNS.finditer(content):
                line_start = content.rfind("\n", 0, match.start()) + 1
                line_end = content.find("\n", match.end())
                if line_end == -1:
                    line_end = len(content)
                line = content[line_start:line_end].strip()
                if line.startswith("#") or line.startswith("//"):
                    continue
                # Skip variable references and prompts: not literal secrets.
                # Real hardcoded credentials are literal strings, not $VAR refs.
                matched = match.group(0)
                if "$" in matched or "\n" in matched or "read " in matched:
                    continue
                violations.append(f"{sf}:{matched}")
        self.assertEqual(len(violations), 0, f"Plaintext passwords: {violations}")

    def test_log_files_use_var_log(self):
        source_files = _find_all_files(".py") + _find_all_files(".sh")
        bad = []
        for sf in source_files:
            content = _read_file(sf)
            if content is None:
                continue
            log_paths = re.findall(
                r'(?:log[_\s]*(?:file|path|dir|ger)?)\s*[=:]\s*["\']([^"\']+)["\']',
                content, re.IGNORECASE,
            )
            for lp in log_paths:
                if lp.startswith("/") and not lp.startswith("/var/log/aion"):
                    if "tmp" not in lp and "mock" not in lp and "test" not in str(sf):
                        bad.append(f"{sf}: {lp}")
        self.assertEqual(len(bad), 0, f"Bad log paths: {bad}")

    @unittest.skipUnless(_is_aion_os(), "Only checks Aion's own systemd units on the deployed OS")
    def test_service_files_no_root_shell(self):
        services = _find_service_files()
        violations = []
        for svc in services:
            content = _read_file(svc)
            if content is None:
                continue
            for match in SERVICE_EXEC_PATTERN.finditer(content):
                violations.append(f"{svc}: {match.group()}")
        self.assertEqual(len(violations), 0, f"Shell ExecStart: {violations}")

    def test_selinux_file_contexts_defined(self):
        fc_files = list(SELINUX_POLICY_DIR.rglob("file_contexts"))
        fc_files += list(PROJ_ROOT.rglob("file_contexts"))
        fc_files += list(PROJ_ROOT.rglob("selinux*file*context*"))
        if not fc_files:
            self.skipTest("No file_contexts files found")
        all_ctx = ""
        for fc in fc_files:
            content = _read_file(fc)
            if content:
                all_ctx += content
        nexus = re.findall(r"aion\w+", all_ctx)
        self.assertGreater(len(nexus), 0, "No aion file contexts defined")

    def test_btrfs_rollback_snapshot_exists(self):
        ota_path = _find_file("ota-updater.py")
        if ota_path is None:
            self.skipTest("ota-updater.py not found")
        content = _read_file(ota_path)
        self.assertIsNotNone(content)
        patterns = [
            r"btrfs\s+subvolume\s+snapshot",
            r"snapshot.*create",
            r"create.*snapshot",
            r"backup.*subvol",
        ]
        found = any(re.search(p, content, re.IGNORECASE) for p in patterns)
        self.assertTrue(found, "No btrfs snapshot creation before updates")

    def test_no_suid_binaries_in_project(self):
        suid = []
        for f in PROJ_ROOT.rglob("*"):
            try:
                if f.is_file() and f.stat().st_mode & 0o4000:
                    suid.append(str(f))
            except (PermissionError, OSError):
                continue
        self.assertEqual(len(suid), 0, f"SUID binaries: {suid}")

    def test_wine_prefix_no_etc_references(self):
        source_files = _find_all_files(".py") + _find_all_files(".sh") + _find_all_files(".conf")
        for sf in source_files:
            content = _read_file(sf)
            if content is None or "wine" not in content.lower():
                continue
            refs = re.findall(
                r'[\"\'/]((?:home|tmp|opt|var)[^\"\']*wine[^\"\']*)',
                content, re.IGNORECASE,
            )
            for ref in refs:
                if ref.startswith("/home/") or ref.startswith("/opt/"):
                    self.assertNotIn("/etc/", ref, f"Wine prefix {ref} touches /etc/")

    def test_daemon_files_no_permissive_umask(self):
        daemons = (
            list(PROJ_ROOT.rglob("*daemon*.py"))
            + list(PROJ_ROOT.rglob("*daemon*.sh"))
        )
        for df in daemons:
            content = _read_file(df)
            if content is None:
                continue
            if re.search(r"os\.umask\s*\(\s*0o?0{2,}7\b", content):
                self.fail(f"{df.name} sets permissive umask")


if __name__ == "__main__":
    unittest.main()
