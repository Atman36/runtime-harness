#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-operator-jobs-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"

project_root="$workspace/projects/demo-project"
task_path="$project_root/tasks/TASK-001.md"
rm -rf "$project_root/runs" "$project_root/state/queue" "$project_root/state/operator_jobs"
mkdir -p "$project_root/runs" "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval}

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cat >/dev/null
echo "operator job success"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

queued_out="$tmp_root/openclaw-enqueue.json"
python3 "$workspace/scripts/claw.py" openclaw enqueue "$project_root" "$task_path" > "$queued_out"

queued_status="$tmp_root/operator-status-queued.json"
python3 "$workspace/scripts/claw.py" operator-status "$project_root" --status queued > "$queued_status"

run_id="$(python3 - "$queued_out" <<'PY'
import json
import sys
print(json.loads(open(sys.argv[1]).read())["run_id"])
PY
)"

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once >/dev/null

result_out="$tmp_root/operator-result.json"
python3 "$workspace/scripts/claw.py" operator-result "$project_root" "$run_id" > "$result_out"

manual_enqueue="$tmp_root/manual-enqueue.json"
python3 "$workspace/scripts/claw.py" enqueue "$task_path" > "$manual_enqueue"

cancel_run_id="$(python3 - "$manual_enqueue" <<'PY'
import json
import sys
print(json.loads(open(sys.argv[1]).read())["job_id"])
PY
)"

cancel_out="$tmp_root/operator-cancel.json"
python3 "$workspace/scripts/claw.py" operator-cancel "$project_root" "$cancel_run_id" --note "cancelled in test" > "$cancel_out"

cancelled_status="$tmp_root/operator-status-cancelled.json"
python3 "$workspace/scripts/claw.py" operator-status "$project_root" --status cancelled > "$cancelled_status"

python3 - "$queued_status" "$result_out" "$cancel_out" "$cancelled_status" "$project_root" "$run_id" "$cancel_run_id" <<'PY'
import json
import sys
from pathlib import Path

queued = json.loads(Path(sys.argv[1]).read_text())
result = json.loads(Path(sys.argv[2]).read_text())
cancelled = json.loads(Path(sys.argv[3]).read_text())
cancelled_status = json.loads(Path(sys.argv[4]).read_text())
project_root = Path(sys.argv[5])
run_id = sys.argv[6]
cancel_run_id = sys.argv[7]

assert queued["count"] == 1, queued
queued_job = queued["jobs"][0]
assert queued_job["job_id"] == run_id, queued_job
assert queued_job["source"] == "openclaw", queued_job
assert queued_job["status"] == "queued", queued_job

assert result["job_id"] == run_id, result
assert result["status"] == "completed", result
assert result["result_status"] == "success", result
assert result["summary"] == "operator job success", result
assert result["log_path"].endswith("/stdout.log"), result
assert result["stream_path"].endswith("/agent_stream.jsonl"), result
assert result["report_path"].endswith("/report.md"), result

assert cancelled["job_id"] == cancel_run_id, cancelled
assert cancelled["status"] == "cancelled", cancelled
assert cancelled["note"] == "cancelled in test", cancelled

assert cancelled_status["count"] == 1, cancelled_status
assert cancelled_status["jobs"][0]["job_id"] == cancel_run_id, cancelled_status

task_text = (project_root / "tasks" / "TASK-001.md").read_text(encoding="utf-8")
assert "status: cancelled" in task_text, task_text
PY

python3 "$workspace/scripts/validate_artifacts.py" "$project_root/state/operator_jobs/$run_id.json" --quiet >/dev/null
python3 "$workspace/scripts/validate_artifacts.py" "$project_root/state/operator_jobs/$cancel_run_id.json" --quiet >/dev/null

echo "operator jobs test: ok"
