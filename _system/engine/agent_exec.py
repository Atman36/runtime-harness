from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AgentCommand:
    agent: str
    command: list[str]
    cwd: Path
    timeout_seconds: int
    prompt_mode: str


def _read_agents_registry(repo_root: Path) -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load((repo_root / "_system" / "registry" / "agents.yaml").read_text(encoding="utf-8")) or {}
    agents = payload.get("agents", {}) if isinstance(payload, dict) else {}
    return {str(name): cfg for name, cfg in agents.items() if isinstance(cfg, dict)}


def _format_args(template: str, replacements: dict[str, str]) -> list[str]:
    rendered = template
    for key, value in replacements.items():
        rendered = rendered.replace("{" + key + "}", value)
    return shlex.split(rendered)


def build_agent_command(
    repo_root: Path,
    *,
    agent: str,
    project_root: Path,
    prompt: str | None = None,
    prompt_path: Path | None = None,
    workspace_root: Path | None = None,
) -> AgentCommand:
    registry = _read_agents_registry(Path(repo_root).resolve())
    if agent not in registry:
        raise KeyError(f"Unknown agent: {agent}")

    config = registry[agent]
    command = [str(config.get("command") or agent)]
    args = str(config.get("args") or "").strip()
    replacements = {
        "project_root": str(project_root),
        "workspace_root": str((workspace_root or project_root).resolve()),
        "prompt_path": str(prompt_path) if prompt_path else "",
    }
    if args:
        command.extend(_format_args(args, replacements))

    prompt_mode = str(config.get("prompt_mode") or "arg").strip() or "arg"
    if prompt_mode == "arg" and prompt:
        command.append(prompt)
    elif prompt_mode == "stdin":
        # The caller is responsible for sending the prompt via stdin.
        pass

    cwd_mode = str(config.get("cwd") or "project_root").strip()
    cwd = (workspace_root or project_root) if cwd_mode == "workspace_root" else project_root
    timeout_seconds = int(config.get("default_timeout_seconds") or 3600)
    return AgentCommand(agent=agent, command=command, cwd=Path(cwd).resolve(), timeout_seconds=timeout_seconds, prompt_mode=prompt_mode)
