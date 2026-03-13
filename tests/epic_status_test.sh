#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== epic-status test ==="

# Test 1: epic-status on _claw-dev — should return valid JSON with epics or epic
OUT=$(python3 scripts/claw.py epic-status projects/_claw-dev)
echo "All epics: $OUT"
echo "$OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'epics' in d or 'epic' in d" \
    || { echo "FAIL: epic-status should return valid JSON with epics"; exit 1; }

# Test 2: filter by epic 12
OUT=$(python3 scripts/claw.py epic-status projects/_claw-dev --epic 12)
echo "Epic 12: $OUT"
TOTAL=$(echo "$OUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['total'])")
[ "$TOTAL" -gt 0 ] || { echo "FAIL: epic 12 should have tasks, got $TOTAL"; exit 1; }

# Test 3: orchestrate accepts --scope epic:12 flag
python3 scripts/claw.py orchestrate --help | grep -q "scope" \
    || { echo "FAIL: orchestrate should have --scope flag"; exit 1; }

# Test 4: orchestrate --scope epic:NONEXISTENT stops immediately (no tasks = complete)
OUT=$(python3 scripts/claw.py orchestrate projects/_claw-dev --max-steps 1 --scope epic:NONEXISTENT 2>/dev/null || true)
echo "Nonexistent scope: $OUT"

echo "PASS: epic-status test"
