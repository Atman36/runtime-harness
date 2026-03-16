from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from _system.engine.runtime import resolve_project_root


DIRECTIVE_VERSION = 1
_CTX_PREFIX = "ctx:"
_VALID_KEYS = ("project", "agent", "branch")
_WORKTREE_MODES = {"git_worktree", "isolated_checkout"}
_SHARED_MODES = {"", "project_root", "shared_project"}
_DIRECTIVE_PATTERN = re.compile(r"(?<!\S)/(?P<key>agent|project)(?:[:=]|\s+)(?P<value>[^\s]+)")
_BRANCH_PATTERN = re.compile(r"(?<!\S)@(?P<value>[^\s]+)")


def _normalize_token(value: Any) -> str:
    token = str(value or "").strip()
    return token.rstrip(".,;:")


def _read_yaml_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return {}
    return dict(loaded)


def known_agents(repo_root: Path) -> set[str]:
    payload = _read_yaml_object(Path(repo_root) / "_system" / "registry" / "agents.yaml")
    agents = payload.get("agents") if isinstance(payload.get("agents"), dict) else {}
    names = {str(name).strip() for name in agents.keys() if str(name).strip()}
    return names or {"codex", "claude"}


def extract_ctx_footer(message_text: str | None) -> tuple[dict[str, str], str | None, str]:
    text = str(message_text or "")
    lines = text.splitlines()
    end = len(lines)
    while end > 0 and not lines[end - 1].strip():
        end -= 1
    if end == 0:
        return {}, None, ""

    footer_line = lines[end - 1].strip()
    if not footer_line.lower().startswith(_CTX_PREFIX):
        return {}, None, "\n".join(lines[:end])

    body = footer_line[len(_CTX_PREFIX):].strip()
    footer: dict[str, str] = {}
    if body:
        for token in body.split():
            if "=" not in token:
                raise ValueError(f"Invalid ctx footer token: {token}")
            key, raw_value = token.split("=", 1)
            key = _normalize_token(key).lower()
            value = _normalize_token(raw_value)
            if key not in _VALID_KEYS:
                raise ValueError(f"Unsupported ctx footer key: {key}")
            if not value:
                raise ValueError(f"ctx footer key '{key}' requires a value")
            existing = footer.get(key)
            if existing and existing != value:
                raise ValueError(f"Conflicting ctx footer values for '{key}': {existing} vs {value}")
            footer[key] = value

    return footer, footer_line, "\n".join(lines[: end - 1]).rstrip()


def parse_message_directives(message_text: str | None) -> dict[str, str]:
    text = str(message_text or "")
    parsed: dict[str, str] = {}

    for match in _DIRECTIVE_PATTERN.finditer(text):
        key = match.group("key")
        value = _normalize_token(match.group("value"))
        if not value:
            raise ValueError(f"Directive '/{key}' requires a value")
        existing = parsed.get(key)
        if existing and existing != value:
            raise ValueError(f"Conflicting /{key} directives: {existing} vs {value}")
        parsed[key] = value

    for match in _BRANCH_PATTERN.finditer(text):
        value = _normalize_token(match.group("value"))
        if not value:
            continue
        existing = parsed.get("branch")
        if existing and existing != value:
            raise ValueError(f"Conflicting @branch directives: {existing} vs {value}")
        parsed["branch"] = value

    return parsed


def render_ctx_footer(context: dict[str, Any] | None) -> str | None:
    payload = context if isinstance(context, dict) else {}
    parts = [f"{key}={_normalize_token(payload.get(key))}" for key in _VALID_KEYS if _normalize_token(payload.get(key))]
    if not parts:
        return None
    return f"{_CTX_PREFIX} {' '.join(parts)}"


def _normalize_defaults(defaults: dict[str, Any] | None) -> dict[str, str]:
    payload = defaults if isinstance(defaults, dict) else {}
    normalized: dict[str, str] = {}
    for key in _VALID_KEYS:
        value = _normalize_token(payload.get(key))
        if value:
            normalized[key] = value
    return normalized


def _resolve_value(key: str, reply_context: dict[str, str], directives: dict[str, str], defaults: dict[str, str]) -> tuple[str | None, str | None]:
    if reply_context.get(key):
        return reply_context[key], "reply_context"
    if directives.get(key):
        return directives[key], "directive"
    if defaults.get(key):
        return defaults[key], "default"
    return None, None


def _resolve_project_reference(repo_root: Path, reference: str) -> Path:
    candidate = _normalize_token(reference)
    direct_path = Path(candidate).expanduser()
    if direct_path.is_absolute() or "/" in candidate or "\\" in candidate:
        return resolve_project_root(str(direct_path if direct_path.is_absolute() else (Path(repo_root) / direct_path)))
    return resolve_project_root(str(Path(repo_root) / "projects" / candidate))


def _project_defaults(project_root: Path) -> tuple[str, str]:
    project_state = _read_yaml_object(project_root / "state" / "project.yaml")
    execution = project_state.get("execution") if isinstance(project_state.get("execution"), dict) else {}
    workspace_mode = _normalize_token(execution.get("workspace_mode")) or "shared_project"
    project_slug = _normalize_token(project_state.get("slug")) or project_root.name
    return project_slug, workspace_mode


def bind_operator_context(
    repo_root: Path | str,
    message_text: str | None,
    *,
    reply_message_text: str | None = None,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_repo_root = Path(repo_root).expanduser().resolve()
    reply_context, reply_footer, _reply_body = extract_ctx_footer(reply_message_text)
    message_footer_context, _message_footer, message_body = extract_ctx_footer(message_text)
    directives = parse_message_directives(message_body)
    normalized_defaults = _normalize_defaults(defaults)

    if message_footer_context:
        for key, value in message_footer_context.items():
            existing = normalized_defaults.get(key)
            if existing and existing != value:
                raise ValueError(f"Message ctx footer conflicts with ambient default for '{key}': {existing} vs {value}")
            normalized_defaults[key] = value

    project_ref, project_source = _resolve_value("project", reply_context, directives, normalized_defaults)
    agent, agent_source = _resolve_value("agent", reply_context, directives, normalized_defaults)
    branch, branch_source = _resolve_value("branch", reply_context, directives, normalized_defaults)

    if agent:
        available_agents = known_agents(normalized_repo_root)
        if agent not in available_agents:
            raise ValueError(f"Unknown agent '{agent}'. Known agents: {', '.join(sorted(available_agents))}")

    project_root: Path | None = None
    project_slug: str | None = None
    workspace_mode: str | None = None
    workspace_source: str | None = None
    if project_ref:
        project_root = _resolve_project_reference(normalized_repo_root, project_ref)
        project_slug, workspace_mode = _project_defaults(project_root)
        workspace_source = "project_default"

    if branch and not project_root:
        raise ValueError("Branch directive requires project context via reply context, /project, or defaults")

    if branch and workspace_mode in _SHARED_MODES:
        workspace_mode = "git_worktree"
        workspace_source = "branch_target"

    if not workspace_mode and project_root:
        workspace_mode = "shared_project"
        workspace_source = workspace_source or "project_default"

    return {
        "binding_version": DIRECTIVE_VERSION,
        "message_text": str(message_text or ""),
        "message_body": message_body,
        "directives": directives,
        "reply_context": reply_context,
        "reply_ctx_footer": reply_footer,
        "defaults": normalized_defaults,
        "resolved": {
            "project": project_slug,
            "project_root": str(project_root) if project_root is not None else None,
            "agent": agent,
            "branch": branch,
            "workspace_mode": workspace_mode,
            "workspace_materialization_required": workspace_mode in _WORKTREE_MODES if workspace_mode else False,
        },
        "sources": {
            "project": project_source,
            "agent": agent_source,
            "branch": branch_source,
            "workspace_mode": workspace_source,
        },
        "ctx_footer": render_ctx_footer(
            {
                "project": project_slug,
                "agent": agent,
                "branch": branch,
            }
        ),
    }
