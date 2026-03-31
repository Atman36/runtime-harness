from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from typing import Any
from uuid import uuid4


RUN_CONTROL_FILE = "task_control.json"


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_control_path(run_dir: Path) -> Path:
    return run_dir / RUN_CONTROL_FILE


def load_run_control(run_dir: Path) -> dict[str, Any] | None:
    path = run_control_path(run_dir)
    if not path.is_file():
        return None
    try:
        payload = read_json(path)
    except (json.JSONDecodeError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def initialize_run_control(run_dir: Path, *, run_id: str, runtime_task_id: str | None = None) -> Path:
    path = run_control_path(run_dir)
    existing = load_run_control(run_dir) or {}
    payload = {
        "control_version": 1,
        "run_id": run_id,
        "runtime_task_id": str(runtime_task_id or existing.get("runtime_task_id") or "").strip() or None,
        "agent_pid": None,
        "agent_pgid": None,
        "started_at": existing.get("started_at"),
        "updated_at": utc_now(),
        "stop": {
            "requested": False,
            "requested_at": None,
            "requested_by": None,
            "note": None,
            "signal": None,
            "force": False,
            "completed_at": None,
            "outcome": None,
        },
    }
    write_json_atomic(path, payload)
    return path


def record_agent_process(run_dir: Path, *, pid: int, pgid: int | None = None) -> dict[str, Any]:
    path = run_control_path(run_dir)
    payload = load_run_control(run_dir) or {
        "control_version": 1,
        "run_id": run_dir.name,
        "runtime_task_id": None,
        "stop": {},
    }
    payload["agent_pid"] = int(pid)
    payload["agent_pgid"] = int(pgid if pgid is not None else pid)
    payload["started_at"] = payload.get("started_at") or utc_now()
    payload["updated_at"] = utc_now()
    stop = payload.get("stop") if isinstance(payload.get("stop"), dict) else {}
    payload["stop"] = {
        "requested": bool(stop.get("requested", False)),
        "requested_at": stop.get("requested_at"),
        "requested_by": stop.get("requested_by"),
        "note": stop.get("note"),
        "signal": stop.get("signal"),
        "force": bool(stop.get("force", False)),
        "completed_at": stop.get("completed_at"),
        "outcome": stop.get("outcome"),
    }
    write_json_atomic(path, payload)
    return payload


def mark_stop_requested(
    run_dir: Path,
    *,
    requested_by: str,
    note: str | None = None,
    force: bool = False,
) -> dict[str, Any] | None:
    payload = load_run_control(run_dir)
    if payload is None:
        return None
    stop = payload.get("stop") if isinstance(payload.get("stop"), dict) else {}
    stop.update(
        {
            "requested": True,
            "requested_at": utc_now(),
            "requested_by": requested_by,
            "note": (note or "").strip() or None,
            "signal": "SIGKILL" if force else "SIGTERM",
            "force": bool(force),
        }
    )
    payload["stop"] = stop
    payload["updated_at"] = utc_now()
    write_json_atomic(run_control_path(run_dir), payload)
    return payload


def finalize_stop_state(run_dir: Path, *, outcome: str, completed_at: str | None = None) -> dict[str, Any] | None:
    payload = load_run_control(run_dir)
    if payload is None:
        return None
    stop = payload.get("stop") if isinstance(payload.get("stop"), dict) else {}
    stop["completed_at"] = completed_at or utc_now()
    stop["outcome"] = outcome
    payload["stop"] = stop
    payload["updated_at"] = utc_now()
    write_json_atomic(run_control_path(run_dir), payload)
    return payload


def is_process_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def request_stop_signal(run_dir: Path, *, requested_by: str, note: str | None = None, force: bool = False) -> dict[str, Any]:
    payload = mark_stop_requested(run_dir, requested_by=requested_by, note=note, force=force)
    if payload is None:
        return {"status": "missing_control", "run_id": run_dir.name}

    pgid = payload.get("agent_pgid")
    pid = payload.get("agent_pid")
    if isinstance(pgid, int) and pgid > 0:
        target_signal = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.killpg(int(pgid), target_signal)
        except ProcessLookupError:
            finalize_stop_state(run_dir, outcome="not_running")
            return {"status": "not_running", "run_id": run_dir.name, "signal": target_signal.name}
        finalize_stop_state(run_dir, outcome="requested")
        return {"status": "requested", "run_id": run_dir.name, "signal": target_signal.name}

    if isinstance(pid, int) and pid > 0:
        target_signal = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.kill(int(pid), target_signal)
        except ProcessLookupError:
            finalize_stop_state(run_dir, outcome="not_running")
            return {"status": "not_running", "run_id": run_dir.name, "signal": target_signal.name}
        finalize_stop_state(run_dir, outcome="requested")
        return {"status": "requested", "run_id": run_dir.name, "signal": target_signal.name}

    finalize_stop_state(run_dir, outcome="not_running")
    return {"status": "not_running", "run_id": run_dir.name}
