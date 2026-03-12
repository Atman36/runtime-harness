#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-queue-lifecycle-test.XXXXXX")"
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
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
rm -rf "$workspace/projects/demo-project/runs" "$workspace/projects/demo-project/reviews" "$workspace/projects/demo-project/state/queue"
mkdir -p "$workspace/projects/demo-project/runs" "$workspace/projects/demo-project/reviews" "$workspace/projects/demo-project/state/queue"/{pending,running,done,failed,awaiting_approval}

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

prompt="$(cat)"
printf '%s\n' "$prompt" | grep -F 'Task: TASK-001' >/dev/null
echo "QUEUE LIFECYCLE SUCCESS"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

task_path="$workspace/projects/demo-project/tasks/TASK-001.md"
project_root="$workspace/projects/demo-project"
today="$(date +"%Y-%m-%d")"
queue_root="$project_root/state/queue"
awaiting_job="$queue_root/awaiting_approval/RUN-0001.json"
pending_one="$queue_root/pending/RUN-0001.json"
done_one="$queue_root/done/RUN-0001.json"
pending_two="$queue_root/pending/RUN-0002.json"
running_two="$queue_root/running/RUN-0002.json"
status_path="$workspace/status-awaiting.json"
approve_path="$workspace/approve.json"
reclaim_path="$workspace/reclaim.json"

python3 "$workspace/scripts/claw.py" enqueue --awaiting-approval "$task_path"

assert_file "$awaiting_job"
assert_not_exists "$pending_one"

python3 "$workspace/scripts/claw.py" status "$project_root" RUN-0001 > "$status_path"
assert_contains "$status_path" '"queue_state": "awaiting_approval"'

python3 "$workspace/scripts/claw.py" approve "$project_root" RUN-0001 > "$approve_path"

assert_contains "$approve_path" '"status": "approved"'
assert_file "$pending_one"
assert_not_exists "$awaiting_job"

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once

assert_file "$done_one"
assert_not_exists "$pending_one"

python3 "$workspace/scripts/claw.py" enqueue "$task_path"
assert_file "$pending_two"

WORKSPACE="$workspace" PYTHONPATH="$workspace" python3 - <<'PY'
import os
import time
from pathlib import Path

from _system.engine.file_queue import FileQueue

workspace = Path(os.environ["WORKSPACE"])
project_root = workspace / "projects" / "demo-project"
queue = FileQueue(project_root / "state" / "queue")
claimed = queue.claim()
target = project_root / "state" / "queue" / "running" / "RUN-0002.json"
old_timestamp = time.time() - 30
os.utime(target, (old_timestamp, old_timestamp))
print(claimed.job_id)
PY

assert_file "$running_two"

python3 "$workspace/scripts/claw.py" reclaim "$project_root" --stale-after-seconds 5 > "$reclaim_path"

assert_contains "$reclaim_path" '"status": "reclaimed"'
assert_contains "$reclaim_path" '"reclaimed": 1'
assert_file "$pending_two"
assert_not_exists "$running_two"

echo "queue lifecycle test: ok"
