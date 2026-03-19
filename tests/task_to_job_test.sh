#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-task-run-test.XXXXXX")"
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

assert_dir() {
  local path="$1"
  if [ ! -d "$path" ]; then
    echo "Expected directory to exist: $path" >&2
    exit 1
  fi
}

assert_contains() {
  local path="$1"
  local expected="$2"
  if ! grep -Fq -- "$expected" "$path"; then
    echo "Expected '$expected' in $path" >&2
    exit 1
  fi
}

mkdir -p "$workspace"

cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
mkdir -p "$workspace/scripts"
cp "$repo_root/scripts/run_task.sh" "$workspace/scripts/run_task.sh"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
rm -rf "$workspace/projects/demo-project/runs" "$workspace/projects/demo-project/state/queue"
mkdir -p "$workspace/projects/demo-project/runs" "$workspace/projects/demo-project/state/queue"/{pending,running,done,failed,awaiting_approval}

task_path="$workspace/projects/demo-project/tasks/TASK-001.md"
project_root="$workspace/projects/demo-project"
today="$(date +"%Y-%m-%d")"
run_day_root="$project_root/runs/$today"
run_one="$run_day_root/RUN-0001"
run_two="$run_day_root/RUN-0002"
invalid_boolean_task="$workspace/projects/demo-project/tasks/TASK-INVALID-BOOLEAN.md"
invalid_risk_flags_task="$workspace/projects/demo-project/tasks/TASK-INVALID-RISK.md"
invalid_project_slug_workspace="$tmp_root/invalid-project-slug"

python3 - "$task_path" "$project_root/state/project.yaml" <<'EOF'
from pathlib import Path
import sys

task_path = Path(sys.argv[1])
project_state_path = Path(sys.argv[2])

task_text = task_path.read_text(encoding="utf-8")
# preferred_agent is already "auto"; ensure it is set (no-op if already correct)
task_text = task_text.replace("preferred_agent: codex", "preferred_agent: auto", 1)
# Add ambiguity and replace the empty tags list with the design tag
task_text = task_text.replace(
    "priority: high\nproject: demo-project\n",
    "priority: high\nambiguity: high\nproject: demo-project\n",
    1,
)
task_text = task_text.replace("tags: []", "tags:\n  - design", 1)
task_path.write_text(task_text, encoding="utf-8")

project_state_path.write_text(
    project_state_path.read_text(encoding="utf-8")
    + "\n"
    + "default_agent: codex\n"
    + "execution:\n"
    + "  workspace_mode: git_worktree\n"
    + "  default_edit_scope:\n"
    + "    - apps\n"
    + "    - tests\n"
    + "  parallel_safe: true\n",
    encoding="utf-8",
)
EOF

bash "$workspace/scripts/run_task.sh" "$task_path"
bash "$workspace/scripts/run_task.sh" "$task_path"

assert_dir "$run_day_root"
assert_dir "$run_one"
assert_dir "$run_two"

assert_file "$run_one/task.md"
assert_file "$run_one/spec.md"
assert_file "$run_one/prompt.txt"
assert_file "$run_one/meta.json"
assert_file "$run_one/job.json"
assert_file "$run_one/result.json"
assert_file "$run_one/report.md"
assert_file "$run_one/stdout.log"
assert_file "$run_one/stderr.log"

assert_contains "$run_one/prompt.txt" "Project: demo-project"
assert_contains "$run_one/prompt.txt" "Task: TASK-001"
assert_contains "$run_one/prompt.txt" "Spec: ../specs/SPEC-001.md"

assert_contains "$run_one/meta.json" "\"run_id\": \"RUN-0001\""
assert_contains "$run_one/meta.json" "\"task_id\": \"TASK-001\""
assert_contains "$run_one/meta.json" "\"status\": \"created\""
assert_contains "$run_one/meta.json" "\"preferred_agent\": \"claude\""

assert_contains "$run_one/job.json" "\"run_id\": \"RUN-0001\""
assert_contains "$run_one/job.json" "\"run_path\": \"runs/$today/RUN-0001\""
assert_contains "$run_one/job.json" "\"project\": \"demo-project\""
assert_contains "$run_one/job.json" "\"task\": {"
assert_contains "$run_one/job.json" "\"spec\": {"
assert_contains "$run_one/job.json" "\"preferred_agent\": \"claude\""

PYTHONPATH="$workspace" python3 - "$workspace" "$task_path" "$run_one" <<'EOF'
import json
import sys
from pathlib import Path

from _system.engine.task_planner import plan_task_run

workspace = Path(sys.argv[1])
task_path = Path(sys.argv[2])
run_dir = Path(sys.argv[3])

plan = plan_task_run(workspace, task_path)
expected_routing = {
    "selected_agent": plan.routing.selected_agent,
    "selection_source": plan.routing.selection_source,
    "routing_rule": plan.routing.routing_rule,
}
expected_execution = {
    "workspace_mode": plan.execution.workspace_mode,
    "workspace_root": plan.execution.workspace_root,
    "workspace_materialization_required": plan.execution.workspace_materialization_required,
    "edit_scope": plan.execution.edit_scope,
    "parallel_safe": plan.execution.parallel_safe,
    "concurrency_group": plan.execution.concurrency_group,
}

job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))

assert job["preferred_agent"] == plan.routing.selected_agent, job
assert job["review_policy"] == plan.review_policy, job
assert job["task"]["priority"] == plan.priority, job
assert job["routing"] == expected_routing, job["routing"]
assert job["execution"] == expected_execution, job["execution"]

assert meta["preferred_agent"] == plan.routing.selected_agent, meta
assert meta["review_policy"] == plan.review_policy, meta
assert meta["priority"] == plan.priority, meta
assert meta["routing"] == expected_routing, meta["routing"]
assert meta["execution"] == expected_execution, meta["execution"]
EOF

assert_contains "$run_one/result.json" "\"status\": \"pending\""
assert_contains "$run_one/report.md" "- Project: demo-project"
assert_contains "$run_one/report.md" "- Task: TASK-001"
assert_contains "$run_one/report.md" "- Status: pending"

cp "$task_path" "$invalid_boolean_task"
python3 - "$invalid_boolean_task" <<'EOF'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace("id: TASK-001", "id: TASK-INVALID-BOOLEAN", 1)
text = text.replace("needs_review: false", "needs_review: maybe", 1)
path.write_text(text, encoding="utf-8")
EOF

if bash "$workspace/scripts/run_task.sh" "$invalid_boolean_task" >"$workspace/invalid-boolean.out" 2>"$workspace/invalid-boolean.err"; then
  echo "Expected run_task.sh to reject invalid needs_review boolean" >&2
  exit 1
fi

assert_contains "$workspace/invalid-boolean.err" "Task front matter needs_review must be true or false"

cp "$task_path" "$invalid_risk_flags_task"
python3 - "$invalid_risk_flags_task" <<'EOF'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace("id: TASK-001", "id: TASK-INVALID-RISK", 1)
text = text.replace("risk_flags: []", "risk_flags: nope", 1)
path.write_text(text, encoding="utf-8")
EOF

if bash "$workspace/scripts/run_task.sh" "$invalid_risk_flags_task" >"$workspace/invalid-risk.out" 2>"$workspace/invalid-risk.err"; then
  echo "Expected run_task.sh to reject invalid risk_flags JSON" >&2
  exit 1
fi

assert_contains "$workspace/invalid-risk.err" "Task front matter risk_flags must be a JSON array"

mkdir -p "$invalid_project_slug_workspace"
cp -R "$repo_root/_system" "$invalid_project_slug_workspace/_system"
cp -R "$repo_root/projects" "$invalid_project_slug_workspace/projects"
mkdir -p "$invalid_project_slug_workspace/scripts"
cp "$repo_root/scripts/run_task.sh" "$invalid_project_slug_workspace/scripts/run_task.sh"
cp "$repo_root/scripts/build_run.py" "$invalid_project_slug_workspace/scripts/build_run.py"
cp "$repo_root/scripts/hooklib.py" "$invalid_project_slug_workspace/scripts/hooklib.py"
rm -rf "$invalid_project_slug_workspace/projects/demo-project/runs" "$invalid_project_slug_workspace/projects/demo-project/state/queue"
mkdir -p "$invalid_project_slug_workspace/projects/demo-project/runs" "$invalid_project_slug_workspace/projects/demo-project/state/queue"/{pending,running,done,failed,awaiting_approval}

python3 - "$invalid_project_slug_workspace/projects/demo-project/state/project.yaml" <<'EOF'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace("slug: demo-project", "slug: wrong-slug", 1)
path.write_text(text, encoding="utf-8")
EOF

if bash "$invalid_project_slug_workspace/scripts/run_task.sh" \
  "$invalid_project_slug_workspace/projects/demo-project/tasks/TASK-001.md" \
  >"$invalid_project_slug_workspace/invalid-project.out" 2>"$invalid_project_slug_workspace/invalid-project.err"; then
  echo "Expected run_task.sh to reject mismatched project slug" >&2
  exit 1
fi

assert_contains "$invalid_project_slug_workspace/invalid-project.err" "Project slug 'wrong-slug' in state/project.yaml does not match directory 'demo-project'"

echo "task to job test: ok"
