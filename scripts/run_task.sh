#!/usr/bin/env bash

set -euo pipefail

execute_after_create=0

if [ "$#" -ge 1 ] && [ "$1" = "--execute" ]; then
  execute_after_create=1
  shift
fi

if [ "$#" -ne 1 ]; then
  echo "Usage: bash scripts/run_task.sh [--execute] <task-path>" >&2
  exit 1
fi

task_input="$1"

if [ ! -f "$task_input" ]; then
  echo "Task file not found: $task_input" >&2
  exit 1
fi

script_dir="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
task_path="$(cd "$(dirname "$task_input")" && pwd)/$(basename "$task_input")"
task_dir="$(dirname "$task_path")"
project_root="$(cd "$task_dir/.." && pwd)"
project_slug="$(basename "$project_root")"
project_state_path="$project_root/state/project.yaml"

prompt_template="$repo_root/_system/templates/prompt.template.md"
report_template="$repo_root/_system/templates/report.template.md"

if [ ! -f "$prompt_template" ]; then
  echo "Prompt template not found: $prompt_template" >&2
  exit 1
fi

if [ ! -f "$report_template" ]; then
  echo "Report template not found: $report_template" >&2
  exit 1
fi

read_front_matter_value() {
  local file_path="$1"
  local key="$2"

  awk -F': ' -v search_key="$key" '
    BEGIN {
      in_front_matter = 0
    }
    /^---$/ {
      if (in_front_matter == 0) {
        in_front_matter = 1
        next
      }
      exit
    }
    in_front_matter == 1 && $1 == search_key {
      value = substr($0, index($0, ":") + 1)
      sub(/^ /, "", value)
      print value
      exit
    }
  ' "$file_path" | sed 's/^"//; s/"$//'
}

read_project_state_value() {
  local file_path="$1"
  local key="$2"

  awk -F': ' -v search_key="$key" '$1 == search_key { print substr($0, index($0, ":") + 2); exit }' "$file_path"
}

resolve_path() {
  local base_dir="$1"
  local target_path="$2"
  local target_dir
  local target_name

  if [[ "$target_path" = /* ]]; then
    target_dir="$(cd "$(dirname "$target_path")" && pwd)"
    target_name="$(basename "$target_path")"
    printf '%s/%s\n' "$target_dir" "$target_name"
    return
  fi

  target_dir="$(cd "$base_dir/$(dirname "$target_path")" && pwd)"
  target_name="$(basename "$target_path")"
  printf '%s/%s\n' "$target_dir" "$target_name"
}

render_template() {
  local template_path="$1"
  local destination_path="$2"

  sed \
    -e "s|{{PROJECT_SLUG}}|$project_slug|g" \
    -e "s|{{TASK_ID}}|$task_id|g" \
    -e "s|{{SPEC_PATH}}|$spec_reference|g" \
    -e "s|{{CREATED_AT}}|$created_at|g" \
    "$template_path" > "$destination_path"
}

json_escape() {
  printf '%s' "$1" | python3 -c 'import json, sys; print(json.dumps(sys.stdin.read())[1:-1], end="")'
}

validate_json_boolean() {
  local raw_value="$1"
  local field_name="$2"
  local file_path="$3"

  case "$raw_value" in
    true|false)
      ;;
    *)
      echo "Task front matter $field_name must be true or false: $file_path" >&2
      exit 1
      ;;
  esac
}

validate_json_array() {
  local raw_value="$1"
  local field_name="$2"
  local file_path="$3"

  if ! python3 - "$raw_value" <<'PY'
import json
import sys

try:
    parsed = json.loads(sys.argv[1])
except json.JSONDecodeError:
    raise SystemExit(1)

if not isinstance(parsed, list):
    raise SystemExit(1)
PY
  then
    echo "Task front matter $field_name must be a JSON array: $file_path" >&2
    exit 1
  fi
}

task_id="$(read_front_matter_value "$task_path" "id")"
task_title="$(read_front_matter_value "$task_path" "title")"
task_status="$(read_front_matter_value "$task_path" "status")"
spec_reference="$(read_front_matter_value "$task_path" "spec")"
preferred_agent="$(read_front_matter_value "$task_path" "preferred_agent")"
review_policy="$(read_front_matter_value "$task_path" "review_policy")"
priority="$(read_front_matter_value "$task_path" "priority")"
project_from_task="$(read_front_matter_value "$task_path" "project")"
needs_review="$(read_front_matter_value "$task_path" "needs_review")"
risk_flags="$(read_front_matter_value "$task_path" "risk_flags")"

if [ -z "$needs_review" ]; then
  needs_review="false"
fi

if [ -z "$risk_flags" ]; then
  risk_flags="[]"
fi

validate_json_boolean "$needs_review" "needs_review" "$task_path"
validate_json_array "$risk_flags" "risk_flags" "$task_path"

if [ -z "$task_id" ] || [ -z "$spec_reference" ]; then
  echo "Task front matter must include id and spec: $task_path" >&2
  exit 1
fi

if [ -n "$project_from_task" ] && [ "$project_from_task" != "$project_slug" ]; then
  echo "Task project '$project_from_task' does not match project directory '$project_slug'" >&2
  exit 1
fi

if [ ! -f "$project_state_path" ]; then
  echo "Project state file not found: $project_state_path" >&2
  exit 1
fi

project_slug_from_state="$(read_project_state_value "$project_state_path" "slug")"

if [ -z "$project_slug_from_state" ]; then
  echo "Project state file must include slug: $project_state_path" >&2
  exit 1
fi

if [ "$project_slug_from_state" != "$project_slug" ]; then
  echo "Project slug '$project_slug_from_state' in state/project.yaml does not match directory '$project_slug'" >&2
  exit 1
fi

spec_path="$(resolve_path "$task_dir" "$spec_reference")"

if [ ! -f "$spec_path" ]; then
  echo "Spec file not found: $spec_path" >&2
  exit 1
fi

run_date="$(date +"%Y-%m-%d")"
created_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
run_day_root="$project_root/runs/$run_date"

mkdir -p "$run_day_root"

last_run_number="$(
  find "$run_day_root" -mindepth 1 -maxdepth 1 -type d -name 'RUN-*' -print 2>/dev/null \
    | sed 's#.*/RUN-##' \
    | sort -n \
    | tail -n 1
)"

if [ -z "$last_run_number" ]; then
  next_run_number=1
else
  next_run_number=$((10#$last_run_number + 1))
fi

run_id="$(printf 'RUN-%04d' "$next_run_number")"
run_dir="$run_day_root/$run_id"

mkdir -p "$run_dir"

cp "$task_path" "$run_dir/task.md"
cp "$spec_path" "$run_dir/spec.md"
render_template "$prompt_template" "$run_dir/prompt.txt"
render_template "$report_template" "$run_dir/report.md"
: > "$run_dir/stdout.log"
: > "$run_dir/stderr.log"

task_source_rel="${task_path#$project_root/}"
spec_source_rel="${spec_path#$project_root/}"

cat > "$run_dir/meta.json" <<EOF
{
  "run_id": "$(json_escape "$run_id")",
  "run_date": "$(json_escape "$run_date")",
  "created_at": "$(json_escape "$created_at")",
  "status": "created",
  "project": "$(json_escape "$project_slug")",
  "task_id": "$(json_escape "$task_id")",
  "task_title": "$(json_escape "$task_title")",
  "task_path": "$(json_escape "$task_source_rel")",
  "spec_path": "$(json_escape "$spec_source_rel")",
  "preferred_agent": "$(json_escape "$preferred_agent")",
  "review_policy": "$(json_escape "$review_policy")",
  "priority": "$(json_escape "$priority")"
}
EOF

cat > "$run_dir/job.json" <<EOF
{
  "job_version": 1,
  "run_id": "$(json_escape "$run_id")",
  "run_path": "runs/$(json_escape "$run_date")/$(json_escape "$run_id")",
  "created_at": "$(json_escape "$created_at")",
  "project": "$(json_escape "$project_slug")",
  "preferred_agent": "$(json_escape "$preferred_agent")",
  "review_policy": "$(json_escape "$review_policy")",
  "task": {
    "id": "$(json_escape "$task_id")",
    "title": "$(json_escape "$task_title")",
    "status": "$(json_escape "$task_status")",
    "priority": "$(json_escape "$priority")",
    "source_path": "$(json_escape "$task_source_rel")",
    "copied_path": "task.md",
    "needs_review": $needs_review,
    "risk_flags": $risk_flags
  },
  "spec": {
    "source_path": "$(json_escape "$spec_source_rel")",
    "copied_path": "spec.md"
  },
  "artifacts": {
    "prompt_path": "prompt.txt",
    "meta_path": "meta.json",
    "report_path": "report.md",
    "result_path": "result.json",
    "stdout_path": "stdout.log",
    "stderr_path": "stderr.log"
  }
}
EOF

cat > "$run_dir/result.json" <<EOF
{
  "run_id": "$(json_escape "$run_id")",
  "status": "pending",
  "created_at": "$(json_escape "$created_at")",
  "agent": "$(json_escape "$preferred_agent")"
}
EOF

printf 'Created task run: %s\n' "$run_dir"

if [ "$execute_after_create" -eq 1 ]; then
  bash "$script_dir/execute_job.sh" "$run_dir"
fi
