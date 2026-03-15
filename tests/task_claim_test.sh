#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-task-claim-test.XXXXXX")"
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
claims_root="$project_root/state/claims"
wakes_root="$project_root/state/wakes"

rm -rf "$claims_root" "$wakes_root"

claim_one="$tmp_root/claim-one.json"
python3 "$workspace/scripts/claw.py" task-claim "$project_root" \
  --task-id TASK-001 \
  --agent codex \
  --reason "starting work" > "$claim_one"

claim_two="$tmp_root/claim-two.json"
python3 "$workspace/scripts/claw.py" task-claim "$project_root" \
  --task-id TASK-001 \
  --agent codex \
  --reason "repeat" > "$claim_two"

conflict_out="$tmp_root/claim-conflict.json"
set +e
python3 "$workspace/scripts/claw.py" task-claim "$project_root" \
  --task-id TASK-001 \
  --agent claude > "$conflict_out"
conflict_code=$?
set -e
if [ "$conflict_code" -eq 0 ]; then
  echo "Expected conflict claim to return non-zero exit code" >&2
  exit 1
fi

release_blocked="$tmp_root/release-blocked.json"
python3 "$workspace/scripts/claw.py" task-release "$project_root" \
  --task-id TASK-001 \
  --agent codex \
  --status blocked \
  --reason "needs input" > "$release_blocked"

release_claim="$tmp_root/claim-again.json"
python3 "$workspace/scripts/claw.py" task-claim "$project_root" \
  --task-id TASK-001 \
  --agent codex \
  --reason "unblocked" > "$release_claim"

release_out="$tmp_root/release.json"
python3 "$workspace/scripts/claw.py" task-release "$project_root" \
  --task-id TASK-001 \
  --agent codex \
  --status released \
  --reason "handing off" > "$release_out"

inbox_out="$tmp_root/inbox.json"
python3 "$workspace/scripts/claw.py" inbox "$project_root" --agent codex > "$inbox_out"

claim_file="$claims_root/TASK-001.json"
assert_file "$claim_file"

task_path="$project_root/tasks/TASK-001.md"
assert_file "$task_path"

python3 - "$claim_one" "$claim_two" "$conflict_out" "$release_blocked" "$release_out" "$inbox_out" "$claim_file" "$task_path" <<'PY'
import json
import sys
from pathlib import Path

claim_one = json.loads(Path(sys.argv[1]).read_text())
claim_two = json.loads(Path(sys.argv[2]).read_text())
conflict = json.loads(Path(sys.argv[3]).read_text())
release_blocked = json.loads(Path(sys.argv[4]).read_text())
release_out = json.loads(Path(sys.argv[5]).read_text())
inbox = json.loads(Path(sys.argv[6]).read_text())
claim_payload = json.loads(Path(sys.argv[7]).read_text())
task_text = Path(sys.argv[8]).read_text()

assert claim_one["status"] == "claimed", claim_one
assert claim_two["status"] == "already_claimed", claim_two
assert conflict["status"] == "conflict", conflict
assert release_blocked["status"] == "blocked", release_blocked
assert release_out["status"] == "released", release_out
assert "status: released" in task_text, task_text

assert claim_payload["status"] in {"claimed", "released", "blocked"}, claim_payload
assert claim_payload["task_id"] == "TASK-001", claim_payload
assert claim_payload["owner"] == "codex", claim_payload
assert len(claim_payload["events"]) >= 1, claim_payload

assert inbox["agent"] == "codex", inbox
assert "available" in inbox, inbox
PY

python3 "$workspace/scripts/validate_artifacts.py" "$claim_file" --quiet >/dev/null

echo "task claim test: ok"
