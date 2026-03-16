from __future__ import annotations

import re
from dataclasses import asdict
from typing import Any

from _system.engine.workflow_contract import GuardrailPolicy


PATH_TOKEN_RE = re.compile(r"`([^`\n]+)`")


def extract_referenced_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in PATH_TOKEN_RE.finditer(text or ""):
        value = match.group(1).strip()
        if "/" not in value:
            continue
        if value not in paths:
            paths.append(value)
    return paths


def estimate_budget_units(
    policy: GuardrailPolicy,
    *,
    selected_agent: str,
    workspace_mode: str,
    risk_flags: list[str],
) -> int:
    budget = policy.budget
    units = max(1, int(budget.base_run_cost))
    units += int(budget.agent_costs.get(selected_agent, 0))
    units += int(budget.workspace_mode_costs.get(workspace_mode, 0))
    for flag in risk_flags:
        units += int(budget.risk_flag_costs.get(flag, 0))
    return max(1, units)


def _path_matches(reference: str, configured: str) -> bool:
    normalized_reference = reference.strip().strip("/")
    normalized_configured = configured.strip().strip("/")
    if not normalized_reference or not normalized_configured:
        return False
    return normalized_reference == normalized_configured or normalized_reference.startswith(normalized_configured + "/")


def evaluate_guardrails(
    policy: GuardrailPolicy,
    *,
    current_consumed_units: int,
    run_id: str,
    task_id: str,
    task_title: str,
    selected_agent: str,
    workspace_mode: str,
    risk_flags: list[str],
    referenced_paths: list[str],
    approval_override: bool = False,
    approval_id: str | None = None,
) -> dict[str, Any]:
    reasons: list[str] = []
    decision = "allow"

    estimated_units = estimate_budget_units(
        policy,
        selected_agent=selected_agent,
        workspace_mode=workspace_mode,
        risk_flags=risk_flags,
    )
    projected_units = current_consumed_units + estimated_units
    budget = policy.budget
    warning_triggered = False
    hard_stop_triggered = False

    governance_actions: list[dict[str, str]] = []
    governance = policy.governance

    matched_risk_flags = [flag for flag in risk_flags if flag in set(governance.approval_required_risk_flags)]
    for flag in matched_risk_flags:
        governance_actions.append({"type": "risk_flag", "value": flag})
    if matched_risk_flags:
        reasons.append("governance_risk_flag")

    matched_paths = [path for path in referenced_paths if any(_path_matches(path, configured) for configured in governance.approval_required_paths)]
    for path in matched_paths:
        governance_actions.append({"type": "sensitive_path", "value": path})
    if matched_paths:
        reasons.append("governance_sensitive_path")

    if selected_agent in set(governance.approval_required_agents):
        governance_actions.append({"type": "agent", "value": selected_agent})
        reasons.append("governance_agent")

    if workspace_mode in set(governance.approval_required_workspace_modes):
        governance_actions.append({"type": "workspace_mode", "value": workspace_mode})
        reasons.append("governance_workspace_mode")

    if budget.enabled:
        if budget.warning_limit > 0 and projected_units >= budget.warning_limit:
            warning_triggered = True
            if "budget_soft_limit" not in reasons:
                reasons.append("budget_soft_limit")
            decision = "warn"
        if budget.hard_limit > 0 and projected_units >= budget.hard_limit:
            hard_stop_triggered = True
            if "budget_hard_limit" not in reasons:
                reasons.append("budget_hard_limit")
            decision = "pause"

    if governance_actions:
        decision = "pause"

    if approval_override and decision == "pause":
        decision = "allow"

    return {
        "snapshot_version": 1,
        "run_id": run_id,
        "task_id": task_id,
        "task_title": task_title,
        "decision": decision,
        "reason_codes": reasons,
        "budget": {
            "enabled": budget.enabled,
            "warning_limit": budget.warning_limit,
            "hard_limit": budget.hard_limit,
            "current_consumed_units": current_consumed_units,
            "estimated_units": estimated_units,
            "projected_units": projected_units,
            "consumed_units": 0,
            "warning_triggered": warning_triggered,
            "hard_stop_triggered": hard_stop_triggered,
            "policy": asdict(budget),
        },
        "governance": {
            "actions": governance_actions,
            "referenced_paths": referenced_paths,
            "policy": asdict(governance),
        },
        "approval_override": {
            "approved": approval_override,
            "approval_id": approval_id,
        },
    }


def summarize_project_guardrails(
    policy: GuardrailPolicy,
    run_snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    consumed_units = 0
    warning_runs = 0
    pending_runs = 0
    last_run_id = None
    for snapshot in run_snapshots:
        budget = snapshot.get("budget") if isinstance(snapshot.get("budget"), dict) else {}
        approval_override = snapshot.get("approval_override") if isinstance(snapshot.get("approval_override"), dict) else {}
        consumed_units += max(0, int(budget.get("consumed_units", 0) or 0))
        if bool(budget.get("warning_triggered", False)):
            warning_runs += 1
        if snapshot.get("decision") == "pause" and not bool(approval_override.get("approved", False)) and max(0, int(budget.get("consumed_units", 0) or 0)) == 0:
            pending_runs += 1
        last_run_id = snapshot.get("run_id") or last_run_id

    budget = policy.budget
    return {
        "snapshot_version": 1,
        "budget": {
            "enabled": budget.enabled,
            "warning_limit": budget.warning_limit,
            "hard_limit": budget.hard_limit,
            "consumed_units": consumed_units,
            "warning_runs": warning_runs,
            "soft_limit_reached": bool(budget.enabled and budget.warning_limit > 0 and consumed_units >= budget.warning_limit),
            "hard_limit_reached": bool(budget.enabled and budget.hard_limit > 0 and consumed_units >= budget.hard_limit),
            "policy": asdict(budget),
        },
        "pending_runs": pending_runs,
        "last_run_id": last_run_id,
        "governance": {
            "policy": asdict(policy.governance),
        },
    }
