#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-orchestration-loop-test.XXXXXX")"
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
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/reconcile_hooks.py" "$workspace/scripts/reconcile_hooks.py"
cp "$repo_root/scripts/run_task.sh" "$workspace/scripts/run_task.sh"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"

project_root="$workspace/projects/demo-project"

reset_project() {
  rm -rf "$project_root/runs" "$project_root/reviews" "$project_root/state/queue" "$project_root/state/hooks" "$project_root/state/approvals"
  mkdir -p \
    "$project_root/runs" \
    "$project_root/reviews/decisions" \
    "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter} \
    "$project_root/state/hooks"/{pending,failed,sent}
  python3 - "$project_root" <<'PY'
from pathlib import Path
import sys

project_root = Path(sys.argv[1])
for task_path in (project_root / "tasks").glob("TASK-*.md"):
    text = task_path.read_text(encoding="utf-8")
    text = text.replace("status: done", "status: todo")
    text = text.replace("status: in_progress", "status: todo")
    task_path.write_text(text, encoding="utf-8")
PY
}

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
echo "ORCHESTRATE SUCCESS"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

cat > "$workspace/scripts/fake_fail_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
echo "ORCHESTRATE FAILURE" >&2
exit 9
EOF
chmod +x "$workspace/scripts/fake_fail_agent.sh"

reset_project

success_out="$workspace/orchestrate-success.json"
CLAW_AGENT_COMMAND="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" orchestrate "$project_root" --max-steps 2 --skip-review >"$success_out"

python3 - "$success_out" "$project_root" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(open(sys.argv[1]).read())
project_root = Path(sys.argv[2])
assert payload["steps"] == 2, payload
assert payload["accepted_runs"] == ["RUN-0001", "RUN-0002"], payload
assert payload["ready_tasks"][0]["task_id"] == "TASK-003", payload
assert payload["test_command"] == "bash tests/run_all.sh", payload

for task_id in ("TASK-001", "TASK-002"):
    task_path = project_root / "tasks" / f"{task_id}.md"
    text = task_path.read_text(encoding="utf-8")
    assert "status: done" in text, text

task_three = (project_root / "tasks" / "TASK-003.md").read_text(encoding="utf-8")
assert "status: todo" in task_three, task_three
PY

reset_project

failure_out="$workspace/orchestrate-failure.json"
set +e
CLAW_AGENT_COMMAND="bash $workspace/scripts/fake_fail_agent.sh" \
  python3 "$workspace/scripts/claw.py" orchestrate "$project_root" --max-steps 1 --skip-review >"$failure_out"
failure_rc=$?
set -e
[ "$failure_rc" -eq 0 ] || { echo "orchestrate should stop on approval request, not crash" >&2; exit 1; }

pending_approval="$(find "$project_root/state/approvals/pending" -maxdepth 1 -name 'APPROVAL-*.json' | head -1)"
assert_file "$pending_approval"

python3 - "$failure_out" "$pending_approval" "$project_root/tasks/TASK-001.md" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(open(sys.argv[1]).read())
approval = json.loads(open(sys.argv[2]).read())
task_text = Path(sys.argv[3]).read_text(encoding="utf-8")

assert payload["status"] == "awaiting_approval", payload
assert payload["test_command"] == "bash tests/run_all.sh", payload
assert approval["requested_action"] == "retry", approval
assert "status: in_progress" in task_text, task_text
PY

approval_id="$(python3 -c "import json,sys; print(json.loads(open('$pending_approval').read())['approval_id'])")"
python3 "$workspace/scripts/claw.py" resolve-approval "$project_root" "$approval_id" --decision approved --notes "retry" >/dev/null

python3 - "$project_root/tasks/TASK-001.md" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text(encoding="utf-8")
assert "status: todo" in text, text
PY

echo "orchestration loop test: ok"
