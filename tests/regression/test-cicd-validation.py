#!/usr/bin/env python3
"""
NexusOS CI/CD Pipeline Validation Tests
Verifies all workflow files, build scripts, and deployment configs.
"""
import json
import re
import unittest
from pathlib import Path

PROJ_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS_DIR = PROJ_ROOT / ".github" / "workflows"
DEPLOY_DIR = PROJ_ROOT / "deploy" / "github"
OTA_DIR = PROJ_ROOT / "deploy" / "ota"
PIPELINE_FILE = DEPLOY_DIR / "release-pipeline.yml"
if not PIPELINE_FILE.exists():
    PIPELINE_FILE = WORKFLOWS_DIR / "release-pipeline.yml"
PAGES_FILE = DEPLOY_DIR / "pages-deploy.yml"
if not PAGES_FILE.exists():
    PAGES_FILE = WORKFLOWS_DIR / "pages-deploy.yml"
BUILDER_SCRIPT = PROJ_ROOT / "NexusOS-Builder.sh"
MANIFEST_FILE = OTA_DIR / "manifest.json"
if not MANIFEST_FILE.exists():
    MANIFEST_FILE = PROJ_ROOT / "manifest.json"
LICENSE_FILE = PROJ_ROOT / "LICENSE"
GITIGNORE_FILE = PROJ_ROOT / ".gitignore"
OTA_SERVICE = OTA_DIR / "nexusos-ota.service"
if not OTA_SERVICE.exists():
    OTA_SERVICE = PROJ_ROOT / "nexusos-ota.service"
OTA_TIMER = OTA_DIR / "nexusos-ota.timer"
if not OTA_TIMER.exists():
    OTA_TIMER = PROJ_ROOT / "nexusos-ota.timer"


def _read_file(path):
    try:
        return Path(path).read_text(errors="replace")
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _find_file(name, search_root=None):
    root = search_root or PROJ_ROOT
    candidates = list(root.rglob(name))
    return candidates[0] if candidates else None


def _find_workflow(name):
    path = WORKFLOWS_DIR / name
    if path.exists():
        return path
    path = DEPLOY_DIR / name
    if path.exists():
        return path
    return _find_file(name, PROJ_ROOT)


def _parse_yaml_simple(content):
    sections = {}
    current_section = None
    current_lines = []
    for line in content.splitlines():
        if not line.startswith(" ") and line.strip() and not line.startswith("#"):
            if current_section:
                sections[current_section] = "\n".join(current_lines)
            current_section = line.split(":")[0].strip()
            current_lines = [line]
        else:
            current_lines.append(line)
    if current_section:
        sections[current_section] = "\n".join(current_lines)
    return sections


class TestCICDValidation(unittest.TestCase):

    def test_release_pipeline_structure(self):
        self.assertTrue(
            PIPELINE_FILE.exists(),
            f"Release pipeline not found at {PIPELINE_FILE}"
        )
        content = _read_file(PIPELINE_FILE)
        self.assertIsNotNone(content, "Cannot read release-pipeline.yml")
        required_jobs = ["build-iso", "update-manifest", "distribute"]
        for job in required_jobs:
            self.assertIn(
                job, content,
                f"Release pipeline missing required job: {job}"
            )

    def test_release_pipeline_uses_archiso(self):
        content = _read_file(PIPELINE_FILE)
        self.assertIsNotNone(content, "Cannot read release-pipeline.yml")
        archiso_indicators = ["archiso", "mkarchiso", "arch-iso"]
        found = any(ind in content.lower() for ind in archiso_indicators)
        self.assertTrue(
            found,
            "Release pipeline build step does not reference archiso or mkarchiso"
        )

    def test_release_pipeline_generates_checksums(self):
        content = _read_file(PIPELINE_FILE)
        self.assertIsNotNone(content, "Cannot read release-pipeline.yml")
        checksum_indicators = ["sha256sum", "SHA256", "sha256", "checksum"]
        found = any(ind in content for ind in checksum_indicators)
        self.assertTrue(
            found,
            "Release pipeline does not generate SHA256 checksums"
        )

    def test_release_pipeline_creates_release(self):
        content = _read_file(PIPELINE_FILE)
        self.assertIsNotNone(content, "Cannot read release-pipeline.yml")
        release_indicators = [
            "softprops/action-gh-release",
            "gh release create",
            "release-create",
            "actions/create-release",
        ]
        found = any(ind in content for ind in release_indicators)
        self.assertTrue(
            found,
            "Release pipeline does not create a GitHub release"
        )

    def test_pages_deploy_uses_deploy_pages(self):
        pages_file = _find_workflow("pages-deploy.yml")
        if pages_file is None:
            pages_file = _find_workflow("deploy-pages.yml")
        if pages_file is None:
            self.skipTest("Pages deploy workflow not found")
        content = _read_file(pages_file)
        self.assertIsNotNone(content, "Cannot read pages deploy workflow")
        self.assertIn(
            "actions/deploy-pages", content,
            "Pages deploy workflow does not use actions/deploy-pages"
        )

    def test_pages_deploy_has_build_step(self):
        pages_file = _find_workflow("pages-deploy.yml")
        if pages_file is None:
            pages_file = _find_workflow("deploy-pages.yml")
        if pages_file is None:
            self.skipTest("Pages deploy workflow not found")
        content = _read_file(pages_file)
        self.assertIsNotNone(content, "Cannot read pages deploy workflow")
        build_indicators = [
            "npm run build",
            "npm build",
            "next build",
            "yarn build",
            "pnpm build",
            "make build",
        ]
        found = any(ind in content for ind in build_indicators)
        self.assertTrue(
            found,
            "Pages deploy workflow has no build step"
        )

    def test_builder_script_has_iso_output(self):
        self.assertTrue(
            BUILDER_SCRIPT.exists(),
            f"Builder script not found at {BUILDER_SCRIPT}"
        )
        content = _read_file(BUILDER_SCRIPT)
        self.assertIsNotNone(content, "Cannot read NexusOS-Builder.sh")
        iso_patterns = [r"\.iso", r"iso_output", r"output.*iso", r"NexusOS.*\.iso"]
        found = any(re.search(p, content, re.IGNORECASE) for p in iso_patterns)
        self.assertTrue(
            found,
            "Builder script does not reference .iso output"
        )

    def test_builder_script_has_checksum(self):
        content = _read_file(BUILDER_SCRIPT)
        self.assertIsNotNone(content, "Cannot read NexusOS-Builder.sh")
        self.assertIn(
            "sha256sum", content,
            "Builder script does not generate SHA256 checksums"
        )

    def test_builder_script_has_compression(self):
        content = _read_file(BUILDER_SCRIPT)
        self.assertIsNotNone(content, "Cannot read NexusOS-Builder.sh")
        compression_indicators = ["xz", "zstd", "gzip", "lz4", "lzma"]
        found = any(ind in content.lower() for ind in compression_indicators)
        self.assertTrue(
            found,
            "Builder script does not use compression for ISO"
        )

    def test_builder_script_validates_size(self):
        content = _read_file(BUILDER_SCRIPT)
        self.assertIsNotNone(content, "Cannot read NexusOS-Builder.sh")
        size_indicators = [
            r"stat.*size",
            r"du\s+-[sh]",
            r"ls.*-l",
            r"file.*size",
            r"500\s*[MmGg]",
            r"MIN_SIZE",
            r"min.*size",
        ]
        found = any(re.search(p, content, re.IGNORECASE) for p in size_indicators)
        self.assertTrue(
            found,
            "Builder script does not validate ISO size"
        )

    def test_manifest_has_required_fields(self):
        if not MANIFEST_FILE.exists():
            manifest_candidates = list(PROJ_ROOT.rglob("manifest.json"))
            if manifest_candidates:
                manifest_path = manifest_candidates[0]
            else:
                self.skipTest("manifest.json not found")
        else:
            manifest_path = MANIFEST_FILE
        content = _read_file(manifest_path)
        self.assertIsNotNone(content, "Cannot read manifest.json")
        try:
            manifest = json.loads(content)
        except json.JSONDecodeError as e:
            self.fail(f"manifest.json is not valid JSON: {e}")
        required_fields = [
            "latest_version",
            "download_url",
            "sha256",
            "incremental_patches",
        ]
        for field in required_fields:
            self.assertIn(
                field, manifest,
                f"manifest.json missing required field: {field}"
            )

    def test_ota_service_has_timer(self):
        timer_path = _find_file("nexusos-ota.timer")
        if timer_path is None:
            self.skipTest("nexusos-ota.timer not found")
        content = _read_file(timer_path)
        self.assertIsNotNone(content, "Cannot read nexusos-ota.timer")
        self.assertIn(
            "OnUnitActiveSec", content,
            "nexusos-ota.timer missing OnUnitActiveSec directive"
        )

    def test_gitignore_excludes_build(self):
        if not GITIGNORE_FILE.exists():
            self.skipTest(".gitignore not found")
        content = _read_file(GITIGNORE_FILE)
        self.assertIsNotNone(content, "Cannot read .gitignore")
        self.assertIn(
            "build/", content,
            ".gitignore does not exclude build/ directory"
        )
        iso_excluded = bool(
            re.search(r"\*\.iso", content)
            or re.search(r"\.iso$", content, re.MULTILINE)
        )
        self.assertTrue(
            iso_excluded,
            ".gitignore does not exclude *.iso files"
        )

    def test_license_is_proprietary(self):
        if not LICENSE_FILE.exists():
            self.skipTest("LICENSE file not found")
        content = _read_file(LICENSE_FILE)
        self.assertIsNotNone(content, "Cannot read LICENSE file")
        self.assertIn(
            "Proprietary", content,
            "LICENSE file does not contain 'Proprietary'"
        )
        self.assertIn(
            "ALL RIGHTS RESERVED", content.upper(),
            "LICENSE file does not contain 'ALL RIGHTS RESERVED'"
        )

    def test_release_pipeline_has_version_tagging(self):
        content = _read_file(PIPELINE_FILE)
        self.assertIsNotNone(content, "Cannot read release-pipeline.yml")
        tagging_indicators = [
            "tag_name",
            "release-tag",
            "git tag",
            "version_tag",
            "v${{",
        ]
        found = any(ind in content for ind in tagging_indicators)
        self.assertTrue(
            found,
            "Release pipeline does not tag releases with version"
        )

    def test_all_workflow_files_are_valid_yaml(self):
        if not WORKFLOWS_DIR.exists():
            self.skipTest("No .github/workflows directory found")
        workflow_files = list(WORKFLOWS_DIR.glob("*.yml"))
        workflow_files += list(WORKFLOWS_DIR.glob("*.yaml"))
        self.assertGreater(len(workflow_files), 0, "No workflow files found")
        for wf in workflow_files:
            content = _read_file(wf)
            if content is None:
                self.fail(f"Cannot read workflow file: {wf}")
            if content.strip().startswith("---"):
                pass
            self.assertIn(
                "on:", content,
                f"Workflow {wf.name} missing 'on:' trigger"
            )
            has_jobs = bool(re.search(r"^jobs:", content, re.MULTILINE))
            self.assertTrue(
                has_jobs,
                f"Workflow {wf.name} missing 'jobs:' section"
            )

    def test_builder_script_is_executable_format(self):
        self.assertTrue(
            BUILDER_SCRIPT.exists(),
            f"Builder script not found at {BUILDER_SCRIPT}"
        )
        content = _read_file(BUILDER_SCRIPT)
        self.assertIsNotNone(content, "Cannot read NexusOS-Builder.sh")
        self.assertTrue(
            content.strip().startswith("#!/"),
            "Builder script does not have a shebang line"
        )

    def test_release_pipeline_caching_enabled(self):
        content = _read_file(PIPELINE_FILE)
        self.assertIsNotNone(content, "Cannot read release-pipeline.yml")
        cache_indicators = [
            "actions/cache",
            "cache:",
            "pip cache",
            "npm cache",
            "yarn cache",
        ]
        found = any(ind in content for ind in cache_indicators)
        self.assertTrue(
            found,
            "Release pipeline does not use any caching mechanism"
        )

    def test_workflow_permissions_restricted(self):
        if not WORKFLOWS_DIR.exists():
            self.skipTest("No .github/workflows directory found")
        workflow_files = list(WORKFLOWS_DIR.glob("*.yml"))
        workflow_files += list(WORKFLOWS_DIR.glob("*.yaml"))
        for wf in workflow_files:
            content = _read_file(wf)
            if content is None:
                continue
            has_permissions = bool(
                re.search(r"^permissions:", content, re.MULTILINE)
            )
            self.assertTrue(
                has_permissions,
                f"Workflow {wf.name} does not declare explicit permissions"
            )

    def test_ota_service_wants_dependency(self):
        service_path = _find_file("nexusos-ota.service")
        if service_path is None:
            self.skipTest("nexusos-ota.service not found")
        content = _read_file(service_path)
        self.assertIsNotNone(content, "Cannot read nexusos-ota.service")
        self.assertIn(
            "[Install]", content,
            "nexusos-ota.service missing [Install] section"
        )
        has_wanted = bool(re.search(r"WantedBy\s*=", content))
        self.assertTrue(
            has_wanted,
            "nexusos-ota.service missing WantedBy in [Install] section"
        )


if __name__ == "__main__":
    unittest.main()
