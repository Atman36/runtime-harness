#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-concurrency-stress-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}

trap cleanup EXIT

assert_eq() {
  local actual="$1"
  local expected="$2"
  if [ "$actual" != "$expected" ]; then
    echo "Expected '$expected', got '$actual'" >&2
    exit 1
  fi
}

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
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

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
echo "CONCURRENCY SUCCESS"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

for _ in 1 2 3 4 5 6; do
  python3 "$workspace/scripts/claw.py" enqueue "$task_path" >/dev/null
done

for round in 1 2 3; do
  for index in 1 2 3 4 5 6; do
    CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
      python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review >"$workspace/worker-$round-$index.stdout" 2>"$workspace/worker-$round-$index.stderr" &
  done
  wait

  done_count="$(find "$project_root/state/queue/done" -maxdepth 1 -name 'RUN-*.json' | wc -l | tr -d ' ')"
  pending_count="$(find "$project_root/state/queue/pending" -maxdepth 1 -name 'RUN-*.json' | wc -l | tr -d ' ')"
  if [ "$done_count" = "6" ]; then
    break
  fi
  if [ "$pending_count" = "0" ]; then
    break
  fi
done

running_count="$(find "$project_root/state/queue/running" -maxdepth 1 -name 'RUN-*.json' | wc -l | tr -d ' ')"

assert_eq "$done_count" "6"
assert_eq "$pending_count" "0"
assert_eq "$running_count" "0"

WORKSPACE="$workspace" TODAY="$today" python3 - <<'PY'
import json
import os
from pathlib import Path

workspace = Path(os.environ["WORKSPACE"])
project_root = workspace / "projects" / "demo-project"
today = os.environ["TODAY"]

done_dir = project_root / "state" / "queue" / "done"
run_ids = sorted(path.stem for path in done_dir.glob("RUN-*.json"))
assert run_ids == [f"RUN-{index:04d}" for index in range(1, 7)], run_ids

for run_id in run_ids:
    result = json.loads((project_root / "runs" / today / run_id / "result.json").read_text(encoding="utf-8"))
    assert result["status"] == "success", result
PY

git init "$workspace" >/dev/null 2>&1
git -C "$workspace" config user.name "Codex"
git -C "$workspace" config user.email "codex@example.com"
git -C "$workspace" add . >/dev/null
git -C "$workspace" commit -m "test fixture" >/dev/null

python3 - "$project_root/tasks/TASK-001.md" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace("preferred_agent: auto\n", "preferred_agent: codex\nworkspace_mode: git_worktree\n")
path.write_text(text, encoding="utf-8")
PY

bash "$workspace/scripts/run_task.sh" "$task_path" >/dev/null
run_path="$project_root/runs/$today/RUN-0007"

for index in 1 2; do
  WORKSPACE="$workspace" RUN_PATH="$run_path" python3 -c '
import os
import sys
from pathlib import Path

workspace = Path(os.environ["WORKSPACE"])
run_dir = Path(os.environ["RUN_PATH"])
sys.path.insert(0, str(workspace / "scripts"))

from execute_job import ensure_git_worktree, project_root_from_run_dir

project_root = project_root_from_run_dir(run_dir)
ctx = ensure_git_worktree(project_root, run_dir)
print(ctx.workspace_root)
' >"$workspace/worktree-root-$index.txt" &
done
wait

cmp "$workspace/worktree-root-1.txt" "$workspace/worktree-root-2.txt" >/dev/null
[ -d "$(cat "$workspace/worktree-root-1.txt")" ] || { echo "Expected worktree root to exist" >&2; exit 1; }

echo "concurrency stress test: ok"
