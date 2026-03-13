# SPEC-001 — Reason codes registry and structured error envelope

## Context

The claw project is at `/Users/Apple/progect/claw`.

`scripts/claw.py` already has `_openclaw_error(message, code)` at line 1794
that emits `{"error": message, "code": code}` to stderr. The `code` values are
ad-hoc strings scattered across the codebase. This spec formalizes them.

## Goal

1. Create `_system/engine/error_codes.py` — a registry of all stable reason
   codes with guidance metadata.
2. Update `_openclaw_error()` to emit enriched envelopes from the registry.
3. Add a `reason_code` field to the JSON output of `cmd_orchestrate` and
   `cmd_openclaw_wake` (stdout, not stderr).

## Files to create / modify

### CREATE: `/Users/Apple/progect/claw/_system/engine/error_codes.py`

```python
"""Stable reason codes and guidance for claw CLI errors."""
from __future__ import annotations

# Each entry: code -> {description, likely_cause, next_action}
REASON_CODES: dict[str, dict[str, str]] = {
    "queue_empty": {
        "description": "No runnable jobs in the queue",
        "likely_cause": "All tasks are done, blocked, or awaiting human input",
        "next_action": "Check task statuses with `claw dashboard`; enqueue a new task if needed",
    },
    "approval_pending": {
        "description": "A job is waiting for human approval",
        "likely_cause": "An approval request was created by the orchestrator or a run",
        "next_action": "Run `claw resolve-approval` to approve or reject",
    },
    "review_pending": {
        "description": "One or more review decisions are pending",
        "likely_cause": "A run triggered a review batch that has not been decided yet",
        "next_action": "Run `claw review-batch` or wait for the reviewer agent to finish",
    },
    "failure_budget_exhausted": {
        "description": "Consecutive failure count reached the configured limit",
        "likely_cause": "Repeated run failures without a successful accepted run",
        "next_action": "Inspect recent failed runs; fix the underlying issue; reset budget with `claw orchestrate`",
    },
    "task_graph_cycle": {
        "description": "A cycle was detected in the task dependency graph",
        "likely_cause": "Two or more tasks have circular depends_on references",
        "next_action": "Run `claw task-lint` to identify the cycle; edit task front matter to break it",
    },
    "task_parse_failed": {
        "description": "A task file could not be parsed",
        "likely_cause": "Malformed YAML front matter or missing required fields",
        "next_action": "Inspect the task file and fix the front matter",
    },
    "run_artifact_invalid": {
        "description": "A run artifact failed schema validation",
        "likely_cause": "Generated artifact does not match the expected contract schema",
        "next_action": "Run `python3 scripts/validate_artifacts.py` for details",
    },
    "reviewer_policy_invalid": {
        "description": "The reviewer policy file is missing or malformed",
        "likely_cause": "_system/registry/reviewer_policy.yaml is absent or has invalid YAML",
        "next_action": "Restore the reviewer policy from _system/registry/ templates",
    },
    "hook_dispatch_failed": {
        "description": "One or more hooks failed to dispatch",
        "likely_cause": "Hook endpoint is unreachable or returned a non-2xx response",
        "next_action": "Check hook files in state/hooks/failed/; retry with `claw reconcile`",
    },
    "NOT_FOUND": {
        "description": "A required file or directory was not found",
        "likely_cause": "The specified project path or resource does not exist",
        "next_action": "Verify the path and that the project was created with `claw create-project`",
    },
    "BUILD_FAILED": {
        "description": "Failed to build the run artifacts",
        "likely_cause": "Task or spec file is missing or malformed",
        "next_action": "Check task and spec files; run `claw launch-plan <task>` for a dry-run preview",
    },
    "POLICY_ERROR": {
        "description": "A policy validation error occurred",
        "likely_cause": "Reviewer policy or routing rules contain invalid configuration",
        "next_action": "Review files in _system/registry/",
    },
    "INVALID_PAYLOAD": {
        "description": "The input payload is not a valid JSON object",
        "likely_cause": "stdin did not contain valid JSON or was empty",
        "next_action": "Ensure the caller pipes valid JSON to the command",
    },
    "ERROR": {
        "description": "An unclassified error occurred",
        "likely_cause": "See the error message for details",
        "next_action": "Check the error message and consult logs",
    },
}


def build_error_envelope(code: str, message: str) -> dict:
    """Return a structured error dict for JSON output to stderr."""
    guidance = REASON_CODES.get(code, REASON_CODES["ERROR"])
    return {
        "error": message,
        "code": code,
        "likely_cause": guidance["likely_cause"],
        "next_action": guidance["next_action"],
    }
```

### MODIFY: `/Users/Apple/progect/claw/_system/engine/__init__.py`

Add the following import and export:

After the existing imports, add:
```python
from _system.engine.error_codes import REASON_CODES, build_error_envelope
```

Add `"REASON_CODES"` and `"build_error_envelope"` to `__all__`.

### MODIFY: `/Users/Apple/progect/claw/scripts/claw.py`

**1. Add import at the top** (after the existing `from _system.engine import ...` line):

```python
from _system.engine.error_codes import build_error_envelope  # noqa: E402
```

**2. Replace `_openclaw_error` function** (currently at line 1794):

```python
def _openclaw_error(message: str, code: str = "ERROR") -> None:
    """Write a structured JSON error envelope to stderr."""
    json.dump(build_error_envelope(code, message), sys.stderr, ensure_ascii=False)
    sys.stderr.write("\n")
```

**3. In `cmd_orchestrate`** (around line 1630 where the payload dict is built),
add a `reason_code` field that maps `last_status` to a reason code:

```python
STATUS_TO_REASON_CODE = {
    "idle": "queue_empty",
    "awaiting_approval": "approval_pending",
    "awaiting_review": "review_pending",
    "failure_budget_exhausted": "failure_budget_exhausted",
    "error": "ERROR",
    "accepted": None,
}
```

Add `"reason_code": STATUS_TO_REASON_CODE.get(last_status)` to the payload dict
in `cmd_orchestrate`.

**4. In `cmd_openclaw_wake`** (around line 2043), add to the payload dict:
```python
"reason_code": "hook_dispatch_failed" if any(
    o.get("status") == "failed" for o in dispatch_outcomes + reconcile_outcomes
) else None,
```

## Acceptance Criteria

- `_system/engine/error_codes.py` exists and is importable
- `_openclaw_error("some error", "NOT_FOUND")` emits:
  `{"error": "some error", "code": "NOT_FOUND", "likely_cause": "...", "next_action": "..."}`
- `claw orchestrate` JSON output includes `"reason_code"` field
- `claw openclaw wake` JSON output includes `"reason_code"` field
- All existing tests still pass: `bash /Users/Apple/progect/claw/tests/run_all.sh`

## Constraints

- Do not change the `code` field — it must remain for backward compatibility
- `reason_code` in stdout payloads may be `null` on success paths
- Keep `_openclaw_error` writing to stderr (not stdout)
