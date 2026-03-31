#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-task-lifecycle-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}

trap cleanup EXIT

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

pass() {
  echo "ok: $1"
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

reset_project() {
  rm -rf \
    "$project_root/runs" \
    "$project_root/reviews" \
    "$project_root/state/queue" \
    "$project_root/state/hooks" \
    "$project_root/state/approvals" \
    "$project_root/state/orchestration_state.json" \
    "$project_root/state/review_cadence.json" \
    "$project_root/state/metrics_snapshot.json"
  mkdir -p \
    "$project_root/runs" \
    "$project_root/reviews/decisions" \
    "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter} \
    "$project_root/state/hooks"/{pending,failed,sent}
  python3 - "$task_path" <<'PY'
from pathlib import Path
import re
import sys
import yaml

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
match = re.match(r"\A---\n(.*?)\n---\n?", text, re.DOTALL)
front_matter = yaml.safe_load(match.group(1)) or {}
body = text[match.end():]
for key in ["runtime_task_id", "type", "runtime_status", "startTime", "endTime", "outputFile", "outputOffset", "notified"]:
    front_matter.pop(key, None)
front_matter["status"] = "todo"
path.write_text("---\n" + yaml.safe_dump(front_matter, sort_keys=False, allow_unicode=False).strip() + "\n---\n" + body, encoding="utf-8")
PY
}

python3 - "$workspace/scripts/fake_success_agent.py" <<'PY'
from pathlib import Path
import sys

Path(sys.argv[1]).write_text(
    "#!/usr/bin/env python3\n"
    "import sys\n"
    "sys.stdin.read()\n"
    "print('TASK LIFECYCLE SUCCESS')\n",
    encoding="utf-8",
)
PY
chmod +x "$workspace/scripts/fake_success_agent.py"

reset_project

enqueue_out="$tmp_root/enqueue.json"
(cd "$workspace" && python3 scripts/claw.py enqueue "$task_path") > "$enqueue_out"

python3 - "$enqueue_out" "$task_path" "$project_root" <<'PY' || fail "enqueue should seed pending lifecycle state"
import json
import re
import sys
from pathlib import Path

enqueue_payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
task_text = Path(sys.argv[2]).read_text(encoding="utf-8")
state = json.loads((Path(sys.argv[3]) / "state" / "orchestration_state.json").read_text(encoding="utf-8"))

assert enqueue_payload["status"] == "queued", enqueue_payload
assert re.search(r"runtime_task_id: a[0-9a-z]{8}", task_text), task_text
assert "type: local_agent" in task_text, task_text
assert "runtime_status: pending" in task_text, task_text
assert "outputFile: runs/" in task_text, task_text
assert "outputOffset: 0" in task_text, task_text

tasks = state.get("tasks") or {}
assert len(tasks) == 1, tasks
entry = next(iter(tasks.values()))
assert entry["status"] == "pending", entry
assert entry["type"] == "local_agent", entry
assert entry["task_id"] == "TASK-001", entry
assert entry["selected_agent"] == "codex", entry
assert entry["outputOffset"] == 0, entry
assert state["agentRegistry"]["codex"]["active_task_ids"] == [entry["id"]], state
print("ok")
PY
pass "enqueue persists pending task lifecycle metadata"

worker_out="$tmp_root/worker.json"
(cd "$workspace" && CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" python3 scripts/claw.py worker "$project_root" --once --skip-review) > "$worker_out"

python3 - "$worker_out" "$task_path" "$project_root" <<'PY' || fail "worker should finalize lifecycle state"
import json
import re
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8").strip().splitlines()[-1])
task_text = Path(sys.argv[2]).read_text(encoding="utf-8")
state = json.loads((Path(sys.argv[3]) / "state" / "orchestration_state.json").read_text(encoding="utf-8"))

assert payload["queue_state"] == "done", payload
assert payload["result_status"] == "success", payload
assert "runtime_status: completed" in task_text, task_text
assert re.search(r"outputOffset: [1-9][0-9]*", task_text), task_text
assert "notified: true" in task_text, task_text

entry = next(iter((state.get("tasks") or {}).values()))
assert entry["status"] == "completed", entry
assert entry["notified"] is True, entry
assert entry["endTime"], entry
assert entry["outputOffset"] > 0, entry
assert state["agentRegistry"]["codex"]["active_task_ids"] == [], state
print("ok")
PY
pass "worker updates terminal lifecycle state and clears active registry"

python3 "$workspace/scripts/validate_artifacts.py" "$project_root/state/orchestration_state.json" --quiet >/dev/null
pass "orchestration_state schema validates"

echo ""
echo "task_lifecycle_test: all tests passed"
