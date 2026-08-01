#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ERRORS=0
WARNINGS=0

log_ok()   { echo -e "${GREEN}[PASS]${NC} $*"; }
log_fail() { echo -e "${RED}[FAIL]${NC} $*" >&2; ERRORS=$((ERRORS + 1)); }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*" >&2; WARNINGS=$((WARNINGS + 1)); }
log_info() { echo -e "[INFO] $*"; }

echo "=============================================="
echo "  Aion Pre-Flight Sanity Check"
echo "=============================================="
echo ""

# ── 1. Immutable Root Integrity ────────────────────────────────────────
echo "--- [1/5] Immutable Root Integrity ---"

IMMUTABLE_PATHS=("/usr" "/usr/bin" "/usr/lib" "/usr/share" "/boot")
SERVICES_DIR="$REPO_ROOT/core"

if [ -d "$SERVICES_DIR" ]; then
    for service_dir in "$SERVICES_DIR"/*/; do
        [ ! -d "$service_dir" ] && continue
        service_name=$(basename "$service_dir")

        for ext in sh py; do
            while IFS= read -r -d '' script; do
                violations=0

                while IFS= read -r line; do
                    linenum=$(echo "$line" | cut -d: -f1)
                    content=$(echo "$line" | cut -d: -f2-)

                    if echo "$content" | grep -qE '^\s*#'; then
                        continue
                    fi

                    for protected in "${IMMUTABLE_PATHS[@]}"; do
                        if echo "$content" | grep -qE "(tee|cp|mv|install|mkdir|touch|chmod|chown).*${protected}(/|$)"; then
                            log_fail "$service_name/$(basename "$script"):${linenum} writes to immutable path: $protected"
                            violations=$((violations + 1))
                        fi
                        if echo "$content" | grep -qE ">\s*${protected}/|>>\s*${protected}/"; then
                            log_fail "$service_name/$(basename "$script"):${linenum} redirects output to immutable path: $protected"
                            violations=$((violations + 1))
                        fi
                    done
                done < <(grep -rnE '(tee|cp|mv|install|mkdir|touch|chmod|chown|>\s*|>>\s*)' "$script" 2>/dev/null || true)

                if [ "$violations" -eq 0 ]; then
                    log_ok "$service_name/$(basename "$script"): immutable root respected"
                fi
            done < <(find "$service_dir" -name "*.$ext" -type f -print0 2>/dev/null)
        done
    done
else
    log_warn "No services directory found at $SERVICES_DIR"
fi

echo ""

# ── 2. JSON Configuration Validation ──────────────────────────────────
echo "--- [2/5] JSON Configuration Validation ---"

JSON_FILES=(
    "$REPO_ROOT/config/aion-config.json"
    "$REPO_ROOT/deploy/ota/manifest.json"
)

for json_file in "${JSON_FILES[@]}"; do
    if [ ! -f "$json_file" ]; then
        log_warn "JSON file not found: $json_file"
        continue
    fi

    rel_path="${json_file#$REPO_ROOT/}"

    if python3 -m json.tool "$json_file" > /dev/null 2>&1; then
        log_ok "$rel_path: valid JSON"
    else
        log_fail "$rel_path: invalid JSON syntax"
    fi

    python3 -c "
import json, sys
with open('$json_file') as f:
    data = json.load(f)
    if not isinstance(data, dict):
        print('Root is not a dictionary', file=sys.stderr)
        sys.exit(1)
    if len(data) == 0:
        print('JSON is empty', file=sys.stderr)
        sys.exit(1)
    print('Structure OK')
" 2>/dev/null && log_ok "$rel_path: valid structure" || log_fail "$rel_path: invalid structure"
done

echo ""

# ── 3. systemd-boot Configuration ─────────────────────────────────────
echo "--- [3/5] systemd-boot Configuration ---"

LOADER_CONF="$REPO_ROOT/config/loader.conf"
ENTRIES_DIR="$REPO_ROOT/config/loader/entries"

if [ -f "$LOADER_CONF" ]; then
    log_ok "loader.conf found"

    if grep -q "^default.*aion-" "$LOADER_CONF" 2>/dev/null; then
        log_ok "loader.conf: default entry points to Aion"
    else
        log_fail "loader.conf: missing Aion default entry"
    fi

    if grep -q "^timeout" "$LOADER_CONF" 2>/dev/null; then
        log_ok "loader.conf: timeout configured"
    else
        log_warn "loader.conf: no timeout set (will use firmware default)"
    fi
else
    log_warn "loader.conf not found (will use defaults from 01-base-system.sh)"
fi

if [ -d "$ENTRIES_DIR" ]; then
    entry_count=$(find "$ENTRIES_DIR" -name "*.conf" -type f 2>/dev/null | wc -l)
    if [ "$entry_count" -ge 2 ]; then
        log_ok "BLS entries: $entry_count boot entries found (A/B slots)"
    elif [ "$entry_count" -eq 1 ]; then
        log_warn "BLS entries: only 1 entry found (expected 2 for A/B)"
    else
        log_fail "BLS entries: no boot entries found"
    fi
else
    log_warn "BLS entries directory not found (will be created during build)"
fi

echo ""

# ── 4. OTA Updater Boot Counter Failsafe ──────────────────────────────
echo "--- [4/5] OTA Boot Counter Failsafe ---"

OTA_FILE="$REPO_ROOT/deploy/ota/ota-updater.py"

if [ -f "$OTA_FILE" ]; then
    rel_path="${OTA_FILE#$REPO_ROOT/}"

    if grep -q "mark-good" "$OTA_FILE"; then
        log_ok "$rel_path: mark-good (boot counter reset) present"
    else
        log_fail "$rel_path: missing mark-good for boot counter reset"
    fi

    if grep -q "AB_MANAGER" "$OTA_FILE"; then
        log_ok "$rel_path: A/B manager integration present"
    else
        log_warn "$rel_path: no A/B manager reference (manual BLS fallback only)"
    fi

    if grep -q "rollback" "$OTA_FILE"; then
        log_ok "$rel_path: rollback functionality present"
    else
        log_fail "$rel_path: missing rollback functionality"
    fi

    if grep -q "systemd-bless-boot" "$REPO_ROOT/build/scripts/01-base-system.sh" 2>/dev/null; then
        log_ok "01-base-system.sh: systemd-bless-boot configured for auto-rollback"
    else
        log_warn "01-base-system.sh: systemd-bless-boot not found (check auto-rollback setup)"
    fi

    if grep -q "set_boot_slot\|set.*boot.*slot\|switch.*slot" "$OTA_FILE"; then
        log_ok "$rel_path: slot switching implemented"
    else
        log_fail "$rel_path: missing slot switching logic"
    fi
else
    log_fail "OTA updater not found: $OTA_FILE"
fi

echo ""

# ── 5. Build Script Dependencies ──────────────────────────────────────
echo "--- [5/5] Build Script Dependencies ---"

BUILD_SCRIPTS=(
    "$REPO_ROOT/build/scripts/01-base-system.sh"
    "$REPO_ROOT/build/scripts/02-gaming-kernel.sh"
    "$REPO_ROOT/build/scripts/03-gaming-stack.sh"
    "$REPO_ROOT/build/scripts/04-desktop-environment.sh"
    "$REPO_ROOT/build/scripts/05-update-system.sh"
    "$REPO_ROOT/build/06-preflight-sanity.sh"
)

for script in "${BUILD_SCRIPTS[@]}"; do
    if [ ! -f "$script" ]; then
        log_fail "Build script missing: ${script#$REPO_ROOT/}"
        continue
    fi

    if [ ! -x "$script" ]; then
        log_warn "Build script not executable: ${script#$REPO_ROOT/}"
    fi

    if bash -n "$script" 2>/dev/null; then
        log_ok "${script#$REPO_ROOT/}: syntax valid"
    else
        log_fail "${script#$REPO_ROOT/}: syntax error"
    fi
done

echo ""

# ── Summary ───────────────────────────────────────────────────────────
echo "=============================================="
if [ "$ERRORS" -gt 0 ]; then
    echo -e "${RED}FAILED${NC}: $ERRORS error(s), $WARNINGS warning(s)"
    echo "Fix all errors before deploying."
    exit 1
elif [ "$WARNINGS" -gt 0 ]; then
    echo -e "${YELLOW}PASSED with warnings${NC}: $WARNINGS warning(s)"
    echo "Review warnings before deploying."
    exit 0
else
    echo -e "${GREEN}ALL CHECKS PASSED${NC}"
    echo "System is ready for deployment."
    exit 0
fi
