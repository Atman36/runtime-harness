from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class OrgGraphError(ValueError):
    def __init__(self, message: str, *, code: str = "ORG_GRAPH_ERROR", details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class DelegationCheck:
    allowed: bool
    reason_code: str | None = None
    details: dict | None = None


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return {}
    return dict(loaded)


def _graph_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "org_graph" in payload and isinstance(payload.get("org_graph"), dict):
        return dict(payload.get("org_graph"))
    return dict(payload)


def _merge_graph(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    if not override:
        return dict(base)
    merged = dict(base)
    base_agents = base.get("agents") if isinstance(base.get("agents"), dict) else {}
    override_agents = override.get("agents") if isinstance(override.get("agents"), dict) else {}
    merged_agents: dict[str, Any] = {}
    for agent in set(base_agents) | set(override_agents):
        base_cfg = base_agents.get(agent)
        override_cfg = override_agents.get(agent)
        if isinstance(base_cfg, dict) and isinstance(override_cfg, dict):
            merged_agents[agent] = {**base_cfg, **override_cfg}
        elif agent in override_agents:
            merged_agents[agent] = override_cfg
        else:
            merged_agents[agent] = base_cfg
    merged["agents"] = merged_agents
    base_policy = base.get("delegation") if isinstance(base.get("delegation"), dict) else {}
    override_policy = override.get("delegation") if isinstance(override.get("delegation"), dict) else {}
    if base_policy or override_policy:
        merged["delegation"] = {**base_policy, **override_policy}
    return merged


def org_graph_path(repo_root: Path) -> Path:
    return Path(repo_root) / "_system" / "registry" / "org_graph.yaml"


def project_org_graph_path(project_root: Path) -> Path:
    return Path(project_root) / "docs" / "ORG_GRAPH.yaml"


def load_org_graph(repo_root: Path, project_root: Path | None = None) -> dict[str, Any]:
    base_payload = _read_yaml(org_graph_path(repo_root))
    base_graph = _graph_from_payload(base_payload)
    override_graph: dict[str, Any] = {}
    if project_root is not None:
        override_payload = _read_yaml(project_org_graph_path(project_root))
        override_graph = _graph_from_payload(override_payload)

    graph = _merge_graph(base_graph, override_graph)
    validate_org_graph(graph)
    return graph


def validate_org_graph(graph: dict[str, Any]) -> None:
    if not isinstance(graph, dict):
        raise OrgGraphError("Org graph payload must be a JSON object", code="ORG_GRAPH_INVALID")

    agents = graph.get("agents")
    if not isinstance(agents, dict) or not agents:
        raise OrgGraphError("Org graph missing agents map", code="ORG_GRAPH_INVALID")

    errors: list[str] = []
    delegation_policy = graph.get("delegation")
    if delegation_policy is not None and not isinstance(delegation_policy, dict):
        errors.append("delegation policy must be an object")
    elif isinstance(delegation_policy, dict):
        allow_self_delegate = delegation_policy.get("allow_self_delegate")
        if allow_self_delegate is not None and not isinstance(allow_self_delegate, bool):
            errors.append("delegation.allow_self_delegate must be a boolean")

    for agent, cfg in agents.items():
        if not isinstance(cfg, dict):
            errors.append(f"agent {agent} must be an object")
            continue
        capabilities = cfg.get("capabilities")
        if capabilities is not None:
            if not isinstance(capabilities, list):
                errors.append(f"agent {agent} capabilities must be a list")
            else:
                for capability in capabilities:
                    if not isinstance(capability, str):
                        errors.append(f"agent {agent} capabilities entries must be strings")
        can_delegate = cfg.get("can_delegate")
        if can_delegate is not None and not isinstance(can_delegate, bool):
            errors.append(f"agent {agent} can_delegate must be a boolean")
        reports_to = cfg.get("reports_to")
        if reports_to not in (None, ""):
            if not isinstance(reports_to, str):
                errors.append(f"agent {agent} reports_to must be a string")
            elif reports_to not in agents:
                errors.append(f"agent {agent} reports_to '{reports_to}' is not defined in agents")
        delegates_to = cfg.get("delegates_to")
        if delegates_to is not None:
            if not isinstance(delegates_to, list):
                errors.append(f"agent {agent} delegates_to must be a list")
            else:
                for target in delegates_to:
                    if not isinstance(target, str):
                        errors.append(f"agent {agent} delegates_to entries must be strings")
                    elif target not in agents:
                        errors.append(f"agent {agent} delegates_to '{target}' is not defined in agents")

    for agent in agents:
        visited: set[str] = set()
        current = agent
        while True:
            cfg = agents.get(current)
            if not isinstance(cfg, dict):
                break
            reports_to = cfg.get("reports_to")
            if not reports_to:
                break
            if reports_to in visited:
                errors.append(f"cycle detected in reports_to chain for {agent}")
                break
            visited.add(reports_to)
            current = reports_to

    if errors:
        raise OrgGraphError("Org graph validation failed", code="ORG_GRAPH_INVALID", details={"errors": errors})


def delegation_targets(graph: dict[str, Any], *, delegator: str) -> set[str]:
    agents = graph.get("agents") if isinstance(graph.get("agents"), dict) else {}
    cfg = agents.get(delegator)
    if not isinstance(cfg, dict):
        raise OrgGraphError(f"Delegator '{delegator}' not found in org graph", code="ORG_GRAPH_UNKNOWN_AGENT")

    if cfg.get("can_delegate") is False:
        return set()

    if "delegates_to" in cfg:
        delegates_to = cfg.get("delegates_to")
        if not isinstance(delegates_to, list):
            return set()
        return {str(target) for target in delegates_to if str(target).strip()}

    direct_reports = set()
    for agent, candidate in agents.items():
        if not isinstance(candidate, dict):
            continue
        if str(candidate.get("reports_to") or "").strip() == delegator:
            direct_reports.add(agent)
    return direct_reports


def validate_delegation(graph: dict[str, Any], *, delegator: str, delegatee: str) -> DelegationCheck:
    agents = graph.get("agents") if isinstance(graph.get("agents"), dict) else {}
    if delegator not in agents:
        return DelegationCheck(False, reason_code="unknown_delegator", details={"delegator": delegator})
    if delegatee not in agents:
        return DelegationCheck(False, reason_code="unknown_delegatee", details={"delegatee": delegatee})
    delegation_policy = graph.get("delegation") if isinstance(graph.get("delegation"), dict) else {}
    allow_self_delegate = bool(delegation_policy.get("allow_self_delegate", False))
    if delegator == delegatee and not allow_self_delegate:
        return DelegationCheck(False, reason_code="self_delegate_forbidden")
    if delegator == delegatee:
        return DelegationCheck(True)

    allowed = delegation_targets(graph, delegator=delegator)
    if delegatee not in allowed:
        return DelegationCheck(
            False,
            reason_code="delegation_forbidden",
            details={"allowed": sorted(allowed)},
        )
    return DelegationCheck(True)


def escalation_chain(graph: dict[str, Any], *, agent: str) -> list[str]:
    agents = graph.get("agents") if isinstance(graph.get("agents"), dict) else {}
    if agent not in agents:
        raise OrgGraphError(f"Agent '{agent}' not found in org graph", code="ORG_GRAPH_UNKNOWN_AGENT")

    chain: list[str] = []
    seen: set[str] = set()
    current = agent
    while True:
        cfg = agents.get(current)
        if not isinstance(cfg, dict):
            break
        reports_to = str(cfg.get("reports_to") or "").strip()
        if not reports_to:
            break
        if reports_to in seen:
            raise OrgGraphError("Cycle detected in reports_to chain", code="ORG_GRAPH_CYCLE")
        seen.add(reports_to)
        chain.append(reports_to)
        current = reports_to
    return chain
