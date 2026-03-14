#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-advisory-test.XXXXXX")"
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

assert_not_contains() {
  local path="$1"
  local unexpected="$2"
  if grep -Fq -- "$unexpected" "$path"; then
    echo "Did not expect '$unexpected' in $path" >&2
    exit 1
  fi
}

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/run_task.sh" "$workspace/scripts/run_task.sh"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/dispatch_hooks.py" "$workspace/scripts/dispatch_hooks.py"
cp "$repo_root/scripts/reconcile_hooks.py" "$workspace/scripts/reconcile_hooks.py"

project_root="$workspace/projects/demo-project"
rm -rf "$project_root/runs" "$project_root/reviews" "$project_root/state/queue"
mkdir -p "$project_root/runs" "$project_root/reviews"
mkdir -p "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval}
mkdir -p "$project_root/state/hooks"/{pending,failed,sent}

task_path="$project_root/tasks/TASK-001.md"
today="$(date +"%Y-%m-%d")"
run_one="$project_root/runs/$today/RUN-0001"
target_file="$project_root/docs/advisory-target.txt"
advisory_env_path="$workspace/advisory-env.txt"

python3 - "$task_path" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace("status: todo\n", "status: todo\nmode: advisory\n", 1)
path.write_text(text, encoding="utf-8")
PY

cat > "$workspace/scripts/fake_advisory_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

printf '%s' "${CLAW_ADVISORY:-}" > "${ADVISORY_ENV_PATH:?ADVISORY_ENV_PATH is required}"
cat >/dev/null
echo "ADVISORY AGENT OK"
EOF
chmod +x "$workspace/scripts/fake_advisory_agent.sh"

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_advisory_agent.sh" \
ADVISORY_ENV_PATH="$advisory_env_path" \
  bash "$workspace/scripts/run_task.sh" --execute "$task_path"

assert_file "$run_one/job.json"
assert_file "$run_one/meta.json"
assert_file "$run_one/result.json"
assert_file "$run_one/stderr.log"
assert_file "$advisory_env_path"

assert_contains "$run_one/job.json" '"mode": "advisory"'
assert_contains "$run_one/meta.json" '"advisory": true'
assert_contains "$run_one/result.json" '"status": "success"'
assert_contains "$run_one/result.json" '"valid": true'
assert_contains "$run_one/stderr.log" 'WARNING advisory artifact missing: advice.md'
assert_contains "$run_one/stderr.log" 'WARNING advisory artifact missing: patch.diff'
assert_contains "$run_one/stderr.log" 'WARNING advisory artifact missing: review_findings.json'
assert_contains "$advisory_env_path" '1'

git -C "$project_root" init >/dev/null 2>&1
printf 'before\n' > "$target_file"

cat > "$run_one/patch.diff" <<'EOF'
diff --git a/docs/advisory-target.txt b/docs/advisory-target.txt
--- a/docs/advisory-target.txt
+++ b/docs/advisory-target.txt
@@ -1 +1 @@
-before
+after
EOF

cat > "$run_one/review_findings.json" <<'EOF'
{
  "severity": "medium",
  "findings": [
    {
      "file": "docs/advisory-target.txt",
      "line": 1,
      "issue": "Needs operator review",
      "suggestion": "Apply after review"
    }
  ],
  "recommendation": "apply_patch"
}
EOF

dry_run_out="$tmp_root/dry-run.out"
python3 "$workspace/scripts/claw.py" apply-patch "$project_root" "RUN-0001" > "$dry_run_out"

assert_contains "$dry_run_out" '"status": "dry_run"'
assert_contains "$dry_run_out" '"severity": "medium"'
assert_contains "$dry_run_out" 'diff --git a/docs/advisory-target.txt b/docs/advisory-target.txt'
assert_contains "$target_file" 'before'
assert_not_contains "$target_file" 'after'

confirm_out="$tmp_root/confirm.out"
python3 "$workspace/scripts/claw.py" apply-patch "$project_root" "RUN-0001" --confirm > "$confirm_out"

assert_contains "$confirm_out" '"status": "applied"'
assert_contains "$confirm_out" '"severity": "medium"'
assert_contains "$target_file" 'after'
assert_contains "$run_one/events.jsonl" '"event_type": "patch_applied"'
assert_contains "$run_one/events.jsonl" '"severity": "medium"'

echo "advisory mode test: ok"
