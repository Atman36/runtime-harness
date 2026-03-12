#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-review-batch-cli-test.XXXXXX")"
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

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"

project_root="$workspace/projects/demo-project"
today="$(date +"%Y-%m-%d")"
failed_run_dir="$project_root/runs/$today/RUN-9001"
reviews_dir="$project_root/reviews"

rm -rf "$project_root/runs" "$project_root/reviews"
mkdir -p "$failed_run_dir" "$reviews_dir"

cat > "$failed_run_dir/meta.json" <<EOF
{
  "run_id": "RUN-9001",
  "run_date": "$today",
  "status": "failed",
  "project": "demo-project",
  "task_id": "TASK-001",
  "task_title": "Review CLI task",
  "preferred_agent": "codex"
}
EOF

cat > "$failed_run_dir/result.json" <<EOF
{
  "run_id": "RUN-9001",
  "status": "failed",
  "agent": "codex"
}
EOF

cat > "$failed_run_dir/job.json" <<EOF
{
  "job_version": 1,
  "run_id": "RUN-9001",
  "run_path": "runs/$today/RUN-9001",
  "created_at": "2026-01-01T00:00:00Z",
  "project": "demo-project",
  "preferred_agent": "codex",
  "task": {
    "id": "TASK-001",
    "title": "Review CLI task",
    "needs_review": false,
    "risk_flags": []
  },
  "spec": {"source_path": "specs/SPEC-001.md", "copied_path": "spec.md"},
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

dry_run_out="$tmp_root/review_batch_dry_run.txt"
python3 "$workspace/scripts/claw.py" review-batch "$project_root" --dry-run > "$dry_run_out"

assert_file "$dry_run_out"
assert_contains "$dry_run_out" "Project: demo-project"
assert_contains "$dry_run_out" "Would write immediate batch"

if compgen -G "$reviews_dir/REVIEW-*.json" > /dev/null; then
  echo "Dry run should not create review batch files" >&2
  exit 1
fi

run_out="$tmp_root/review_batch_run.txt"
python3 "$workspace/scripts/claw.py" review-batch "$project_root" > "$run_out"

assert_file "$run_out"
assert_contains "$run_out" "Project: demo-project"
assert_contains "$run_out" "Written: reviews/REVIEW-"

batch_json="$(find "$reviews_dir" -maxdepth 1 -name 'REVIEW-*.json' | head -n 1)"
decision_json="$(find "$reviews_dir/decisions" -maxdepth 1 -name '*.json' | head -n 1)"

assert_file "$batch_json"
assert_file "$decision_json"
assert_contains "$batch_json" '"trigger_type": "immediate"'
assert_contains "$decision_json" '"run_id": "RUN-9001"'

echo "review batch cli test: ok"
