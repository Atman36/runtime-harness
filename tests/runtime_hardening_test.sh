#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-runtime-hardening-test.XXXXXX")"
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

assert_contains() {
  local path="$1"
  local expected="$2"
  if ! grep -Fq -- "$expected" "$path"; then
    echo "Expected '$expected' in $path" >&2
    exit 1
  fi
}

mkdir -p "$workspace/scripts" "$workspace/bin"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/dispatch_hooks.py" "$workspace/scripts/dispatch_hooks.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/reconcile_hooks.py" "$workspace/scripts/reconcile_hooks.py"
cp "$repo_root/scripts/run_task.sh" "$workspace/scripts/run_task.sh"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"

project_root="$workspace/projects/demo-project"
task_path="$project_root/tasks/TASK-001.md"
today="$(date +"%Y-%m-%d")"

rm -rf "$project_root/runs" "$project_root/reviews" "$project_root/state/queue" "$project_root/state/hooks"
mkdir -p \
  "$project_root/runs" \
  "$project_root/reviews" \
  "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter} \
  "$project_root/state/hooks"/{pending,failed,sent}

cat > "$workspace/bin/agent-stub" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' "$*" > "${AGENT_ARGS_PATH:?AGENT_ARGS_PATH is required}"
pwd > "${AGENT_CWD_PATH:?AGENT_CWD_PATH is required}"
cat > "${AGENT_STDIN_PATH:?AGENT_STDIN_PATH is required}"
echo "SAFE AGENT OK"
EOF
chmod +x "$workspace/bin/agent-stub"

cat > "$workspace/scripts/capture_hook.py" <<'EOF'
#!/usr/bin/env python3
import os
import sys

destination = os.environ["HOOK_CAPTURE_PATH"]
with open(destination, "w", encoding="utf-8") as handle:
    handle.write(sys.stdin.read())
EOF
chmod +x "$workspace/scripts/capture_hook.py"

cat > "$workspace/scripts/slow_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
sleep 2
echo "SLOW AGENT OK"
EOF
chmod +x "$workspace/scripts/slow_agent.sh"

PATH="$workspace/bin:$PATH" \
AGENT_ARGS_PATH="$workspace/agent-args.txt" \
AGENT_CWD_PATH="$workspace/agent-cwd.txt" \
AGENT_STDIN_PATH="$workspace/agent-stdin.txt" \
CLAW_AGENT_COMMAND_CODEX='["agent-stub","exec","--mode","override"]' \
  bash "$workspace/scripts/run_task.sh" --execute "$task_path" >/dev/null

run_one="$project_root/runs/$today/RUN-0001"
assert_file "$run_one/result.json"
assert_contains "$workspace/agent-args.txt" "exec --mode override"
assert_contains "$workspace/agent-stdin.txt" "Task: TASK-001"

set +e
CLAW_AGENT_COMMAND_CODEX='bash -lc "echo hacked"' \
  python3 "$workspace/scripts/execute_job.py" "$run_one" >"$workspace/unsafe-agent.stdout" 2>"$workspace/unsafe-agent.stderr"
unsafe_agent_rc=$?
set -e
[ "$unsafe_agent_rc" -ne 0 ] || { echo "Expected unsafe agent override to fail" >&2; exit 1; }
assert_contains "$workspace/unsafe-agent.stderr" "trusted argv"

HOOK_CAPTURE_PATH="$workspace/hook-capture.json" \
CLAW_HOOK_COMMAND='["python3","'"$workspace"'/scripts/capture_hook.py"]' \
  python3 "$workspace/scripts/dispatch_hooks.py" "$project_root" >/dev/null
assert_file "$workspace/hook-capture.json"
assert_contains "$workspace/hook-capture.json" "\"run_id\": \"RUN-0001\""

run_two="$project_root/runs/$today/RUN-0002"
CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/slow_agent.sh" \
  bash "$workspace/scripts/run_task.sh" "$task_path" >/dev/null

set +e
CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/slow_agent.sh" \
CLAW_AGENT_TIMEOUT_SECONDS=0 \
  python3 "$workspace/scripts/execute_job.py" "$run_two" >"$workspace/timeout.stdout" 2>"$workspace/timeout.stderr"
timeout_rc=$?
set -e
[ "$timeout_rc" -eq 124 ] || { echo "Expected timeout exit code 124, got $timeout_rc" >&2; exit 1; }
assert_contains "$run_two/result.json" '"exit_code": 124'
assert_contains "$run_two/meta.json" '"timeout_seconds": 1'

printf '{"broken": ' > "$run_two/result.json"
python3 "$workspace/scripts/claw.py" status "$project_root" RUN-0002 >"$workspace/status.json"
assert_contains "$workspace/status.json" '"run_id": "RUN-0002"'
assert_contains "$workspace/status.json" '"result_status": "unknown"'

cat > "$workspace/_system/registry/reviewer_policy.yaml" <<'EOF'
reviewer_policy:
  cadence:
    successful_runs_batch: 5
  default_mapping:
    codex: missing-agent
    claude: codex
EOF

set +e
python3 "$workspace/scripts/generate_review_batch.py" "$project_root" >"$workspace/review.stdout" 2>"$workspace/review.stderr"
review_rc=$?
set -e
[ "$review_rc" -ne 0 ] || { echo "Expected reviewer policy validation failure" >&2; exit 1; }
assert_contains "$workspace/review.stderr" "Unknown reviewer agent"

cat > "$project_root/state/hooks/failed/side-effect.json" <<'EOF'
{
  "hook_id": "side-effect",
  "delivery_attempts": 3,
  "max_delivery_attempts": 3,
  "delivery": {
    "status": "failed"
  }
}
EOF

WORKSPACE="$workspace" python3 - <<'PY'
import json
import os
import sys
from pathlib import Path

workspace = Path(os.environ["WORKSPACE"])
sys.path.insert(0, str(workspace / "scripts"))
sys.path.insert(0, str(workspace))

from scripts.reconcile_hooks import is_dead_letter
from _system.engine.agent_exec import build_agent_command

hook_path = workspace / "projects" / "demo-project" / "state" / "hooks" / "failed" / "side-effect.json"
before = hook_path.read_text(encoding="utf-8")
assert is_dead_letter(hook_path) is True
after = hook_path.read_text(encoding="utf-8")
assert before == after, (before, after)

registry_path = workspace / "_system" / "registry" / "agents.yaml"
registry_path.write_text(
    "agents:\n"
    "  codex:\n"
    "    command: agent-stub\n"
    "    args: exec --stdin-mode\n"
    "    prompt_mode: stdin\n"
    "    cwd: project_root\n"
    "    default_timeout_seconds: 5\n",
    encoding="utf-8",
)
command = build_agent_command(
    workspace,
    agent="codex",
    project_root=workspace / "projects" / "demo-project",
    prompt="ignored",
    prompt_path=workspace / "prompt.txt",
)
assert command.prompt_mode == "stdin", command
assert "<" not in command.command, command.command
PY

echo "runtime hardening test: ok"
