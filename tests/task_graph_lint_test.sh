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
mkdir -p "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter}

lint_out_file="$tmp_root/lint_out.json"

# ── Test 1: task-lint emits task_parse_failed for malformed YAML ──────────────
cat > "$tasks_dir/TASK-MALFORMED.md" <<'EOF'
---
id: TASK-MALFORMED
title: broken task
status: todo
dependencies: [
  unclosed bracket
---
EOF

(cd "$workspace" && python3 scripts/claw.py task-lint "$project_root") > "$lint_out_file" 2>/dev/null || true

python3 - "$lint_out_file" <<'PY' || fail "Test 1: task_parse_failed not emitted"
import sys, json
data = json.loads(open(sys.argv[1]).read())
codes = [i["code"] for i in data["issues"]]
assert "task_parse_failed" in codes, f"Expected task_parse_failed in {codes}"
parse_issues = [i for i in data["issues"] if i["code"] == "task_parse_failed"]
assert parse_issues[0]["task_id"] == "TASK-MALFORMED", f"wrong task_id: {parse_issues[0]}"
print("ok")
PY
pass "task-lint emits task_parse_failed for malformed YAML"

# ── Test 2: task-lint returns JSON (not a Python traceback) ───────────────────
python3 - "$lint_out_file" <<'PY' || fail "Test 2: output is not valid JSON"
import sys, json
data = json.loads(open(sys.argv[1]).read())
assert "issues" in data
assert "issue_count" in data
print("ok")
PY
pass "task-lint output is valid JSON (no traceback)"

# ── Test 3: task-lint exit code is 1 when issues exist ───────────────────────
set +e
(cd "$workspace" && python3 scripts/claw.py task-lint "$project_root") >/dev/null 2>/dev/null
exit_code=$?
set -e
[ "$exit_code" -eq 1 ] || fail "Test 3: expected exit code 1 with issues, got $exit_code"
pass "task-lint exits 1 when issues present"

# ── Test 4: task-lint exits 0 when no issues ─────────────────────────────────
rm -f "$tasks_dir/TASK-MALFORMED.md"
cat > "$tasks_dir/TASK-VALID.md" <<'EOF'
---
id: TASK-VALID
title: "Valid task"
status: todo
preferred_agent: auto
review_policy: standard
priority: low
project: lint-test-project
needs_review: false
risk_flags: []
dependencies: []
---

# Valid task
EOF

set +e
(cd "$workspace" && python3 scripts/claw.py task-lint "$project_root") >/dev/null 2>/dev/null
exit_code=$?
set -e
[ "$exit_code" -eq 0 ] || fail "Test 4: expected exit code 0 with no issues, got $exit_code"
pass "task-lint exits 0 when no issues"

# ── Test 5: task-lint detects unknown dependency ─────────────────────────────
cat > "$tasks_dir/TASK-BROKEN-DEP.md" <<'EOF'
---
id: TASK-BROKEN-DEP
title: "Task with bad dep"
status: todo
preferred_agent: auto
dependencies: [TASK-NONEXISTENT]
---
EOF

(cd "$workspace" && python3 scripts/claw.py task-lint "$project_root") > "$lint_out_file" 2>/dev/null || true

python3 - "$lint_out_file" <<'PY' || fail "Test 5: unknown_dependency not detected"
import sys, json
data = json.loads(open(sys.argv[1]).read())
codes = [i["code"] for i in data["issues"]]
assert "unknown_dependency" in codes, f"Expected unknown_dependency in {codes}"
print("ok")
PY
pass "task-lint detects unknown_dependency"

echo ""
echo "task_graph_lint_test: all tests passed"
