from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

from _system.engine.task_lifecycle import (
    is_terminal_task_status,
    normalize_agent_registry,
    normalize_task_state_entry,
    parse_iso_timestamp,
    task_terminal_grace_ms,
)

ORCHESTRATION_STATE_VERSION = 2
ORCHESTRATION_STATE_FILE = "orchestration_state.json"

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACTS_DIR = REPO_ROOT / "_system" / "contracts"
ORCHESTRATION_STATE_SCHEMA = CONTRACTS_DIR / "orchestration_state.schema.json"


def default_dream_state() -> dict[str, Any]:
    return {
        "last_started_at": None,
        "last_completed_at": None,
        "last_checked_at": None,
        "last_result": None,
        "last_run_count": 0,
        "last_files_touched": [],
    }


def default_orchestration_state() -> dict[str, Any]:
    return {
        "state_version": ORCHESTRATION_STATE_VERSION,
        "consecutive_failures": 0,
        "last_run_id": None,
        "last_decision": None,
        "last_updated_at": None,
        "tasks": {},
        "agentRegistry": {},
        "dream": default_dream_state(),
    }


def _check_node(data: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    label = path or "<root>"
    schema_type = schema.get("type")

    if schema_type:
        types = schema_type if isinstance(schema_type, list) else [schema_type]
        if not any(_matches_type(data, item) for item in types):
            errors.append(f"{label}: expected type {schema_type}, got {type(data).__name__}")
            return

    if "const" in schema and data != schema["const"]:
        errors.append(f"{label}: expected {schema['const']!r}, got {data!r}")
    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{label}: {data!r} not in {schema['enum']}")

    if isinstance(data, dict):
        for required in schema.get("required", []):
            if required not in data:
                errors.append(f"{label}: missing required field '{required}'")

        for key, sub_schema in schema.get("properties", {}).items():
            if key in data:
                _check_node(data[key], sub_schema, f"{path}.{key}" if path else key, errors)

        additional = schema.get("additionalProperties", True)
        if isinstance(additional, dict):
            known_keys = set(schema.get("properties", {}).keys())
            for key, value in data.items():
                if key in known_keys:
                    continue
                _check_node(value, additional, f"{path}.{key}" if path else key, errors)
    elif isinstance(data, list):
        item_schema = schema.get("items", {})
        for index, item in enumerate(data):
            _check_node(item, item_schema, f"{label}[{index}]", errors)


def _matches_type(data: Any, expected: str) -> bool:
    if expected == "null":
        return data is None
    if expected == "boolean":
        return isinstance(data, bool)
    if expected == "integer":
        return isinstance(data, int) and not isinstance(data, bool)
    if expected == "number":
        return isinstance(data, (int, float)) and not isinstance(data, bool)
    if expected == "string":
        return isinstance(data, str)
    if expected == "array":
        return isinstance(data, list)
    if expected == "object":
        return isinstance(data, dict)
    return False


def validate_orchestration_state(payload: Any) -> list[str]:
    if not ORCHESTRATION_STATE_SCHEMA.is_file():
        return [f"Schema not found: {ORCHESTRATION_STATE_SCHEMA}"]
    try:
        schema = json.loads(ORCHESTRATION_STATE_SCHEMA.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"Invalid orchestration state schema JSON: {exc}"]

    errors: list[str] = []
    _check_node(payload, schema, "", errors)
    return errors


def normalize_dream_state(payload: Any) -> dict[str, Any]:
    raw = payload if isinstance(payload, dict) else {}
    files = raw.get("last_files_touched")
    if isinstance(files, list):
        normalized_files = [str(item).strip() for item in files if str(item).strip()]
    else:
        normalized_files = []
    try:
        run_count = max(0, int(raw.get("last_run_count", 0) or 0))
    except (TypeError, ValueError):
        run_count = 0
    return {
        "last_started_at": str(raw.get("last_started_at") or "").strip() or None,
        "last_completed_at": str(raw.get("last_completed_at") or "").strip() or None,
        "last_checked_at": str(raw.get("last_checked_at") or "").strip() or None,
        "last_result": str(raw.get("last_result") or "").strip() or None,
        "last_run_count": run_count,
        "last_files_touched": normalized_files,
    }


def evict_terminal_task_records(state: dict[str, Any], *, now_ms: int | None = None) -> tuple[dict[str, Any], list[str]]:
    normalized = deepcopy(state)
    tasks = normalized.get("tasks") if isinstance(normalized.get("tasks"), dict) else {}
    current_ms = now_ms
    if current_ms is None:
        from time import time

        current_ms = int(time() * 1000)

    kept: dict[str, dict[str, Any]] = {}
    evicted: list[str] = []
    for runtime_id, raw_entry in tasks.items():
        if not isinstance(raw_entry, dict):
            continue
        entry = normalize_task_state_entry(str(runtime_id), raw_entry)
        if not is_terminal_task_status(entry.get("status")) or not entry.get("notified", False):
            kept[entry["id"]] = entry
            continue

        terminal_at = (
            parse_iso_timestamp(entry.get("endTime"))
            or parse_iso_timestamp(entry.get("updated_at"))
            or parse_iso_timestamp(entry.get("startTime"))
        )
        if terminal_at is None:
            kept[entry["id"]] = entry
            continue
        age_ms = current_ms - int(terminal_at.timestamp() * 1000)
        if age_ms < task_terminal_grace_ms(entry.get("type")):
            kept[entry["id"]] = entry
            continue
        evicted.append(entry["id"])

    normalized["tasks"] = kept
    return normalized, evicted


def normalize_orchestration_state(payload: Any) -> dict[str, Any]:
    state = default_orchestration_state()
    raw = payload if isinstance(payload, dict) else {}

    try:
        consecutive_failures = max(0, int(raw.get("consecutive_failures", 0) or 0))
    except (TypeError, ValueError):
        consecutive_failures = 0

    raw_tasks = raw.get("tasks")
    normalized_tasks: dict[str, dict[str, Any]] = {}
    if isinstance(raw_tasks, dict):
        for runtime_id, raw_entry in raw_tasks.items():
            if not isinstance(raw_entry, dict):
                continue
            try:
                entry = normalize_task_state_entry(str(runtime_id), raw_entry)
            except ValueError:
                continue
            normalized_tasks[entry["id"]] = entry

    state.update(
        {
            "state_version": ORCHESTRATION_STATE_VERSION,
            "consecutive_failures": consecutive_failures,
            "last_run_id": raw.get("last_run_id"),
            "last_decision": raw.get("last_decision"),
            "last_updated_at": raw.get("last_updated_at"),
            "tasks": normalized_tasks,
            "agentRegistry": normalize_agent_registry(raw.get("agentRegistry")),
            "dream": normalize_dream_state(raw.get("dream")),
        }
    )
    pruned, _evicted = evict_terminal_task_records(state)
    return pruned


def read_orchestration_state(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return default_orchestration_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default_orchestration_state()
    return normalize_orchestration_state(payload)


def write_orchestration_state(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_orchestration_state(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(normalized, handle, indent=2)
            handle.write("\n")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return normalized
