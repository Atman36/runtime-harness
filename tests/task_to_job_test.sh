#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-task-run-test.XXXXXX")"
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

mkdir -p "$workspace"

cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
mkdir -p "$workspace/scripts"
cp "$repo_root/scripts/run_task.sh" "$workspace/scripts/run_task.sh"

task_path="$workspace/projects/demo-project/tasks/TASK-001.md"
project_root="$workspace/projects/demo-project"
today="$(date +"%Y-%m-%d")"
run_day_root="$project_root/runs/$today"
run_one="$run_day_root/RUN-0001"
run_two="$run_day_root/RUN-0002"

bash "$workspace/scripts/run_task.sh" "$task_path"
bash "$workspace/scripts/run_task.sh" "$task_path"

assert_dir "$run_day_root"
assert_dir "$run_one"
assert_dir "$run_two"

assert_file "$run_one/task.md"
assert_file "$run_one/spec.md"
assert_file "$run_one/prompt.txt"
assert_file "$run_one/meta.json"
assert_file "$run_one/job.json"
assert_file "$run_one/result.json"
assert_file "$run_one/report.md"
assert_file "$run_one/stdout.log"
assert_file "$run_one/stderr.log"

assert_contains "$run_one/prompt.txt" "Project: demo-project"
assert_contains "$run_one/prompt.txt" "Task: TASK-001"
assert_contains "$run_one/prompt.txt" "Spec: ../specs/SPEC-001.md"

assert_contains "$run_one/meta.json" "\"run_id\": \"RUN-0001\""
assert_contains "$run_one/meta.json" "\"task_id\": \"TASK-001\""
assert_contains "$run_one/meta.json" "\"status\": \"created\""
assert_contains "$run_one/meta.json" "\"preferred_agent\": \"codex\""

assert_contains "$run_one/job.json" "\"run_id\": \"RUN-0001\""
assert_contains "$run_one/job.json" "\"project\": \"demo-project\""
assert_contains "$run_one/job.json" "\"task\": {"
assert_contains "$run_one/job.json" "\"spec\": {"

assert_contains "$run_one/result.json" "\"status\": \"pending\""
assert_contains "$run_one/report.md" "- Project: demo-project"
assert_contains "$run_one/report.md" "- Task: TASK-001"
assert_contains "$run_one/report.md" "- Status: pending"

echo "task to job test: ok"
