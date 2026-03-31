#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-mark-done-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}

trap cleanup EXIT

mkdir -p "$workspace"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp -R "$repo_root/scripts" "$workspace/scripts"

project_root="$workspace/projects/demo-project"
task_one="$project_root/tasks/TASK-001.md"
task_two="$project_root/tasks/TASK-002.md"

python3 - "$task_one" "$task_two" <<'PY'
from pathlib import Path
import sys

for raw_path in sys.argv[1:]:
    path = Path(raw_path)
    text = path.read_text(encoding="utf-8")
    text = text.replace("status: done", "status: todo")
    path.write_text(text, encoding="utf-8")

task_one = Path(sys.argv[1])
text = task_one.read_text(encoding="utf-8")
text = text.replace("needs_review: false", "needs_review: true")
task_one.write_text(text, encoding="utf-8")
PY

set +e
python3 "$workspace/scripts/claw.py" mark-done "$project_root" TASK-001 >"$tmp_root/review-required.json" 2>"$tmp_root/review-required.err"
rc=$?
set -e
[ "$rc" -ne 0 ] || { echo "Expected mark-done without reviewer to fail for needs_review task" >&2; exit 1; }

python3 - "$tmp_root/review-required.json" "$task_one" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
task_text = Path(sys.argv[2]).read_text(encoding="utf-8")
assert payload["status"] == "review_required", payload
assert "status: todo" in task_text, task_text
PY

python3 "$workspace/scripts/claw.py" mark-done "$project_root" TASK-001 \
  --reviewer human \
  --commit deadbeef \
  --notes "manual completion" >"$tmp_root/task-one.json"

python3 "$workspace/scripts/claw.py" mark-done "$project_root" TASK-002 \
  --commit cafefood >"$tmp_root/task-two.json"

python3 - "$tmp_root/task-one.json" "$tmp_root/task-two.json" "$project_root" "$task_one" "$task_two" <<'PY'
import json
import sys
from pathlib import Path

task_one_payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
task_two_payload = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
project_root = Path(sys.argv[3])
task_one_text = Path(sys.argv[4]).read_text(encoding="utf-8")
task_two_text = Path(sys.argv[5]).read_text(encoding="utf-8")

assert task_one_payload["status"] == "done", task_one_payload
assert task_one_payload["reviewer"] == "human", task_one_payload
assert task_one_payload["commit"] == "deadbeef", task_one_payload
assert task_one_payload["receipt_path"].startswith("state/manual_completions/"), task_one_payload
assert "status: done" in task_one_text, task_one_text

assert task_two_payload["status"] == "done", task_two_payload
assert task_two_payload["reviewer"] is None, task_two_payload
assert task_two_payload["commit"] == "cafefood", task_two_payload
assert "status: done" in task_two_text, task_two_text

receipt_files = sorted((project_root / "state" / "manual_completions").glob("TASK-*.json"))
assert len(receipt_files) == 2, receipt_files

snapshot = json.loads((project_root / "state" / "tasks_snapshot.json").read_text(encoding="utf-8"))
statuses = {entry["task_id"]: entry["status"] for entry in snapshot["tasks"]}
assert statuses["TASK-001"] == "done", statuses
assert statuses["TASK-002"] == "done", statuses
PY

echo "mark-done test: ok"
