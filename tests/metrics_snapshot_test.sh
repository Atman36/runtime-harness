#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-metrics-snapshot-test.XXXXXX")"
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

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/dispatch_hooks.py" "$workspace/scripts/dispatch_hooks.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/reconcile_hooks.py" "$workspace/scripts/reconcile_hooks.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"

project_root="$workspace/projects/demo-project"
today="$(date +"%Y-%m-%d")"
snapshot_path="$project_root/state/metrics_snapshot.json"

rm -rf "$project_root/runs" "$project_root/reviews" "$project_root/state/queue"
mkdir -p "$project_root/runs/$today/RUN-1001" "$project_root/runs/$today/RUN-1002"
mkdir -p "$project_root/reviews"
mkdir -p "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval}
mkdir -p "$project_root/state/hooks"/{pending,failed,sent}

cat > "$project_root/runs/$today/RUN-1001/meta.json" <<EOF
{
  "run_id": "RUN-1001",
  "run_date": "$today",
  "status": "completed",
  "project": "demo-project",
  "task_id": "TASK-001",
  "task_title": "Successful run",
  "preferred_agent": "codex"
}
EOF

cat > "$project_root/runs/$today/RUN-1001/result.json" <<EOF
{
  "run_id": "RUN-1001",
  "status": "success",
  "agent": "codex",
  "finished_at": "2026-03-13T10:00:00Z"
}
EOF

cat > "$project_root/runs/$today/RUN-1001/job.json" <<EOF
{
  "job_version": 1,
  "run_id": "RUN-1001",
  "run_path": "runs/$today/RUN-1001",
  "created_at": "2026-03-13T09:55:00Z",
  "project": "demo-project",
  "preferred_agent": "codex",
  "task": {
    "id": "TASK-001",
    "title": "Successful run",
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

cat > "$project_root/runs/$today/RUN-1002/meta.json" <<EOF
{
  "run_id": "RUN-1002",
  "run_date": "$today",
  "status": "failed",
  "project": "demo-project",
  "task_id": "TASK-002",
  "task_title": "Failed run",
  "preferred_agent": "codex"
}
EOF

cat > "$project_root/runs/$today/RUN-1002/result.json" <<EOF
{
  "run_id": "RUN-1002",
  "status": "failed",
  "agent": "codex",
  "finished_at": "2026-03-13T10:05:00Z"
}
EOF

cat > "$project_root/runs/$today/RUN-1002/job.json" <<EOF
{
  "job_version": 1,
  "run_id": "RUN-1002",
  "run_path": "runs/$today/RUN-1002",
  "created_at": "2026-03-13T10:00:00Z",
  "project": "demo-project",
  "preferred_agent": "codex",
  "task": {
    "id": "TASK-002",
    "title": "Failed run",
    "needs_review": true,
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

python3 "$workspace/scripts/claw.py" review-batch "$project_root" >/dev/null

assert_file "$snapshot_path"

status_out="$tmp_root/status.json"
python3 "$workspace/scripts/claw.py" openclaw status "$project_root" > "$status_out"

python3 - "$snapshot_path" "$status_out" <<'PY'
import json, sys
snapshot = json.loads(open(sys.argv[1]).read())
status = json.loads(open(sys.argv[2]).read())

assert snapshot["project"] == "demo-project", snapshot
assert snapshot["runs"]["total"] == 2, snapshot
assert snapshot["runs"]["by_status"]["success"] == 1, snapshot
assert snapshot["runs"]["by_status"]["failed"] == 1, snapshot
assert snapshot["reviews"]["batch_count"] == 1, snapshot
assert snapshot["reviews"]["pending_decisions"] == 1, snapshot
assert len(snapshot["recent_runs"]) == 2, snapshot

assert "metrics" in status, status
assert status["pending_reviews"] == 1, status
assert status["metrics"]["runs"]["total"] == 2, status
assert status["metrics"]["reviews"]["batch_count"] == 1, status
assert status["recent_runs"][0]["run_id"] == "RUN-1002", status
PY

echo "metrics snapshot test: ok"
