#!/usr/bin/env python3
"""NexusOS Comprehensive Test Suite — validates all project components."""

import ast
import glob
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _iter(pattern):
    return [Path(p) for p in glob.glob(str(ROOT / pattern), recursive=True)]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. File Integrity
# ---------------------------------------------------------------------------
class TestFileIntegrity(unittest.TestCase):
    EXPECTED_PY = [
        "core/security/security-bypass-daemon.py",
        "core/input-engine/input-daemon.py",
        "ui/oobe/oobe_wizard.py",
        "ui/wallpaper-engine/wallpaper-engine.py",
        "android/apk-installer/apk-handler.py",
        "android/key-mapper/key-mapper-daemon.py",
        "performance/throttler/resource-throttler.py",
        "deploy/ota/ota-updater.py",
    ]
    EXPECTED_SH = [
        "ui/plasma-config/strip-plasma.sh",
        "core/services/immount-root.sh",
        "android/waydroid/waydroid-init.sh",
        "ui/icons/icon-manager.sh",
        "performance/compression/btrfs-compression.sh",
        "deploy/github/build-iso.sh",
    ]
    EXPECTED_JSON = [
        "config/nexusos-config.json",
        "core/input-engine/input-mapping.json",
        "android/key-mapper/keymaps.json",
        "deploy/ota/manifest.json",
    ]
    EXPECTED_SERVICE = [
        "core/security/nexusos-security.service",
        "core/services/nexusos-init.service",
        "core/input-engine/nexusos-input.service",
        "android/key-mapper/nexusos-key-mapper.service",
        "deploy/ota/nexusos-ota.service",
        "performance/throttler/nexusos-throttler.service",
        "ui/oobe/nexusos-oobe.service",
    ]
    EXPECTED_TIMER = ["deploy/ota/nexusos-ota.timer"]
    EXPECTED_YAML = [
        "deploy/github/release-pipeline.yml",
        "deploy/github/pages-deploy.yml",
    ]
    EXPECTED_MISC = [
        "android/apk-installer/nexusos-apk-handler.desktop",
        "core/security/selinux-policy.te",
        "core/security/selinux-policy.fc",
        "ui/plasma-config/nexusos-plasma.conf",
        "performance/zram/zram-generator.conf",
        "LICENSE",
    ]

    def _check(self, files, label):
        for f in files:
            full = ROOT / f
            self.assertTrue(full.exists(), f"Missing {label}: {f}")
            self.assertGreater(full.stat().st_size, 0, f"Empty {label}: {f}")

    def test_python_files_exist(self):
        self._check(self.EXPECTED_PY, "Python file")

    def test_shell_files_exist(self):
        self._check(self.EXPECTED_SH, "Shell script")

    def test_json_files_exist(self):
        self._check(self.EXPECTED_JSON, "JSON config")

    def test_service_files_exist(self):
        self._check(self.EXPECTED_SERVICE, "Service unit")

    def test_timer_files_exist(self):
        self._check(self.EXPECTED_TIMER, "Timer unit")

    def test_yaml_files_exist(self):
        self._check(self.EXPECTED_YAML, "YAML workflow")

    def test_misc_files_exist(self):
        self._check(self.EXPECTED_MISC, "Misc file")

    def test_no_empty_python_files(self):
        for f in _iter("**/*.py"):
            self.assertGreater(f.stat().st_size, 0, f"Empty Python file: {f.relative_to(ROOT)}")

    def test_shebang_python(self):
        for f in _iter("**/*.py"):
            first = f.read_text(encoding="utf-8").split("\n", 1)[0]
            self.assertTrue(
                first.startswith("#!/"),
                f"Missing shebang in {f.relative_to(ROOT)}: {first!r}",
            )


# ---------------------------------------------------------------------------
# 2. Python Syntax
# ---------------------------------------------------------------------------
class TestPythonSyntax(unittest.TestCase):
    def test_all_python_valid_syntax(self):
        errors = []
        for f in _iter("**/*.py"):
            try:
                ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
            except SyntaxError as e:
                errors.append(f"{f.relative_to(ROOT)}:{e.lineno}: {e.msg}")
        self.assertFalse(errors, "Syntax errors:\n" + "\n".join(errors))


# ---------------------------------------------------------------------------
# 3. Python Imports & Structure
# ---------------------------------------------------------------------------
class TestPythonImports(unittest.TestCase):
    def test_security_daemon_imports(self):
        src = _read("core/security/security-bypass-daemon.py")
        for mod in ["PyQt6", "inotify_simple", "signal", "json", "logging"]:
            self.assertIn(mod, src, f"security-bypass-daemon missing import: {mod}")

    def test_input_daemon_imports(self):
        src = _read("core/input-engine/input-daemon.py")
        for mod in ["evdev", "asyncio", "signal"]:
            self.assertIn(mod, src, f"input-daemon missing import: {mod}")

    def test_oobe_wizard_imports(self):
        src = _read("ui/oobe/oobe_wizard.py")
        for mod in ["PyQt6", "fcntl", "json", "socket"]:
            self.assertIn(mod, src, f"oobe_wizard missing import: {mod}")

    def test_wallpaper_engine_imports(self):
        src = _read("ui/wallpaper-engine/wallpaper-engine.py")
        for mod in ["PIL", "PyQt6", "signal"]:
            self.assertIn(mod, src, f"wallpaper-engine missing import: {mod}")

    def test_apk_handler_imports(self):
        src = _read("android/apk-installer/apk-handler.py")
        for mod in ["PyQt6", "zipfile", "subprocess"]:
            self.assertIn(mod, src, f"apk-handler missing import: {mod}")

    def test_key_mapper_imports(self):
        src = _read("android/key-mapper/key-mapper-daemon.py")
        for mod in ["threading", "struct", "fcntl", "dataclasses"]:
            self.assertIn(mod, src, f"key-mapper-daemon missing import: {mod}")

    def test_throttler_imports(self):
        src = _read("performance/throttler/resource-throttler.py")
        for mod in ["ctypes", "subprocess", "resource", "cgroup"]:
            # cgroup is checked via string presence as it's referenced in code
            if mod == "cgroup":
                self.assertIn("cgroup", src, "throttler missing cgroup reference")
            else:
                self.assertIn(mod, src, f"throttler missing import: {mod}")

    def test_ota_updater_imports(self):
        src = _read("deploy/ota/ota-updater.py")
        for mod in ["hashlib", "argparse", "json", "subprocess"]:
            self.assertIn(mod, src, f"ota-updater missing import: {mod}")

    def test_security_daemon_constants(self):
        src = _read("core/security/security-bypass-daemon.py")
        self.assertIn("LOG_DIR", src)
        self.assertIn("LOG_FILE", src)
        self.assertIn("CONFIG_PATH", src)

    def test_oobe_step_titles(self):
        src = _read("ui/oobe/oobe_wizard.py")
        self.assertIn("STEP_TITLES", src)
        for step in ["Welcome", "Language", "Controller", "Display", "Security", "Ready"]:
            self.assertIn(f'"{step}"', src, f"oobe_wizard missing step: {step}")

    def test_throttler_constants(self):
        src = _read("performance/throttler/resource-throttler.py")
        self.assertIn("GAMING_SLICE", src)
        self.assertIn("INSTALL_SLICE", src)
        self.assertIn("POLL_INTERVAL", src)


# ---------------------------------------------------------------------------
# 4. Bash Syntax
# ---------------------------------------------------------------------------
class TestBashSyntax(unittest.TestCase):
    def _find_bash(self):
        for candidate in [
            r"C:\Program Files\Git\bin\bash.exe",
            r"C:\Program Files (x86)\Git\bin\bash.exe",
        ]:
            if os.path.isfile(candidate):
                return candidate
        return "bash"

    def test_all_bash_valid_syntax(self):
        errors = []
        bash_exe = self._find_bash()
        for f in _iter("**/*.sh"):
            unix_path = str(f).replace("\\", "/")
            result = subprocess.run(
                [bash_exe, "-n", unix_path],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                errors.append(f"{f.relative_to(ROOT)}: {result.stderr.strip()}")
        self.assertFalse(errors, "Bash syntax errors:\n" + "\n".join(errors))


# ---------------------------------------------------------------------------
# 5. Configuration (JSON + INI-style)
# ---------------------------------------------------------------------------
class TestConfiguration(unittest.TestCase):
    def _load_json(self, path):
        return json.loads(_read(path))

    def test_nexusos_config_structure(self):
        cfg = self._load_json("config/nexusos-config.json")
        for key in ["system", "security", "input", "performance", "display", "android", "ota", "chrome"]:
            self.assertIn(key, cfg, f"nexusos-config.json missing top-level key: {key}")

    def test_nexusos_config_system(self):
        sys_cfg = self._load_json("config/nexusos-config.json")["system"]
        self.assertEqual(sys_cfg["name"], "NexusOS")
        self.assertEqual(sys_cfg["version"], "1.0.0")
        self.assertIn(sys_cfg["selinux_mode"], ["enforcing", "permissive", "disabled"])

    def test_nexusos_config_security(self):
        sec = self._load_json("config/nexusos-config.json")["security"]
        self.assertIsInstance(sec["trusted_paths"], list)
        self.assertTrue(len(sec["trusted_paths"]) > 0)
        self.assertIn(sec["sandbox_backend"], ["bubblewrap", "firejail", "flatpak"])

    def test_nexusos_config_performance(self):
        perf = self._load_json("config/nexusos-config.json")["performance"]
        self.assertTrue(perf["zram_enabled"])
        self.assertEqual(perf["compression_algorithm"], "zstd")
        self.assertGreater(perf["swappiness"], 0)
        self.assertLessEqual(perf["max_ram_percent"], 100)

    def test_nexusos_config_display(self):
        disp = self._load_json("config/nexusos-config.json")["display"]
        self.assertTrue(disp["accent_color"].startswith("#"))
        self.assertTrue(disp["background_color"].startswith("#"))
        self.assertGreater(disp["taskbar_height"], 0)

    def test_nexusos_config_android(self):
        andr = self._load_json("config/nexusos-config.json")["android"]
        self.assertTrue(andr["waydroid_enabled"])
        self.assertIn(andr["waydroid_image"], ["VANILLA", "GAPPS"])

    def test_nexusos_config_ota(self):
        ota = self._load_json("config/nexusos-config.json")["ota"]
        self.assertIn("manifest_url", ota)
        self.assertGreater(ota["check_interval_hours"], 0)

    def test_nexusos_config_chrome(self):
        chrome = self._load_json("config/nexusos-config.json")["chrome"]
        self.assertIsInstance(chrome["flags"], list)
        self.assertTrue(chrome["disable_telemetry"])

    def test_input_mapping_structure(self):
        im = self._load_json("core/input-engine/input-mapping.json")
        self.assertIn("button_mappings", im)
        for profile in ["xbox", "playstation", "generic"]:
            self.assertIn(profile, im["button_mappings"], f"Missing profile: {profile}")

    def test_input_mapping_values(self):
        im = self._load_json("core/input-engine/input-mapping.json")
        self.assertGreater(im["deadzone"], 0)
        self.assertLessEqual(im["deadzone"], 1)
        self.assertGreater(im["mouse_sensitivity"], 0)
        self.assertTrue(im["gamepad_enabled"])

    def test_keymaps_structure(self):
        km = self._load_json("android/key-mapper/keymaps.json")
        self.assertIn("version", km)
        self.assertIn("profiles", km)
        for profile in ["fps", "moba", "racing", "rpg"]:
            self.assertIn(profile, km["profiles"], f"Missing profile: {profile}")

    def test_keymaps_profile_has_zones(self):
        km = self._load_json("android/key-mapper/keymaps.json")
        for name, profile in km["profiles"].items():
            self.assertIn("zones", profile, f"Profile {name} missing zones")
            self.assertIsInstance(profile["zones"], list)
            self.assertGreater(len(profile["zones"]), 0, f"Profile {name} has no zones")
            self.assertIn("key_bindings", profile, f"Profile {name} missing key_bindings")

    def test_keymaps_zone_coords(self):
        km = self._load_json("android/key-mapper/keymaps.json")
        for name, profile in km["profiles"].items():
            for i, zone in enumerate(profile["zones"]):
                self.assertGreaterEqual(zone["x_min"], 0, f"{name} zone {i} x_min < 0")
                self.assertLessEqual(zone["x_max"], 1, f"{name} zone {i} x_max > 1")
                self.assertGreaterEqual(zone["y_min"], 0, f"{name} zone {i} y_min < 0")
                self.assertLessEqual(zone["y_max"], 1, f"{name} zone {i} y_max > 1")
                self.assertGreater(zone["x_max"], zone["x_min"], f"{name} zone {i} x_max <= x_min")
                self.assertGreater(zone["y_max"], zone["y_min"], f"{name} zone {i} y_max <= y_min")

    def test_manifest_structure(self):
        m = self._load_json("deploy/ota/manifest.json")
        for key in ["latest_version", "download_url", "sha256", "release_date", "min_required_version"]:
            self.assertIn(key, m, f"manifest.json missing key: {key}")

    def test_manifest_sha256_length(self):
        m = self._load_json("deploy/ota/manifest.json")
        self.assertEqual(len(m["sha256"]), 64, "SHA256 should be 64 hex chars")

    def test_manifest_patches_list(self):
        m = self._load_json("deploy/ota/manifest.json")
        self.assertIsInstance(m.get("incremental_patches", []), list)

    def test_plasma_conf_colors(self):
        src = _read("ui/plasma-config/nexusos-plasma.conf")
        self.assertIn("#121212", src, "Missing background color #121212")
        self.assertIn("#1A2238", src, "Missing panel color #1A2238")
        self.assertIn("#00D2FF", src, "Missing accent color #00D2FF")

    def test_zram_config(self):
        src = _read("performance/zram/zram-generator.conf")
        self.assertIn("zstd", src, "zram should use zstd compression")
        self.assertIn("swap", src, "zram should be swap type")


# ---------------------------------------------------------------------------
# 6. Security
# ---------------------------------------------------------------------------
class TestSecurity(unittest.TestCase):
    def test_no_hardcoded_secrets_in_python(self):
        secret_patterns = re.compile(
            r"""(?:password|secret|api_key|apikey|token)\s*=\s*['"][^'"]{8,}['"]""",
            re.IGNORECASE,
        )
        findings = []
        for f in _iter("**/*.py"):
            for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
                if secret_patterns.search(line) and not line.strip().startswith("#"):
                    findings.append(f"{f.relative_to(ROOT)}:{i}")
        self.assertFalse(findings, "Potential hardcoded secrets:\n" + "\n".join(findings))

    def test_no_hardcoded_secrets_in_json(self):
        secret_patterns = re.compile(
            r"""(?:password|secret|api_key|apikey|private_key)\s*[:=]\s*['"][^'"]{8,}['"]""",
            re.IGNORECASE,
        )
        findings = []
        for f in _iter("**/*.json"):
            content = f.read_text(encoding="utf-8")
            if secret_patterns.search(content):
                findings.append(f.relative_to(ROOT))
        self.assertFalse(findings, "Potential secrets in JSON:\n" + "\n".join(findings))

    def test_selinux_policy_defines_types(self):
        src = _read("core/security/selinux-policy.te")
        self.assertIn("nexusos_game_t", src, "Missing type nexusos_game_t")
        self.assertIn("nexusos_bypass_t", src, "Missing type nexusos_bypass_t")
        self.assertIn("policy_module", src, "Missing policy_module declaration")

    def test_selinux_fc_has_labels(self):
        src = _read("core/security/selinux-policy.fc")
        self.assertGreater(len(src.strip()), 0, "selinux-policy.fc is empty")
        self.assertIn("nexusos", src, "selinux-policy.fc should reference nexusos types")

    def test_security_service_hardening(self):
        src = _read("core/security/nexusos-security.service")
        for directive in ["ProtectSystem", "RestrictNamespaces", "MemoryDenyWriteExecute",
                          "LockPersonality", "SystemCallArchitectures"]:
            self.assertIn(directive, src, f"security.service missing hardening: {directive}")

    def test_security_service_limit_restart(self):
        src = _read("core/security/nexusos-security.service")
        self.assertIn("StartLimitBurst", src, "Missing StartLimitBurst")
        self.assertIn("RestartSec", src, "Missing RestartSec")

    def test_input_service_security(self):
        src = _read("core/input-engine/nexusos-input.service")
        self.assertIn("ProtectSystem=strict", src, "Input service not strict-protected")
        self.assertIn("RestrictNamespaces", src)

    def test_ota_service_security(self):
        src = _read("deploy/ota/nexusos-ota.service")
        self.assertIn("ProtectSystem=strict", src)
        self.assertIn("ProtectHome", src)

    def test_throttler_service_security(self):
        src = _read("performance/throttler/nexusos-throttler.service")
        self.assertIn("ProtectSystem=strict", src)
        self.assertIn("MemoryMax", src, "Missing memory limit")

    def test_python_files_executable_shebang(self):
        for f in _iter("**/*.py"):
            first_line = f.read_text(encoding="utf-8").split("\n", 1)[0]
            self.assertIn("python3", first_line, f"{f.relative_to(ROOT)} shebang missing python3")


# ---------------------------------------------------------------------------
# 7. Performance
# ---------------------------------------------------------------------------
class TestPerformance(unittest.TestCase):
    def test_zram_enabled(self):
        cfg = json.loads(_read("config/nexusos-config.json"))
        self.assertTrue(cfg["performance"]["zram_enabled"], "zram should be enabled")
        self.assertEqual(cfg["performance"]["compression_algorithm"], "zstd")
        self.assertGreater(cfg["performance"]["zram_size_mb"], 0)

    def test_throttle_weights(self):
        cfg = json.loads(_read("config/nexusos-config.json"))
        perf = cfg["performance"]
        self.assertEqual(perf["throttle_game_cpu_weight"], 100)
        self.assertEqual(perf["throttle_background_cpu_weight"], 10)
        self.assertGreater(perf["throttle_game_cpu_weight"], perf["throttle_background_cpu_weight"])

    def test_target_free_ram(self):
        cfg = json.loads(_read("config/nexusos-config.json"))
        self.assertEqual(cfg["performance"]["target_free_ram_gb"], 7)

    def test_swappiness_value(self):
        cfg = json.loads(_read("config/nexusos-config.json"))
        sw = cfg["performance"]["swappiness"]
        self.assertGreater(sw, 100, "Swappiness should be >100 for zram systems")
        self.assertLessEqual(sw, 200, "Swappiness should be <=200")

    def test_btrfs_compression_uses_zstd(self):
        src = _read("performance/compression/btrfs-compression.sh")
        self.assertIn("zstd", src, "btrfs-compression.sh should use zstd")

    def test_throttler_slices(self):
        src = _read("performance/throttler/resource-throttler.py")
        self.assertIn("nexusos-gaming.slice", src)
        self.assertIn("nexusos-install.slice", src)

    def test_ota_timer_interval(self):
        src = _read("deploy/ota/nexusos-ota.timer")
        self.assertIn("OnUnitActiveSec=6h", src, "OTA timer should check every 6 hours")
        self.assertIn("Persistent=true", src, "OTA timer should be persistent")

    def test_display_compositing(self):
        cfg = json.loads(_read("config/nexusos-config.json"))
        disp = cfg["display"]
        self.assertTrue(disp["compositing_enabled"])
        self.assertTrue(disp["vsync"])
        self.assertGreater(disp["compositing_latency"], 0)


# ---------------------------------------------------------------------------
# 8. Android
# ---------------------------------------------------------------------------
class TestAndroid(unittest.TestCase):
    def test_waydroid_init_references_waydroid(self):
        src = _read("android/waydroid/waydroid-init.sh")
        self.assertIn("waydroid", src.lower(), "waydroid-init.sh should reference waydroid")

    def test_key_mapper_requires_waydroid(self):
        src = _read("android/key-mapper/nexusos-key-mapper.service")
        self.assertIn("waydroid-session.service", src, "key-mapper should require waydroid-session")

    def test_keymapper_daemon_uses_threading(self):
        src = _read("android/key-mapper/key-mapper-daemon.py")
        self.assertIn("threading", src, "key-mapper-daemon should use threading")

    def test_apk_handler_desktop_mime(self):
        src = _read("android/apk-installer/nexusos-apk-handler.desktop")
        self.assertIn("application/vnd.android.package-archive", src,
                        "Desktop entry should handle APK MIME type")

    def test_apk_handler_uses_zipfile(self):
        src = _read("android/apk-installer/apk-handler.py")
        self.assertIn("zipfile", src, "APK handler should use zipfile module")

    def test_android_config_enabled(self):
        cfg = json.loads(_read("config/nexusos-config.json"))
        andr = cfg["android"]
        self.assertTrue(andr["waydroid_enabled"])
        self.assertTrue(andr["keymapping_enabled"])
        self.assertTrue(andr["auto_install_apk"])

    def test_keymaps_has_fps_profile(self):
        km = json.loads(_read("android/key-mapper/keymaps.json"))
        fps = km["profiles"]["fps"]
        self.assertTrue(fps.get("touch_to_mouse", False))
        self.assertIn("left_stick_zone", fps)
        self.assertIn("right_stick_zone", fps)

    def test_keymaps_has_all_profiles(self):
        km = json.loads(_read("android/key-mapper/keymaps.json"))
        expected = {"fps", "moba", "racing", "rpg"}
        self.assertEqual(set(km["profiles"].keys()), expected)


# ---------------------------------------------------------------------------
# 9. Deployment
# ---------------------------------------------------------------------------
class TestDeployment(unittest.TestCase):
    def test_release_pipeline_has_build_job(self):
        src = _read("deploy/github/release-pipeline.yml")
        self.assertIn("build-iso", src)
        self.assertIn("archiso", src, "Build should use archiso")

    def test_release_pipeline_has_manifest_job(self):
        src = _read("deploy/github/release-pipeline.yml")
        self.assertIn("update-manifest", src)

    def test_release_pipeline_has_distribute_job(self):
        src = _read("deploy/github/release-pipeline.yml")
        self.assertIn("distribute", src)

    def test_release_pipeline_uses_checkout(self):
        src = _read("deploy/github/release-pipeline.yml")
        self.assertIn("actions/checkout@v4", src)

    def test_release_pipeline_uses_gh_release(self):
        src = _read("deploy/github/release-pipeline.yml")
        self.assertIn("softprops/action-gh-release", src)

    def test_pages_deploy_uses_deploy_pages(self):
        src = _read("deploy/github/pages-deploy.yml")
        self.assertIn("actions/deploy-pages@v4", src)

    def test_pages_deploy_uses_upload(self):
        src = _read("deploy/github/pages-deploy.yml")
        self.assertIn("actions/upload-pages-artifact", src)

    def test_pages_deploy_has_verify(self):
        src = _read("deploy/github/pages-deploy.yml")
        self.assertIn("verify", src, "Pages deploy should include verification step")

    def test_ota_service_references_updater(self):
        src = _read("deploy/ota/nexusos-ota.service")
        self.assertIn("ota-updater.py", src)
        self.assertIn("--check-only", src)

    def test_ota_updater_uses_hashlib(self):
        src = _read("deploy/ota/ota-updater.py")
        self.assertIn("hashlib", src, "OTA updater should verify checksums")

    def test_ota_manifest_version_format(self):
        m = json.loads(_read("deploy/ota/manifest.json"))
        self.assertRegex(m["latest_version"], r"^\d+\.\d+\.\d+$")

    def test_manifest_download_url_https(self):
        m = json.loads(_read("deploy/ota/manifest.json"))
        self.assertTrue(m["download_url"].startswith("https://"))


# ---------------------------------------------------------------------------
# 10. UI
# ---------------------------------------------------------------------------
class TestUI(unittest.TestCase):
    def test_oobe_wizard_uses_pyqt6(self):
        src = _read("ui/oobe/oobe_wizard.py")
        self.assertIn("PyQt6", src)

    def test_oobe_wizard_has_main_window(self):
        src = _read("ui/oobe/oobe_wizard.py")
        self.assertIn("QMainWindow", src)

    def test_wallpaper_engine_uses_pyqt6(self):
        src = _read("ui/wallpaper-engine/wallpaper-engine.py")
        self.assertIn("PyQt6", src)

    def test_wallpaper_engine_has_tray(self):
        src = _read("ui/wallpaper-engine/wallpaper-engine.py")
        self.assertIn("QSystemTrayIcon", src)

    def test_wallpaper_engine_uses_pil(self):
        src = _read("ui/wallpaper-engine/wallpaper-engine.py")
        self.assertIn("PIL", src)

    def test_strip_plasma_references_baloo(self):
        src = _read("ui/plasma-config/strip-plasma.sh")
        self.assertIn("baloo", src, "strip-plasma.sh should disable baloo")

    def test_plasma_conf_theme_name(self):
        src = _read("ui/plasma-config/nexusos-plasma.conf")
        self.assertIn("NexusOS-Dark", src)

    def test_plasma_conf_kwin_compositing(self):
        src = _read("ui/plasma-config/nexusos-plasma.conf")
        self.assertIn("Compositing=true", src)

    def test_display_config_colors_match_plasma(self):
        cfg = json.loads(_read("config/nexusos-config.json"))
        plasma = _read("ui/plasma-config/nexusos-plasma.conf")
        self.assertIn(cfg["display"]["accent_color"], plasma)
        self.assertIn(cfg["display"]["background_color"], plasma)
        self.assertIn(cfg["display"]["panel_color"], plasma)

    def test_oobe_service_runs_as_user(self):
        src = _read("ui/oobe/nexusos-oobe.service")
        self.assertIn("User=%i", src, "OOBE should run as invoking user")

    def test_oobe_service_conditional(self):
        src = _read("ui/oobe/nexusos-oobe.service")
        self.assertIn(".oobe-complete", src, "OOBE should check completion marker")


if __name__ == "__main__":
    unittest.main(verbosity=2)
