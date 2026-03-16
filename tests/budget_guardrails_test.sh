#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-budget-guardrails-test.XXXXXX")"
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

reset_runtime() {
  rm -rf \
    "$project_root/runs" \
    "$project_root/reviews" \
    "$project_root/state/queue" \
    "$project_root/state/hooks" \
    "$project_root/state/approvals" \
    "$project_root/state/guardrails" \
    "$project_root/state/metrics_snapshot.json"
  mkdir -p \
    "$project_root/runs" \
    "$project_root/reviews/decisions" \
    "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter} \
    "$project_root/state/hooks"/{pending,failed,sent}
}

cat > "$workspace/scripts/fake_success_agent.py" <<'EOF'
#!/usr/bin/env python3
import sys

sys.stdin.read()
print("GUARDRAIL SUCCESS")
EOF
chmod +x "$workspace/scripts/fake_success_agent.py"

write_budget_workflow() {
  cat > "$project_root/docs/WORKFLOW.md" <<'EOF'
---
contract_version: 1
project: "demo-project"
approval_gates:
  require_human_approval_on_failure: true
  require_approval_before_first_run: false
retry_policy:
  failure_budget: 3
  backoff_base_seconds: 30
  backoff_max_seconds: 300
guardrails:
  budget:
    enabled: true
    warning_limit: 2
    hard_limit: 3
    base_run_cost: 1
    agent_costs:
      codex: 0
    workspace_mode_costs:
      shared_project: 0
      project_root: 0
      git_worktree: 1
      isolated_checkout: 2
    risk_flag_costs: {}
  governance:
    approval_required_risk_flags: []
    approval_required_paths: []
    approval_required_workspace_modes: []
    approval_required_agents: []
commands:
  test: "bash tests/run_all.sh"
  lint: ""
  build: ""
  smoke: ""
---
EOF
}

write_governance_workflow() {
  cat > "$project_root/docs/WORKFLOW.md" <<'EOF'
---
contract_version: 1
project: "demo-project"
approval_gates:
  require_human_approval_on_failure: true
  require_approval_before_first_run: false
retry_policy:
  failure_budget: 3
  backoff_base_seconds: 30
  backoff_max_seconds: 300
guardrails:
  budget:
    enabled: false
    warning_limit: 0
    hard_limit: 0
    base_run_cost: 1
    agent_costs: {}
    workspace_mode_costs: {}
    risk_flag_costs: {}
  governance:
    approval_required_risk_flags:
      - governance
    approval_required_paths:
      - _system/registry/agents.yaml
    approval_required_workspace_modes: []
    approval_required_agents: []
commands:
  test: "bash tests/run_all.sh"
  lint: ""
  build: ""
  smoke: ""
---
EOF
}

reset_runtime
write_budget_workflow

enqueue_one="$workspace/enqueue-1.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
  python3 "$workspace/scripts/claw.py" enqueue "$project_root/tasks/TASK-001.md" >"$enqueue_one"

worker_one="$workspace/worker-1.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review >"$worker_one"

python3 - "$enqueue_one" "$worker_one" "$project_root" <<'PY'
import json
import sys
from pathlib import Path

enqueue = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
worker_lines = [line.strip() for line in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines() if line.strip()]
worker = json.loads(worker_lines[-1])
project_root = Path(sys.argv[3])
run_dir = project_root / enqueue["run_path"]
snapshot = json.loads((run_dir / "guardrail_snapshot.json").read_text(encoding="utf-8"))
project_snapshot = json.loads((project_root / "state" / "guardrails" / "budget_snapshot.json").read_text(encoding="utf-8"))

assert worker["queue_state"] == "done", worker
assert snapshot["decision"] == "allow", snapshot
assert snapshot["budget"]["estimated_units"] == 1, snapshot
assert snapshot["budget"]["consumed_units"] == 1, snapshot
assert snapshot["budget"]["warning_triggered"] is False, snapshot
assert project_snapshot["budget"]["consumed_units"] == 1, project_snapshot
assert project_snapshot["budget"]["soft_limit_reached"] is False, project_snapshot
PY

enqueue_two="$workspace/enqueue-2.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
  python3 "$workspace/scripts/claw.py" enqueue "$project_root/tasks/TASK-001.md" >"$enqueue_two"

worker_two="$workspace/worker-2.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review >"$worker_two"

python3 - "$enqueue_two" "$project_root" <<'PY'
import json
import sys
from pathlib import Path

enqueue = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
project_root = Path(sys.argv[2])
run_dir = project_root / enqueue["run_path"]
snapshot = json.loads((run_dir / "guardrail_snapshot.json").read_text(encoding="utf-8"))
project_snapshot = json.loads((project_root / "state" / "guardrails" / "budget_snapshot.json").read_text(encoding="utf-8"))

assert snapshot["decision"] == "warn", snapshot
assert "budget_soft_limit" in snapshot["reason_codes"], snapshot
assert snapshot["budget"]["warning_triggered"] is True, snapshot
assert project_snapshot["budget"]["consumed_units"] == 2, project_snapshot
assert project_snapshot["budget"]["soft_limit_reached"] is True, project_snapshot
assert project_snapshot["budget"]["hard_limit_reached"] is False, project_snapshot
PY

enqueue_three="$workspace/enqueue-3.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
  python3 "$workspace/scripts/claw.py" enqueue "$project_root/tasks/TASK-001.md" >"$enqueue_three"

worker_three="$workspace/worker-3.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review >"$worker_three"

status_three="$workspace/status-3.json"
run_three_id="$(python3 - "$enqueue_three" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(Path(payload["run_path"]).name)
PY
)"
python3 "$workspace/scripts/claw.py" status "$project_root" "$run_three_id" >"$status_three"

python3 - "$worker_three" "$status_three" "$project_root" "$enqueue_three" <<'PY'
import json
import sys
from pathlib import Path

worker_lines = [line.strip() for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if line.strip()]
worker = json.loads(worker_lines[-1])
status = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
project_root = Path(sys.argv[3])
enqueue = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
run_dir = project_root / enqueue["run_path"]
snapshot = json.loads((run_dir / "guardrail_snapshot.json").read_text(encoding="utf-8"))
approval_files = sorted((project_root / "state" / "approvals" / "pending").glob("APPROVAL-*.json"))

assert worker["queue_state"] == "awaiting_approval", worker
assert snapshot["decision"] == "pause", snapshot
assert "budget_hard_limit" in snapshot["reason_codes"], snapshot
assert snapshot["budget"]["consumed_units"] == 0, snapshot
assert approval_files, approval_files
assert status["queue_state"] == "awaiting_approval", status
assert status["guardrails"]["decision"] == "pause", status
assert "budget_hard_limit" in status["guardrails"]["reason_codes"], status
PY

approval_id="$(python3 - "$project_root" <<'PY'
import json
import sys
from pathlib import Path

project_root = Path(sys.argv[1])
approval = sorted((project_root / "state" / "approvals" / "pending").glob("APPROVAL-*.json"))[0]
print(json.loads(approval.read_text(encoding="utf-8"))["approval_id"])
PY
)"
python3 "$workspace/scripts/claw.py" resolve-approval "$project_root" "$approval_id" --decision approved --notes "allow budget override" >/dev/null

worker_four="$workspace/worker-4.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review >"$worker_four"

python3 - "$worker_four" "$project_root" "$enqueue_three" <<'PY'
import json
import sys
from pathlib import Path

worker_lines = [line.strip() for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if line.strip()]
worker = json.loads(worker_lines[-1])
project_root = Path(sys.argv[2])
enqueue = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
run_dir = project_root / enqueue["run_path"]
snapshot = json.loads((run_dir / "guardrail_snapshot.json").read_text(encoding="utf-8"))
project_snapshot = json.loads((project_root / "state" / "guardrails" / "budget_snapshot.json").read_text(encoding="utf-8"))

assert worker["queue_state"] == "done", worker
assert snapshot["budget"]["consumed_units"] == 1, snapshot
assert snapshot["approval_override"]["approved"] is True, snapshot
assert project_snapshot["budget"]["consumed_units"] == 3, project_snapshot
assert project_snapshot["budget"]["hard_limit_reached"] is True, project_snapshot
PY

reset_runtime
write_governance_workflow

cat > "$project_root/specs/SPEC-901.md" <<'EOF'
# SPEC-901

Update `_system/registry/agents.yaml` to adjust the codex adapter runtime contract.
EOF

cat > "$project_root/tasks/TASK-901.md" <<'EOF'
---
id: TASK-901
title: "Governance-sensitive agent config change"
status: todo
spec: ../specs/SPEC-901.md
preferred_agent: codex
review_policy: standard
priority: medium
project: demo-project
needs_review: false
risk_flags: [governance]
dependencies: []
tags: [governance]
---

# Task

## Goal
Adjust agent configuration.
EOF

enqueue_gov="$workspace/enqueue-gov.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
  python3 "$workspace/scripts/claw.py" enqueue "$project_root/tasks/TASK-901.md" >"$enqueue_gov"

worker_gov="$workspace/worker-gov.json"
CLAW_AGENT_COMMAND="python3 $workspace/scripts/fake_success_agent.py" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once --skip-review >"$worker_gov"

dashboard_gov="$workspace/dashboard-gov.json"
python3 "$workspace/scripts/claw.py" dashboard "$project_root" >"$dashboard_gov"

python3 - "$worker_gov" "$project_root" "$enqueue_gov" "$dashboard_gov" <<'PY'
import json
import sys
from pathlib import Path

worker_lines = [line.strip() for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if line.strip()]
worker = json.loads(worker_lines[-1])
project_root = Path(sys.argv[2])
enqueue = json.loads(Path(sys.argv[3]).read_text(encoding="utf-8"))
dashboard = json.loads(Path(sys.argv[4]).read_text(encoding="utf-8"))
run_dir = project_root / enqueue["run_path"]
snapshot = json.loads((run_dir / "guardrail_snapshot.json").read_text(encoding="utf-8"))
project = dashboard["projects"][0]

assert worker["queue_state"] == "awaiting_approval", worker
assert snapshot["decision"] == "pause", snapshot
assert "governance_sensitive_path" in snapshot["reason_codes"], snapshot
assert "governance_risk_flag" in snapshot["reason_codes"], snapshot
assert project["guardrails"]["pending_runs"] == 1, project
assert project["guardrails"]["budget"]["enabled"] is False, project
PY

echo "budget guardrails test: ok"
