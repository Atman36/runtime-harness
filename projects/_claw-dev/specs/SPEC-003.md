# SPEC-003 — Workflow contract schema and validator

## Context

The claw project is at `/Users/Apple/progect/claw`.

Currently, orchestration policy (approval gates, retry limits, timeout policy,
edit boundaries) is implicit — it lives in runtime defaults scattered across
`scripts/claw.py`. This spec adds an optional, explicit **workflow contract**
per project that:
- Makes policy visible and auditable
- Can be validated before orchestration starts
- Serves as a human-readable control surface

A missing contract is NOT an error — defaults apply.

## Goal

1. Create `_system/contracts/workflow.schema.json` — JSON Schema for the contract.
2. Create `_system/engine/workflow_contract.py` — loader and validator.
3. Create `projects/_template/docs/WORKFLOW.md` — template contract (YAML front matter + Markdown body).
4. Hook into `scripts/claw.py`:
   - `cmd_orchestrate()`: load contract, validate, include in output
   - `cmd_openclaw_status()`: include `contract_loaded` flag in output
5. Hook into `scripts/validate_artifacts.py`: add `--workflow` flag to validate
   the contract file for a project.

## Files to create / modify

### CREATE: `/Users/Apple/progect/claw/_system/contracts/workflow.schema.json`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Claw Workflow Contract",
  "description": "Per-project workflow policy contract for claw orchestration",
  "type": "object",
  "required": ["contract_version", "project", "approval_gates", "retry_policy"],
  "properties": {
    "contract_version": {
      "type": "integer",
      "const": 1,
      "description": "Schema version; must be 1"
    },
    "project": {
      "type": "string",
      "pattern": "^[a-z0-9][a-z0-9-]*$",
      "description": "Project slug matching the project directory name"
    },
    "approval_gates": {
      "type": "object",
      "required": ["require_human_approval_on_failure"],
      "properties": {
        "require_human_approval_on_failure": {
          "type": "boolean",
          "description": "If true, orchestrate stops and asks for approval after failure_budget is exhausted"
        },
        "require_approval_before_first_run": {
          "type": "boolean",
          "description": "If true, the first run of any task requires prior human approval"
        }
      },
      "additionalProperties": false
    },
    "retry_policy": {
      "type": "object",
      "required": ["failure_budget"],
      "properties": {
        "failure_budget": {
          "type": "integer",
          "minimum": 1,
          "maximum": 10,
          "description": "Number of consecutive failures before asking human"
        },
        "backoff_base_seconds": {
          "type": "integer",
          "minimum": 0,
          "description": "Base backoff in seconds between retries"
        },
        "backoff_max_seconds": {
          "type": "integer",
          "minimum": 0,
          "description": "Maximum backoff cap in seconds"
        }
      },
      "additionalProperties": false
    },
    "timeout_policy": {
      "type": "object",
      "properties": {
        "worker_lease_seconds": {
          "type": "integer",
          "minimum": 60,
          "description": "How long a worker may hold a job lease before it is reclaimed"
        },
        "run_timeout_seconds": {
          "type": "integer",
          "minimum": 60,
          "description": "Maximum wall-clock time for a single agent run"
        }
      },
      "additionalProperties": false
    },
    "scope": {
      "type": "object",
      "properties": {
        "edit_scope": {
          "type": "array",
          "items": {"type": "string"},
          "description": "Directories the agent is allowed to modify"
        },
        "allowed_agents": {
          "type": "array",
          "items": {"type": "string", "enum": ["claude", "codex", "auto"]},
          "description": "Agents allowed to run tasks in this project"
        }
      },
      "additionalProperties": false
    },
    "notes": {
      "type": "string",
      "description": "Human-readable notes about this contract"
    }
  },
  "additionalProperties": false
}
```

### CREATE: `/Users/Apple/progect/claw/_system/engine/workflow_contract.py`

```python
"""Workflow contract loader and validator for claw projects."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

CONTRACT_FILENAME = "WORKFLOW.md"
CONTRACT_FRONT_MATTER_RE = None  # set lazily

WORKFLOW_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "contracts" / "workflow.schema.json"


def _front_matter_re():
    global CONTRACT_FRONT_MATTER_RE
    if CONTRACT_FRONT_MATTER_RE is None:
        import re
        CONTRACT_FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
    return CONTRACT_FRONT_MATTER_RE


def load_workflow_contract(project_root: Path) -> dict[str, Any] | None:
    """Load workflow contract from docs/WORKFLOW.md front matter.

    Returns the parsed contract dict, or None if no contract file exists.
    """
    contract_path = project_root / "docs" / CONTRACT_FILENAME
    if not contract_path.is_file():
        return None
    text = contract_path.read_text(encoding="utf-8")
    match = _front_matter_re().match(text)
    if match is None:
        return None
    loaded = yaml.safe_load(match.group(1))
    if not isinstance(loaded, dict):
        return None
    return dict(loaded)


def validate_workflow_contract(contract: dict[str, Any]) -> list[str]:
    """Validate a workflow contract dict against the schema.

    Returns list of error strings. Empty list means valid.
    """
    errors: list[str] = []

    # Required fields
    if contract.get("contract_version") != 1:
        errors.append("contract_version must be 1")

    project = contract.get("project")
    if not isinstance(project, str) or not project:
        errors.append("project must be a non-empty string")

    approval_gates = contract.get("approval_gates")
    if not isinstance(approval_gates, dict):
        errors.append("approval_gates must be an object")
    else:
        if "require_human_approval_on_failure" not in approval_gates:
            errors.append("approval_gates.require_human_approval_on_failure is required")
        elif not isinstance(approval_gates["require_human_approval_on_failure"], bool):
            errors.append("approval_gates.require_human_approval_on_failure must be a boolean")

    retry_policy = contract.get("retry_policy")
    if not isinstance(retry_policy, dict):
        errors.append("retry_policy must be an object")
    else:
        budget = retry_policy.get("failure_budget")
        if not isinstance(budget, int) or not (1 <= budget <= 10):
            errors.append("retry_policy.failure_budget must be an integer between 1 and 10")

    return errors


def contract_summary(contract: dict[str, Any] | None) -> dict[str, Any]:
    """Return a compact summary of the contract for embedding in status output."""
    if contract is None:
        return {"contract_loaded": False}
    errors = validate_workflow_contract(contract)
    return {
        "contract_loaded": True,
        "contract_version": contract.get("contract_version"),
        "project": contract.get("project"),
        "failure_budget": (contract.get("retry_policy") or {}).get("failure_budget"),
        "require_human_approval_on_failure": (
            contract.get("approval_gates") or {}
        ).get("require_human_approval_on_failure"),
        "contract_errors": errors,
    }
```

### MODIFY: `/Users/Apple/progect/claw/_system/engine/__init__.py`

Add import:
```python
from _system.engine.workflow_contract import (
    load_workflow_contract,
    validate_workflow_contract,
    contract_summary,
)
```

Add to `__all__`: `"load_workflow_contract"`, `"validate_workflow_contract"`, `"contract_summary"`.

### MODIFY: `/Users/Apple/progect/claw/scripts/claw.py`

**1. Add import near the top** (after existing `from _system.engine import ...`):

```python
from _system.engine.workflow_contract import contract_summary, load_workflow_contract, validate_workflow_contract  # noqa: E402
```

**2. In `cmd_orchestrate()`** (read the function first), before the `while steps < max_steps:` loop:

```python
# Load and validate workflow contract (optional)
workflow_contract = load_workflow_contract(project_root)
contract_errors = validate_workflow_contract(workflow_contract) if workflow_contract else []
if contract_errors:
    # Contract exists but is invalid — surface as a warning in output, don't abort
    pass  # will be included in payload
```

Add `"contract": contract_summary(workflow_contract)` to the `payload` dict.

**3. In `cmd_openclaw_status()`** (read the function first), add to `payload`:

```python
"contract": contract_summary(load_workflow_contract(project_root)),
```

### CREATE: `/Users/Apple/progect/claw/projects/_template/docs/WORKFLOW.md`

```markdown
---
contract_version: 1
project: {{PROJECT_SLUG}}
approval_gates:
  require_human_approval_on_failure: true
  require_approval_before_first_run: false
retry_policy:
  failure_budget: 3
  backoff_base_seconds: 30
  backoff_max_seconds: 300
timeout_policy:
  worker_lease_seconds: 600
  run_timeout_seconds: 3600
scope:
  edit_scope:
    - _system
    - scripts
  allowed_agents:
    - claude
    - codex
    - auto
notes: "Workflow contract for {{PROJECT_SLUG}}. Edit to customize orchestration policy."
---

# Workflow Contract — {{PROJECT_SLUG}}

This file defines the orchestration policy for this project.
Edit the YAML front matter above to change the policy.

## Approval Gates

- `require_human_approval_on_failure`: When true, the orchestrator pauses and
  requests human approval after the failure budget is exhausted.
- `require_approval_before_first_run`: When true, every new task requires prior
  human approval before the first run.

## Retry Policy

- `failure_budget`: Number of consecutive failures allowed before escalating.
- `backoff_base_seconds` / `backoff_max_seconds`: Exponential backoff bounds.

## Timeout Policy

- `worker_lease_seconds`: How long a worker may hold a job before reclaim.
- `run_timeout_seconds`: Maximum wall-clock time per agent run.

## Scope

- `edit_scope`: Directories the agent may modify.
- `allowed_agents`: Which agent keys are permitted for this project.
```

### MODIFY: `/Users/Apple/progect/claw/scripts/validate_artifacts.py`

Add a `--workflow` flag:

After the existing `group.add_argument("--all", ...)` line in `main()`, add:
```python
group.add_argument("--workflow", metavar="PROJECT_ROOT",
                   help="Validate the workflow contract for a project")
```

Before the `if args.all:` block, add:
```python
if args.workflow:
    # Import here to avoid circular deps
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    from _system.engine.workflow_contract import load_workflow_contract, validate_workflow_contract
    project_root = Path(args.workflow).expanduser().resolve()
    contract = load_workflow_contract(project_root)
    if contract is None:
        print(f"No workflow contract found at {project_root / 'docs' / 'WORKFLOW.md'}")
        return 0
    errors = validate_workflow_contract(contract)
    if errors:
        print(f"Workflow contract errors ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
        return 1
    if not args.quiet:
        print("Workflow contract is valid.")
    return 0
```

Note: The `if args.workflow:` block must be placed before `if args.all:` since
argparse uses a mutually exclusive group.

## Acceptance Criteria

- `_system/contracts/workflow.schema.json` exists and is valid JSON
- `_system/engine/workflow_contract.py` is importable
- `projects/_template/docs/WORKFLOW.md` exists with the template content
- `python3 scripts/validate_artifacts.py --workflow projects/demo-project` runs
  without error (returns 0 even if no contract, because contract is optional)
- `claw orchestrate projects/demo-project` output includes `"contract"` field
- `claw openclaw status projects/demo-project` output includes `"contract"` field
- All existing tests still pass: `bash /Users/Apple/progect/claw/tests/run_all.sh`

## Constraints

- Missing contract is NOT an error — `contract_summary(None)` returns
  `{"contract_loaded": false}`
- Do not change existing orchestration behavior — contract is informational only
  in this first version (no enforcement of approval_gates from contract yet)
- WORKFLOW.md uses YAML front matter (not pure YAML) to remain human-readable
- Keep `validate_artifacts.py` self-contained — import workflow_contract lazily
