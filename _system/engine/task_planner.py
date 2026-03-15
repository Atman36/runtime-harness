from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from _system.engine.task_claims import TaskClaimStore, claims_root_for_project


FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


@dataclass(frozen=True)
class RoutingDecision:
    selected_agent: str
    selection_source: str
    routing_rule: str | None = None


@dataclass(frozen=True)
class ExecutionPlan:
    workspace_mode: str
    workspace_root: str
    workspace_materialization_required: bool
    edit_scope: list[str]
    parallel_safe: bool
    concurrency_group: str


@dataclass(frozen=True)
class TaskRunPlan:
    project_slug: str
    project_root: Path
    task_path: Path
    spec_path: Path
    task_id: str
    task_title: str
    review_policy: str
    priority: str
    routing: RoutingDecision
    execution: ExecutionPlan


def _read_yaml_file(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return {}
    return dict(loaded)


def read_front_matter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = FRONT_MATTER_RE.match(text)
    if match is None:
        return {}
    loaded = yaml.safe_load(match.group(1)) or {}
    if not isinstance(loaded, dict):
        return {}
    return dict(loaded)


def find_project_root(task_path: Path) -> Path:
    for ancestor in [task_path.parent, *task_path.parents]:
        if (ancestor / "state" / "project.yaml").is_file():
            return ancestor
    raise FileNotFoundError(f"Project state file not found for task: {task_path}")


def resolve_path(base_dir: Path, target_path: str) -> Path:
    candidate = Path(target_path).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    return candidate


def load_project_state(project_root: Path) -> dict[str, Any]:
    return _read_yaml_file(project_root / "state" / "project.yaml")


def load_routing_rules(repo_root: Path) -> list[dict[str, Any]]:
    payload = _read_yaml_file(repo_root / "_system" / "registry" / "routing_rules.yaml")
    rules = payload.get("routing_rules", [])
    return [rule for rule in rules if isinstance(rule, dict)]


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _task_tags(front_matter: dict[str, Any]) -> list[str]:
    tags = _normalize_string_list(front_matter.get("tags"))
    risk_flags = _normalize_string_list(front_matter.get("risk_flags"))
    merged: list[str] = []
    for item in [*tags, *risk_flags]:
        if item not in merged:
            merged.append(item)
    return merged


def select_agent(repo_root: Path, front_matter: dict[str, Any], project_state: dict[str, Any]) -> RoutingDecision:
    preferred_agent = str(front_matter.get("preferred_agent") or "").strip()
    if preferred_agent and preferred_agent != "auto":
        return RoutingDecision(selected_agent=preferred_agent, selection_source="task_front_matter")

    spec_clarity = str(front_matter.get("spec_clarity") or project_state.get("default_spec_clarity") or "clear").strip().lower()
    ambiguity = str(front_matter.get("ambiguity") or project_state.get("default_ambiguity") or "low").strip().lower()
    tags = set(_task_tags(front_matter))

    for rule in load_routing_rules(repo_root):
        when = rule.get("when") if isinstance(rule.get("when"), dict) else {}
        any_tags = set(_normalize_string_list(when.get("any_tags")))
        fallback = bool(when.get("fallback", False))
        rule_spec_clarity = str(when.get("spec_clarity") or "").strip().lower()
        rule_ambiguity = str(when.get("ambiguity") or "").strip().lower()

        tags_ok = True if not any_tags else bool(tags & any_tags)
        clarity_ok = True if not rule_spec_clarity else spec_clarity == rule_spec_clarity
        ambiguity_ok = True if not rule_ambiguity else ambiguity == rule_ambiguity

        if fallback or (tags_ok and clarity_ok and ambiguity_ok):
            selected = str(rule.get("preferred_agent") or "codex").strip() or "codex"
            return RoutingDecision(
                selected_agent=selected,
                selection_source="routing_rules",
                routing_rule=str(rule.get("name") or "").strip() or None,
            )

    default_agent = str(project_state.get("default_agent") or "codex").strip() or "codex"
    return RoutingDecision(selected_agent=default_agent, selection_source="project_default")


def _claimed_agent_for_task(project_root: Path, task_id: str) -> str | None:
    try:
        store = TaskClaimStore(claims_root_for_project(project_root))
        claim = store.load_claim(task_id)
    except Exception:
        return None
    if not isinstance(claim, dict):
        return None
    if str(claim.get("status") or "").strip() != "claimed":
        return None
    owner = str(claim.get("owner") or "").strip()
    return owner or None


def build_execution_plan(project_root: Path, front_matter: dict[str, Any], project_state: dict[str, Any]) -> ExecutionPlan:
    execution = project_state.get("execution") if isinstance(project_state.get("execution"), dict) else {}
    workspace_mode = str(front_matter.get("workspace_mode") or execution.get("workspace_mode") or "shared_project").strip()
    edit_scope = _normalize_string_list(front_matter.get("edit_scope") or execution.get("default_edit_scope") or ["_system", "scripts"])
    parallel_safe = bool(front_matter.get("parallel_safe", execution.get("parallel_safe", workspace_mode != "shared_project")))
    workspace_materialization_required = workspace_mode in {"git_worktree", "isolated_checkout"}
    workspace_root = str(project_root)
    concurrency_suffix = ",".join(edit_scope) if edit_scope else "all"
    concurrency_group = f"{project_root.name}:{workspace_mode}:{concurrency_suffix}"
    return ExecutionPlan(
        workspace_mode=workspace_mode,
        workspace_root=workspace_root,
        workspace_materialization_required=workspace_materialization_required,
        edit_scope=edit_scope,
        parallel_safe=parallel_safe,
        concurrency_group=concurrency_group,
    )


def plan_task_run(repo_root: Path, task_path: Path | str) -> TaskRunPlan:
    task = Path(task_path).expanduser().resolve()
    if not task.is_file():
        raise FileNotFoundError(f"Task file not found: {task}")

    front_matter = read_front_matter(task)
    project_root = find_project_root(task)
    project_state = load_project_state(project_root)
    spec_ref = str(front_matter.get("spec") or "").strip()
    if not spec_ref:
        raise ValueError(f"Task front matter missing spec: {task}")
    spec_path = resolve_path(task.parent, spec_ref)
    if not spec_path.is_file():
        raise FileNotFoundError(f"Spec file not found: {spec_path}")

    task_id = str(front_matter.get("id") or "").strip()
    if not task_id:
        raise ValueError(f"Task front matter missing id: {task}")

    claimed_agent = _claimed_agent_for_task(project_root, task_id)
    if claimed_agent:
        routing = RoutingDecision(selected_agent=claimed_agent, selection_source="task_claim")
    else:
        routing = select_agent(Path(repo_root).resolve(), front_matter, project_state)
    execution = build_execution_plan(project_root, front_matter, project_state)
    return TaskRunPlan(
        project_slug=project_root.name,
        project_root=project_root,
        task_path=task,
        spec_path=spec_path,
        task_id=task_id,
        task_title=str(front_matter.get("title") or "").strip(),
        review_policy=str(front_matter.get("review_policy") or "standard").strip() or "standard",
        priority=str(front_matter.get("priority") or "").strip(),
        routing=routing,
        execution=execution,
    )


def plan_to_dict(plan: TaskRunPlan) -> dict[str, Any]:
    return {
        "project": plan.project_slug,
        "task_id": plan.task_id,
        "task_title": plan.task_title,
        "task_path": str(plan.task_path),
        "spec_path": str(plan.spec_path),
        "review_policy": plan.review_policy,
        "priority": plan.priority,
        "routing": {
            "selected_agent": plan.routing.selected_agent,
            "selection_source": plan.routing.selection_source,
            "routing_rule": plan.routing.routing_rule,
        },
        "execution": {
            "workspace_mode": plan.execution.workspace_mode,
            "workspace_root": plan.execution.workspace_root,
            "workspace_materialization_required": plan.execution.workspace_materialization_required,
            "edit_scope": plan.execution.edit_scope,
            "parallel_safe": plan.execution.parallel_safe,
            "concurrency_group": plan.execution.concurrency_group,
        },
    }
