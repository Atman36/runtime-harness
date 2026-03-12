#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-orchestration-autonomy-test.XXXXXX")"
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
  python3 - "$project_root" <<'PY'
from pathlib import Path
import sys

project_root = Path(sys.argv[1])
for task_path in (project_root / "tasks").glob("TASK-*.md"):
    text = task_path.read_text(encoding="utf-8")
    text = text.replace("status: done", "status: todo")
    text = text.replace("status: queued", "status: todo")
    text = text.replace("status: in_progress", "status: todo")
    text = text.replace("needs_review: true", "needs_review: false")
    task_path.write_text(text, encoding="utf-8")
PY
}

cat > "$workspace/scripts/fake_success_agent.py" <<'EOF'
#!/usr/bin/env python3
import sys

sys.stdin.read()
print("AUTONOMY SUCCESS")
EOF
chmod +x "$workspace/scripts/fake_success_agent.py"

cat > "$workspace/scripts/fake_fail_agent.py" <<'EOF'
#!/usr/bin/env python3
import sys

sys.stdin.read()
print("AUTONOMY FAILURE", file=sys.stderr)
raise SystemExit(9)
EOF
chmod +x "$workspace/scripts/fake_fail_agent.py"

cat > "$workspace/scripts/fake_follow_up_reviewer.py" <<'EOF'
#!/usr/bin/env python3
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdin.read()
decisions_dir = Path.cwd() / "reviews" / "decisions"
for path in sorted(decisions_dir.glob("*.json")):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("decision") != "pending":
        continue
    payload["decided_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload["decision"] = "needs_follow_up"
    payload["findings"] = [
        {
            "severity": "warning",
            "description": "Follow-up task required to finish the requested slice.",
            "follow_up_required": True,
        }
    ]
    payload["follow_up_actions"] = [
        {
            "action_id": "FOLLOW-001",
            "description": "Document the remaining review delta",
            "status": "pending",
            "assigned_agent": "codex",
        }
    ]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print("AUTO REVIEW COMPLETE")
EOF
chmod +x "$workspace/scripts/fake_follow_up_reviewer.py"

reset_project

python3 - "$project_root/tasks/TASK-001.md" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace("needs_review: false", "needs_review: true", 1)
path.write_text(text, encoding="utf-8")
PY

auto_review_out="$workspace/auto-review-follow-up.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
CLAW_AGENT_COMMAND_CLAUDE="python3 $workspace/scripts/fake_follow_up_reviewer.py" \
  python3 "$workspace/scripts/claw.py" orchestrate "$project_root" --max-steps 1 >"$auto_review_out"

python3 - "$auto_review_out" "$project_root" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
project_root = Path(sys.argv[2])

assert payload["status"] == "accepted", payload
assert payload["accepted_runs"] == ["RUN-0001"], payload
assert payload["pending_reviews"] == [], payload
assert payload["pending_approvals"] == [], payload

task_paths = sorted((project_root / "tasks").glob("TASK-*.md"))
follow_up_path = None
for path in task_paths:
    text = path.read_text(encoding="utf-8")
    if "follow_up_action_id: FOLLOW-001" in text:
        follow_up_path = path
        break

assert follow_up_path is not None, task_paths
follow_up_text = follow_up_path.read_text(encoding="utf-8")
assert "status: queued" in follow_up_text, follow_up_text
assert "preferred_agent: codex" in follow_up_text, follow_up_text
assert "- TASK-001" in follow_up_text, follow_up_text

task_one = (project_root / "tasks" / "TASK-001.md").read_text(encoding="utf-8")
assert "status: done" in task_one, task_one

pending_jobs = sorted((project_root / "state" / "queue" / "pending").glob("*.json"))
assert len(pending_jobs) == 1, pending_jobs
job = json.loads(pending_jobs[0].read_text(encoding="utf-8"))
assert job["task"]["id"] == follow_up_path.stem, job

decision_files = sorted((project_root / "reviews" / "decisions").glob("*.json"))
assert len(decision_files) == 1, decision_files
decision = json.loads(decision_files[0].read_text(encoding="utf-8"))
assert decision["decision"] == "needs_follow_up", decision
assert decision["follow_up_actions"][0]["status"] == "in_progress", decision
assert decision["follow_up_actions"][0]["task_id"] == follow_up_path.stem, decision
PY

reset_project

failure_one_out="$workspace/failure-budget-1.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_fail_agent.py" \
  python3 "$workspace/scripts/claw.py" orchestrate "$project_root" --max-steps 1 --skip-review --failure-budget 2 >"$failure_one_out"

python3 - "$failure_one_out" "$project_root" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
project_root = Path(sys.argv[2])
state = json.loads((project_root / "state" / "orchestration_state.json").read_text(encoding="utf-8"))

assert payload["status"] == "awaiting_approval", payload
assert payload["orchestration"]["consecutive_failures"] == 1, payload
assert payload["orchestration"]["failure_budget_exhausted"] is False, payload
assert state["consecutive_failures"] == 1, state
PY

approval_one="$(find "$project_root/state/approvals/pending" -maxdepth 1 -name 'APPROVAL-*.json' | head -1)"
approval_one_id="$(python3 -c "import json; print(json.loads(open('$approval_one').read())['approval_id'])")"
python3 "$workspace/scripts/claw.py" resolve-approval "$project_root" "$approval_one_id" --decision approved --notes "retry" >/dev/null

failure_two_out="$workspace/failure-budget-2.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_fail_agent.py" \
  python3 "$workspace/scripts/claw.py" orchestrate "$project_root" --max-steps 1 --skip-review --failure-budget 2 >"$failure_two_out"

python3 - "$failure_two_out" "$project_root" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
project_root = Path(sys.argv[2])
state = json.loads((project_root / "state" / "orchestration_state.json").read_text(encoding="utf-8"))

assert payload["status"] == "failure_budget_exhausted", payload
assert payload["orchestration"]["consecutive_failures"] == 2, payload
assert payload["orchestration"]["failure_budget_exhausted"] is True, payload
assert state["consecutive_failures"] == 2, state
assert payload["pending_approvals"], payload
PY

approval_two="$(find "$project_root/state/approvals/pending" -maxdepth 1 -name 'APPROVAL-*.json' | head -1)"
approval_two_id="$(python3 -c "import json; print(json.loads(open('$approval_two').read())['approval_id'])")"
python3 "$workspace/scripts/claw.py" resolve-approval "$project_root" "$approval_two_id" --decision approved --notes "retry" >/dev/null

success_reset_out="$workspace/failure-budget-reset.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
  python3 "$workspace/scripts/claw.py" orchestrate "$project_root" --max-steps 1 --skip-review --failure-budget 2 >"$success_reset_out"

python3 - "$success_reset_out" "$project_root" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
project_root = Path(sys.argv[2])
state = json.loads((project_root / "state" / "orchestration_state.json").read_text(encoding="utf-8"))

assert payload["status"] == "accepted", payload
assert payload["orchestration"]["consecutive_failures"] == 0, payload
assert payload["orchestration"]["failure_budget_exhausted"] is False, payload
assert state["consecutive_failures"] == 0, state
PY

echo "orchestration autonomy test: ok"
