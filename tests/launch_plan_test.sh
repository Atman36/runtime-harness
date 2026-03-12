#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-launch-plan-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}

trap cleanup EXIT

assert_contains() {
  local path="$1"
  local expected="$2"
  if ! grep -Fq -- "$expected" "$path"; then
    echo "FAIL: Expected '$expected' in $path" >&2
    cat "$path" >&2
    exit 1
  fi
}

assert_not_contains() {
  local path="$1"
  local unexpected="$2"
  if grep -Fq -- "$unexpected" "$path"; then
    echo "FAIL: Expected '$unexpected' NOT in $path" >&2
    cat "$path" >&2
    exit 1
  fi
}

# Set up workspace
mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
rm -rf "$workspace/projects/demo-project/runs" "$workspace/projects/demo-project/state/queue"
mkdir -p "$workspace/projects/demo-project/runs" \
  "$workspace/projects/demo-project/state/queue"/{pending,running,done,failed,awaiting_approval}

task_path="$workspace/projects/demo-project/tasks/TASK-001.md"
output_path="$tmp_root/launch_plan.json"

# --- Test 1: basic output ---
python3 "$workspace/scripts/claw.py" launch-plan "$task_path" > "$output_path"

# Verify all required top-level and nested fields are present
assert_contains "$output_path" '"task_path"'
assert_contains "$output_path" '"spec_path"'
assert_contains "$output_path" '"selected_agent"'
assert_contains "$output_path" '"selection_source"'
assert_contains "$output_path" '"routing_rule"'
assert_contains "$output_path" '"workspace_mode"'
assert_contains "$output_path" '"workspace_root"'
assert_contains "$output_path" '"workspace_materialization_required"'
assert_contains "$output_path" '"edit_scope"'
assert_contains "$output_path" '"parallel_safe"'
assert_contains "$output_path" '"concurrency_group"'
assert_contains "$output_path" '"command_preview"'

# Verify command_preview sub-fields
assert_contains "$output_path" '"command"'
assert_contains "$output_path" '"cwd"'
assert_contains "$output_path" '"prompt_mode"'
assert_contains "$output_path" '"timeout_seconds"'

# Verify task-specific values for TASK-001 (preferred_agent: codex in front matter)
assert_contains "$output_path" '"selected_agent": "codex"'
assert_contains "$output_path" '"selection_source": "task_front_matter"'
assert_contains "$output_path" '"workspace_materialization_required": false'
assert_contains "$output_path" '"prompt_mode": "arg"'
assert_contains "$output_path" '<prompt>'
assert_contains "$output_path" 'TASK-001'

# Verify output is valid JSON
python3 -c "import json, sys; data = json.load(open('$output_path')); assert 'command_preview' in data"

# --- Test 2: routing via rules (create a task with no preferred_agent) ---
routed_task="$workspace/projects/demo-project/tasks/TASK-ROUTED.md"
cat > "$routed_task" <<'TASKEOF'
---
id: TASK-ROUTED
title: "Test routing via rules"
status: todo
spec: ../specs/SPEC-001.md
preferred_agent: auto
review_policy: standard
priority: normal
project: demo-project
needs_review: false
risk_flags: []
tags:
  - implementation
---

# Task
Test routing via rules.
TASKEOF

routed_output="$tmp_root/launch_plan_routed.json"
python3 "$workspace/scripts/claw.py" launch-plan "$routed_task" > "$routed_output"

assert_contains "$routed_output" '"selection_source": "routing_rules"'
assert_contains "$routed_output" '"routing_rule"'
assert_contains "$routed_output" '"command_preview"'
python3 -c "import json; data = json.load(open('$routed_output')); assert data['routing']['selection_source'] == 'routing_rules'"

# --- Test 3: error on missing task path ---
if python3 "$workspace/scripts/claw.py" launch-plan "/nonexistent/path/task.md" \
    > "$tmp_root/err_out.txt" 2> "$tmp_root/err_err.txt"; then
  echo "FAIL: Expected launch-plan to fail with nonexistent task path" >&2
  exit 1
fi
assert_contains "$tmp_root/err_err.txt" "Task file not found"

# --- Test 4: output is machine-readable JSON (no extra human text) ---
# The stdout must parse as JSON with no leading/trailing non-JSON content
python3 - "$output_path" <<'EOF'
import json, sys
with open(sys.argv[1]) as f:
    text = f.read()
data = json.loads(text)  # must not raise
assert isinstance(data, dict), "output must be a JSON object"
# All required top-level fields
for field in ("task_path", "spec_path", "routing", "execution", "command_preview"):
    assert field in data, f"missing field: {field}"
# routing fields
for field in ("selected_agent", "selection_source", "routing_rule"):
    assert field in data["routing"], f"missing routing field: {field}"
# execution fields
for field in ("workspace_mode", "workspace_root", "workspace_materialization_required",
              "edit_scope", "parallel_safe", "concurrency_group"):
    assert field in data["execution"], f"missing execution field: {field}"
# command_preview fields
for field in ("command", "cwd", "prompt_mode", "timeout_seconds"):
    assert field in data["command_preview"], f"missing command_preview field: {field}"
EOF

echo "launch plan test: ok"
