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
mkdir -p "$workspace/bin"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/run_task.sh" "$workspace/scripts/run_task.sh"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/execute_job.sh" "$workspace/scripts/execute_job.sh"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
rm -rf "$workspace/projects/demo-project/runs" "$workspace/projects/demo-project/state/queue"
mkdir -p "$workspace/projects/demo-project/runs" "$workspace/projects/demo-project/state/queue"/{pending,running,done,failed,awaiting_approval}

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

cat > "$workspace/bin/agent-stub" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%s\n' "$*" > "${AGENT_ARGS_PATH:?AGENT_ARGS_PATH is required}"
pwd > "${AGENT_CWD_PATH:?AGENT_CWD_PATH is required}"

stdin_payload="$(cat)"
printf '%s' "$stdin_payload" > "${AGENT_STDIN_PATH:?AGENT_STDIN_PATH is required}"

echo "STUB SUCCESS"
echo "$(printf '%s\n' "$stdin_payload" | grep -F 'Task: TASK-001' | head -n 1)"
EOF
chmod +x "$workspace/bin/agent-stub"

task_path="$workspace/projects/demo-project/tasks/TASK-001.md"
project_root="$workspace/projects/demo-project"
project_root_resolved="$(cd "$project_root" && pwd)"
today="$(date +"%Y-%m-%d")"
run_day_root="$project_root/runs/$today"
run_one="$run_day_root/RUN-0001"
run_two="$run_day_root/RUN-0002"
run_three="$run_day_root/RUN-0003"
hook_one_pending="$project_root/state/hooks/pending/${today}--RUN-0001.json"

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  bash "$workspace/scripts/run_task.sh" --execute "$task_path"

assert_dir "$run_one"
assert_file "$run_one/meta.json"
assert_file "$run_one/result.json"
assert_file "$run_one/report.md"
assert_file "$run_one/stdout.log"
assert_file "$run_one/stderr.log"
assert_file "$run_one/agent_stream.jsonl"
assert_contains "$run_one/meta.json" '"status": "completed"'
assert_contains "$run_one/result.json" '"status": "success"'
assert_contains "$run_one/result.json" '"exit_code": 0'
assert_contains "$run_one/result.json" '"validation": {'
assert_contains "$run_one/result.json" '"valid": true'
assert_contains "$run_one/result.json" '"delivery": {'
assert_contains "$run_one/result.json" '"status": "pending_delivery"'
assert_contains "$run_one/result.json" '"hook_status": "pending"'
assert_contains "$run_one/meta.json" '"delivery": {'
assert_contains "$run_one/meta.json" '"status": "pending_delivery"'
assert_contains "$run_one/stdout.log" 'FAKE SUCCESS'
assert_contains "$run_one/report.md" '- Agent: codex'
assert_contains "$run_one/report.md" '- Status: success'
assert_contains "$run_one/report.md" 'FAKE SUCCESS'
assert_file "$hook_one_pending"

python3 - "$run_one/agent_stream.jsonl" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
assert len(records) >= 3, records
assert records[0]["type"] == "status", records
assert records[0]["text"] == "run_start", records
assert records[-1]["type"] == "status", records
assert records[-1]["text"] == "run_end", records
assert any(record["type"] == "message" and "FAKE SUCCESS" in record["text"] for record in records), records
for index, record in enumerate(records, start=1):
    assert isinstance(record["ts"], str) and record["ts"], record
    assert isinstance(record["at"], str) and record["at"], record
    assert record["type"] in {"message", "reasoning", "command", "status", "stderr"}, record
    assert isinstance(record["text"], str), record
    assert isinstance(record["message"], str), record
    assert isinstance(record["phase"], str) and record["phase"], record
    assert record["job_id"] == "RUN-0001", record
    assert record["run_id"] == "RUN-0001", record
    assert record["seq"] == index, records
PY

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
assert_contains "$run_two/result.json" '"validation": {'
assert_contains "$run_two/stderr.log" 'FAKE FAILURE'
assert_contains "$run_two/report.md" '- Status: failed'
assert_contains "$run_two/report.md" '- Exit code: 7'
assert_contains "$run_two/report.md" 'inspect stderr.log'
assert_file "$run_two/agent_stream.jsonl"

python3 - "$run_two/agent_stream.jsonl" <<'PY'
import json
import sys
from pathlib import Path

records = [json.loads(line) for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if line.strip()]
assert any(record["type"] == "stderr" and "FAKE FAILURE" in record["text"] for record in records), records
assert records[-1]["type"] == "status", records
assert records[-1]["text"] == "run_end", records
PY

cat > "$workspace/_system/registry/agents.yaml" <<'EOF'
agents:
    # valid YAML with deeper agent indentation than the original parser expected
    codex:
      label: Codex
      command: agent-stub # inline comment should not become part of the command
      args: exec --mode stdin --flag registry
      prompt_mode: stdin
      cwd: project_root
      default_timeout_seconds: 12
      tags:
        - primary
        - sandboxed
    claude:
      label: Claude
      command: agent-stub
      args: -p --output-format text
      prompt_mode: arg
      cwd: project_root
      default_timeout_seconds: 34
EOF

bash "$workspace/scripts/run_task.sh" "$task_path"

WORKSPACE="$workspace" PYTHONPATH="$workspace/scripts" python3 - <<'PY'
import os
from pathlib import Path

from execute_job import parse_agents_registry

registry = parse_agents_registry(Path(os.environ["WORKSPACE"]) / "_system" / "registry" / "agents.yaml")
assert registry["codex"]["command"] == "agent-stub", registry
assert registry["codex"]["tags"] == ["primary", "sandboxed"], registry
PY

PATH="$workspace/bin:$PATH" \
AGENT_ARGS_PATH="$workspace/agent-args.txt" \
AGENT_CWD_PATH="$workspace/agent-cwd.txt" \
AGENT_STDIN_PATH="$workspace/agent-stdin.txt" \
  bash "$workspace/scripts/execute_job.sh" "$run_three"

assert_dir "$run_three"
assert_contains "$run_three/result.json" '"status": "success"'
assert_contains "$run_three/result.json" '"exit_code": 0'
assert_contains "$run_three/stdout.log" 'STUB SUCCESS'
assert_file "$run_three/agent_stream.jsonl"
assert_contains "$workspace/agent-args.txt" 'exec --mode stdin --flag registry'
assert_contains "$workspace/agent-stdin.txt" 'Task: TASK-001'
assert_contains "$workspace/agent-cwd.txt" "$project_root_resolved"

echo "execute job test: ok"
