# SPEC-006 — WORKFLOW.md enforcement in orchestrate

## Context

The claw project is at `/Users/Apple/progect/claw`.

`_system/engine/workflow_contract.py` already provides a full typed `WorkflowContract`
dataclass with `scope.allowed_agents` and `scope.edit_scope`. However, `claw orchestrate`
currently only loads the contract and includes `contract_summary()` in the output payload —
it does NOT enforce `allowed_agents` when selecting an agent or `edit_scope` when launching
a task. This spec wires the contract into actual enforcement.

## Goal

1. Enforce `allowed_agents` in `cmd_orchestrate`: if the task's `preferred_agent` is not
   in the contract's `allowed_agents`, stop with `reason_code: contract_violation`
2. Enforce `edit_scope` in `cmd_launch_plan`: warn if a spec references files outside scope
3. Add `cmd_workflow_validate` — standalone `claw workflow-validate --project slug`
4. Add tests

## Files to modify / create

### MODIFY: `scripts/claw.py`

Read the file before editing. Key functions to touch:
- `cmd_orchestrate` (line ~1698): add allowed_agents gate
- `cmd_launch_plan` (line ~1918): add edit_scope warning
- `build_parser` (line ~2258): register `workflow-validate`

**1. In `cmd_orchestrate`, after selecting `next_task` (line ~1729 area):**

After `next_task = ready_tasks[0]`, before `update_task_status(...)`, add the agent gate:

```python
            # Enforce allowed_agents from workflow contract
            if workflow_contract and workflow_contract.scope.allowed_agents:
                task_agent = next_task.get("preferred_agent", "auto")
                allowed = set(workflow_contract.scope.allowed_agents)
                if task_agent not in allowed and task_agent != "auto":
                    payload = {
                        "status": "contract_violation",
                        "reason_code": "contract_violation",
                        "project": project_root.name,
                        "steps": steps,
                        "accepted_runs": accepted_runs,
                        "task_id": next_task["task_id"],
                        "agent": task_agent,
                        "allowed_agents": list(allowed),
                        "message": (
                            f"Task {next_task['task_id']} requires agent '{task_agent}' "
                            f"but WORKFLOW.md only allows {sorted(allowed)}"
                        ),
                    }
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                    return 1
```

**2. In `cmd_launch_plan` (read function first), add edit_scope warning:**

After building the `plan` dict, before printing it, add:

```python
    # Warn if spec references files outside workflow edit_scope
    contract = load_workflow_contract(project_root)
    scope_warnings: list[str] = []
    if contract and contract.scope.edit_scope:
        # Read spec file paths section if present (heuristic: look for file paths in spec)
        spec_path = Path(job.get("spec_path", "")) if job else None
        if spec_path and spec_path.is_file():
            spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
            # Find lines mentioning files/paths
            path_re = re.compile(r'`([^`]+/[^`]+\.[a-z]+)`')
            for m in path_re.finditer(spec_text):
                file_path = m.group(1)
                top_dir = Path(file_path).parts[0] if Path(file_path).parts else None
                if top_dir and top_dir not in contract.scope.edit_scope:
                    scope_warnings.append(
                        f"Spec references '{file_path}' outside edit_scope {list(contract.scope.edit_scope)}"
                    )
    if scope_warnings:
        plan["scope_warnings"] = scope_warnings
```

Note: `re` is already imported at the top of `claw.py` — check before adding a new import.

**3. Add `cmd_workflow_validate`:**

```python
def cmd_workflow_validate(args: argparse.Namespace) -> int:
    """Validate the workflow contract for a project."""
    try:
        project_root = resolve_project_root(args.project_root)
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    contract = load_workflow_contract(project_root)
    if contract is None:
        payload = {
            "status": "no_contract",
            "project": project_root.name,
            "message": "No docs/WORKFLOW.md found — defaults apply",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    # Basic structural validation via contract_summary
    summary = contract_summary(contract)
    errors = summary.get("contract_errors", [])

    # Extra semantic checks
    if contract.scope.allowed_agents:
        from _system.engine.workflow_contract import VALID_AGENTS
        unknown = set(contract.scope.allowed_agents) - VALID_AGENTS
        if unknown:
            errors.append(f"Unknown agent(s) in allowed_agents: {sorted(unknown)}")

    payload = {
        "status": "valid" if not errors else "invalid",
        "project": project_root.name,
        "contract_version": contract.contract_version,
        "allowed_agents": list(contract.scope.allowed_agents),
        "edit_scope": list(contract.scope.edit_scope),
        "failure_budget": contract.retry_policy.failure_budget,
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1
```

**4. Register in `build_parser()`:**

```python
workflow_validate = subcommands.add_parser(
    "workflow-validate",
    help="Validate the WORKFLOW.md contract for a project"
)
workflow_validate.add_argument("project_root", help="Project root path or slug")
workflow_validate.set_defaults(func=cmd_workflow_validate)
```

Also add `"contract_violation"` to `_STATUS_REASON_CODE` in `cmd_orchestrate`.

### CREATE: `tests/workflow_enforcement_test.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== workflow enforcement test ==="

# Test 1: workflow-validate on demo-project (should be valid)
OUT=$(python3 scripts/claw.py workflow-validate projects/demo-project)
echo "demo-project validate: $OUT"
STATUS=$(echo "$OUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[ "$STATUS" = "valid" ] || { echo "FAIL: demo-project contract should be valid, got: $STATUS"; exit 1; }

# Test 2: workflow-validate on a project without WORKFLOW.md returns no_contract (not error)
SLUG="wf-test-$$"
FAKE_PROJECT="$(mktemp -d)"
mkdir -p "$FAKE_PROJECT/state" "$FAKE_PROJECT/tasks"
cleanup() { rm -rf "$FAKE_PROJECT"; }
trap cleanup EXIT

OUT=$(python3 scripts/claw.py workflow-validate "$FAKE_PROJECT")
echo "No-contract result: $OUT"
STATUS=$(echo "$OUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[ "$STATUS" = "no_contract" ] || { echo "FAIL: missing WORKFLOW.md should return no_contract"; exit 1; }

echo "PASS: workflow enforcement test"
```

### Add to `tests/run_all.sh`

```bash
bash tests/workflow_enforcement_test.sh
```

## Acceptance Criteria

- `claw orchestrate` stops with `reason_code: contract_violation` if task's `preferred_agent`
  is not in `allowed_agents` (and agent is not `auto`)
- `claw launch-plan` output includes `scope_warnings` if spec references files outside `edit_scope`
- `claw workflow-validate projects/demo-project` returns `{"status": "valid", ...}`
- `claw workflow-validate` on a project without WORKFLOW.md returns `{"status": "no_contract"}`
- `bash tests/workflow_enforcement_test.sh` passes
- `bash tests/run_all.sh` still passes

## Constraints

- Missing WORKFLOW.md is NOT an error — defaults apply, orchestration continues
- **`allowed_agents` semantics:**
  - `allowed_agents: []` (empty) or missing → no restriction, any agent is permitted
  - `allowed_agents: [auto]` → `auto` mode is permitted; the planner resolves to a concrete agent,
    and that resolved agent must also be in the allowed list (or the list must include `"auto"` as
    explicit opt-in for planner-resolved routing)
  - `allowed_agents: [claude, codex]` → only explicit agents; `auto` tasks are allowed since
    `auto` defers to the planner which picks from the allowed set
  - Enforcement only fires when: (a) contract exists AND (b) `allowed_agents` is non-empty AND
    (c) `task.preferred_agent` is a concrete agent name not in the list
- Do not break existing `cmd_orchestrate` behavior for projects without a contract
- **Gate boundaries:** this task = contract gate only. Do NOT add dependency/graph checks here — those belong in TASK-007
