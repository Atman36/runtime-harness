#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-decision-log-test.XXXXXX")"
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

assert_contains() {
  local path="$1"
  local expected="$2"
  if ! grep -Fq -- "$expected" "$path"; then
    fail "expected '$expected' in $path"
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

reset_project() {
  rm -rf \
    "$project_root/runs" \
    "$project_root/reviews" \
    "$project_root/state/queue" \
    "$project_root/state/hooks" \
    "$project_root/state/approvals" \
    "$project_root/state/decision_log.jsonl" \
    "$project_root/state/orchestration_state.json" \
    "$project_root/state/metrics_snapshot.json" \
    "$project_root/state/review_cadence.json"
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
print("DECISION LOG SUCCESS")
EOF
chmod +x "$workspace/scripts/fake_success_agent.py"

cat > "$workspace/scripts/fake_fail_agent.py" <<'EOF'
#!/usr/bin/env python3
import sys

sys.stdin.read()
print("DECISION LOG FAILURE", file=sys.stderr)
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
            "description": "Follow-up task required to finish the slice.",
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
print("DECISION LOG REVIEW")
EOF
chmod +x "$workspace/scripts/fake_follow_up_reviewer.py"

reset_project

python3 - "$project_root" "$workspace" <<'PY' || fail "decision log helper append/read failed"
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
workspace = Path(sys.argv[2])
sys.path.insert(0, str(workspace))

from _system.engine.decision_log import append_decision, decision_log_path, read_decisions

append_decision(project_root, "routing", run_id="RUN-0001", task_id="TASK-001", reason_code="routing_rules", details={"agent": "codex"}, outcome="dispatched")
append_decision(project_root, "retry", run_id="RUN-0001", task_id="TASK-001", reason_code="run_failed", details={"attempt": 2}, outcome="queued")

records = read_decisions(project_root)
assert len(records) == 2, records
assert [record["kind"] for record in records] == ["routing", "retry"], records
assert read_decisions(project_root, last_n=1)[0]["kind"] == "retry"
assert decision_log_path(project_root).is_file()
print("ok")
PY
pass "decision log helper keeps append order and last_n slicing"

cli_out="$workspace/decision-log-cli.txt"
python3 "$workspace/scripts/claw.py" decision-log "$project_root" --last 1 >"$cli_out"
assert_contains "$cli_out" "retry"
assert_contains "$cli_out" "reason:run_failed"
pass "decision-log CLI prints recent records"

reset_project

routing_out="$workspace/routing.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
  python3 "$workspace/scripts/claw.py" orchestrate "$project_root" --max-steps 1 --skip-review >"$routing_out"

python3 - "$project_root" <<'PY' || fail "routing decision was not logged"
import json
import sys
from pathlib import Path

log_path = Path(sys.argv[1]) / "state" / "decision_log.jsonl"
records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
routing = [record for record in records if record["kind"] == "routing"]
assert len(routing) == 1, records
record = routing[0]
assert record["run_id"] == "RUN-0001", record
assert record["task_id"] == "TASK-001", record
assert record["outcome"] == "dispatched", record
assert record["reason_code"] in {"task_front_matter", "routing_rules", "project_default"}, record
assert record["details"]["selected_agent"] in {"codex", "claude"}, record
print("ok")
PY
pass "orchestrate logs routing dispatch decisions"

reset_project

python3 "$workspace/scripts/claw.py" enqueue "$task_path" >/dev/null
pending_path="$project_root/state/queue/pending/RUN-0001.json"
TARGET_PATH="$pending_path" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["TARGET_PATH"])
payload = json.loads(path.read_text(encoding="utf-8"))
payload.setdefault("queue", {})["max_attempts"] = 3
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

set +e
CLAW_AGENT_COMMAND_CODEX="python3 $workspace/scripts/fake_fail_agent.py" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review >/dev/null 2>"$workspace/retry.stderr"
retry_rc=$?
set -e
[ "$retry_rc" -eq 9 ] || fail "worker retry scenario should preserve agent exit code"

python3 - "$project_root" <<'PY' || fail "retry decision was not logged"
import json
import sys
from pathlib import Path

log_path = Path(sys.argv[1]) / "state" / "decision_log.jsonl"
records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
retry_records = [record for record in records if record["kind"] == "retry"]
assert len(retry_records) == 1, records
record = retry_records[0]
assert record["run_id"] == "RUN-0001", record
assert record["reason_code"] == "run_failed", record
assert record["outcome"] == "queued", record
assert record["details"]["attempt_count"] == 1, record
assert record["details"]["retry_backoff_seconds"] > 0, record
print("ok")
PY
pass "worker logs retry decisions with backoff metadata"

reset_project

failure_out="$workspace/approval.json"
set +e
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_fail_agent.py" \
  python3 "$workspace/scripts/claw.py" orchestrate "$project_root" --max-steps 1 --skip-review >"$failure_out"
failure_rc=$?
set -e
[ "$failure_rc" -eq 0 ] || fail "orchestrate failure path should stop at approval request"

python3 - "$project_root" <<'PY' || fail "approval decision was not logged"
import json
import sys
from pathlib import Path

log_path = Path(sys.argv[1]) / "state" / "decision_log.jsonl"
records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
approval_records = [record for record in records if record["kind"] == "approval_requested"]
assert len(approval_records) == 1, records
record = approval_records[0]
assert record["run_id"] == "RUN-0001", record
assert record["reason_code"] == "run_failed", record
assert record["outcome"] == "waiting", record
assert record["details"]["requested_action"] == "retry", record
assert record["details"]["source"] == "runtime", record
print("ok")
PY
pass "runtime failure logs approval_requested decisions"

reset_project

python3 - "$task_path" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
text = text.replace("needs_review: false", "needs_review: true", 1)
path.write_text(text, encoding="utf-8")
PY

follow_up_out="$workspace/follow-up.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
CLAW_AGENT_COMMAND_CLAUDE="python3 $workspace/scripts/fake_follow_up_reviewer.py" \
  python3 "$workspace/scripts/claw.py" orchestrate "$project_root" --max-steps 1 >"$follow_up_out"

python3 - "$project_root" <<'PY' || fail "follow-up creation was not logged"
import json
import sys
from pathlib import Path

log_path = Path(sys.argv[1]) / "state" / "decision_log.jsonl"
records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
follow_up_records = [record for record in records if record["kind"] == "follow_up_created"]
assert len(follow_up_records) == 1, records
record = follow_up_records[0]
assert record["run_id"] == "RUN-0001", record
assert record["reason_code"] == "needs_follow_up", record
assert record["outcome"] == "created", record
assert record["details"]["action_id"] == "FOLLOW-001", record
assert record["details"]["created_task_id"].startswith("TASK-"), record
assert record["details"]["created_task_path"].startswith("tasks/TASK-"), record
print("ok")
PY
pass "review follow-up materialization logs created task decisions"

echo ""
echo "decision_log_test: all tests passed"
