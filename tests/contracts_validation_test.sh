#!/usr/bin/env bash
# Validates that validate_artifacts.py correctly accepts valid artifacts and
# rejects invalid ones for each schema (job, result, meta).

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-contracts-test.XXXXXX")"
validator="$repo_root/scripts/validate_artifacts.py"

cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT

# ── helpers ──────────────────────────────────────────────────────────────────

assert_valid() {
  local label="$1"
  local file="$2"
  if ! python3 "$validator" "$file" --quiet 2>/dev/null; then
    echo "FAIL: $label — expected valid, got errors" >&2
    python3 "$validator" "$file" 2>&1 >&2 || true
    exit 1
  fi
}

assert_invalid() {
  local label="$1"
  local file="$2"
  if python3 "$validator" "$file" --quiet 2>/dev/null; then
    echo "FAIL: $label — expected validation errors, but got none" >&2
    exit 1
  fi
}

# ── job.json ─────────────────────────────────────────────────────────────────

job_run="$tmp_root/job_run"
mkdir -p "$job_run"

cat > "$job_run/job.json" <<'EOF'
{
  "job_version": 1,
  "run_id": "RUN-0001",
  "run_path": "runs/2024-03-12/RUN-0001",
  "created_at": "2024-03-12T10:00:00Z",
  "project": "demo-project",
  "preferred_agent": "claude",
  "review_policy": "standard",
  "routing": {
    "selected_agent": "claude",
    "selection_source": "routing_rules",
    "routing_rule": "claude-ambiguous-design"
  },
  "execution": {
    "workspace_mode": "git_worktree",
    "workspace_root": "/tmp/demo-project",
    "workspace_materialization_required": true,
    "edit_scope": ["apps", "tests"],
    "parallel_safe": true,
    "concurrency_group": "demo-project:git_worktree:apps,tests"
  },
  "task": {
    "id": "TASK-001",
    "title": "Test task",
    "status": "todo",
    "priority": "medium",
    "source_path": "tasks/TASK-001.md",
    "copied_path": "task.md",
    "needs_review": false,
    "risk_flags": []
  },
  "spec": {
    "source_path": "specs/SPEC-001.md",
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
assert_valid "valid job.json" "$job_run/job.json"

# Missing required fields: run_path, created_at, task, spec, artifacts
invalid_job_run="$tmp_root/invalid_job_run"
mkdir -p "$invalid_job_run"
cat > "$invalid_job_run/job.json" <<'EOF'
{
  "job_version": 1,
  "run_id": "RUN-0001",
  "project": "demo-project"
}
EOF
assert_invalid "job.json missing required fields" "$invalid_job_run/job.json"

# Wrong job_version (const violation)
wrong_ver_run="$tmp_root/wrong_ver_run"
mkdir -p "$wrong_ver_run"
cat > "$wrong_ver_run/job.json" <<'EOF'
{
  "job_version": 2,
  "run_id": "RUN-0001",
  "run_path": "runs/2024-03-12/RUN-0001",
  "created_at": "2024-03-12T10:00:00Z",
  "project": "demo-project",
  "preferred_agent": "codex",
  "task": {
    "id": "TASK-001",
    "needs_review": false,
    "risk_flags": []
  },
  "spec": {
    "source_path": "specs/SPEC-001.md",
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
assert_invalid "job.json wrong job_version" "$wrong_ver_run/job.json"

# needs_review must be boolean, not string
bad_needs_review_run="$tmp_root/bad_needs_review_run"
mkdir -p "$bad_needs_review_run"
cat > "$bad_needs_review_run/job.json" <<'EOF'
{
  "job_version": 1,
  "run_id": "RUN-0001",
  "run_path": "runs/2024-03-12/RUN-0001",
  "created_at": "2024-03-12T10:00:00Z",
  "project": "demo-project",
  "preferred_agent": "codex",
  "routing": {
    "selected_agent": "codex",
    "selection_source": "task_front_matter",
    "routing_rule": null
  },
  "execution": {
    "workspace_mode": "shared_project",
    "workspace_root": "/tmp/demo-project",
    "workspace_materialization_required": false,
    "edit_scope": ["_system", "scripts"],
    "parallel_safe": false,
    "concurrency_group": "demo-project:shared_project:_system,scripts"
  },
  "task": {
    "id": "TASK-001",
    "needs_review": "maybe",
    "risk_flags": []
  },
  "spec": {
    "source_path": "specs/SPEC-001.md",
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
assert_invalid "job.json needs_review not boolean" "$bad_needs_review_run/job.json"

bad_execution_run="$tmp_root/bad_execution_run"
mkdir -p "$bad_execution_run"
cat > "$bad_execution_run/job.json" <<'EOF'
{
  "job_version": 1,
  "run_id": "RUN-0001",
  "run_path": "runs/2024-03-12/RUN-0001",
  "created_at": "2024-03-12T10:00:00Z",
  "project": "demo-project",
  "preferred_agent": "codex",
  "routing": {
    "selected_agent": "codex",
    "selection_source": "project_default",
    "routing_rule": null
  },
  "execution": {
    "workspace_mode": "shared_project",
    "workspace_root": "/tmp/demo-project",
    "workspace_materialization_required": false,
    "edit_scope": "scripts",
    "parallel_safe": false,
    "concurrency_group": "demo-project:shared_project:scripts"
  },
  "task": {
    "id": "TASK-001",
    "needs_review": false,
    "risk_flags": []
  },
  "spec": {
    "source_path": "specs/SPEC-001.md",
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
assert_invalid "job.json execution.edit_scope must be an array" "$bad_execution_run/job.json"

# ── result.json ───────────────────────────────────────────────────────────────

result_run="$tmp_root/result_run"
mkdir -p "$result_run"

cat > "$result_run/result.json" <<'EOF'
{
  "run_id": "RUN-0001",
  "status": "success",
  "created_at": "2024-03-12T10:00:00Z",
  "started_at": "2024-03-12T10:01:00Z",
  "finished_at": "2024-03-12T10:05:00Z",
  "agent": "codex",
  "exit_code": 0,
  "duration_seconds": 240.5,
  "command": "codex exec <prompt>",
  "summary": "Task completed successfully.",
  "validation": {
    "valid": true,
    "errors": {
      "job.json": [],
      "result.json": [],
      "meta.json": []
    }
  }
}
EOF
assert_valid "valid result.json (success)" "$result_run/result.json"

# Pending initial state (sparse)
pending_result_run="$tmp_root/pending_result_run"
mkdir -p "$pending_result_run"
cat > "$pending_result_run/result.json" <<'EOF'
{
  "run_id": "RUN-0001",
  "status": "pending",
  "created_at": "2024-03-12T10:00:00Z",
  "agent": "codex"
}
EOF
assert_valid "valid result.json (pending, sparse)" "$pending_result_run/result.json"

# Invalid status enum
bad_status_run="$tmp_root/bad_status_run"
mkdir -p "$bad_status_run"
cat > "$bad_status_run/result.json" <<'EOF'
{
  "run_id": "RUN-0001",
  "status": "unknown_state"
}
EOF
assert_invalid "result.json invalid status enum" "$bad_status_run/result.json"

# Missing required run_id
missing_run_id_run="$tmp_root/missing_run_id_run"
mkdir -p "$missing_run_id_run"
cat > "$missing_run_id_run/result.json" <<'EOF'
{
  "status": "success"
}
EOF
assert_invalid "result.json missing run_id" "$missing_run_id_run/result.json"

# ── meta.json ─────────────────────────────────────────────────────────────────

meta_run="$tmp_root/meta_run"
mkdir -p "$meta_run"

cat > "$meta_run/meta.json" <<'EOF'
{
  "run_id": "RUN-0001",
  "run_date": "2024-03-12",
  "created_at": "2024-03-12T10:00:00Z",
  "status": "created",
  "project": "demo-project",
  "task_id": "TASK-001",
  "task_title": "Test task",
  "task_path": "tasks/TASK-001.md",
  "spec_path": "specs/SPEC-001.md",
  "preferred_agent": "claude",
  "review_policy": "standard",
  "priority": "medium",
  "routing": {
    "selected_agent": "claude",
    "selection_source": "routing_rules",
    "routing_rule": "claude-ambiguous-design"
  },
  "execution": {
    "workspace_mode": "git_worktree",
    "workspace_root": "/tmp/demo-project",
    "workspace_materialization_required": true,
    "edit_scope": ["apps", "tests"],
    "parallel_safe": true,
    "concurrency_group": "demo-project:git_worktree:apps,tests"
  }
}
EOF
assert_valid "valid meta.json (created status)" "$meta_run/meta.json"

# Completed state with executor and hook
completed_meta_run="$tmp_root/completed_meta_run"
mkdir -p "$completed_meta_run"
cat > "$completed_meta_run/meta.json" <<'EOF'
{
  "run_id": "RUN-0002",
  "run_date": "2024-03-12",
  "created_at": "2024-03-12T10:00:00Z",
  "status": "completed",
  "project": "demo-project",
  "task_id": "TASK-002",
  "preferred_agent": "codex",
  "routing": {
    "selected_agent": "codex",
    "selection_source": "project_default",
    "routing_rule": null
  },
  "execution": {
    "workspace_mode": "shared_project",
    "workspace_root": "/tmp/demo-project",
    "workspace_materialization_required": false,
    "edit_scope": ["_system", "scripts"],
    "parallel_safe": false,
    "concurrency_group": "demo-project:shared_project:_system,scripts"
  },
  "started_at": "2024-03-12T10:01:00Z",
  "finished_at": "2024-03-12T10:05:00Z",
  "last_exit_code": 0,
  "executor": {"agent": "codex", "command": "codex exec <prompt>", "timeout_seconds": 3600},
  "hook": {"delivery_status": "sent"}
}
EOF
assert_valid "valid meta.json (completed with executor)" "$completed_meta_run/meta.json"

# Invalid status enum
bad_meta_status_run="$tmp_root/bad_meta_status_run"
mkdir -p "$bad_meta_status_run"
cat > "$bad_meta_status_run/meta.json" <<'EOF'
{
  "run_id": "RUN-0001",
  "status": "done",
  "project": "demo-project"
}
EOF
assert_invalid "meta.json invalid status enum" "$bad_meta_status_run/meta.json"

# Missing required project field
no_project_meta_run="$tmp_root/no_project_meta_run"
mkdir -p "$no_project_meta_run"
cat > "$no_project_meta_run/meta.json" <<'EOF'
{
  "run_id": "RUN-0001",
  "status": "created"
}
EOF
assert_invalid "meta.json missing project field" "$no_project_meta_run/meta.json"

bad_meta_execution_run="$tmp_root/bad_meta_execution_run"
mkdir -p "$bad_meta_execution_run"
cat > "$bad_meta_execution_run/meta.json" <<'EOF'
{
  "run_id": "RUN-0003",
  "status": "created",
  "project": "demo-project",
  "routing": {
    "selected_agent": "codex",
    "selection_source": "project_default",
    "routing_rule": null
  },
  "execution": {
    "workspace_mode": "shared_project",
    "workspace_root": "/tmp/demo-project",
    "workspace_materialization_required": false,
    "edit_scope": "_system",
    "parallel_safe": false,
    "concurrency_group": "demo-project:shared_project:_system"
  }
}
EOF
assert_invalid "meta.json execution.edit_scope must be an array" "$bad_meta_execution_run/meta.json"

# ── run directory validation ──────────────────────────────────────────────────

full_run="$tmp_root/full_run"
mkdir -p "$full_run"
cp "$job_run/job.json"       "$full_run/job.json"
cp "$result_run/result.json" "$full_run/result.json"
cp "$meta_run/meta.json"     "$full_run/meta.json"

if ! python3 "$validator" "$full_run" --quiet 2>/dev/null; then
  echo "FAIL: valid run directory should pass validation" >&2
  python3 "$validator" "$full_run" 2>&1 >&2 || true
  exit 1
fi

# ── unrecognised artifact name ────────────────────────────────────────────────

unknown_file="$tmp_root/report.json"
cat > "$unknown_file" <<'EOF'
{"foo": "bar"}
EOF
if python3 "$validator" "$unknown_file" --quiet 2>/dev/null; then
  echo "FAIL: unrecognised artifact name should return error" >&2
  exit 1
fi

echo "contracts validation test: ok"
