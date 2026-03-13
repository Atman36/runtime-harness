"""Stable reason codes and structured error guidance for claw CLI."""
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


def build_error_envelope(code: str, message: str) -> dict[str, str]:
    """Return a structured error dict for JSON output to stderr."""
    guidance = REASON_CODES.get(code, REASON_CODES["ERROR"])
    return {
        "error": message,
        "code": code,
        "likely_cause": guidance["likely_cause"],
        "next_action": guidance["next_action"],
    }
