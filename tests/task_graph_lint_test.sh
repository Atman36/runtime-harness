#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-task-graph-lint-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

pass() {
  echo "ok: $1"
}

mkdir -p "$workspace"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp -R "$repo_root/scripts" "$workspace/scripts"

bash "$workspace/scripts/create_project.sh" lint-test-project "$workspace"
project_root="$workspace/projects/lint-test-project"
tasks_dir="$project_root/tasks"
specs_dir="$project_root/specs"
mkdir -p "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter}

cat > "$tasks_dir/TASK-001.md" <<'EOF'
---
id: TASK-001
title: "Implement alpha"
status: todo
spec: ../specs/SPEC-001.md
preferred_agent: auto
review_policy: standard
priority: high
project: lint-test-project
needs_review: false
risk_flags: []
dependencies: []
---

# Task
EOF

cat > "$specs_dir/SPEC-001.md" <<'EOF'
# SPEC-001

Touch `scripts/shared.py` and `docs/alpha.md`.
EOF

cat > "$tasks_dir/TASK-002.md" <<'EOF'
---
id: TASK-002
title: "Implement beta"
status: todo
spec: ../specs/SPEC-002.md
preferred_agent: auto
review_policy: standard
priority: medium
project: lint-test-project
needs_review: false
risk_flags: []
dependencies: []
---

# Task
EOF

cat > "$specs_dir/SPEC-002.md" <<'EOF'
# SPEC-002

Touch `scripts/shared.py` and `docs/beta.md`.
EOF

cat > "$tasks_dir/TASK-003.md" <<'EOF'
---
id: TASK-003
title: "Independent task"
status: todo
spec: ../specs/SPEC-003.md
preferred_agent: auto
review_policy: standard
priority: low
project: lint-test-project
needs_review: false
risk_flags: []
dependencies: []
---

# Task
EOF

cat > "$specs_dir/SPEC-003.md" <<'EOF'
# SPEC-003

Touch `scripts/independent.py`.
EOF

lint_out_file="$tmp_root/task_graph_lint.json"
dashboard_out_file="$tmp_root/dashboard.json"
orchestrate_out_file="$tmp_root/orchestrate-stderr.json"
task_lint_out_file="$tmp_root/task_lint.json"

# Test 1: task-graph-lint includes warning_count and blocking_count
set +e
(cd "$workspace" && python3 scripts/claw.py task-graph-lint "$project_root") > "$lint_out_file" 2>/dev/null
exit_code=$?
set -e
[ "$exit_code" -eq 0 ] || fail "Test 1: expected exit code 0 with overlap warnings, got $exit_code"

python3 - "$lint_out_file" <<'PY' || fail "Test 1: task-graph-lint payload missing expected counters"
import json
import sys

data = json.loads(open(sys.argv[1]).read())
assert "blocking_count" in data, data
assert "warning_count" in data, data
assert data["blocking_count"] == 0, data
assert data["warning_count"] >= 1, data
codes = [issue["code"] for issue in data["issues"]]
assert "file_overlap" in codes, codes
print("ok")
PY
pass "task-graph-lint reports overlap warnings without blocking"

# Test 2: task-lint stays backward compatible and ignores overlap warnings
set +e
(cd "$workspace" && python3 scripts/claw.py task-lint "$project_root") > "$task_lint_out_file" 2>/dev/null
exit_code=$?
set -e
[ "$exit_code" -eq 0 ] || fail "Test 2: task-lint should still exit 0 on overlap-only project, got $exit_code"

python3 - "$task_lint_out_file" <<'PY' || fail "Test 2: task-lint output changed unexpectedly"
import json
import sys

data = json.loads(open(sys.argv[1]).read())
codes = [issue["code"] for issue in data["issues"]]
assert "file_overlap" not in codes, codes
print("ok")
PY
pass "task-lint remains backward compatible"

# Test 3: dashboard ready_tasks does not propose overlapping tasks together
(cd "$workspace" && python3 scripts/claw.py dashboard "$project_root" --ready-limit 3) > "$dashboard_out_file"

python3 - "$dashboard_out_file" <<'PY' || fail "Test 3: overlapping tasks were offered together"
import json
import sys

data = json.loads(open(sys.argv[1]).read())
project = data["projects"][0]
ready_ids = [task["task_id"] for task in project["ready_tasks"]]
assert "TASK-003" in ready_ids, ready_ids
assert not ({"TASK-001", "TASK-002"} <= set(ready_ids)), ready_ids
print("ok")
PY
pass "dashboard filters overlapping ready tasks from parallel suggestions"

# Test 4: running task also blocks overlapping task from ready list
python3 - "$tasks_dir/TASK-001.md" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
path.write_text(text.replace("status: todo", "status: in_progress", 1), encoding="utf-8")
PY

(cd "$workspace" && python3 scripts/claw.py dashboard "$project_root" --ready-limit 3) > "$dashboard_out_file"

python3 - "$dashboard_out_file" <<'PY' || fail "Test 4: overlap with running task was not filtered"
import json
import sys

data = json.loads(open(sys.argv[1]).read())
project = data["projects"][0]
ready_ids = [task["task_id"] for task in project["ready_tasks"]]
assert "TASK-002" not in ready_ids, ready_ids
assert "TASK-003" in ready_ids, ready_ids
print("ok")
PY
pass "dashboard excludes tasks overlapping with running work"

# Test 5: orchestrate aborts on unknown_dependency with reason_code
python3 - "$tasks_dir/TASK-001.md" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
path.write_text(text.replace("status: in_progress", "status: todo", 1), encoding="utf-8")
PY

cat > "$tasks_dir/TASK-004.md" <<'EOF'
---
id: TASK-004
title: "Broken dependency"
status: todo
spec: ../specs/SPEC-004.md
preferred_agent: auto
review_policy: standard
priority: medium
project: lint-test-project
needs_review: false
risk_flags: []
dependencies: [TASK-999]
---

# Task
EOF

cat > "$specs_dir/SPEC-004.md" <<'EOF'
# SPEC-004

Touch `scripts/broken_dep.py`.
EOF

set +e
(cd "$workspace" && python3 scripts/claw.py orchestrate "$project_root" --max-steps 1) > /dev/null 2> "$orchestrate_out_file"
exit_code=$?
set -e
[ "$exit_code" -eq 1 ] || fail "Test 5: orchestrate should fail on unknown dependency, got $exit_code"

python3 - "$orchestrate_out_file" <<'PY' || fail "Test 5: orchestrate did not emit unknown_dependency envelope"
import json
import sys

data = json.loads(open(sys.argv[1]).read())
assert data["code"] == "unknown_dependency", data
print("ok")
PY
pass "orchestrate aborts with reason_code unknown_dependency"

echo ""
echo "task_graph_lint_test: all tests passed"
