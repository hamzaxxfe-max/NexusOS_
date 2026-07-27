#!/usr/bin/env bash
# NexusOS Regression Test Runner
# Runs all 4 regression test suites, captures results, prints summary.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

declare -a SUITE_NAMES=()
declare -a SUITE_EXIT_CODES=()
declare -a SUITE_OUTPUTS=()
declare -a SUITE_FILES=(
    "test-ui-alignment.py"
    "test-memory-stress.py"
    "test-security-rollback.py"
    "test-cicd-validation.py"
)

declare -a SUITE_LABELS=(
    "UI Alignment"
    "Memory Stress"
    "Security Rollback"
    "CI/CD Validation"
)

TOTAL_PASS=0
TOTAL_FAIL=0
TOTAL_SKIP=0
TOTAL_ERROR=0
ALL_PASSED=true

run_suite() {
    local label="$1"
    local file="$2"
    local full_path="${SCRIPT_DIR}/${file}"

    echo -e "\n${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
    echo -e "${CYAN}${BOLD}  Running: ${label}${RESET}"
    echo -e "${CYAN}${BOLD}  File:    ${file}${RESET}"
    echo -e "${CYAN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}\n"

    if [ ! -f "$full_path" ]; then
        echo -e "${RED}  ERROR: Test file not found: ${full_path}${RESET}"
        SUITE_NAMES+=("$label")
        SUITE_EXIT_CODES+=(99)
        SUITE_OUTPUTS+=("File not found")
        ALL_PASSED=false
        return
    fi

    local tmp_output
    tmp_output=$(mktemp)

    python -m pytest "$full_path" -v --tb=short 2>&1 | tee "$tmp_output"
    local exit_code=${PIPESTATUS[0]}

    SUITE_NAMES+=("$label")
    SUITE_EXIT_CODES+=($exit_code)

    local passed failed skipped
    passed=$(grep -oP 'passed=\K\d+' "$tmp_output" 2>/dev/null || echo "0")
    failed=$(grep -oP 'failed=\K\d+' "$tmp_output" 2>/dev/null || echo "0")
    skipped=$(grep -oP 'skipped=\K\d+' "$tmp_output" 2>/dev/null || echo "0")
    errors=$(grep -oP 'error=\K\d+' "$tmp_output" 2>/dev/null || echo "0")

    TOTAL_PASS=$((TOTAL_PASS + ${passed:-0}))
    TOTAL_FAIL=$((TOTAL_FAIL + ${failed:-0}))
    TOTAL_SKIP=$((TOTAL_SKIP + ${skipped:-0}))
    TOTAL_ERROR=$((TOTAL_ERROR + ${errors:-0}))

    if [ $exit_code -ne 0 ]; then
        ALL_PASSED=false
        SUITE_OUTPUTS+=("FAIL (exit ${exit_code})")
    else
        SUITE_OUTPUTS+=("PASS (${passed} passed, ${skipped} skipped)")
    fi

    rm -f "$tmp_output"
}

print_summary() {
    echo ""
    echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}${CYAN}║            NexusOS Regression Test Summary                  ║${RESET}"
    echo -e "${BOLD}${CYAN}╠══════════════════════════════════════════════════════════════╣${RESET}"
    printf "${BOLD}${CYAN}║${RESET} %-25s │ %-30s ${BOLD}${CYAN}║${RESET}\n" "Suite" "Result"
    echo -e "${BOLD}${CYAN}╠══════════════════════════════════════════════════════════════╣${RESET}"

    local i
    for i in "${!SUITE_NAMES[@]}"; do
        local name="${SUITE_NAMES[$i]}"
        local result="${SUITE_OUTPUTS[$i]}"
        local code="${SUITE_EXIT_CODES[$i]}"

        local color="$GREEN"
        if [ "$code" -ne 0 ]; then
            color="$RED"
        fi

        printf "${BOLD}${CYAN}║${RESET} ${color}%-25s${RESET} │ ${color}%-30s${RESET} ${BOLD}${CYAN}║${RESET}\n" \
            "$name" "$result"
    done

    echo -e "${BOLD}${CYAN}╠══════════════════════════════════════════════════════════════╣${RESET}"
    printf "${BOLD}${CYAN}║${RESET} ${BOLD}Total:${RESET} %-4s passed  %-4s failed  %-4s skipped  %-4s errors${BOLD}${CYAN}║${RESET}\n" \
        "$TOTAL_PASS" "$TOTAL_FAIL" "$TOTAL_SKIP" "$TOTAL_ERROR"
    echo -e "${BOLD}${CYAN}╠══════════════════════════════════════════════════════════════╣${RESET}"

    if [ "$ALL_PASSED" = true ]; then
        echo -e "${BOLD}${CYAN}║${RESET}  ${GREEN}${BOLD}ALL SUITES PASSED${RESET}                                      ${BOLD}${CYAN}║${RESET}"
    else
        echo -e "${BOLD}${CYAN}║${RESET}  ${RED}${BOLD}SOME SUITES FAILED${RESET}                                      ${BOLD}${CYAN}║${RESET}"
    fi

    echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${RESET}"
    echo ""
}

main() {
    echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${BOLD}${CYAN}║         NexusOS Comprehensive Regression Test Suite         ║${RESET}"
    echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${RESET}"
    echo ""
    echo "  Project root: ${PROJ_ROOT}"
    echo "  Test dir:     ${SCRIPT_DIR}"
    echo "  Date:         $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
    echo ""

    local i
    for i in "${!SUITE_FILES[@]}"; do
        run_suite "${SUITE_LABELS[$i]}" "${SUITE_FILES[$i]}"
    done

    print_summary

    if [ "$ALL_PASSED" = false ]; then
        exit 1
    fi
    exit 0
}

main "$@"
