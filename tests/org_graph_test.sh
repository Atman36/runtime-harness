#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-org-graph-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}

trap cleanup EXIT

assert_file() {
  local path="$1"
  if [ ! -f "$path" ]; then
    echo "Expected file to exist: $path" >&2
    exit 1
  fi
}

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/dispatch_hooks.py" "$workspace/scripts/dispatch_hooks.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/reconcile_hooks.py" "$workspace/scripts/reconcile_hooks.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"

project_root="$workspace/projects/demo-project"
project_override="$project_root/docs/ORG_GRAPH.yaml"

org_out="$tmp_root/org-graph.json"
python3 "$workspace/scripts/claw.py" org-graph "$project_root" > "$org_out"

delegate_out="$tmp_root/delegate.json"
python3 "$workspace/scripts/claw.py" task-delegate "$project_root" \
  --task-id TASK-001 \
  --agent claude \
  --assignee codex \
  --reason "needs implementation" \
  --title "Delegated implementation" > "$delegate_out"

forbidden_out="$tmp_root/delegate-forbidden.json"
set +e
python3 "$workspace/scripts/claw.py" task-delegate "$project_root" \
  --task-id TASK-001 \
  --agent codex \
  --assignee claude \
  --reason "cross team" > "$forbidden_out"
forbidden_code=$?
set -e
if [ "$forbidden_code" -eq 0 ]; then
  echo "Expected forbidden delegation to fail" >&2
  exit 1
fi

python3 "$workspace/scripts/claw.py" task-claim "$project_root" \
  --task-id TASK-002 \
  --agent codex \
  --reason "blocked" >/dev/null

python3 "$workspace/scripts/claw.py" task-release "$project_root" \
  --task-id TASK-002 \
  --agent codex \
  --status blocked \
  --reason "needs input" >/dev/null

escalate_out="$tmp_root/escalate.json"
python3 "$workspace/scripts/claw.py" task-escalate "$project_root" \
  --task-id TASK-002 \
  --agent codex \
  --reason "blocked on decision" > "$escalate_out"

cat > "$project_override" <<'YAML'
org_graph:
  agents:
    codex:
      can_delegate: true
      delegates_to:
        - codex
  delegation:
    allow_self_delegate: true
YAML

override_out="$tmp_root/org-graph-override.json"
python3 "$workspace/scripts/claw.py" org-graph "$project_root" > "$override_out"

self_delegate_out="$tmp_root/delegate-self.json"
python3 "$workspace/scripts/claw.py" task-delegate "$project_root" \
  --task-id TASK-003 \
  --agent codex \
  --assignee codex \
  --reason "split into a focused subtask" > "$self_delegate_out"

python3 - "$org_out" "$delegate_out" "$forbidden_out" "$escalate_out" "$override_out" "$self_delegate_out" "$project_root" <<'PY'
import json
import sys
from pathlib import Path

org_payload = json.loads(Path(sys.argv[1]).read_text())
delegate_payload = json.loads(Path(sys.argv[2]).read_text())
forbidden_payload = json.loads(Path(sys.argv[3]).read_text())
escalate_payload = json.loads(Path(sys.argv[4]).read_text())
override_payload = json.loads(Path(sys.argv[5]).read_text())
self_delegate_payload = json.loads(Path(sys.argv[6]).read_text())
project_root = Path(sys.argv[7])

assert org_payload["status"] == "ok", org_payload
assert org_payload["org_graph"]["agents"]["codex"]["reports_to"] == "claude", org_payload

assert delegate_payload["status"] == "delegated", delegate_payload
new_task_path = project_root / delegate_payload["task_path"]
assert new_task_path.is_file(), new_task_path
text = new_task_path.read_text()
assert "parent_task_id: TASK-001" in text, text
assert "delegated_by: claude" in text, text
assert "delegated_to: codex" in text, text
assert "delegation_type: delegation" in text, text

assert forbidden_payload["status"] == "forbidden", forbidden_payload
assert forbidden_payload["reason_code"] in {"delegation_forbidden", "self_delegate_forbidden"}, forbidden_payload

assert escalate_payload["status"] == "escalated", escalate_payload
escalated_task_path = project_root / escalate_payload["task_path"]
assert escalated_task_path.is_file(), escalated_task_path
escalated_text = escalated_task_path.read_text()
assert "parent_task_id: TASK-002" in escalated_text, escalated_text
assert "delegated_to: claude" in escalated_text, escalated_text
assert "delegation_type: escalation" in escalated_text, escalated_text

codex_cfg = override_payload["org_graph"]["agents"]["codex"]
assert codex_cfg["reports_to"] == "claude", override_payload
assert "implementation" in codex_cfg["capabilities"], override_payload
assert codex_cfg["can_delegate"] is True, override_payload
assert codex_cfg["delegates_to"] == ["codex"], override_payload

assert self_delegate_payload["status"] == "delegated", self_delegate_payload
self_delegate_task = project_root / self_delegate_payload["task_path"]
assert self_delegate_task.is_file(), self_delegate_task
self_delegate_text = self_delegate_task.read_text()
assert "parent_task_id: TASK-003" in self_delegate_text, self_delegate_text
assert "delegated_by: codex" in self_delegate_text, self_delegate_text
assert "delegated_to: codex" in self_delegate_text, self_delegate_text
PY

echo "org graph test: ok"
