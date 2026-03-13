#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== workflow enforcement test ==="

# Test 1: workflow-validate on demo-project (should be valid)
OUT=$(python3 scripts/claw.py workflow-validate projects/demo-project)
echo "demo-project validate: $OUT"
STATUS=$(echo "$OUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[ "$STATUS" = "valid" ] || { echo "FAIL: demo-project contract should be valid, got: $STATUS"; exit 1; }

# Test 2: workflow-validate on a project without WORKFLOW.md returns no_contract (not error)
FAKE_PROJECT="$(mktemp -d)"
mkdir -p "$FAKE_PROJECT/state" "$FAKE_PROJECT/tasks" "$FAKE_PROJECT/docs"
echo "slug: fake-project" > "$FAKE_PROJECT/state/project.yaml"
cleanup() { rm -rf "$FAKE_PROJECT"; }
trap cleanup EXIT

OUT=$(python3 scripts/claw.py workflow-validate "$FAKE_PROJECT")
echo "No-contract result: $OUT"
STATUS=$(echo "$OUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[ "$STATUS" = "no_contract" ] || { echo "FAIL: missing WORKFLOW.md should return no_contract, got: $STATUS"; exit 1; }

# Test 3: launch-plan on a task in a project with edit_scope — check output is valid JSON
PLAN=$(python3 scripts/claw.py launch-plan projects/_claw-dev/tasks/TASK-006.md 2>/dev/null || true)
echo "launch-plan TASK-006 output length: ${#PLAN}"
[ -n "$PLAN" ] || { echo "FAIL: launch-plan returned empty output"; exit 1; }
echo "$PLAN" | python3 -c "import sys,json; json.load(sys.stdin)" || { echo "FAIL: launch-plan output is not valid JSON"; exit 1; }

echo "PASS: workflow enforcement test"
