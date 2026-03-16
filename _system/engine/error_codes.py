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
    "unknown_dependency": {
        "description": "A task references a dependency that does not exist",
        "likely_cause": "A task front matter dependency points to a missing or misspelled task id",
        "next_action": "Run `claw task-lint` or `claw task-graph-lint`; then fix the dependency reference",
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
    "TRANSPORT_REGISTRY_INVALID": {
        "description": "The transport registry is missing or malformed",
        "likely_cause": "_system/registry/operator_transports.yaml is absent or contains invalid YAML",
        "next_action": "Restore the transport registry and validate provider entries",
    },
    "TRANSPORT_CONFIG_INVALID": {
        "description": "Transport backend config is malformed",
        "likely_cause": "operator_transport in state/project.yaml does not match the backend contract",
        "next_action": "Run `claw openclaw doctor <project>` and fix the reported backend config",
    },
    "TRANSPORT_ID_INVALID": {
        "description": "Transport backend id is invalid",
        "likely_cause": "A backend id is empty, reserved, or does not match the allowed pattern",
        "next_action": "Rename the backend id to a lowercase slug like `file_exchange`",
    },
    "TRANSPORT_PROVIDER_UNKNOWN": {
        "description": "Transport backend references an unknown provider",
        "likely_cause": "state/project.yaml points to a provider not declared in the transport registry",
        "next_action": "Declare the provider in _system/registry/operator_transports.yaml or fix the provider id",
    },
    "TRANSPORT_PROVIDER_DUPLICATE": {
        "description": "Multiple backends map to the same transport provider",
        "likely_cause": "operator_transport.backends contains duplicate provider entries",
        "next_action": "Keep a single configured backend per provider",
    },
    "TRANSPORT_PROVIDER_LOAD_FAILED": {
        "description": "Transport backend provider failed to load",
        "likely_cause": "The registry module/factory path is wrong or the provider raised during import",
        "next_action": "Fix the provider module path or inspect the provider implementation",
    },
    "TRANSPORT_BINARY_MISSING": {
        "description": "Transport backend prerequisite binary is missing",
        "likely_cause": "The transport provider depends on a CLI that is not installed on this machine",
        "next_action": "Install the missing binary or disable that transport backend",
    },
    "TRANSPORT_UNSUPPORTED_COMBINATION": {
        "description": "Transport backend does not support the current project setup",
        "likely_cause": "The configured workspace mode or project setup is incompatible with the selected transport",
        "next_action": "Adjust the project execution config or choose a compatible transport backend",
    },
    "TRANSPORT_BACKEND_NOT_FOUND": {
        "description": "The requested transport backend is not configured",
        "likely_cause": "The project has no matching enabled backend for that provider or id",
        "next_action": "Inspect `claw openclaw transports <project>` and configure the missing backend",
    },
    "TRANSPORT_BACKEND_DISABLED": {
        "description": "The requested transport backend is disabled",
        "likely_cause": "The backend exists in project config but enabled=false",
        "next_action": "Enable the backend in state/project.yaml or choose another transport",
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
