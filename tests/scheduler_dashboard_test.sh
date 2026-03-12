#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-scheduler-dashboard-test.XXXXXX")"
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

assert_eq() {
  local actual="$1"
  local expected="$2"
  if [ "$actual" != "$expected" ]; then
    echo "Expected '$expected', got '$actual'" >&2
    exit 1
  fi
}

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/reconcile_hooks.py" "$workspace/scripts/reconcile_hooks.py"
cp "$repo_root/scripts/run_task.sh" "$workspace/scripts/run_task.sh"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"

project_one="$workspace/projects/demo-project"
project_two="$workspace/projects/second-project"
cp -R "$project_one" "$project_two"

WORKSPACE="$workspace" python3 - <<'PY'
import os
from pathlib import Path

workspace = Path(os.environ["WORKSPACE"])
project_two = workspace / "projects" / "second-project"
(project_two / "state" / "project.yaml").write_text(
    "slug: second-project\nstatus: active\ncreated_from_template: true\ndefault_task_template: ../../../_system/templates/task.template.md\ndefault_spec_template: ../../../_system/templates/spec.template.md\n",
    encoding="utf-8",
)
for task_path in (project_two / "tasks").glob("TASK-*.md"):
    text = task_path.read_text(encoding="utf-8").replace("project: demo-project", "project: second-project")
    task_path.write_text(text, encoding="utf-8")
PY

for project_root in "$project_one" "$project_two"; do
  rm -rf "$project_root/runs" "$project_root/reviews" "$project_root/state/queue" "$project_root/state/hooks" "$project_root/state/approvals"
  mkdir -p \
    "$project_root/runs" \
    "$project_root/reviews/decisions" \
    "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter} \
    "$project_root/state/hooks"/{pending,failed,sent}
done

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
echo "SCHEDULER SUCCESS"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

python3 "$workspace/scripts/claw.py" enqueue "$project_one/tasks/TASK-001.md" >/dev/null
python3 "$workspace/scripts/claw.py" enqueue "$project_one/tasks/TASK-002.md" >/dev/null
python3 "$workspace/scripts/claw.py" enqueue "$project_two/tasks/TASK-001.md" >/dev/null

scheduler_out="$workspace/scheduler.json"
CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" scheduler --once --max-jobs 2 "$project_one" "$project_two" >"$scheduler_out"

done_one="$(find "$project_one/state/queue/done" -maxdepth 1 -name 'RUN-*.json' | wc -l | tr -d ' ')"
pending_one="$(find "$project_one/state/queue/pending" -maxdepth 1 -name 'RUN-*.json' | wc -l | tr -d ' ')"
done_two="$(find "$project_two/state/queue/done" -maxdepth 1 -name 'RUN-*.json' | wc -l | tr -d ' ')"

assert_eq "$done_one" "1"
assert_eq "$pending_one" "1"
assert_eq "$done_two" "1"

python3 - "$scheduler_out" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
projects = [item["project"] for item in payload["processed_jobs"]]
assert projects == ["demo-project", "second-project"], projects
PY

python3 "$workspace/scripts/claw.py" ask-human "$project_one" RUN-0001 --reason "needs_product_decision" --action retry >/dev/null

cat > "$project_two/reviews/decisions/REVIEW-pending--RUN-0001.json" <<'EOF'
{
  "review_id": "review-1",
  "run_id": "RUN-0001",
  "reviewer_agent": "claude",
  "decided_at": null,
  "decision": "pending",
  "findings": [],
  "batch_id": "REVIEW-pending",
  "trigger": "failed"
}
EOF

cat > "$project_one/state/queue/dead_letter/RUN-0999.json" <<'EOF'
{
  "job_id": "RUN-0999",
  "task": {"id": "TASK-999"},
  "queue": {
    "state": "dead_letter",
    "updated_at": "2026-03-13T12:00:00Z",
    "last_error": "synthetic dead letter"
  }
}
EOF

dashboard_out="$workspace/dashboard.json"
python3 "$workspace/scripts/claw.py" dashboard --all >"$dashboard_out"

python3 - "$dashboard_out" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
assert payload["summary"]["project_count"] == 2, payload
assert payload["summary"]["pending_approvals"] == 1, payload
assert payload["summary"]["pending_reviews"] == 1, payload

projects = {item["project"]: item for item in payload["projects"]}
assert projects["demo-project"]["pending_approvals"] == 1, projects["demo-project"]
assert projects["demo-project"]["recent_failures"][0]["error"] == "synthetic dead letter", projects["demo-project"]
assert projects["second-project"]["pending_reviews"] == 1, projects["second-project"]
assert projects["second-project"]["ready_tasks"], projects["second-project"]
PY

echo "scheduler dashboard test: ok"
