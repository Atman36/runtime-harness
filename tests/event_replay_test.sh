#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-event-replay-test.XXXXXX")"
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

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/dispatch_hooks.py" "$workspace/scripts/dispatch_hooks.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/reconcile_hooks.py" "$workspace/scripts/reconcile_hooks.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"

project_root="$workspace/projects/demo-project"
rm -rf "$project_root/runs" "$project_root/reviews" "$project_root/state/queue"
mkdir -p "$project_root/runs" "$project_root/reviews"
mkdir -p "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter}
mkdir -p "$project_root/state/hooks"/{pending,failed,sent}

task_path="$project_root/tasks/TASK-001.md"

claw() {
  python3 "$workspace/scripts/claw.py" "$@"
}

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
echo "Event replay fake agent output"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

enqueue_out="$tmp_root/enqueue.json"
claw openclaw enqueue "$project_root" "$task_path" > "$enqueue_out"

run_id="$(python3 -c "import json; print(json.load(open('$enqueue_out'))['run_id'])")"
run_path_rel="$(python3 -c "import json; print(json.load(open('$enqueue_out'))['run_path'])")"
run_dir="$project_root/$run_path_rel"
events_path="$run_dir/events.jsonl"
snapshot_path="$run_dir/event_snapshot.json"

[ -f "$events_path" ] || fail "events.jsonl missing after enqueue"
[ -f "$snapshot_path" ] || fail "event_snapshot.json missing after enqueue"

replay_pending_out="$tmp_root/replay-pending.json"
claw openclaw replay-events "$project_root" "$run_id" > "$replay_pending_out"

python3 - "$replay_pending_out" <<'PY' || fail "pending replay snapshot invalid"
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
snapshot = payload["snapshot"]
assert payload["run_id"].startswith("RUN-"), payload
assert snapshot["event_count"] >= 2, snapshot
assert snapshot["queue_state"] == "pending", snapshot
assert snapshot["run_status"] == "queued", snapshot
assert snapshot["last_event_type"] == "run_enqueued", snapshot
assert payload["events"][0]["event_type"] == "run_created", payload["events"]
print("ok")
PY
pass "enqueue writes append-only events and replayable pending snapshot"

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  claw worker "$project_root" --once --skip-review >/dev/null

replay_done_out="$tmp_root/replay-done.json"
claw openclaw replay-events "$project_root" "$run_id" > "$replay_done_out"

python3 - "$replay_done_out" <<'PY' || fail "completed replay snapshot invalid"
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
snapshot = payload["snapshot"]
assert snapshot["queue_state"] == "done", snapshot
assert snapshot["run_status"] == "success", snapshot
assert snapshot["delivery_status"] == "pending_delivery", snapshot
event_types = [event["event_type"] for event in payload["events"]]
assert "job_claimed" in event_types, event_types
assert "run_finished" in event_types, event_types
print("ok")
PY
pass "worker updates replay snapshot with claimed and finished events"

wake_out="$tmp_root/wake.json"
claw openclaw wake "$project_root" > "$wake_out"

replay_sent_out="$tmp_root/replay-sent.json"
claw openclaw replay-events "$project_root" "$run_id" > "$replay_sent_out"

summary_out="$tmp_root/summary.json"
claw openclaw summary "$project_root" "$run_id" > "$summary_out"

python3 - "$replay_sent_out" "$summary_out" <<'PY' || fail "wake replay snapshot invalid"
import json
import sys

replay_payload = json.loads(open(sys.argv[1]).read())
summary_payload = json.loads(open(sys.argv[2]).read())
snapshot = replay_payload["snapshot"]
assert snapshot["delivery_status"] == "delivered", snapshot
assert snapshot["last_event_type"] == "delivery_sent", snapshot
assert "event_snapshot" in summary_payload, summary_payload
assert summary_payload["event_snapshot"]["delivery_status"] == "delivered", summary_payload
print("ok")
PY
pass "wake appends delivery event and summary exposes event snapshot"

echo ""
echo "event_replay_test: all tests passed"
