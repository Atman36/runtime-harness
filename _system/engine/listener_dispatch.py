from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from _system.engine.trusted_command import parse_trusted_argv


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class _TemplateValues(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _string_context(context: dict) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for key, value in context.items():
        if value is None:
            rendered[str(key)] = ""
        elif isinstance(value, Path):
            rendered[str(key)] = str(value)
        else:
            rendered[str(key)] = str(value)
    return rendered


def load_listeners(registry_path: Path) -> list[dict]:
    if not registry_path.is_file():
        raise FileNotFoundError(f"listeners registry not found: {registry_path}")
    loaded = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError("listeners.yaml malformed: expected mapping")
    listeners = loaded.get("listeners", [])
    if listeners is None:
        return []
    if not isinstance(listeners, list):
        raise ValueError("listeners.yaml malformed: 'listeners' must be a list")
    normalized: list[dict] = []
    for index, raw in enumerate(listeners, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"listeners.yaml malformed: listener #{index} must be a mapping")
        listener_id = str(raw.get("id") or "").strip()
        event = str(raw.get("event") or "").strip()
        command = raw.get("command")
        if not listener_id:
            raise ValueError(f"listeners.yaml malformed: listener #{index} is missing id")
        if not event:
            raise ValueError(f"listeners.yaml malformed: listener '{listener_id}' is missing event")
        if not isinstance(command, (str, list)) or (isinstance(command, str) and not command.strip()):
            raise ValueError(f"listeners.yaml malformed: listener '{listener_id}' must define command")
        condition = raw.get("condition") or {}
        if not isinstance(condition, dict):
            raise ValueError(f"listeners.yaml malformed: listener '{listener_id}' condition must be a mapping")
        normalized.append(
            {
                "id": listener_id,
                "event": event,
                "command": command,
                "condition": condition,
                "enabled": bool(raw.get("enabled", False)),
            }
        )
    return normalized


def match_listeners(listeners: list[dict], event_type: str, context: dict) -> list[dict]:
    matched: list[dict] = []
    for listener in listeners:
        if not listener.get("enabled"):
            continue
        if str(listener.get("event") or "").strip() != event_type:
            continue
        conditions = listener.get("condition") if isinstance(listener.get("condition"), dict) else {}
        for key, expected in conditions.items():
            actual = context.get(key)
            if str(actual) != str(expected):
                break
        else:
            matched.append(listener)
    return matched


def render_listener_command(listener: dict, context: dict) -> list[str]:
    env_name = f"listener {listener.get('id')}"
    values = _TemplateValues(_string_context(context))
    raw_command = listener.get("command")
    if isinstance(raw_command, list):
        rendered = [str(token).format_map(values) for token in raw_command]
        return parse_trusted_argv(json.dumps(rendered, ensure_ascii=False), env_name=env_name) or []
    if isinstance(raw_command, str):
        return parse_trusted_argv(raw_command.format_map(values), env_name=env_name) or []
    raise ValueError(f"{env_name} must define command as string or list")


def append_listener_log(log_path: Path, payload: dict) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")


def dispatch_listeners(matched: list[dict], context: dict, log_path: Path, *, cwd: Path | None = None) -> list[dict]:
    entries: list[dict] = []
    for listener in matched:
        entry = {
            "ts": context.get("ts") or utc_now(),
            "listener_id": listener.get("id"),
            "event": context.get("event") or listener.get("event"),
            "run_id": context.get("run_id"),
            "status": "success",
            "error": None,
        }
        try:
            argv = render_listener_command(listener, context)
            completed = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=False)
            entry["command"] = argv
            entry["exit_code"] = completed.returncode
            if completed.returncode != 0:
                entry["status"] = "failed"
                error_text = (completed.stderr or completed.stdout or "").strip()
                entry["error"] = error_text or f"listener exited with code {completed.returncode}"
        except Exception as exc:
            entry["status"] = "failed"
            entry["error"] = str(exc)
        append_listener_log(log_path, entry)
        entries.append(entry)
    return entries


def dispatch_event_listeners(
    registry_path: Path,
    event_type: str,
    context: dict,
    log_path: Path,
    *,
    cwd: Path | None = None,
) -> list[dict]:
    enriched = dict(context)
    enriched["event"] = event_type
    try:
        listeners = load_listeners(registry_path)
        matched = match_listeners(listeners, event_type, enriched)
    except Exception as exc:
        failure = {
            "ts": enriched.get("ts") or utc_now(),
            "listener_id": "__registry__",
            "event": event_type,
            "run_id": enriched.get("run_id"),
            "status": "failed",
            "error": str(exc),
        }
        append_listener_log(log_path, failure)
        return [failure]
    return dispatch_listeners(matched, enriched, log_path, cwd=cwd)
