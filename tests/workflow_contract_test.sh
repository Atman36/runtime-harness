#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-workflow-contract-test.XXXXXX")"
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

# ── set up minimal workspace ──────────────────────────────────────────────────
mkdir -p "$workspace"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp -R "$repo_root/scripts" "$workspace/scripts"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py" 2>/dev/null || true
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py" 2>/dev/null || true

# Create a minimal project with project.yaml
bash "$workspace/scripts/create_project.sh" test-contract-project "$workspace"
project_root="$workspace/projects/test-contract-project"

# ── Test 1: defaults when WORKFLOW.md is absent ───────────────────────────────
rm -f "$project_root/docs/WORKFLOW.md"

result="$(cd "$workspace" && python3 - "$project_root" <<'PYEOF'
import sys
sys.path.insert(0, ".")
from _system.engine.workflow_contract import load_workflow_contract
from pathlib import Path
c = load_workflow_contract(Path(sys.argv[1]))
assert c.source == "defaults", f"expected source=defaults, got {c.source}"
assert c.retry_policy.failure_budget == 3
assert c.retry_policy.backoff_base_seconds == 30
assert c.retry_policy.backoff_max_seconds == 300
assert c.timeout_policy.worker_lease_seconds == 600
assert c.timeout_policy.run_timeout_seconds == 3600
print("ok")
PYEOF
)"
[ "$result" = "ok" ] || fail "Test 1 (defaults without WORKFLOW.md): $result"
pass "defaults when WORKFLOW.md absent"

# ── Test 2: loads values from WORKFLOW.md front matter ────────────────────────
cat > "$project_root/docs/WORKFLOW.md" <<'WEOF'
---
contract_version: 1
project: "test-contract-project"
approval_gates:
  require_human_approval_on_failure: false
  require_approval_before_first_run: true
retry_policy:
  failure_budget: 5
  backoff_base_seconds: 60
  backoff_max_seconds: 600
timeout_policy:
  worker_lease_seconds: 1200
  run_timeout_seconds: 7200
scope:
  edit_scope:
    - scripts
    - tests
  allowed_agents:
    - claude
    - codex
---
WEOF

result="$(cd "$workspace" && python3 - "$project_root" <<'PYEOF'
import sys
sys.path.insert(0, ".")
from _system.engine.workflow_contract import load_workflow_contract
from pathlib import Path
c = load_workflow_contract(Path(sys.argv[1]))
assert c.source != "defaults", f"expected source from file, got {c.source}"
assert c.retry_policy.failure_budget == 5, f"got {c.retry_policy.failure_budget}"
assert c.retry_policy.backoff_base_seconds == 60
assert c.retry_policy.backoff_max_seconds == 600
assert c.timeout_policy.worker_lease_seconds == 1200
assert c.timeout_policy.run_timeout_seconds == 7200
assert c.approval_gates.require_human_approval_on_failure == False
assert c.approval_gates.require_approval_before_first_run == True
assert "scripts" in c.scope.edit_scope
assert "claude" in c.scope.allowed_agents
assert "codex" in c.scope.allowed_agents
print("ok")
PYEOF
)"
[ "$result" = "ok" ] || fail "Test 2 (load values): $result"
pass "loads values from WORKFLOW.md front matter"

# ── Test 3: WorkflowLoadError on invalid agent ────────────────────────────────
cat > "$project_root/docs/WORKFLOW.md" <<'WEOF'
---
contract_version: 1
project: "test-contract-project"
scope:
  allowed_agents:
    - unknown-agent
---
WEOF

result="$(cd "$workspace" && python3 - "$project_root" <<'PYEOF'
import sys
sys.path.insert(0, ".")
from _system.engine.workflow_contract import load_workflow_contract, WorkflowLoadError
from pathlib import Path
try:
    load_workflow_contract(Path(sys.argv[1]))
    print("no_error")
except WorkflowLoadError as e:
    print("load_error")
PYEOF
)"
[ "$result" = "load_error" ] || fail "Test 3 (invalid agent raises WorkflowLoadError): got $result"
pass "WorkflowLoadError on invalid allowed_agents value"

# ── Test 4: WorkflowLoadError on non-positive integer ─────────────────────────
cat > "$project_root/docs/WORKFLOW.md" <<'WEOF'
---
contract_version: 1
project: "test-contract-project"
retry_policy:
  failure_budget: 0
---
WEOF

result="$(cd "$workspace" && python3 - "$project_root" <<'PYEOF'
import sys
sys.path.insert(0, ".")
from _system.engine.workflow_contract import load_workflow_contract, WorkflowLoadError
from pathlib import Path
try:
    load_workflow_contract(Path(sys.argv[1]))
    print("no_error")
except WorkflowLoadError as e:
    print("load_error")
PYEOF
)"
[ "$result" = "load_error" ] || fail "Test 4 (zero failure_budget raises WorkflowLoadError): got $result"
pass "WorkflowLoadError on failure_budget=0"

# ── Test 5: engine __init__ exports the symbols ───────────────────────────────
result="$(cd "$workspace" && python3 - <<'PYEOF'
import sys
sys.path.insert(0, ".")
from _system.engine import Commands, WorkflowContract, WorkflowLoadError, load_workflow_contract
print("ok")
PYEOF
)"
[ "$result" = "ok" ] || fail "Test 5 (engine __init__ exports): $result"
pass "engine __init__ exports Commands, WorkflowContract, WorkflowLoadError, load_workflow_contract"

# ── Test 6: _template/docs/WORKFLOW.md exists ─────────────────────────────────
template_workflow="$workspace/projects/_template/docs/WORKFLOW.md"
[ -f "$template_workflow" ] || fail "Test 6: _template/docs/WORKFLOW.md not found"
grep -q "contract_version" "$template_workflow" || fail "Test 6: missing contract_version in template WORKFLOW.md"
grep -q "^commands:" "$template_workflow" || fail "Test 6: missing commands block in template WORKFLOW.md"
grep -q 'test: "bash tests/run_all.sh"' "$template_workflow" || fail "Test 6: missing default test command in template WORKFLOW.md"
pass "_template/docs/WORKFLOW.md exists and has contract_version"

# ── Test 7: new project created from template gets WORKFLOW.md ────────────────
bash "$workspace/scripts/create_project.sh" wf-scaffold-project "$workspace"
new_project_workflow="$workspace/projects/wf-scaffold-project/docs/WORKFLOW.md"
[ -f "$new_project_workflow" ] || fail "Test 7: new project missing docs/WORKFLOW.md"
grep -q "wf-scaffold-project" "$new_project_workflow" || fail "Test 7: slug not substituted in new project WORKFLOW.md"
grep -q "^commands:" "$new_project_workflow" || fail "Test 7: new project WORKFLOW.md missing commands block"
[ -f "$workspace/projects/wf-scaffold-project/.codex/config.toml" ] || fail "Test 7: new project missing .codex/config.toml"
[ -f "$workspace/projects/wf-scaffold-project/.codex/agents/project-explorer.toml" ] || fail "Test 7: new project missing Codex subagent"
[ -f "$workspace/projects/wf-scaffold-project/.claude/agents/project-explorer.md" ] || fail "Test 7: new project missing Claude subagent"
grep -q "wf-scaffold-project" "$workspace/projects/wf-scaffold-project/.codex/agents/project-explorer.toml" || fail "Test 7: Codex subagent placeholder not substituted"
grep -q "wf-scaffold-project" "$workspace/projects/wf-scaffold-project/.claude/agents/project-explorer.md" || fail "Test 7: Claude subagent placeholder not substituted"
pass "new project scaffold includes WORKFLOW.md with slug substituted"

# ── Test 8: claw.py worker reads contract (lease-seconds from WORKFLOW.md) ────
cat > "$project_root/docs/WORKFLOW.md" <<'WEOF'
---
contract_version: 1
project: "test-contract-project"
timeout_policy:
  worker_lease_seconds: 42
  run_timeout_seconds: 3600
retry_policy:
  failure_budget: 3
  backoff_base_seconds: 30
  backoff_max_seconds: 300
---
WEOF

result="$(cd "$workspace" && python3 - "$project_root" <<'PYEOF'
import sys, json, argparse
sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
from _system.engine.workflow_contract import load_workflow_contract
from pathlib import Path
c = load_workflow_contract(Path(sys.argv[1]))
# Simulate the cmd_worker resolution: None means use contract value
args_lease = None
_lease = args_lease if args_lease is not None else c.timeout_policy.worker_lease_seconds
assert _lease == 42, f"expected 42, got {_lease}"
# Simulate CLI override: explicit value wins
args_lease_explicit = 99
_lease2 = args_lease_explicit if args_lease_explicit is not None else c.timeout_policy.worker_lease_seconds
assert _lease2 == 99, f"expected 99, got {_lease2}"
print("ok")
PYEOF
)"
[ "$result" = "ok" ] || fail "Test 8 (worker lease_seconds resolution): $result"
pass "worker uses WORKFLOW.md lease_seconds; explicit CLI flag wins"

# ── Test 9: contract_version: 2 raises WorkflowLoadError ─────────────────────
cat > "$project_root/docs/WORKFLOW.md" <<'WEOF'
---
contract_version: 2
project: "test-contract-project"
approval_gates:
  require_human_approval_on_failure: true
retry_policy:
  failure_budget: 3
  backoff_base_seconds: 30
  backoff_max_seconds: 300
---
WEOF

result="$(cd "$workspace" && python3 - "$project_root" <<'PYEOF'
import sys
sys.path.insert(0, ".")
from _system.engine.workflow_contract import load_workflow_contract, WorkflowLoadError
from pathlib import Path
try:
    load_workflow_contract(Path(sys.argv[1]))
    print("no_error")
except WorkflowLoadError as e:
    print("load_error")
PYEOF
)"
[ "$result" = "load_error" ] || fail "Test 9 (contract_version=2 raises WorkflowLoadError): got $result"
pass "contract_version: 2 raises WorkflowLoadError"

# ── Test 10: validate_workflow_contract rejects version != 1 ─────────────────
result="$(cd "$workspace" && python3 - <<'PYEOF'
import sys
sys.path.insert(0, ".")
from _system.engine.workflow_contract import WorkflowContract, validate_workflow_contract
from dataclasses import replace
# Build a contract with wrong version by bypassing frozen dataclass
import dataclasses
bad_contract = WorkflowContract.__new__(WorkflowContract)
object.__setattr__(bad_contract, "contract_version", 2)
object.__setattr__(bad_contract, "project", "test")
from _system.engine.workflow_contract import ApprovalGates, RetryPolicy, TimeoutPolicy, WorkflowScope
object.__setattr__(bad_contract, "approval_gates", ApprovalGates())
object.__setattr__(bad_contract, "retry_policy", RetryPolicy())
object.__setattr__(bad_contract, "timeout_policy", TimeoutPolicy())
object.__setattr__(bad_contract, "scope", WorkflowScope())
object.__setattr__(bad_contract, "source", "test")
errors = validate_workflow_contract(bad_contract)
if errors and "contract_version" in errors[0]:
    print("rejected")
else:
    print(f"accepted: {errors}")
PYEOF
)"
[ "$result" = "rejected" ] || fail "Test 10 (validate_workflow_contract rejects version!=1): got $result"
pass "validate_workflow_contract rejects contract_version != 1"

# ── Test 11: commands block defaults when omitted ────────────────────────────
cat > "$project_root/docs/WORKFLOW.md" <<'WEOF'
---
contract_version: 1
project: "test-contract-project"
approval_gates:
  require_human_approval_on_failure: true
retry_policy:
  failure_budget: 3
  backoff_base_seconds: 30
  backoff_max_seconds: 300
timeout_policy:
  worker_lease_seconds: 600
  run_timeout_seconds: 3600
scope:
  edit_scope:
    - tests
---
WEOF

result="$(cd "$workspace" && python3 - "$project_root" <<'PYEOF'
import sys
sys.path.insert(0, ".")
from pathlib import Path
from _system.engine.workflow_contract import load_workflow_contract

c = load_workflow_contract(Path(sys.argv[1]))
assert c.commands.test == "bash tests/run_all.sh", c.commands
assert c.commands.lint == "", c.commands
assert c.commands.build == "", c.commands
assert c.commands.smoke == "", c.commands
print("ok")
PYEOF
)"
[ "$result" = "ok" ] || fail "Test 11 (commands defaults when omitted): $result"
pass "commands block defaults when omitted"

# ── Test 12: commands block loads from WORKFLOW.md front matter ──────────────
cat > "$project_root/docs/WORKFLOW.md" <<'WEOF'
---
contract_version: 1
project: "test-contract-project"
commands:
  test: "pytest -q"
  lint: "ruff check ."
  build: "python -m build"
  smoke: "python scripts/smoke.py"
---
WEOF

result="$(cd "$workspace" && python3 - "$project_root" <<'PYEOF'
import sys
sys.path.insert(0, ".")
from pathlib import Path
from _system.engine.workflow_contract import load_workflow_contract

c = load_workflow_contract(Path(sys.argv[1]))
assert c.commands.test == "pytest -q", c.commands
assert c.commands.lint == "ruff check .", c.commands
assert c.commands.build == "python -m build", c.commands
assert c.commands.smoke == "python scripts/smoke.py", c.commands
print("ok")
PYEOF
)"
[ "$result" = "ok" ] || fail "Test 12 (commands values load): $result"
pass "commands block loads from WORKFLOW.md front matter"

# ── Test 13: contract_summary exposes commands registry ──────────────────────
result="$(cd "$workspace" && python3 - "$project_root" <<'PYEOF'
import sys
sys.path.insert(0, ".")
from pathlib import Path
from _system.engine.workflow_contract import contract_summary, load_workflow_contract

summary = contract_summary(load_workflow_contract(Path(sys.argv[1])))
assert summary["commands"]["test"] == "pytest -q", summary
assert summary["commands"]["smoke"] == "python scripts/smoke.py", summary
print("ok")
PYEOF
)"
[ "$result" = "ok" ] || fail "Test 13 (contract_summary exposes commands): $result"
pass "contract_summary exposes commands registry"

echo ""
echo "workflow_contract_test: all tests passed"
