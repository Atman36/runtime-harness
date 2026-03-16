#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-live-status-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}

trap cleanup EXIT

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"

project_root="$workspace/projects/demo-project"
rm -rf "$project_root/runs" "$project_root/reviews" "$project_root/state/queue" "$project_root/state/hooks"
mkdir -p \
  "$project_root/runs" \
  "$project_root/reviews" \
  "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter} \
  "$project_root/state/hooks"/{pending,failed,sent}

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
echo "LIVE STATUS SUCCESS"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

task_path="$project_root/tasks/TASK-001.md"

enqueue_out="$tmp_root/enqueue.json"
python3 "$workspace/scripts/claw.py" enqueue "$task_path" > "$enqueue_out"
run_id="$(python3 -c "import json; print(json.load(open('$enqueue_out'))['job_id'])")"
run_path_rel="$(python3 -c "import json; print(json.load(open('$enqueue_out'))['run_path'])")"
run_dir="$project_root/$run_path_rel"

pending_out="$tmp_root/status-pending.json"
python3 "$workspace/scripts/claw.py" status "$project_root" "$run_id" > "$pending_out"

python3 - "$pending_out" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
assert payload["feed_version"] == 1, payload
assert payload["queue_state"] == "pending", payload
assert payload["queue"]["state"] == "pending", payload["queue"]
assert payload["run_status"] == "queued", payload
assert payload["current_step"]["key"] == "run_enqueued", payload["current_step"]
assert payload["stream_tail"] == [], payload["stream_tail"]
assert payload["event_snapshot"]["queue_state"] == "pending", payload["event_snapshot"]
PY

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review >/dev/null

summary_out="$tmp_root/summary.json"
python3 "$workspace/scripts/claw.py" openclaw summary "$project_root" "$run_id" > "$summary_out"

python3 - "$summary_out" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
assert payload["status"] == "success", payload
assert payload["queue_state"] == "done", payload
assert payload["queue"]["state"] == "done", payload["queue"]
assert payload["current_step"]["key"] == "delivery_pending", payload["current_step"]
assert payload["current_step"]["status"] == "complete", payload["current_step"]
assert len(payload["stream_tail"]) >= 2, payload["stream_tail"]
assert payload["stream_tail"][-1]["type"] == "status", payload["stream_tail"]
assert payload["stream_tail"][-1]["text"] == "run_end", payload["stream_tail"]
PY

printf '{\n' > "$run_dir/meta.json"
printf '{\n' > "$run_dir/event_snapshot.json"
printf 'not-json\n' > "$run_dir/events.jsonl"

degraded_out="$tmp_root/status-degraded.json"
python3 "$workspace/scripts/claw.py" status "$project_root" "$run_id" > "$degraded_out"

python3 - "$degraded_out" "$run_id" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
expected_run_id = sys.argv[2]
assert payload["run_id"] == expected_run_id, payload
assert payload["queue_state"] == "done", payload
assert payload["result_status"] == "success", payload
assert payload["run_status"] == "success", payload
assert payload["current_step"]["key"] == "delivery_pending", payload["current_step"]
errors = payload["artifact_errors"]
for key in ("meta", "event_snapshot", "events"):
    assert key in errors, errors
PY

echo "live status feed test: ok"
