#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-dream-knowledge-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}

trap cleanup EXIT

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

rm -rf \
  "$project_root/runs" \
  "$project_root/reviews" \
  "$project_root/state/queue" \
  "$project_root/state/hooks" \
  "$project_root/state/approvals" \
  "$project_root/state/orchestration_state.json" \
  "$project_root/state/knowledge" \
  "$project_root/state/dream_log.md"
mkdir -p \
  "$project_root/runs" \
  "$project_root/reviews/decisions" \
  "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter} \
  "$project_root/state/hooks"/{pending,failed,sent}

cat > "$workspace/scripts/fake_success_agent.py" <<'EOF'
#!/usr/bin/env python3
import sys

sys.stdin.read()
print("knowledge extraction success")
EOF
chmod +x "$workspace/scripts/fake_success_agent.py"

for _ in 1 2 3 4 5; do
  CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
    python3 "$workspace/scripts/claw.py" run "$task_path" --execute >/dev/null
done

manual_out="$tmp_root/manual-dream.json"
python3 "$workspace/scripts/claw.py" dream "$project_root" >"$manual_out"

python3 - "$manual_out" "$project_root" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
project_root = Path(sys.argv[2])

knowledge_root = project_root / "state" / "knowledge"
memory_index = knowledge_root / "MEMORY.md"
project_memory = knowledge_root / "project_memory.md"
run_memories = sorted((knowledge_root / "runs").glob("RUN-*.md"))
state = json.loads((project_root / "state" / "orchestration_state.json").read_text(encoding="utf-8"))

assert payload["status"] == "completed", payload
assert len(run_memories) == 5, run_memories
assert memory_index.is_file(), memory_index
assert project_memory.is_file(), project_memory
assert "Project Memory" in memory_index.read_text(encoding="utf-8"), memory_index.read_text(encoding="utf-8")
assert state["dream"]["last_completed_at"], state
assert state["dream"]["last_files_touched"], state
assert any(entry.get("type") == "dream" for entry in (state.get("tasks") or {}).values()), state
PY

python3 - "$project_root" <<'PY'
import json
from pathlib import Path
import re
import sys
import yaml

project_root = Path(sys.argv[1])
for task_path in (project_root / "tasks").glob("TASK-*.md"):
    text = task_path.read_text(encoding="utf-8")
    match = re.match(r"\A---\n(.*?)\n---\n?", text, re.DOTALL)
    front_matter = yaml.safe_load(match.group(1)) or {}
    body = text[match.end():]
    front_matter["status"] = "done"
    task_path.write_text("---\n" + yaml.safe_dump(front_matter, sort_keys=False, allow_unicode=False).strip() + "\n---\n" + body, encoding="utf-8")

state_path = project_root / "state" / "orchestration_state.json"
state = json.loads(state_path.read_text(encoding="utf-8"))
state["dream"]["last_completed_at"] = None
state["dream"]["last_checked_at"] = None
state["dream"]["last_result"] = None
state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
PY

auto_out="$tmp_root/auto-dream.json"
python3 "$workspace/scripts/claw.py" orchestrate "$project_root" --max-steps 1 --skip-review >"$auto_out"

python3 - "$auto_out" "$project_root" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
project_root = Path(sys.argv[2])

assert payload["status"] == "idle", payload
assert payload["auto_dream"]["status"] == "completed", payload
assert (project_root / "state" / "dream_log.md").is_file(), project_root / "state" / "dream_log.md"
PY

echo "dream knowledge test: ok"
