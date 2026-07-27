#!/usr/bin/env bash
# NexusOS Test Runner — executes the Python test suite and prints a summary.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NEXUSOS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "============================================"
echo "  NexusOS Test Suite"
echo "============================================"
echo ""
echo "Root: $NEXUSOS_ROOT"
echo ""

cd "$NEXUSOS_ROOT"

python3 "$SCRIPT_DIR/test-nexusos.py"
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
