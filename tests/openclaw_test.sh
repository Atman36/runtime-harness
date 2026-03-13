#!/usr/bin/env bash
# Tests for claw openclaw subcommands: status, enqueue, summary, review-batch,
# callback, and wake.

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-openclaw-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT

# ── helpers ───────────────────────────────────────────────────────────────────

assert_json_field() {
  # Assert that a JSON string (passed via stdin or first arg file) contains a field
  local json_file="$1"
  local field="$2"
  python3 - "$json_file" "$field" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
field = sys.argv[2]
assert field in data, f"Missing field {field!r} in JSON. Keys: {list(data.keys())}"
PY
}

assert_json_field_value() {
  local json_file="$1"
  local field="$2"
  local expected="$3"
  python3 - "$json_file" "$field" "$expected" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
field, expected = sys.argv[2], sys.argv[3]
actual = str(data.get(field, ""))
assert actual == expected, f"{field}: expected {expected!r}, got {actual!r}"
PY
}

assert_file() {
  local path="$1"
  if [ ! -f "$path" ]; then
    echo "Expected file to exist: $path" >&2
    exit 1
  fi
}

# ── workspace setup ───────────────────────────────────────────────────────────

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system"  "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/claw.py"                  "$workspace/scripts/claw.py"
cp "$repo_root/scripts/build_run.py"             "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/execute_job.py"           "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/dispatch_hooks.py"        "$workspace/scripts/dispatch_hooks.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py"               "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/reconcile_hooks.py"       "$workspace/scripts/reconcile_hooks.py"
cp "$repo_root/scripts/validate_artifacts.py"    "$workspace/scripts/validate_artifacts.py"

# Clean up project state so tests run on a fresh project
project_root="$workspace/projects/demo-project"
rm -rf "$project_root/runs" "$project_root/reviews" "$project_root/state/queue"
mkdir -p "$project_root/runs" "$project_root/reviews"
mkdir -p "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval}
mkdir -p "$project_root/state/hooks"/{pending,failed,sent}

task_path="$project_root/tasks/TASK-001.md"
today="$(date +"%Y-%m-%d")"

claw() {
  python3 "$workspace/scripts/claw.py" "$@"
}

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
echo "OpenClaw callback summary from fake agent"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

cat > "$workspace/scripts/capture_hook.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

destination="${HOOK_CAPTURE_PATH:?HOOK_CAPTURE_PATH is required}"
cat > "$destination"
EOF
chmod +x "$workspace/scripts/capture_hook.sh"

cat > "$workspace/scripts/capture_system_event.py" <<'EOF'
#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

destination = Path(os.environ["EVENT_CAPTURE_PATH"])
payload = {
    "argv": sys.argv[1:],
}
destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
EOF
chmod +x "$workspace/scripts/capture_system_event.py"

# ── Test 1: openclaw status ────────────────────────────────────────────────────

status_out="$tmp_root/status.json"
claw openclaw status "$project_root" > "$status_out"

assert_file "$status_out"
assert_json_field "$status_out" "project"
assert_json_field "$status_out" "queue"
assert_json_field "$status_out" "recent_runs"
assert_json_field "$status_out" "pending_reviews"

# Verify it is valid JSON and project name matches
python3 - "$status_out" "demo-project" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
expected = sys.argv[2]
assert data["project"] == expected, f"project: expected {expected!r}, got {data['project']!r}"
assert isinstance(data["queue"], dict), "queue should be a dict"
assert isinstance(data["recent_runs"], list), "recent_runs should be a list"
PY

echo "  ok: test1 — openclaw status returns valid JSON with required fields"

# ── Test 2: openclaw enqueue ───────────────────────────────────────────────────

enqueue_out="$tmp_root/enqueue.json"
claw openclaw enqueue "$project_root" "$task_path" > "$enqueue_out"

assert_file "$enqueue_out"
assert_json_field "$enqueue_out" "status"
assert_json_field "$enqueue_out" "run_id"
assert_json_field "$enqueue_out" "run_path"

python3 - "$enqueue_out" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
assert data["status"] == "queued", f"status: expected 'queued', got {data['status']!r}"
assert data["run_id"].startswith("RUN-"), f"run_id should start with RUN-, got {data['run_id']!r}"
PY

echo "  ok: test2 — openclaw enqueue returns JSON with status=queued and run_id"

# ── Test 3: openclaw summary ───────────────────────────────────────────────────

# Read run_id from enqueue output
run_id="$(python3 -c "import json,sys; print(json.loads(open('$enqueue_out').read())['run_id'])")"

summary_out="$tmp_root/summary.json"
claw openclaw summary "$project_root" "$run_id" > "$summary_out"

assert_file "$summary_out"
assert_json_field "$summary_out" "run_id"
assert_json_field "$summary_out" "status"
assert_json_field "$summary_out" "agent"

python3 - "$summary_out" "$run_id" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
expected_run_id = sys.argv[2]
assert data["run_id"] == expected_run_id, f"run_id: expected {expected_run_id!r}, got {data['run_id']!r}"
assert "status" in data, "Missing 'status' field"
assert "agent" in data, "Missing 'agent' field"
PY

echo "  ok: test3 — openclaw summary returns JSON with run_id, status, agent"

# ── Test 4: openclaw review-batch --dry-run ────────────────────────────────────

# Add a failed run so there is a candidate for review
failed_run_dir="$project_root/runs/$today/RUN-9999"
mkdir -p "$failed_run_dir"
cat > "$failed_run_dir/meta.json" <<EOF
{
  "run_id": "RUN-9999",
  "run_date": "$today",
  "status": "failed",
  "project": "demo-project",
  "task_id": "TASK-001",
  "task_title": "Test task",
  "preferred_agent": "codex"
}
EOF
cat > "$failed_run_dir/result.json" <<EOF
{
  "run_id": "RUN-9999",
  "status": "failed",
  "agent": "codex"
}
EOF
cat > "$failed_run_dir/job.json" <<EOF
{
  "job_version": 1,
  "run_id": "RUN-9999",
  "run_path": "runs/$today/RUN-9999",
  "created_at": "2026-01-01T00:00:00Z",
  "project": "demo-project",
  "preferred_agent": "codex",
  "task": {
    "id": "TASK-001",
    "title": "Test task",
    "needs_review": false,
    "risk_flags": []
  },
  "spec": {"source_path": "specs/SPEC-001.md", "copied_path": "spec.md"},
  "artifacts": {
    "prompt_path": "prompt.txt",
    "meta_path": "meta.json",
    "report_path": "report.md",
    "result_path": "result.json",
    "stdout_path": "stdout.log",
    "stderr_path": "stderr.log"
  }
}
EOF

batch_out="$tmp_root/review_batch.json"
claw openclaw review-batch "$project_root" --dry-run > "$batch_out"

assert_file "$batch_out"
assert_json_field "$batch_out" "dry_run"
assert_json_field "$batch_out" "candidates"
assert_json_field "$batch_out" "batches_created"

python3 - "$batch_out" "$project_root/reviews" <<'PY'
import json, sys, pathlib
data = json.loads(open(sys.argv[1]).read())
assert data["dry_run"] is True, f"dry_run: expected True, got {data['dry_run']!r}"
# Dry run should NOT create files
reviews_dir = pathlib.Path(sys.argv[2])
batch_files = list(reviews_dir.glob("REVIEW-*.json"))
assert len(batch_files) == 0, f"dry-run should not create files, found: {batch_files}"
PY

echo "  ok: test4 — openclaw review-batch --dry-run returns JSON with dry_run=true, no files created"

# ── Test 5: openclaw callback ────────────────────────────────────────────────

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/execute_job.py" "$project_root/runs/$today/$run_id" >/dev/null

hook_path="$project_root/state/hooks/pending/${today}--${run_id}.json"
callback_out="$tmp_root/callback.json"
cat "$hook_path" | claw openclaw callback > "$callback_out"

assert_file "$callback_out"
assert_json_field "$callback_out" "event"
assert_json_field "$callback_out" "run_id"
assert_json_field "$callback_out" "summary"
assert_json_field "$callback_out" "chat_text"

python3 - "$callback_out" "$run_id" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
run_id = sys.argv[2]
assert data["event"] == "run.completed", data
assert data["run_id"] == run_id, data
assert data["status"] == "success", data
assert "OpenClaw callback summary" in data["summary"], data
assert run_id in data["chat_text"], data
PY

echo "  ok: test5 — openclaw callback returns chat-friendly completion payload"

# ── Test 6: completion hook emits OpenClaw system event bridge ──────────────

bridge_enqueue_out="$tmp_root/enqueue-bridge.json"
claw openclaw enqueue "$project_root" "$task_path" > "$bridge_enqueue_out"
bridge_run_id="$(python3 -c "import json; print(json.loads(open('$bridge_enqueue_out').read())['run_id'])")"
bridge_hook_path="$project_root/state/hooks/pending/${today}--${bridge_run_id}.json"
bridge_event_out="$tmp_root/system-event.json"

EVENT_CAPTURE_PATH="$bridge_event_out" \
CLAW_OPENCLAW_SYSTEM_EVENT_COMMAND='["python3","'"$workspace"'/scripts/capture_system_event.py"]' \
CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/execute_job.py" "$project_root/runs/$today/$bridge_run_id" >/dev/null

assert_file "$bridge_hook_path"
assert_file "$bridge_event_out"

python3 - "$bridge_event_out" "$project_root" "$bridge_run_id" <<'PY'
import json
import pathlib
import sys

data = json.loads(open(sys.argv[1]).read())
project_root = pathlib.Path(sys.argv[2]).resolve()
run_id = sys.argv[3]
argv = data["argv"]
assert "--mode" in argv, argv
assert argv[argv.index("--mode") + 1] == "now", argv
assert "--text" in argv, argv
text = argv[argv.index("--text") + 1]
assert run_id in text, text
assert f"openclaw wake {project_root} --mode event" in text, text
PY

echo "  ok: test6 — pending completion hook emits OpenClaw system event wake bridge"

# ── Test 7: openclaw wake built-in callback bridge ───────────────────────────

wake_event_out="$tmp_root/wake-event.json"
claw openclaw wake "$project_root" --mode event > "$wake_event_out"

assert_file "$wake_event_out"
assert_json_field "$wake_event_out" "mode"
assert_json_field "$wake_event_out" "callbacks"

python3 - "$wake_event_out" "$project_root/state/hooks/sent/${today}--${bridge_run_id}.json" "$bridge_run_id" <<'PY'
import json
import pathlib
import sys

data = json.loads(open(sys.argv[1]).read())
sent_hook = pathlib.Path(sys.argv[2])
run_id = sys.argv[3]
assert data["mode"] == "event", data
assert isinstance(data["callbacks"], list) and len(data["callbacks"]) >= 1, data
callback = next(item for item in data["callbacks"] if item["run_id"] == run_id)
assert callback["event"] == "run.completed", callback
assert callback["status"] == "success", callback
assert run_id in callback["chat_text"], callback
assert sent_hook.is_file(), sent_hook
PY

echo "  ok: test7 — openclaw wake converts pending hook into callback payload and marks it sent"

# ── Test 8: openclaw wake with hook command ──────────────────────────────────

wake_enqueue_out="$tmp_root/enqueue-wake.json"
claw openclaw enqueue "$project_root" "$task_path" > "$wake_enqueue_out"
wake_run_id="$(python3 -c "import json; print(json.loads(open('$wake_enqueue_out').read())['run_id'])")"

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/execute_job.py" "$project_root/runs/$today/$wake_run_id" >/dev/null

wake_out="$tmp_root/wake.json"
HOOK_CAPTURE_PATH="$tmp_root/wake-hook.json" \
CLAW_HOOK_COMMAND="bash $workspace/scripts/capture_hook.sh" \
  claw openclaw wake "$project_root" --mode cron > "$wake_out"

assert_file "$wake_out"
assert_file "$tmp_root/wake-hook.json"
assert_json_field "$wake_out" "mode"
assert_json_field "$wake_out" "schedule"
assert_json_field "$wake_out" "dispatch"
assert_json_field "$wake_out" "reconcile"

python3 - "$wake_out" "$project_root/state/hooks/sent/${today}--${wake_run_id}.json" "$wake_run_id" <<'PY'
import json, pathlib, sys
data = json.loads(open(sys.argv[1]).read())
sent_hook = pathlib.Path(sys.argv[2])
run_id = sys.argv[3]
assert data["mode"] == "cron", data
assert data["schedule"]["interval_seconds"] == 900, data
assert data["dispatch"]["attempted"] >= 1, data
assert data["dispatch"]["sent"] >= 1, data
assert any(item["run_id"] == run_id for item in data["callbacks"]), data
assert sent_hook.is_file(), sent_hook
PY

echo "  ok: test8 — openclaw wake dispatches pending hooks and reports callback metadata"

echo "openclaw test: ok"
