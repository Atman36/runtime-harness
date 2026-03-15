#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-wake-queue-test.XXXXXX")"
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
wakes_root="$project_root/state/wakes"

rm -rf "$project_root/state/wakes"

enqueue_one="$tmp_root/wake-one.json"
python3 "$workspace/scripts/claw.py" wake-enqueue "$project_root" \
  --agent codex \
  --task-id TASK-001 \
  --reason manual \
  --run-id RUN-0001 \
  --source openclaw.enqueue > "$enqueue_one"

enqueue_two="$tmp_root/wake-two.json"
python3 "$workspace/scripts/claw.py" wake-enqueue "$project_root" \
  --agent codex \
  --task-id TASK-001 \
  --reason approval \
  --run-id RUN-0001 \
  --source resolve-approval \
  --note "human approved follow-up" > "$enqueue_two"

status_out="$tmp_root/wake-status.json"
python3 "$workspace/scripts/claw.py" wake-status "$project_root" > "$status_out"

openclaw_status_out="$tmp_root/openclaw-status.json"
python3 "$workspace/scripts/claw.py" openclaw status "$project_root" > "$openclaw_status_out"

pending_file="$(find "$wakes_root/pending" -maxdepth 1 -name '*.json' | head -n 1)"
assert_file "$pending_file"

python3 - "$enqueue_one" "$enqueue_two" "$status_out" "$openclaw_status_out" "$pending_file" <<'PY'
import json
import sys
from pathlib import Path

enqueue_one = json.loads(Path(sys.argv[1]).read_text())
enqueue_two = json.loads(Path(sys.argv[2]).read_text())
status_payload = json.loads(Path(sys.argv[3]).read_text())
openclaw_status = json.loads(Path(sys.argv[4]).read_text())
pending_payload = json.loads(Path(sys.argv[5]).read_text())

assert enqueue_one["status"] == "queued", enqueue_one
assert enqueue_two["status"] == "coalesced", enqueue_two
assert enqueue_one["wake_id"] == enqueue_two["wake_id"], (enqueue_one, enqueue_two)

assert pending_payload["reason_counts"]["manual"] == 1, pending_payload
assert pending_payload["reason_counts"]["approval"] == 1, pending_payload
assert pending_payload["coalesced_count"] == 2, pending_payload
assert pending_payload["scope"]["agent"] == "codex", pending_payload
assert pending_payload["scope"]["task_id"] == "TASK-001", pending_payload
assert len(pending_payload["events"]) == 2, pending_payload
assert pending_payload["events"][0]["reason"] == "manual", pending_payload
assert pending_payload["events"][1]["reason"] == "approval", pending_payload

assert status_payload["counts"]["pending"] == 1, status_payload
assert status_payload["counts"]["coalesced_events"] == 2, status_payload
assert len(status_payload["pending"]) == 1, status_payload
pending_summary = status_payload["pending"][0]
assert pending_summary["reason_counts"]["manual"] == 1, pending_summary
assert pending_summary["reason_counts"]["approval"] == 1, pending_summary
assert pending_summary["coalesced_count"] == 2, pending_summary

assert openclaw_status["wakes"]["pending"] == 1, openclaw_status
assert openclaw_status["wakes"]["coalesced_events"] == 2, openclaw_status
assert openclaw_status["wakes"]["pending_items"][0]["wake_id"] == enqueue_one["wake_id"], openclaw_status
PY

python3 "$workspace/scripts/validate_artifacts.py" "$pending_file" --quiet >/dev/null

echo "wake queue test: ok"
