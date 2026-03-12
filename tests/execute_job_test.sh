#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-execute-job-test.XXXXXX")"
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

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

prompt="$(cat)"

echo "FAKE SUCCESS"
echo "$(printf '%s\n' "$prompt" | grep -F 'Task: TASK-001' | head -n 1)"
echo "$(printf '%s\n' "$prompt" | grep -F 'Spec: ../specs/SPEC-001.md' | head -n 1)"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

cat > "$workspace/scripts/fake_fail_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

prompt="$(cat)"

echo "FAKE FAILURE" >&2
echo "$(printf '%s\n' "$prompt" | grep -F 'Task: TASK-001' | head -n 1)" >&2
exit 7
EOF
chmod +x "$workspace/scripts/fake_fail_agent.sh"

task_path="$workspace/projects/demo-project/tasks/TASK-001.md"
project_root="$workspace/projects/demo-project"
today="$(date +"%Y-%m-%d")"
run_day_root="$project_root/runs/$today"
run_one="$run_day_root/RUN-0001"
run_two="$run_day_root/RUN-0002"

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  bash "$workspace/scripts/run_task.sh" --execute "$task_path"

assert_dir "$run_one"
assert_file "$run_one/meta.json"
assert_file "$run_one/result.json"
assert_file "$run_one/report.md"
assert_file "$run_one/stdout.log"
assert_file "$run_one/stderr.log"
assert_contains "$run_one/meta.json" '"status": "completed"'
assert_contains "$run_one/result.json" '"status": "success"'
assert_contains "$run_one/result.json" '"exit_code": 0'
assert_contains "$run_one/stdout.log" 'FAKE SUCCESS'
assert_contains "$run_one/report.md" '- Agent: codex'
assert_contains "$run_one/report.md" '- Status: success'
assert_contains "$run_one/report.md" 'FAKE SUCCESS'

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_fail_agent.sh" \
  bash "$workspace/scripts/run_task.sh" "$task_path"

if CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_fail_agent.sh" \
  bash "$workspace/scripts/execute_job.sh" "$run_two"; then
  echo "Expected execute_job.sh to fail for non-zero agent exit" >&2
  exit 1
fi

assert_dir "$run_two"
assert_contains "$run_two/meta.json" '"status": "failed"'
assert_contains "$run_two/result.json" '"status": "failed"'
assert_contains "$run_two/result.json" '"exit_code": 7'
assert_contains "$run_two/stderr.log" 'FAKE FAILURE'
assert_contains "$run_two/report.md" '- Status: failed'
assert_contains "$run_two/report.md" '- Exit code: 7'
assert_contains "$run_two/report.md" 'inspect stderr.log'

echo "execute job test: ok"
