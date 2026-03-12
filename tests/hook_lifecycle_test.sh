#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-hook-lifecycle-test.XXXXXX")"
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

assert_dir() {
  local path="$1"
  if [ ! -d "$path" ]; then
    echo "Expected directory to exist: $path" >&2
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
cp "$repo_root/scripts/dispatch_hooks.py" "$workspace/scripts/dispatch_hooks.py"
cp "$repo_root/scripts/reconcile_hooks.py" "$workspace/scripts/reconcile_hooks.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
rm -rf "$workspace/projects/demo-project/runs" "$workspace/projects/demo-project/reviews" "$workspace/projects/demo-project/state/hooks" "$workspace/projects/demo-project/state/queue"
mkdir -p "$workspace/projects/demo-project/runs" "$workspace/projects/demo-project/reviews" "$workspace/projects/demo-project/state/hooks"/{pending,sent,failed} "$workspace/projects/demo-project/state/queue"/{pending,running,done,failed,awaiting_approval}

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
echo "FAKE SUCCESS"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

cat > "$workspace/scripts/capture_hook.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

destination="${HOOK_CAPTURE_PATH:?HOOK_CAPTURE_PATH is required}"
cat > "$destination"
EOF
chmod +x "$workspace/scripts/capture_hook.sh"

cat > "$workspace/scripts/fail_hook.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
echo "hook delivery failed" >&2
exit 9
EOF
chmod +x "$workspace/scripts/fail_hook.sh"

cat > "$workspace/scripts/slow_hook.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

sleep 2
cat >/dev/null
EOF
chmod +x "$workspace/scripts/slow_hook.sh"

task_path="$workspace/projects/demo-project/tasks/TASK-001.md"
project_root="$workspace/projects/demo-project"
hook_root="$project_root/state/hooks"
today="$(date +"%Y-%m-%d")"
hook_one="$hook_root/sent/${today}--RUN-0001.json"
hook_two_pending="$hook_root/pending/${today}--RUN-0002.json"
hook_two_sent="$hook_root/sent/${today}--RUN-0002.json"
hook_three_failed="$hook_root/failed/${today}--RUN-0003.json"
hook_three_sent="$hook_root/sent/${today}--RUN-0003.json"
hook_four_pending="$hook_root/pending/${today}--RUN-0004.json"
hook_four_sent="$hook_root/sent/${today}--RUN-0004.json"

HOOK_CAPTURE_PATH="$workspace/hook-run-1.json" \
CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
CLAW_HOOK_COMMAND="bash $workspace/scripts/capture_hook.sh" \
  bash "$workspace/scripts/run_task.sh" --execute "$task_path"

assert_dir "$hook_root/pending"
assert_dir "$hook_root/sent"
assert_dir "$hook_root/failed"
assert_file "$hook_one"
assert_not_exists "$hook_root/pending/${today}--RUN-0001.json"
assert_contains "$hook_one" "\"hook_id\": \"${today}--RUN-0001\""
assert_contains "$hook_one" "\"run_status\": \"success\""
assert_contains "$hook_one" "\"status\": \"sent\""
assert_contains "$hook_one" "\"attempt_count\": 1"
assert_contains "$workspace/hook-run-1.json" "\"run_id\": \"RUN-0001\""

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  bash "$workspace/scripts/run_task.sh" --execute "$task_path"

assert_file "$hook_two_pending"
assert_contains "$hook_two_pending" "\"status\": \"pending\""
assert_contains "$hook_two_pending" "\"attempt_count\": 0"

python3 "$workspace/scripts/dispatch_hooks.py" "$project_root"

assert_file "$hook_two_pending"
assert_not_exists "$hook_two_sent"

HOOK_CAPTURE_PATH="$workspace/hook-run-2.json" \
CLAW_HOOK_COMMAND="bash $workspace/scripts/capture_hook.sh" \
  python3 "$workspace/scripts/dispatch_hooks.py" "$project_root"

assert_file "$hook_two_sent"
assert_not_exists "$hook_two_pending"
assert_contains "$hook_two_sent" "\"status\": \"sent\""
assert_contains "$hook_two_sent" "\"attempt_count\": 1"
assert_contains "$workspace/hook-run-2.json" "\"run_id\": \"RUN-0002\""

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
CLAW_HOOK_COMMAND="bash $workspace/scripts/fail_hook.sh" \
  bash "$workspace/scripts/run_task.sh" --execute "$task_path"

assert_file "$hook_three_failed"
assert_contains "$hook_three_failed" "\"status\": \"failed\""
assert_contains "$hook_three_failed" "\"attempt_count\": 1"
assert_contains "$hook_three_failed" "\"exit_code\": 9"

HOOK_CAPTURE_PATH="$workspace/hook-run-3.json" \
CLAW_HOOK_COMMAND="bash $workspace/scripts/capture_hook.sh" \
  python3 "$workspace/scripts/reconcile_hooks.py" "$project_root"

assert_file "$hook_three_sent"
assert_not_exists "$hook_three_failed"
assert_contains "$hook_three_sent" "\"status\": \"sent\""
assert_contains "$hook_three_sent" "\"attempt_count\": 2"
assert_contains "$workspace/hook-run-3.json" "\"run_id\": \"RUN-0003\""

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  bash "$workspace/scripts/run_task.sh" --execute "$task_path"

assert_file "$hook_four_pending"
CLAW_HOOK_STALE_SECONDS=0 \
  python3 "$workspace/scripts/reconcile_hooks.py" "$project_root"

assert_file "$hook_four_pending"
assert_not_exists "$hook_four_sent"

HOOK_CAPTURE_PATH="$workspace/hook-run-4.json" \
CLAW_HOOK_COMMAND="bash $workspace/scripts/capture_hook.sh" \
CLAW_HOOK_STALE_SECONDS=0 \
  python3 "$workspace/scripts/reconcile_hooks.py" "$project_root"

assert_file "$hook_four_sent"
assert_not_exists "$hook_four_pending"
assert_contains "$hook_four_sent" "\"status\": \"sent\""
assert_contains "$hook_four_sent" "\"attempt_count\": 1"
assert_contains "$workspace/hook-run-4.json" "\"run_id\": \"RUN-0004\""

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  bash "$workspace/scripts/run_task.sh" --execute "$task_path"

hook_five_pending="$hook_root/pending/${today}--RUN-0005.json"
hook_five_failed="$hook_root/failed/${today}--RUN-0005.json"

assert_file "$hook_five_pending"

if CLAW_HOOK_COMMAND="bash $workspace/scripts/slow_hook.sh" \
  CLAW_HOOK_TIMEOUT_SECONDS=1 \
  python3 "$workspace/scripts/dispatch_hooks.py" "$project_root"; then
  echo "Expected dispatch_hooks.py to fail when hook delivery times out" >&2
  exit 1
fi

assert_file "$hook_five_failed"
assert_not_exists "$hook_five_pending"
assert_contains "$hook_five_failed" "\"status\": \"failed\""
assert_contains "$hook_five_failed" "\"exit_code\": 124"
assert_contains "$hook_five_failed" "Timed out after 1 seconds"

python3 - "$workspace" <<'EOF'
from pathlib import Path
import sys

workspace = Path(sys.argv[1])
sys.path.insert(0, str(workspace / "scripts"))

import hooklib

project_root = workspace / "projects" / "demo-project"
payload = {
    "hook_id": "atomic-test",
    "delivery": {
        "status": "pending",
    },
}
pending_path = hooklib.write_hook_payload(project_root, dict(payload), "pending")

original = hooklib.write_json_atomic

def fail_write(path, body):
    raise RuntimeError("simulated write failure")

hooklib.write_json_atomic = fail_write

try:
    hooklib.write_hook_payload(project_root, dict(payload), "sent")
except RuntimeError:
    pass
else:
    raise SystemExit("Expected simulated write failure")
finally:
    hooklib.write_json_atomic = original

if not pending_path.is_file():
    raise SystemExit(f"Pending hook disappeared after failed write: {pending_path}")
EOF

echo "hook lifecycle test: ok"
