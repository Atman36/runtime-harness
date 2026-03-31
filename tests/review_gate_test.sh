#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-review-gate-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"

project_root="$workspace/projects/demo-project"
task_path="$project_root/tasks/TASK-001.md"
workflow_path="$project_root/docs/WORKFLOW.md"

python3 - "$workflow_path" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
replacement = """review_gate:\n  enabled: true\n  mode: blocking\n  reviewer: opposite\n---"""
text = text.replace("---\n\n# Workflow Contract", f"{replacement}\n\n# Workflow Contract")
path.write_text(text, encoding="utf-8")
PY

python3 - "$task_path" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace("status: done", "status: todo")
path.write_text(text, encoding="utf-8")
PY

rm -rf "$project_root/runs" "$project_root/reviews" "$project_root/state/queue"
mkdir -p "$project_root/runs" "$project_root/reviews" "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval}

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cat >/dev/null
echo "review gate success"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

enqueue_out="$tmp_root/enqueue.json"
python3 "$workspace/scripts/claw.py" enqueue "$task_path" > "$enqueue_out"
run_id="$(python3 - "$enqueue_out" <<'PY'
import json
import sys
print(json.loads(open(sys.argv[1]).read())["job_id"])
PY
)"

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once >/dev/null

set +e
python3 "$workspace/scripts/claw.py" review-gate "$project_root" "$run_id" > "$tmp_root/review-gate-pending.json"
pending_rc=$?
python3 "$workspace/scripts/claw.py" mark-done "$project_root" TASK-001 --reviewer human > "$tmp_root/mark-done-blocked.json"
blocked_rc=$?
set -e
[ "$pending_rc" -ne 0 ] || { echo "Expected initial review-gate call to block" >&2; exit 1; }
[ "$blocked_rc" -ne 0 ] || { echo "Expected mark-done to block behind review gate" >&2; exit 1; }

decision_file="$(find "$project_root/reviews/decisions" -maxdepth 1 -name '*.json' | head -1)"
[ -n "$decision_file" ] || { echo "Expected review gate to materialize a decision file" >&2; exit 1; }

python3 - "$decision_file" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
payload = json.loads(path.read_text(encoding="utf-8"))
payload["decided_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
payload["decision"] = "approved_with_notes"
payload["findings"] = [
    {
        "severity": "warning",
        "title": "Document the changed command surface",
        "body": "New CLI commands were added and should be reflected in operator docs.",
        "file": "scripts/claw.py",
        "line_start": 1,
        "line_end": 1,
        "confidence": 0.72,
        "recommendation": "Add docs when documentation changes are allowed."
    }
]
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

gate_passed="$tmp_root/review-gate-passed.json"
python3 "$workspace/scripts/claw.py" review-gate "$project_root" "$run_id" > "$gate_passed"
mark_done_passed="$tmp_root/mark-done-passed.json"
python3 "$workspace/scripts/claw.py" mark-done "$project_root" TASK-001 --reviewer human > "$mark_done_passed"

python3 - "$tmp_root/review-gate-pending.json" "$tmp_root/mark-done-blocked.json" "$gate_passed" "$mark_done_passed" "$decision_file" "$project_root" <<'PY'
import json
import sys
from pathlib import Path

pending = json.loads(Path(sys.argv[1]).read_text())
blocked = json.loads(Path(sys.argv[2]).read_text())
passed = json.loads(Path(sys.argv[3]).read_text())
done = json.loads(Path(sys.argv[4]).read_text())
decision_file = Path(sys.argv[5])
project_root = Path(sys.argv[6])

assert pending["status"] == "pending", pending
assert pending["result"]["reason"] in {"review_gate_created", "pending_review"}, pending
assert blocked["status"] == "review_gate_blocked", blocked
assert passed["status"] == "passed", passed
assert done["status"] == "done", done

decision = json.loads(decision_file.read_text(encoding="utf-8"))
finding = decision["findings"][0]
assert finding["title"] == "Document the changed command surface", finding
assert finding["body"].startswith("New CLI commands"), finding

task_text = (project_root / "tasks" / "TASK-001.md").read_text(encoding="utf-8")
assert "status: done" in task_text, task_text
PY

python3 "$workspace/scripts/validate_artifacts.py" "$decision_file" --quiet >/dev/null

echo "review gate test: ok"
