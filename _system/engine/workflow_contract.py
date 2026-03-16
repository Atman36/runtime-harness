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
class Commands:
    test: str = "bash tests/run_all.sh"
    lint: str = ""
    build: str = ""
    smoke: str = ""


@dataclass(frozen=True)
class BudgetGuardrails:
    enabled: bool = False
    warning_limit: int = 0
    hard_limit: int = 0
    base_run_cost: int = 1
    agent_costs: dict[str, int] = field(default_factory=dict)
    workspace_mode_costs: dict[str, int] = field(default_factory=dict)
    risk_flag_costs: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class GovernanceGuardrails:
    approval_required_risk_flags: tuple[str, ...] = field(default_factory=tuple)
    approval_required_paths: tuple[str, ...] = field(default_factory=tuple)
    approval_required_workspace_modes: tuple[str, ...] = field(default_factory=tuple)
    approval_required_agents: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class GuardrailPolicy:
    budget: BudgetGuardrails = field(default_factory=BudgetGuardrails)
    governance: GovernanceGuardrails = field(default_factory=GovernanceGuardrails)


@dataclass(frozen=True)
class WorkflowContract:
    contract_version: int = _DEFAULT_CONTRACT_VERSION
    project: str = ""
    approval_gates: ApprovalGates = field(default_factory=ApprovalGates)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    timeout_policy: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    scope: WorkflowScope = field(default_factory=WorkflowScope)
    commands: Commands = field(default_factory=Commands)
    guardrails: GuardrailPolicy = field(default_factory=GuardrailPolicy)
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


def _require_non_negative_int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    try:
        value = int(value)
    except (TypeError, ValueError) as exc:
        raise WorkflowLoadError(f"{key!r} must be an integer, got {value!r}") from exc
    if value < 0:
        raise WorkflowLoadError(f"{key!r} must be >= 0, got {value!r}")
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


def _parse_commands(raw: Any) -> Commands:
    if raw is None:
        return Commands()
    if not isinstance(raw, dict):
        raise WorkflowLoadError(f"commands must be a mapping, got {type(raw).__name__}")
    return Commands(
        test=str(raw.get("test", "bash tests/run_all.sh")),
        lint=str(raw.get("lint", "")),
        build=str(raw.get("build", "")),
        smoke=str(raw.get("smoke", "")),
    )


def _parse_int_map(raw: Any, *, field_name: str) -> dict[str, int]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise WorkflowLoadError(f"{field_name} must be a mapping, got {type(raw).__name__}")
    parsed: dict[str, int] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            continue
        try:
            parsed[name] = int(value)
        except (TypeError, ValueError) as exc:
            raise WorkflowLoadError(f"{field_name}.{name!r} must be an integer, got {value!r}") from exc
        if parsed[name] < 0:
            raise WorkflowLoadError(f"{field_name}.{name!r} must be >= 0, got {value!r}")
    return parsed


def _parse_string_tuple(raw: Any, *, field_name: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise WorkflowLoadError(f"{field_name} must be a list, got {type(raw).__name__}")
    values = [str(item).strip() for item in raw if str(item).strip()]
    return tuple(values)


def _parse_budget_guardrails(raw: Any) -> BudgetGuardrails:
    if raw is None:
        return BudgetGuardrails()
    if not isinstance(raw, dict):
        raise WorkflowLoadError(f"guardrails.budget must be a mapping, got {type(raw).__name__}")
    warning_limit = _require_non_negative_int(raw, "warning_limit", 0)
    hard_limit = _require_non_negative_int(raw, "hard_limit", 0)
    if warning_limit and hard_limit and hard_limit < warning_limit:
        raise WorkflowLoadError("guardrails.budget.hard_limit must be >= warning_limit")
    return BudgetGuardrails(
        enabled=_require_bool(raw, "enabled", False),
        warning_limit=warning_limit,
        hard_limit=hard_limit,
        base_run_cost=_require_positive_int(raw, "base_run_cost", 1),
        agent_costs=_parse_int_map(raw.get("agent_costs"), field_name="guardrails.budget.agent_costs"),
        workspace_mode_costs=_parse_int_map(raw.get("workspace_mode_costs"), field_name="guardrails.budget.workspace_mode_costs"),
        risk_flag_costs=_parse_int_map(raw.get("risk_flag_costs"), field_name="guardrails.budget.risk_flag_costs"),
    )


def _parse_governance_guardrails(raw: Any) -> GovernanceGuardrails:
    if raw is None:
        return GovernanceGuardrails()
    if not isinstance(raw, dict):
        raise WorkflowLoadError(f"guardrails.governance must be a mapping, got {type(raw).__name__}")
    return GovernanceGuardrails(
        approval_required_risk_flags=_parse_string_tuple(raw.get("approval_required_risk_flags"), field_name="guardrails.governance.approval_required_risk_flags"),
        approval_required_paths=_parse_string_tuple(raw.get("approval_required_paths"), field_name="guardrails.governance.approval_required_paths"),
        approval_required_workspace_modes=_parse_string_tuple(raw.get("approval_required_workspace_modes"), field_name="guardrails.governance.approval_required_workspace_modes"),
        approval_required_agents=_parse_string_tuple(raw.get("approval_required_agents"), field_name="guardrails.governance.approval_required_agents"),
    )


def _parse_guardrails(raw: Any) -> GuardrailPolicy:
    if raw is None:
        return GuardrailPolicy()
    if not isinstance(raw, dict):
        raise WorkflowLoadError(f"guardrails must be a mapping, got {type(raw).__name__}")
    return GuardrailPolicy(
        budget=_parse_budget_guardrails(raw.get("budget")),
        governance=_parse_governance_guardrails(raw.get("governance")),
    )


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
        commands=_parse_commands(fm.get("commands")),
        guardrails=_parse_guardrails(fm.get("guardrails")),
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
        commands=_parse_commands(data.get("commands")),
        guardrails=_parse_guardrails(data.get("guardrails")),
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
        "commands": data.get("commands"),
        "guardrails": data.get("guardrails"),
        "source": data.get("source"),
        "contract_errors": [],
    }
