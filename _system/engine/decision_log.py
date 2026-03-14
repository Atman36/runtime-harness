"""Append-only orchestrator decision log helpers."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

DECISION_LOG_FILE = "decision_log.jsonl"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def decision_log_path(project_root: Path) -> Path:
    return project_root / "state" / DECISION_LOG_FILE


def _append_jsonl_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record, ensure_ascii=False).encode("utf-8") + b"\n"
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, encoded)
    finally:
        os.close(fd)


def _read_jsonl_lines(path: Path) -> list[dict]:
    if not path.is_file():
        return []

    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON in {path} on line {line_number}: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Invalid record in {path} on line {line_number}: expected object")
            records.append(record)
    return records


def append_decision(
    project_root: Path,
    kind: str,
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    reason_code: str | None = None,
    details: dict | None = None,
    outcome: str | None = None,
) -> dict:
    decision = {
        "ts": utc_now(),
        "decision_id": f"dec-{uuid4().hex[:16]}",
        "kind": str(kind).strip(),
        "run_id": str(run_id or "").strip(),
        "task_id": str(task_id or "").strip(),
        "reason_code": str(reason_code or "").strip(),
        "details": details if isinstance(details, dict) else {},
        "outcome": str(outcome or "").strip(),
    }
    _append_jsonl_record(decision_log_path(project_root), decision)
    return decision


def read_decisions(project_root: Path, last_n: int | None = None) -> list[dict]:
    records = _read_jsonl_lines(decision_log_path(project_root))
    if last_n is not None and last_n > 0:
        return records[-last_n:]
    return records


def format_decision_for_display(record: dict) -> str:
    ts = record.get("ts", "?")
    decision_id = record.get("decision_id", "?")
    kind = record.get("kind", "?")
    run_id = record.get("run_id", "")
    task_id = record.get("task_id", "")
    reason_code = record.get("reason_code", "")
    outcome = record.get("outcome", "")
    details = record.get("details", {})

    parts = [f"[{ts}] {decision_id} | {kind}"]
    if run_id:
        parts.append(f"run:{run_id}")
    if task_id:
        parts.append(f"task:{task_id}")
    if reason_code:
        parts.append(f"reason:{reason_code}")
    if outcome:
        parts.append(f"outcome:{outcome}")
    if details:
        detail_str = json.dumps(details, ensure_ascii=False, sort_keys=True)
        if len(detail_str) > 60:
            detail_str = detail_str[:57] + "..."
        parts.append(f"details:{detail_str}")

    return " | ".join(parts)
