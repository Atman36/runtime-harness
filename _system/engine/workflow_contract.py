"""Typed loader and validator for a project's WORKFLOW.md contract."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

import yaml

FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
VALID_AGENTS = {"claude", "codex", "auto"}

_DEFAULT_CONTRACT_VERSION = 1
_DEFAULT_FAILURE_BUDGET = 3
_DEFAULT_BACKOFF_BASE = 30
_DEFAULT_BACKOFF_MAX = 300
_DEFAULT_LEASE_SECONDS = 600
_DEFAULT_RUN_TIMEOUT = 3600


class WorkflowLoadError(ValueError):
    """Raised when a WORKFLOW.md file exists but contains invalid front matter."""


@dataclass(frozen=True)
class ApprovalGates:
    require_human_approval_on_failure: bool = True
    require_approval_before_first_run: bool = False


@dataclass(frozen=True)
class RetryPolicy:
    failure_budget: int = _DEFAULT_FAILURE_BUDGET
    backoff_base_seconds: int = _DEFAULT_BACKOFF_BASE
    backoff_max_seconds: int = _DEFAULT_BACKOFF_MAX


@dataclass(frozen=True)
class TimeoutPolicy:
    worker_lease_seconds: int = _DEFAULT_LEASE_SECONDS
    run_timeout_seconds: int = _DEFAULT_RUN_TIMEOUT


@dataclass(frozen=True)
class WorkflowScope:
    edit_scope: tuple[str, ...] = field(default_factory=tuple)
    allowed_agents: tuple[str, ...] = field(default_factory=lambda: tuple(sorted(VALID_AGENTS)))


@dataclass(frozen=True)
class WorkflowContract:
    contract_version: int = _DEFAULT_CONTRACT_VERSION
    project: str = ""
    approval_gates: ApprovalGates = field(default_factory=ApprovalGates)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_policy: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    scope: WorkflowScope = field(default_factory=WorkflowScope)
    source: str = "defaults"


def _workflow_path(project_root: Path) -> Path:
    return project_root / "docs" / "WORKFLOW.md"


def _parse_front_matter(text: str) -> dict[str, Any] | None:
    match = FRONT_MATTER_RE.match(text)
    if match is None:
        return None
    loaded = yaml.safe_load(match.group(1))
    if not isinstance(loaded, dict):
        return None
    return dict(loaded)


def _require_positive_int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowLoadError(f"{key!r} must be an integer, got {value!r}") from exc
    if value < 1:
        raise WorkflowLoadError(f"{key!r} must be >= 1, got {value!r}")
    return value


def _require_bool(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise WorkflowLoadError(f"{key!r} must be a boolean, got {value!r}")
    return value


def _parse_approval_gates(raw: Any) -> ApprovalGates:
    if raw is None:
        return ApprovalGates()
    if not isinstance(raw, dict):
        raise WorkflowLoadError(f"approval_gates must be a mapping, got {type(raw).__name__}")
    return ApprovalGates(
        require_human_approval_on_failure=_require_bool(raw, "require_human_approval_on_failure", True),
        require_approval_before_first_run=_require_bool(raw, "require_approval_before_first_run", False),
    )


def _parse_retry_policy(raw: Any) -> RetryPolicy:
    if raw is None:
        return RetryPolicy()
    if not isinstance(raw, dict):
        raise WorkflowLoadError(f"retry_policy must be a mapping, got {type(raw).__name__}")
    return RetryPolicy(
        failure_budget=_require_positive_int(raw, "failure_budget", _DEFAULT_FAILURE_BUDGET),
        backoff_base_seconds=_require_positive_int(raw, "backoff_base_seconds", _DEFAULT_BACKOFF_BASE),
        backoff_max_seconds=_require_positive_int(raw, "backoff_max_seconds", _DEFAULT_BACKOFF_MAX),
    )


def _parse_timeout_policy(raw: Any) -> TimeoutPolicy:
    if raw is None:
        return TimeoutPolicy()
    if not isinstance(raw, dict):
        raise WorkflowLoadError(f"timeout_policy must be a mapping, got {type(raw).__name__}")
    return TimeoutPolicy(
        worker_lease_seconds=_require_positive_int(raw, "worker_lease_seconds", _DEFAULT_LEASE_SECONDS),
        run_timeout_seconds=_require_positive_int(raw, "run_timeout_seconds", _DEFAULT_RUN_TIMEOUT),
    )


def _parse_scope(raw: Any) -> WorkflowScope:
    if raw is None:
        return WorkflowScope()
    if not isinstance(raw, dict):
        raise WorkflowLoadError(f"scope must be a mapping, got {type(raw).__name__}")

    edit_scope_raw = raw.get("edit_scope", [])
    if not isinstance(edit_scope_raw, list):
        raise WorkflowLoadError(f"scope.edit_scope must be a list, got {type(edit_scope_raw).__name__}")
    edit_scope = tuple(str(s).strip() for s in edit_scope_raw if str(s).strip())

    allowed_raw = raw.get("allowed_agents", list(sorted(VALID_AGENTS)))
    if not isinstance(allowed_raw, list):
        raise WorkflowLoadError(f"scope.allowed_agents must be a list, got {type(allowed_raw).__name__}")
    allowed_agents: list[str] = []
    for item in allowed_raw:
        agent = str(item).strip()
        if agent not in VALID_AGENTS:
            raise WorkflowLoadError(f"Unknown agent {agent!r} in scope.allowed_agents; valid: {sorted(VALID_AGENTS)}")
        allowed_agents.append(agent)

    return WorkflowScope(edit_scope=edit_scope, allowed_agents=tuple(allowed_agents))


def load_workflow_contract(project_root: Path) -> WorkflowContract:
    path = _workflow_path(project_root)
    if not path.is_file():
        return WorkflowContract(project=project_root.name, source="defaults")

    text = path.read_text(encoding="utf-8")
    fm = _parse_front_matter(text)
    if fm is None:
        return WorkflowContract(project=project_root.name, source="defaults")

    contract_version_raw = fm.get("contract_version", _DEFAULT_CONTRACT_VERSION)
    try:
        contract_version = int(contract_version_raw)
    except (TypeError, ValueError) as exc:
        raise WorkflowLoadError(f"contract_version must be an integer, got {contract_version_raw!r}") from exc

    if contract_version != 1:
        raise WorkflowLoadError(f"contract_version must be 1, got {contract_version!r}")

    project = str(fm.get("project") or project_root.name).strip()
    return WorkflowContract(
        contract_version=contract_version,
        project=project,
        approval_gates=_parse_approval_gates(fm.get("approval_gates")),
        retry_policy=_parse_retry_policy(fm.get("retry_policy")),
        timeout_policy=_parse_timeout_policy(fm.get("timeout_policy")),
        scope=_parse_scope(fm.get("scope")),
        source=str(path),
    )


def validate_workflow_contract(contract: WorkflowContract | dict[str, Any] | None) -> list[str]:
    if contract is None:
        return []
    if isinstance(contract, WorkflowContract):
        if contract.contract_version != 1:
            return [f"contract_version must be 1, got {contract.contract_version!r}"]
        return []
    if is_dataclass(contract):
        return []
    if isinstance(contract, dict):
        try:
            load_workflow_contract_from_dict(contract)
            return []
        except WorkflowLoadError as exc:
            return [str(exc)]
    return [f"Unsupported contract type: {type(contract).__name__}"]


def load_workflow_contract_from_dict(data: dict[str, Any]) -> WorkflowContract:
    project = str(data.get("project") or "").strip()
    return WorkflowContract(
        contract_version=int(data.get("contract_version", _DEFAULT_CONTRACT_VERSION)),
        project=project,
        approval_gates=_parse_approval_gates(data.get("approval_gates")),
        retry_policy=_parse_retry_policy(data.get("retry_policy")),
        timeout_policy=_parse_timeout_policy(data.get("timeout_policy")),
        scope=_parse_scope(data.get("scope")),
        source="dict",
    )


def contract_summary(contract: WorkflowContract | dict[str, Any] | None) -> dict[str, Any]:
    if contract is None:
        return {"contract_loaded": False}
    if isinstance(contract, dict):
        try:
            contract = load_workflow_contract_from_dict(contract)
        except WorkflowLoadError as exc:
            return {"contract_loaded": False, "contract_errors": [str(exc)]}
    data = asdict(contract)
    return {
        "contract_loaded": True,
        "contract_version": data.get("contract_version"),
        "project": data.get("project"),
        "failure_budget": (data.get("retry_policy") or {}).get("failure_budget"),
        "require_human_approval_on_failure": (data.get("approval_gates") or {}).get("require_human_approval_on_failure"),
        "allowed_agents": (data.get("scope") or {}).get("allowed_agents"),
        "source": data.get("source"),
        "contract_errors": [],
    }
