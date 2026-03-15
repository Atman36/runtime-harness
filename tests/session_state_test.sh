#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-session-state-test.XXXXXX")"
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
sessions_root="$project_root/state/sessions"

rm -rf "$sessions_root"

update_out="$tmp_root/session-update.json"
python3 "$workspace/scripts/claw.py" session-update "$project_root" \
  --agent codex \
  --task-id TASK-001 \
  --resume-handle "resume-123" \
  --summary "handoff summary" > "$update_out"

status_out="$tmp_root/session-status.json"
python3 "$workspace/scripts/claw.py" session-status "$project_root" \
  --agent codex \
  --task-id TASK-001 > "$status_out"

claim_out="$tmp_root/session-claim.json"
python3 "$workspace/scripts/claw.py" task-claim "$project_root" \
  --task-id TASK-001 \
  --agent codex \
  --reason "resume" > "$claim_out"

wake_out="$tmp_root/session-wake.json"
python3 "$workspace/scripts/claw.py" wake-enqueue "$project_root" \
  --agent codex \
  --task-id TASK-001 \
  --reason manual > "$wake_out"

wake_status_out="$tmp_root/session-wake-status.json"
python3 "$workspace/scripts/claw.py" wake-status "$project_root" > "$wake_status_out"

reset_out="$tmp_root/session-reset.json"
python3 "$workspace/scripts/claw.py" session-reset "$project_root" \
  --agent codex \
  --task-id TASK-001 \
  --note "stale context" > "$reset_out"

rotate_out="$tmp_root/session-rotate.json"
python3 "$workspace/scripts/claw.py" session-rotate "$project_root" \
  --agent codex \
  --task-id TASK-001 \
  --note "new thread" > "$rotate_out"

session_file="$sessions_root/codex__TASK-001.json"
assert_file "$session_file"

python3 - "$update_out" "$status_out" "$claim_out" "$wake_out" "$wake_status_out" "$reset_out" "$rotate_out" "$session_file" <<'PY'
import json
import sys
from pathlib import Path

update = json.loads(Path(sys.argv[1]).read_text())
status = json.loads(Path(sys.argv[2]).read_text())
claim = json.loads(Path(sys.argv[3]).read_text())
wake = json.loads(Path(sys.argv[4]).read_text())
wake_status = json.loads(Path(sys.argv[5]).read_text())
reset = json.loads(Path(sys.argv[6]).read_text())
rotate = json.loads(Path(sys.argv[7]).read_text())
session_payload = json.loads(Path(sys.argv[8]).read_text())

assert update["resume"]["handle"] == "resume-123", update
assert update["handoff"]["summary"] == "handoff summary", update
assert status["session_id"] == update["session_id"], status
assert session_payload["session_id"] == rotate["session_id"], session_payload

assert claim["session"]["session_id"] == update["session_id"], claim
assert wake["session"]["session_id"] == update["session_id"], wake
pending = wake_status["pending"][0]
assert pending["session"]["session_id"] == update["session_id"], wake_status

assert reset["status"] == "reset", reset
assert reset["resume"] is None, reset
assert reset["handoff"]["summary"] == "", reset

assert rotate["session_id"] != reset["session_id"], rotate
assert rotate["status"] == "reset", rotate
PY

python3 "$workspace/scripts/validate_artifacts.py" "$session_file" --quiet >/dev/null

echo "session state test: ok"
