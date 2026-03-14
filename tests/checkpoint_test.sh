#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-checkpoint-test.XXXXXX")"
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

assert_not_exists() {
  local path="$1"
  if [ -e "$path" ]; then
    echo "Expected path to be absent: $path" >&2
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

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/run_task.sh" "$workspace/scripts/run_task.sh"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/execute_job.sh" "$workspace/scripts/execute_job.sh"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"

rm -rf "$workspace/projects/demo-project/runs" "$workspace/projects/demo-project/state/queue"
mkdir -p "$workspace/projects/demo-project/runs" "$workspace/projects/demo-project/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter}

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

prompt="$(cat)"
printf '%s\n' "$prompt" | grep -F 'Task: TASK-001' >/dev/null
echo "CHECKPOINT AGENT SUCCESS"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

project_root="$workspace/projects/demo-project"
task_path="$project_root/tasks/TASK-001.md"
today="$(date +"%Y-%m-%d")"
run_day_root="$project_root/runs/$today"

run_one="$run_day_root/RUN-0001"
run_two="$run_day_root/RUN-0002"
run_three="$run_day_root/RUN-0003"

python3 "$workspace/scripts/build_run.py" "$task_path" >/dev/null

cat > "$run_one/approval_checkpoint.json" <<'EOF'
{
  "checkpoint_id": "ckpt-test-1",
  "created_at": "2026-03-14T10:00:00Z",
  "reason": "need human input",
  "context": { "question": "continue?" },
  "status": "pending",
  "decision": null,
  "decision_notes": null,
  "resolved_at": null
}
EOF

if CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  bash "$workspace/scripts/execute_job.sh" "$run_one"; then
  echo "Expected execute_job.sh to exit non-zero for pending approval checkpoint" >&2
  exit 1
else
  rc=$?
  if [ "$rc" -ne 2 ]; then
    echo "Expected exit code 2, got $rc" >&2
    exit 1
  fi
fi

assert_contains "$run_one/result.json" '"status": "failed"'
assert_contains "$run_one/result.json" '"exit_code": 2'
assert_contains "$run_one/meta.json" '"status": "failed"'
assert_file "$run_one/approval_checkpoint.json"

python3 "$workspace/scripts/claw.py" enqueue "$task_path" >/dev/null
assert_file "$project_root/state/queue/pending/RUN-0002.json"

cat > "$run_two/approval_checkpoint.json" <<'EOF'
{
  "checkpoint_id": "ckpt-test-2",
  "created_at": "2026-03-14T10:00:00Z",
  "reason": "need human input",
  "context": { "question": "accept?" },
  "status": "pending",
  "decision": null,
  "decision_notes": null,
  "resolved_at": null
}
EOF

worker_out="$workspace/worker-awaiting.json"
worker_rc=0
if CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once > "$worker_out"; then
  worker_rc=0
else
  worker_rc=$?
fi
if [ "$worker_rc" -ne 2 ]; then
  echo "Expected worker exit code 2, got $worker_rc" >&2
  exit 1
fi

assert_contains "$worker_out" '"exit_code": 2'
assert_contains "$worker_out" '"queue_state": "awaiting_approval"'
assert_file "$project_root/state/queue/awaiting_approval/RUN-0002.json"
assert_not_exists "$project_root/state/queue/pending/RUN-0002.json"

resolve_accept_out="$workspace/resolve-accept.json"
python3 "$workspace/scripts/claw.py" resolve-checkpoint "$project_root" RUN-0002 --decision accept --notes "ok" > "$resolve_accept_out"
assert_contains "$resolve_accept_out" '"decision": "accept"'
assert_file "$project_root/state/queue/pending/RUN-0002.json"
assert_not_exists "$project_root/state/queue/awaiting_approval/RUN-0002.json"
assert_contains "$run_two/approval_checkpoint.json" '"status": "resolved"'
assert_contains "$run_two/approval_checkpoint.json" '"decision": "accept"'
assert_contains "$run_two/approval_checkpoint.json" '"decision_notes": "ok"'
assert_contains "$run_two/events.jsonl" '"event_type": "checkpoint_resolved"'

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once >/dev/null
assert_file "$project_root/state/queue/done/RUN-0002.json"
assert_not_exists "$project_root/state/queue/pending/RUN-0002.json"

python3 "$workspace/scripts/claw.py" enqueue "$task_path" >/dev/null
assert_file "$project_root/state/queue/pending/RUN-0003.json"

cat > "$run_three/approval_checkpoint.json" <<'EOF'
{
  "checkpoint_id": "ckpt-test-3",
  "created_at": "2026-03-14T10:00:00Z",
  "reason": "need human input",
  "context": { "question": "reject?" },
  "status": "pending",
  "decision": null,
  "decision_notes": null,
  "resolved_at": null
}
EOF

worker_three_rc=0
if CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once >/dev/null; then
  worker_three_rc=0
else
  worker_three_rc=$?
fi
if [ "$worker_three_rc" -ne 2 ]; then
  echo "Expected worker exit code 2 for RUN-0003, got $worker_three_rc" >&2
  exit 1
fi
assert_file "$project_root/state/queue/awaiting_approval/RUN-0003.json"

resolve_reject_out="$workspace/resolve-reject.json"
python3 "$workspace/scripts/claw.py" resolve-checkpoint "$project_root" RUN-0003 --decision reject > "$resolve_reject_out"
assert_contains "$resolve_reject_out" '"decision": "reject"'
assert_file "$project_root/state/queue/failed/RUN-0003.json"
assert_not_exists "$project_root/state/queue/awaiting_approval/RUN-0003.json"
assert_contains "$run_three/approval_checkpoint.json" '"status": "resolved"'
assert_contains "$run_three/approval_checkpoint.json" '"decision": "reject"'

echo "checkpoint test: ok"
