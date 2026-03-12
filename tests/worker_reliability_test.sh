#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-worker-reliability-test.XXXXXX")"
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

assert_eq() {
  local actual="$1"
  local expected="$2"
  if [ "$actual" != "$expected" ]; then
    echo "Expected '$expected', got '$actual'" >&2
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
rm -rf "$workspace/projects/demo-project/runs" "$workspace/projects/demo-project/reviews" "$workspace/projects/demo-project/state/queue"
mkdir -p "$workspace/projects/demo-project/runs" "$workspace/projects/demo-project/reviews" "$workspace/projects/demo-project/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter}

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
echo "WORKER SUCCESS"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

cat > "$workspace/scripts/fake_fail_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
echo "WORKER FAILURE" >&2
exit 7
EOF
chmod +x "$workspace/scripts/fake_fail_agent.sh"

cat > "$workspace/scripts/fake_sleep_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
sleep 2
echo "WORKER SLOW SUCCESS"
EOF
chmod +x "$workspace/scripts/fake_sleep_success_agent.sh"

task_path="$workspace/projects/demo-project/tasks/TASK-001.md"
project_root="$workspace/projects/demo-project"
today="$(date +"%Y-%m-%d")"
queue_root="$project_root/state/queue"

set_queue_field() {
  local path="$1"
  local field="$2"
  local value="$3"
  TARGET_PATH="$path" FIELD_NAME="$field" FIELD_VALUE="$value" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["TARGET_PATH"])
field = os.environ["FIELD_NAME"]
raw_value = os.environ["FIELD_VALUE"]
try:
    value = int(raw_value)
except ValueError:
    value = raw_value
payload = json.loads(path.read_text(encoding="utf-8"))
payload.setdefault("queue", {})[field] = value
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY
}

python3 "$workspace/scripts/claw.py" enqueue "$task_path" >/dev/null
done_one="$queue_root/done/RUN-0001.json"
pending_one="$queue_root/pending/RUN-0001.json"
success_stdout="$workspace/worker-success.stdout"
CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review >"$success_stdout"
assert_file "$done_one"
assert_not_exists "$pending_one"
assert_contains "$success_stdout" '"queue_state": "done"'

python3 "$workspace/scripts/claw.py" enqueue "$task_path" >/dev/null
pending_two="$queue_root/pending/RUN-0002.json"
retry_stdout="$workspace/worker-retry.stdout"
retry_idle_stdout="$workspace/worker-retry-idle.stdout"
done_two="$queue_root/done/RUN-0002.json"
set_queue_field "$pending_two" "max_attempts" "3"
set +e
CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_fail_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review --retry-backoff-base-seconds 30 >"$retry_stdout" 2>"$workspace/worker-retry.stderr"
retry_rc=$?
set -e
assert_eq "$retry_rc" "7"
assert_file "$pending_two"
assert_not_exists "$done_two"
assert_contains "$retry_stdout" '"queue_state": "retried"'
assert_contains "$pending_two" '"next_retry_at"'
assert_contains "$pending_two" '"retry_backoff_seconds": 30'

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review >"$retry_idle_stdout"
assert_contains "$retry_idle_stdout" '"status": "idle"'
assert_file "$pending_two"

TARGET_PATH="$pending_two" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["TARGET_PATH"])
payload = json.loads(path.read_text(encoding="utf-8"))
payload["queue"]["next_retry_at"] = "2000-01-01T00:00:00Z"
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

retry_resume_stdout="$workspace/worker-retry-resume.stdout"
CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review >"$retry_resume_stdout"
assert_contains "$retry_resume_stdout" '"queue_state": "done"'
assert_file "$done_two"

python3 "$workspace/scripts/claw.py" enqueue "$task_path" >/dev/null
pending_three="$queue_root/pending/RUN-0003.json"
dead_letter_three="$queue_root/dead_letter/RUN-0003.json"
dead_stdout="$workspace/worker-dead.stdout"
set_queue_field "$pending_three" "max_attempts" "1"
set +e
CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_fail_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review >"$dead_stdout" 2>"$workspace/worker-dead.stderr"
dead_rc=$?
set -e
assert_eq "$dead_rc" "7"
assert_file "$dead_letter_three"
assert_not_exists "$pending_three"
assert_contains "$dead_stdout" '"queue_state": "dead_letter"'
assert_contains "$dead_letter_three" '"last_exit_code": 7'
assert_contains "$dead_letter_three" '"last_result_status": "failed"'

python3 "$workspace/scripts/claw.py" enqueue "$task_path" >/dev/null
heartbeat_stdout="$workspace/worker-heartbeat.stdout"
done_four="$queue_root/done/RUN-0004.json"
CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_sleep_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review --lease-seconds 1 --heartbeat-interval-seconds 0.2 >"$heartbeat_stdout"
assert_file "$done_four"
assert_contains "$heartbeat_stdout" '"queue_state": "done"'
assert_contains "$done_four" '"event": "lease_renewed"'

echo "worker reliability test: ok"
