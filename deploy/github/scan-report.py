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
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
    print("Manifest updated: {}".format(args[0]))


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: scan-report.py <bandit|shellcheck|manifest> ...")
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
    print("unknown report kind: {}".format(kind))
    return 2


if __name__ == "__main__":
    sys.exit(main())
