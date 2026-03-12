#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-queue-cli-test.XXXXXX")"
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
cp "$repo_root/scripts/execute_job.sh" "$workspace/scripts/execute_job.sh"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

prompt="$(cat)"
printf '%s\n' "$prompt" | grep -F 'Task: TASK-001' >/dev/null
echo "QUEUE SUCCESS"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

task_path="$workspace/projects/demo-project/tasks/TASK-001.md"
project_root="$workspace/projects/demo-project"
today="$(date +"%Y-%m-%d")"
run_dir="$project_root/runs/$today/RUN-0001"
queue_root="$project_root/state/queue"
pending_job="$queue_root/pending/RUN-0001.json"
done_job="$queue_root/done/RUN-0001.json"
status_path="$workspace/status.json"

python3 "$workspace/scripts/claw.py" enqueue "$task_path"

assert_file "$run_dir/job.json"
assert_file "$pending_job"
assert_contains "$run_dir/job.json" "\"run_path\": \"runs/$today/RUN-0001\""
assert_contains "$pending_job" "\"job_id\": \"RUN-0001\""
assert_contains "$pending_job" "\"run_path\": \"runs/$today/RUN-0001\""

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once

assert_file "$done_job"
assert_not_exists "$pending_job"
assert_contains "$run_dir/meta.json" '"status": "completed"'
assert_contains "$run_dir/result.json" '"status": "success"'
assert_contains "$run_dir/stdout.log" 'QUEUE SUCCESS'

python3 "$workspace/scripts/claw.py" status "$project_root" RUN-0001 > "$status_path"

assert_contains "$status_path" '"run_id": "RUN-0001"'
assert_contains "$status_path" '"queue_state": "done"'
assert_contains "$status_path" '"run_status": "completed"'
assert_contains "$status_path" "\"run_path\": \"runs/$today/RUN-0001\""

echo "queue cli test: ok"
