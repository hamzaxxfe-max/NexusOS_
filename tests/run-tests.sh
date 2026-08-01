#!/usr/bin/env bash
# Aion Test Runner — executes the Python test suite and prints a summary.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AION_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "============================================"
echo "  Aion Test Suite"
echo "============================================"
echo ""
echo "Root: $AION_ROOT"
echo ""

cd "$AION_ROOT"

python3 "$SCRIPT_DIR/test-aion.py"
EXIT_CODE=$?

echo ""
echo "============================================"
if [ "$EXIT_CODE" -eq 0 ]; then
    echo "  ALL TESTS PASSED"
else
    echo "  SOME TESTS FAILED (exit $EXIT_CODE)"
fi
echo "============================================"

exit "$EXIT_CODE"
