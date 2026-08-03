#!/usr/bin/env python3
"""Render a Markdown findings table for a bandit/shellcheck JSON report.

Usage:
    python3 scan-report.py bandit <report.json>
    python3 scan-report.py shellcheck <report.json>
    python3 scan-report.py manifest <version> <url> <sha256> <release_date> <size> <zst_url> <zst_sha256> <zst_size> <signature_url>
"""

import json
import sys


def bandit_table(path: str) -> int:
    with open(path) as f:
        data = json.load(f)
    issues = data.get("results", [])
    print("| Severity | Confidence | Location | Issue |")
    print("|----------|------------|----------|-------|")
    for i in issues:
        print(
            "| {sev} | {conf} | {loc} | {test} |".format(
                sev=i.get("issue_severity", ""),
                conf=i.get("issue_confidence", ""),
                loc="{}:{}".format(i.get("filename", ""), i.get("line_number", "")),
                test=i.get("test_name", ""),
            )
        )
    print()
    print("Total findings: {}".format(len(issues)))
    return len(issues)


def shellcheck_table(path: str) -> int:
    with open(path) as f:
        data = json.load(f)
    print("| Severity | File:Line | Code |")
    print("|----------|-----------|------|")
    total = 0
    for entry in data:
        for c in entry.get("comments", []):
            print(
                "| {sev} | {loc} | {code} |".format(
                    sev=c.get("level", ""),
                    loc="{}:{}".format(entry.get("file", ""), c.get("line", "")),
                    code=c.get("code", ""),
                )
            )
            total += 1
    print()
    print("Total shellcheck findings: {}".format(total))
    return total


def _repo_from_url(url: str) -> str:
    """Extract 'owner/repo' from a GitHub URL, or '' if not a GitHub URL."""
    import re

    m = re.search(r"github\.com/([^/]+/[^/]+)/", url)
    return m.group(1) if m else ""


def update_manifest(args) -> None:
    path = "deploy/ota/manifest.json"
    with open(path) as f:
        manifest = json.load(f)
    manifest["latest_version"] = args[0]
    manifest["download_url"] = args[1]
    manifest["sha256"] = args[2]
    manifest["release_date"] = args[3]
    manifest["size"] = int(args[4]) if args[4].isdigit() else manifest.get("size", 0)
    manifest["zst_url"] = args[5]
    manifest["zst_sha256"] = args[6]
    manifest["zst_size"] = int(args[7]) if args[7].isdigit() else 0
    manifest["signature_url"] = args[8]

    repo = _repo_from_url(args[1])
    if repo:
        branch = args[3][: args[3].find("T")] if "T" in args[3] else ""
        old = manifest.get("manifest_signature_url", "")
        if "username/aion" in old or not old:
            manifest["manifest_signature_url"] = (
                "https://raw.githubusercontent.com/{repo}/master/deploy/ota/manifest.json.sig".format(repo=repo)
            )
        for patch in manifest.get("incremental_patches", []):
            patch_url = patch.get("patch_url", "")
            if "username/aion" in patch_url or not patch_url:
                tag = patch.get("to_version", "")
                patch["patch_url"] = (
                    "https://github.com/{repo}/releases/download/v{tag}/patch-{frm}-to-{to}.xdelta".format(
                        repo=repo,
                        tag=tag,
                        frm=patch.get("from_version", ""),
                        to=tag,
                    )
                )

    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print("Manifest updated: {}".format(args[0]))


def check_html(path: str) -> int:
    """Validate docs/index.html has required structural tags."""
    with open(path, encoding="utf-8") as f:
        html = f.read().lower()
    errors = []
    for tag in ("<html", "<head", "<body", "<title"):
        if tag not in html:
            errors.append("Missing {} tag".format(tag))
    if errors:
        for e in errors:
            print("Error: {}".format(e))
        return 1
    print("HTML validation passed.")
    return 0


def inject_manifest_version(path: str) -> int:
    """Replace the <!--MANIFEST_VERSION--> placeholder in docs/index.html."""
    manifest_path = "deploy/ota/manifest.json"
    with open(manifest_path) as f:
        version = json.load(f).get("latest_version", "0.0.0")
    with open(path, encoding="utf-8") as f:
        html = f.read()
    html = html.replace("<!--MANIFEST_VERSION-->", version)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    print("Injected manifest version: {}".format(version))
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: scan-report.py <bandit|shellcheck|manifest|htmlcheck|inject> ...")
        return 2
    kind = sys.argv[1]
    if kind == "bandit":
        if len(sys.argv) != 3:
            return 2
        return bandit_table(sys.argv[2])
    if kind == "shellcheck":
        if len(sys.argv) != 3:
            return 2
        return shellcheck_table(sys.argv[2])
    if kind == "manifest":
        if len(sys.argv) != 11:
            print("usage: scan-report.py manifest <version> <url> <sha256> <release_date> <size> <zst_url> <zst_sha256> <zst_size> <signature_url>")
            return 2
        update_manifest(sys.argv[2:])
        return 0
    if kind == "htmlcheck":
        if len(sys.argv) != 3:
            return 2
        return check_html(sys.argv[2])
    if kind == "inject":
        if len(sys.argv) != 3:
            return 2
        return inject_manifest_version(sys.argv[2])
    print("unknown report kind: {}".format(kind))
    return 2


if __name__ == "__main__":
    sys.exit(main())
