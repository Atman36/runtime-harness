"""Append-only run event log helpers for replayable OpenClaw status."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

EVENT_LOG_FILE = "events.jsonl"
EVENT_SNAPSHOT_FILE = "event_snapshot.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def project_root_for_run(run_dir: Path) -> Path:
    return run_dir.resolve().parent.parent.parent


def event_log_path(run_dir: Path) -> Path:
    return run_dir / EVENT_LOG_FILE


def event_snapshot_path(run_dir: Path) -> Path:
    return run_dir / EVENT_SNAPSHOT_FILE


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def load_run_events(run_dir: Path) -> list[dict]:
    path = event_log_path(run_dir)
    if not path.is_file():
        return []

    events: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path} on line {line_number}: {exc}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"Invalid event object in {path} on line {line_number}: expected object")
            events.append(event)
    return events


def build_run_event_snapshot(project_root: Path, run_dir: Path, *, events: list[dict] | None = None) -> dict:
    resolved_events = list(events) if events is not None else load_run_events(run_dir)
    resolved_run_dir = run_dir.resolve()
    resolved_project_root = project_root.resolve()
    run_path = resolved_run_dir.relative_to(resolved_project_root).as_posix()

    queue_state = None
    run_status = None
    delivery_status = None
    attempt_count = 0
    last_event_type = None
    last_event_at = None

    for event in resolved_events:
        event_type = str(event.get("event_type") or "").strip()
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        last_event_type = event_type or last_event_type
        last_event_at = event.get("recorded_at") or last_event_at

        payload_queue_state = payload.get("queue_state")
        if isinstance(payload_queue_state, str) and payload_queue_state.strip():
            queue_state = payload_queue_state

        payload_run_status = payload.get("run_status", payload.get("result_status"))
        if isinstance(payload_run_status, str) and payload_run_status.strip():
            run_status = payload_run_status

        payload_delivery = payload.get("delivery_status")
        if isinstance(payload_delivery, str) and payload_delivery.strip():
            delivery_status = payload_delivery

        payload_attempt = payload.get("attempt_count")
        if isinstance(payload_attempt, int):
            attempt_count = payload_attempt

        if event_type == "run_created":
            run_status = run_status or "created"
        elif event_type == "run_enqueued":
            queue_state = queue_state or "pending"
            run_status = run_status or "queued"
        elif event_type == "job_claimed":
            queue_state = "running"
            run_status = run_status or "running"
        elif event_type == "job_retried":
            queue_state = "pending"
        elif event_type == "job_dead_letter":
            queue_state = "dead_letter"
        elif event_type == "approval_granted":
            queue_state = "pending"
        elif event_type == "delivery_sent":
            delivery_status = "delivered"
        elif event_type == "delivery_failed":
            delivery_status = "failed"

    return {
        "snapshot_version": 1,
        "project": project_root.name,
        "run_id": resolved_run_dir.name,
        "run_path": run_path,
        "updated_at": last_event_at,
        "event_count": len(resolved_events),
        "last_event_type": last_event_type,
        "last_event_at": last_event_at,
        "queue_state": queue_state,
        "run_status": run_status,
        "delivery_status": delivery_status,
        "attempt_count": attempt_count,
    }


def refresh_run_event_snapshot(project_root: Path, run_dir: Path, *, events: list[dict] | None = None) -> dict:
    snapshot = build_run_event_snapshot(project_root, run_dir, events=events)
    _write_json_atomic(event_snapshot_path(run_dir), snapshot)
    return snapshot


def append_run_event(
    run_dir: Path,
    event_type: str,
    *,
    payload: dict | None = None,
    project_root: Path | None = None,
) -> dict:
    resolved_project_root = project_root.resolve() if project_root is not None else project_root_for_run(run_dir)
    resolved_run_dir = run_dir.resolve()
    event = {
        "event_version": 1,
        "event_id": uuid4().hex,
        "recorded_at": utc_now(),
        "event_type": str(event_type).strip(),
        "project": resolved_project_root.name,
        "run_id": resolved_run_dir.name,
        "run_path": resolved_run_dir.relative_to(resolved_project_root).as_posix(),
        "payload": payload or {},
    }

    log_path = event_log_path(resolved_run_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False))
        handle.write("\n")

    events = load_run_events(resolved_run_dir)
    refresh_run_event_snapshot(resolved_project_root, resolved_run_dir, events=events)
    return event
