#!/usr/bin/env bash
# =============================================================================
# Aion CI/CD Path Fix Script
# Copies workflow files from deploy/github/ to .github/workflows/
# which is where GitHub Actions expects them.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SOURCE_DIR="$SCRIPT_DIR/github"
TARGET_DIR="$PROJECT_ROOT/.github/workflows"

echo "============================================"
echo "  Aion CI/CD Path Fix"
echo "============================================"
echo ""
echo "Source: $SOURCE_DIR"
echo "Target: $TARGET_DIR"
echo ""

mkdir -p "$TARGET_DIR"

for workflow in release-pipeline.yml pages-deploy.yml; do
    src="$SOURCE_DIR/$workflow"
    dst="$TARGET_DIR/$workflow"
    if [ ! -f "$src" ]; then
        echo "[SKIP] $workflow not found at $src"
        continue
    fi
    cp "$src" "$dst"
    echo "[OK]   Copied $workflow"
done

echo ""
echo "============================================"
echo "  Done!"
echo "============================================"
echo ""
echo "Workflows are now at:"
echo "  .github/workflows/release-pipeline.yml"
echo "  .github/workflows/pages-deploy.yml"
echo ""
echo "Next steps:"
echo "  1. git add .github/workflows/"
echo "  2. git commit -m 'ci: fix workflow paths for GitHub Actions'"
echo "  3. git push"
echo ""
echo "GitHub Actions requires workflows in .github/workflows/."
echo "The files in deploy/github/ are kept as source-of-truth copies."
