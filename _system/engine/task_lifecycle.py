from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any


TASK_POLL_INTERVAL_MS = 1000
TASK_PANEL_GRACE_MS = 30_000

VALID_TASK_TYPES = (
    "local_bash",
    "local_agent",
    "remote_agent",
    "dream",
)
VALID_TASK_STATUSES = (
    "pending",
    "running",
    "completed",
    "failed",
    "killed",
)
TASK_ID_PREFIXES = {
    "local_bash": "b",
    "local_agent": "a",
    "remote_agent": "r",
    "dream": "d",
}
TASK_ID_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def normalize_task_type(value: str | None, *, default: str = "local_agent") -> str:
    normalized = str(value or "").strip()
    if normalized in VALID_TASK_TYPES:
        return normalized
    return default


def normalize_task_status(value: str | None, *, default: str = "pending") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in VALID_TASK_STATUSES:
        return normalized
    return default


def is_terminal_task_status(status: str | None) -> bool:
    normalized = normalize_task_status(status)
    return normalized in {"completed", "failed", "killed"}


def generate_task_runtime_id(task_type: str) -> str:
    normalized_type = normalize_task_type(task_type)
    prefix = TASK_ID_PREFIXES.get(normalized_type, "x")
    token = "".join(TASK_ID_ALPHABET[byte % len(TASK_ID_ALPHABET)] for byte in secrets.token_bytes(8))
    return f"{prefix}{token}"


def output_offset_for_file(path: Path | None) -> int:
    if path is None:
        return 0
    try:
        return max(0, int(path.stat().st_size))
    except OSError:
        return 0


def normalize_task_state_entry(task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized_id = str(task_id or "").strip()
    if not normalized_id:
        raise ValueError("Task lifecycle entry id is required")

    start_time = payload.get("startTime")
    end_time = payload.get("endTime")
    selected_agent = str(payload.get("selected_agent") or "").strip() or None
    run_id = str(payload.get("run_id") or "").strip() or None
    run_path = str(payload.get("run_path") or "").strip() or None
    workflow_status = str(payload.get("workflow_status") or "").strip().lower() or None
    updated_at = str(payload.get("updated_at") or "").strip() or None

    try:
        output_offset = max(0, int(payload.get("outputOffset", 0) or 0))
    except (TypeError, ValueError):
        output_offset = 0

    return {
        "id": normalized_id,
        "task_id": str(payload.get("task_id") or "").strip(),
        "task_path": str(payload.get("task_path") or "").strip(),
        "type": normalize_task_type(payload.get("type")),
        "status": normalize_task_status(payload.get("status")),
        "description": str(payload.get("description") or "").strip(),
        "startTime": str(start_time).strip() if isinstance(start_time, str) and str(start_time).strip() else None,
        "endTime": str(end_time).strip() if isinstance(end_time, str) and str(end_time).strip() else None,
        "outputFile": str(payload.get("outputFile") or "").strip(),
        "outputOffset": output_offset,
        "notified": bool(payload.get("notified", False)),
        "selected_agent": selected_agent,
        "run_id": run_id,
        "run_path": run_path,
        "workflow_status": workflow_status,
        "updated_at": updated_at,
    }


def normalize_agent_registry(payload: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, dict):
        return {}

    normalized: dict[str, dict[str, Any]] = {}
    for agent_name, raw_entry in payload.items():
        if not isinstance(raw_entry, dict):
            continue
        active_task_ids = raw_entry.get("active_task_ids")
        if isinstance(active_task_ids, list):
            active_ids = [str(item).strip() for item in active_task_ids if str(item).strip()]
        else:
            active_ids = []
        normalized[str(agent_name)] = {
            "active_task_ids": active_ids,
            "last_task_id": str(raw_entry.get("last_task_id") or "").strip() or None,
            "updated_at": str(raw_entry.get("updated_at") or "").strip() or None,
        }
    return normalized
