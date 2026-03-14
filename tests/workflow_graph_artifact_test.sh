#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-workflow-graph-test.XXXXXX")"
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

bash "$workspace/scripts/create_project.sh" graph-test-project "$workspace"
project_root="$workspace/projects/graph-test-project"
tasks_dir="$project_root/tasks"
mkdir -p "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter}

cat > "$tasks_dir/TASK-001.md" <<'EOF'
---
id: TASK-001
title: "Bootstrap project"
status: done
preferred_agent: codex
priority: high
needs_review: false
dependencies: []
---
EOF

cat > "$tasks_dir/TASK-002.md" <<'EOF'
---
id: TASK-002
title: "Implement queue worker"
status: todo
preferred_agent: codex
priority: medium
needs_review: true
dependencies:
  - TASK-001
---
EOF

cat > "$tasks_dir/TASK-003.md" <<'EOF'
---
id: TASK-003
title: "Review runtime hardening"
status: todo
preferred_agent: claude
priority: low
needs_review: false
dependencies:
  - TASK-001
  - TASK-002
---
EOF

graph_out="$tmp_root/workflow_graph.json"
(cd "$workspace" && python3 scripts/claw.py workflow-graph "$project_root") > "$graph_out"

artifact_path="$project_root/state/workflow_graph.json"
[ -f "$artifact_path" ] || fail "workflow_graph.json was not written to project state"

python3 - "$graph_out" "$artifact_path" "$workspace" <<'PY' || fail "workflow graph artifact content mismatch"
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(sys.argv[3]) / "scripts"))
from validate_artifacts import load_schema, validate_fallback

printed = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
artifact = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
assert printed == artifact, "CLI output should match persisted artifact"

schema = load_schema("workflow_graph.schema.json")
errors = validate_fallback(artifact, schema)
assert not errors, errors

assert artifact["artifact_version"] == 1, artifact
assert artifact["project"] == "graph-test-project", artifact
assert artifact["node_count"] == 3, artifact
assert artifact["edge_count"] == 3, artifact

node_ids = [node["node_id"] for node in artifact["nodes"]]
assert node_ids == ["TASK-001", "TASK-002", "TASK-003"], node_ids

edges = {
    (
        edge["from"],
        edge["to"],
        edge["edge_type"],
        edge["trigger"],
        edge["reason_code"],
        edge["approval_gate"],
    )
    for edge in artifact["edges"]
}
expected_edges = {
    ("TASK-002", "TASK-001", "sequence", "dependency_resolved", "dependency", False),
    ("TASK-003", "TASK-001", "sequence", "dependency_resolved", "dependency", False),
    ("TASK-003", "TASK-002", "sequence", "dependency_resolved", "dependency", False),
}
assert edges == expected_edges, edges

legacy_artifact = json.loads(json.dumps(artifact))
for edge in legacy_artifact["edges"]:
    edge.pop("edge_type", None)
    edge.pop("trigger", None)
    edge.pop("reason_code", None)
    edge.pop("approval_gate", None)
legacy_errors = validate_fallback(legacy_artifact, schema)
assert not legacy_errors, legacy_errors

task_two = next(node for node in artifact["nodes"] if node["node_id"] == "TASK-002")
assert task_two["ready"] is True, task_two
assert task_two["dependency_blockers"] == [], task_two

task_three = next(node for node in artifact["nodes"] if node["node_id"] == "TASK-003")
assert task_three["ready"] is False, task_three
assert task_three["dependency_blockers"] == ["TASK-002"], task_three

print("ok")
PY
pass "workflow-graph writes portable nodes+edges artifact with schema coverage"

rm -f "$artifact_path"
(cd "$workspace" && python3 scripts/claw.py task-snapshot "$project_root") >/dev/null
[ -f "$artifact_path" ] || fail "task-snapshot did not refresh workflow_graph.json"
pass "task-snapshot refresh also writes workflow_graph.json"

echo ""
echo "workflow_graph_artifact_test: all tests passed"
