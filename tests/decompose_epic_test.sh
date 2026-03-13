#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== decompose-epic test (dry-run only — no LLM call) ==="

# Test 1: missing input file → error
OUT=$(python3 scripts/claw.py decompose-epic --project projects/demo-project --input /nonexistent/roadmap.md 2>&1 || true)
echo "Missing input result: $OUT"
echo "$OUT" | grep -q "error\|not found" || { echo "FAIL: should error on missing file"; exit 1; }
echo "Test 1 PASS: missing input file returns error"

# Test 2: command help works
python3 scripts/claw.py decompose-epic --help > /dev/null
echo "Test 2 PASS: command exists and --help works"

# Test 3: decomposer.py is importable
python3 -c "from _system.engine.decomposer import decompose_epic, _validate_tasks, _extract_json; print('import OK')"
echo "Test 3 PASS: decomposer.py is importable"

# Test 4: _validate_tasks rejects duplicate IDs
python3 - <<'EOF'
from _system.engine.decomposer import _validate_tasks
tasks = [
    {"id": "TASK-1", "dependencies": []},
    {"id": "TASK-1", "dependencies": []},
]
errors = _validate_tasks(tasks, set())
assert any("Duplicate" in e for e in errors), f"Expected duplicate error, got: {errors}"
print("Test 4 PASS: duplicate ID detected")
EOF

# Test 5: _validate_tasks rejects unknown dependency
python3 - <<'EOF'
from _system.engine.decomposer import _validate_tasks
tasks = [
    {"id": "TASK-1", "dependencies": ["TASK-99"]},
]
errors = _validate_tasks(tasks, set())
assert any("unknown dependency" in e for e in errors), f"Expected unknown dep error, got: {errors}"
print("Test 5 PASS: unknown dependency detected")
EOF

# Test 6: _validate_tasks rejects cycles
python3 - <<'EOF'
from _system.engine.decomposer import _validate_tasks
tasks = [
    {"id": "TASK-1", "dependencies": ["TASK-2"]},
    {"id": "TASK-2", "dependencies": ["TASK-1"]},
]
errors = _validate_tasks(tasks, set())
assert any("cycle" in e.lower() for e in errors), f"Expected cycle error, got: {errors}"
print("Test 6 PASS: cycle detected")
EOF

# Test 7: _validate_tasks accepts valid tasks
python3 - <<'EOF'
from _system.engine.decomposer import _validate_tasks
tasks = [
    {"id": "TASK-1", "dependencies": []},
    {"id": "TASK-2", "dependencies": ["TASK-1"]},
]
errors = _validate_tasks(tasks, set())
assert errors == [], f"Expected no errors, got: {errors}"
print("Test 7 PASS: valid tasks accepted")
EOF

# Test 8: _extract_json handles direct JSON and wrapped JSON
python3 - <<'EOF'
from _system.engine.decomposer import _extract_json
import json

# Direct JSON
result = _extract_json('[{"id": "TASK-1"}]')
assert result == [{"id": "TASK-1"}], f"Direct JSON failed: {result}"

# Wrapped in prose
wrapped = 'Here is the plan:\n[{"id": "TASK-1"}]\nDone.'
result = _extract_json(wrapped)
assert result == [{"id": "TASK-1"}], f"Wrapped JSON failed: {result}"
print("Test 8 PASS: _extract_json handles direct and wrapped JSON")
EOF

echo "PASS: decompose-epic all tests"
