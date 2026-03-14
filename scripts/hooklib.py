#!/usr/bin/env python3

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _system.engine.trusted_command import parse_trusted_argv


HOOK_VERSION = 1
HOOK_STATUSES = ("pending", "sent", "failed")
DEFAULT_STALE_SECONDS = 300
DEFAULT_HOOK_TIMEOUT_SECONDS = 30
DEFAULT_OPENCLAW_SYSTEM_EVENT_TIMEOUT_SECONDS = 10
DELIVERY_STATUS_BY_HOOK_STATE = {
    "pending": "pending_delivery",
    "sent": "delivered",
    "failed": "failed",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def trim_text(text: str, limit: int = 1200) -> str:
    compact = (text or "").strip()
    if not compact:
        return ""
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def duration_between(started_at: str | None, finished_at: str | None) -> float | None:
    start = parse_timestamp(started_at)
    finish = parse_timestamp(finished_at)
    if start is None or finish is None:
        return None
    return round((finish - start).total_seconds(), 1)


def project_root_from_hook_path(hook_path: Path) -> Path:
    resolved = hook_path.resolve()
    for ancestor in resolved.parents:
        if ancestor.name == "hooks" and ancestor.parent.name == "state":
            return ancestor.parent.parent
    raise ValueError(f"Could not resolve project root from hook path: {hook_path}")


def hook_root(project_root: Path) -> Path:
    return project_root / "state" / "hooks"


def ensure_hook_dirs(project_root: Path) -> dict[str, Path]:
    root = hook_root(project_root)
    directories = {status: root / status for status in HOOK_STATUSES}
    root.mkdir(parents=True, exist_ok=True)
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
    return directories


def hook_id_for_run(run_id: str, run_date: str) -> str:
    return f"{run_date}--{run_id}"


def hook_path_for(project_root: Path, status: str, hook_id: str) -> Path:
    if status not in HOOK_STATUSES:
        raise ValueError(f"Unsupported hook status: {status}")
    return ensure_hook_dirs(project_root)[status] / f"{hook_id}.json"


def locate_hook(project_root: Path, hook_id: str) -> Path | None:
    for status in HOOK_STATUSES:
        candidate = hook_path_for(project_root, status, hook_id)
        if candidate.is_file():
            return candidate
    return None


def locate_hook_for_run(project_root: Path, run_id: str, run_date: str | None = None, hook_id: str | None = None) -> Path | None:
    resolved_hook_id = (hook_id or "").strip()
    if resolved_hook_id:
        return locate_hook(project_root, resolved_hook_id)

    resolved_run_id = (run_id or "").strip()
    if not resolved_run_id:
        return None

    resolved_run_date = (run_date or "").strip()
    if resolved_run_date:
        return locate_hook(project_root, hook_id_for_run(resolved_run_id, resolved_run_date))

    for status in HOOK_STATUSES:
        matches = sorted((hook_root(project_root) / status).glob(f"*--{resolved_run_id}.json"))
        if matches:
            return matches[0]
    return None


def write_hook_payload(project_root: Path, payload: dict, status: str) -> Path:
    hook_id = payload.get("hook_id")
    if not hook_id:
        raise ValueError("Hook payload must include hook_id")

    target_path = hook_path_for(project_root, status, hook_id)
    payload.setdefault("delivery", {})
    payload["delivery"]["status"] = status

    write_json_atomic(target_path, payload)

    for current_status in HOOK_STATUSES:
        candidate = hook_path_for(project_root, current_status, hook_id)
        if candidate != target_path and candidate.exists():
            candidate.unlink()

    return target_path


def completion_delivery_required(*, result: dict | None = None, meta: dict | None = None) -> bool:
    result_status = str((result or {}).get("status") or "").strip().lower()
    meta_status = str((meta or {}).get("status") or "").strip().lower()
    return result_status in {"success", "failed"} or meta_status in {"completed", "failed"}


def build_delivery_snapshot(
    project_root: Path,
    *,
    run_id: str,
    run_date: str | None,
    result: dict | None = None,
    meta: dict | None = None,
    hook_path: Path | None = None,
    hook_payload: dict | None = None,
) -> dict:
    required = completion_delivery_required(result=result, meta=meta)
    resolved_hook_path = hook_path
    payload = dict(hook_payload or {})

    if resolved_hook_path is None:
        hook_hint = str((meta or {}).get("hook", {}).get("hook_id") or (result or {}).get("hook", {}).get("hook_id") or "").strip()
        resolved_hook_path = locate_hook_for_run(project_root, run_id, run_date, hook_hint)
    if resolved_hook_path is not None and not payload:
        payload = read_json(resolved_hook_path)

    hook_id = str(
        payload.get("hook_id")
        or (meta or {}).get("hook", {}).get("hook_id")
        or (result or {}).get("hook", {}).get("hook_id")
        or (hook_id_for_run(run_id, run_date) if run_id and run_date else "")
    ).strip()
    delivery = payload.get("delivery") or {}
    hook_state = resolved_hook_path.parent.name if resolved_hook_path is not None else ""
    if not hook_state:
        hook_state = str(delivery.get("status") or "").strip()
    delivered_at = delivery.get("sent_at")
    last_error = str(delivery.get("last_error") or "").strip()
    attempt_count = delivery.get("attempt_count")
    updated_at = delivery.get("last_attempt_at") or delivered_at or payload.get("created_at")
    try:
        normalized_attempt_count = max(0, int(attempt_count or 0))
    except (TypeError, ValueError):
        normalized_attempt_count = 0

    if not required:
        status = "not_ready"
    elif resolved_hook_path is None:
        status = "missing"
    else:
        status = DELIVERY_STATUS_BY_HOOK_STATE.get(hook_state, "missing")

    hook_path_rel = None
    if resolved_hook_path is not None:
        try:
            hook_path_rel = resolved_hook_path.relative_to(project_root).as_posix()
        except ValueError:
            hook_path_rel = str(resolved_hook_path)

    return {
        "required": required,
        "status": status,
        "hook_id": hook_id,
        "hook_written": resolved_hook_path is not None,
        "hook_status": hook_state or ("missing" if required else "not_ready"),
        "hook_path": hook_path_rel,
        "callback_available": required,
        "attempt_count": normalized_attempt_count if resolved_hook_path is not None else 0,
        "delivered_at": delivered_at,
        "last_error": last_error,
        "updated_at": updated_at,
    }


def build_hook_snapshot(project_root: Path, hook_path: Path, payload: dict | None = None) -> dict:
    resolved_payload = payload or read_json(hook_path)
    try:
        hook_path_rel = hook_path.relative_to(project_root).as_posix()
    except ValueError:
        hook_path_rel = str(hook_path)
    return {
        "hook_id": resolved_payload.get("hook_id") or hook_path.stem,
        "delivery_status": hook_path.parent.name,
        "path": hook_path_rel,
    }


def persist_run_delivery_state(project_root: Path, hook_path: Path, payload: dict | None = None) -> dict:
    resolved_payload = payload or read_json(hook_path)
    artifacts = resolved_payload.get("artifacts") or {}
    run_id = str(resolved_payload.get("run_id") or "").strip()
    run_date = str(resolved_payload.get("run_date") or "").strip() or None
    delivery_snapshot = build_delivery_snapshot(
        project_root,
        run_id=run_id,
        run_date=run_date,
        hook_path=hook_path,
        hook_payload=resolved_payload,
        result={"status": resolved_payload.get("run_status")},
        meta={"status": "completed" if resolved_payload.get("run_status") == "success" else "failed"},
    )
    hook_snapshot = build_hook_snapshot(project_root, hook_path, resolved_payload)

    for artifact_name in ("result_path", "meta_path"):
        relative_path = artifacts.get(artifact_name)
        if not isinstance(relative_path, str) or not relative_path.strip():
            continue
        artifact_path = (project_root / relative_path).resolve()
        if not artifact_path.is_file():
            continue
        try:
            artifact_payload = read_json(artifact_path)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(artifact_payload, dict):
            continue
        artifact_payload["hook"] = hook_snapshot
        artifact_payload["delivery"] = delivery_snapshot
        write_json_atomic(artifact_path, artifact_payload)

    return delivery_snapshot


def build_hook_payload(run_dir: Path, project_root: Path, job: dict, meta: dict, result: dict) -> dict:
    run_id = job.get("run_id") or meta.get("run_id") or result.get("run_id") or run_dir.name
    run_date = meta.get("run_date") or run_dir.parent.name
    hook_id = hook_id_for_run(run_id, run_date)
    event_type = "run.completed"
    idempotency_key = f"{run_id}-{event_type}-{hook_id}"
    run_rel_dir = run_dir.relative_to(project_root).as_posix()
    summary = result.get("summary", "")

    return {
        "hook_version": HOOK_VERSION,
        "hook_id": hook_id,
        "event": event_type,
        "event_type": event_type,
        "event_version": "1.0",
        "idempotency_key": idempotency_key,
        "delivery_attempts": 0,
        "max_delivery_attempts": 3,
        "project": job.get("project") or meta.get("project") or project_root.name,
        "run_id": run_id,
        "run_date": run_date,
        "task_id": meta.get("task_id") or job.get("task", {}).get("id", ""),
        "task_title": meta.get("task_title") or job.get("task", {}).get("title", ""),
        "preferred_agent": result.get("agent") or meta.get("preferred_agent") or job.get("preferred_agent", ""),
        "run_status": result.get("status", "failed"),
        "created_at": result.get("finished_at") or utc_now(),
        "summary": summary,
        "timestamps": {
            "run_created_at": result.get("created_at") or meta.get("created_at") or job.get("created_at"),
            "started_at": result.get("started_at") or meta.get("started_at"),
            "finished_at": result.get("finished_at") or meta.get("finished_at"),
        },
        "artifacts": {
            "run_dir": run_rel_dir,
            "job_path": f"{run_rel_dir}/job.json",
            "meta_path": f"{run_rel_dir}/meta.json",
            "result_path": f"{run_rel_dir}/result.json",
            "report_path": f"{run_rel_dir}/report.md",
            "stdout_path": f"{run_rel_dir}/stdout.log",
            "stderr_path": f"{run_rel_dir}/stderr.log",
            "stream_path": f"{run_rel_dir}/{job.get('artifacts', {}).get('stream_path', 'agent_stream.jsonl')}",
        },
        "delivery": {
            "status": "pending",
            "attempt_count": 0,
            "last_attempt_at": None,
            "sent_at": None,
            "last_error": "",
        },
        "delivery_attempt_log": [],
    }


def hook_command() -> str | None:
    command = os.environ.get("CLAW_HOOK_COMMAND", "").strip()
    return command or None


def hook_timeout_seconds_from_env() -> int:
    raw_value = os.environ.get("CLAW_HOOK_TIMEOUT_SECONDS", "").strip()
    if not raw_value:
        return DEFAULT_HOOK_TIMEOUT_SECONDS
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_HOOK_TIMEOUT_SECONDS


def openclaw_system_event_command() -> str | None:
    command = os.environ.get("CLAW_OPENCLAW_SYSTEM_EVENT_COMMAND", "").strip()
    return command or None


def openclaw_system_event_timeout_seconds_from_env() -> int:
    raw_value = os.environ.get("CLAW_OPENCLAW_SYSTEM_EVENT_TIMEOUT_SECONDS", "").strip()
    if not raw_value:
        return DEFAULT_OPENCLAW_SYSTEM_EVENT_TIMEOUT_SECONDS
    try:
        return max(1, int(raw_value))
    except ValueError:
        return DEFAULT_OPENCLAW_SYSTEM_EVENT_TIMEOUT_SECONDS


def build_callback_payload(payload: dict) -> dict:
    artifacts = payload.get("artifacts") or {}
    timestamps = payload.get("timestamps") or {}
    run_status = payload.get("run_status") or "unknown"
    summary_text = trim_text(payload.get("summary", ""), 800)
    report_path = artifacts.get("report_path", "")
    run_id = payload.get("run_id", "")
    task_id = payload.get("task_id", "")
    project = payload.get("project", "")
    agent = payload.get("preferred_agent", "")
    task_title = payload.get("task_title", "")
    duration_seconds = duration_between(timestamps.get("started_at"), timestamps.get("finished_at"))

    segments = [
        project or "unknown-project",
        task_id or run_id or "unknown-run",
        run_status,
    ]
    chat_text = " | ".join(segments)
    if agent:
        chat_text += f" | agent={agent}"
    if task_title:
        chat_text += f" | {task_title}"
    if summary_text:
        chat_text += f" | {summary_text}"
    if report_path:
        chat_text += f" | report={report_path}"

    return {
        "event": payload.get("event") or payload.get("event_type") or "run.completed",
        "signal": "completion",
        "hook_id": payload.get("hook_id", ""),
        "idempotency_key": payload.get("idempotency_key", ""),
        "project": project,
        "run_id": run_id,
        "task_id": task_id,
        "task_title": task_title,
        "status": run_status,
        "agent": agent,
        "summary": summary_text,
        "report_path": report_path,
        "duration_seconds": duration_seconds,
        "created_at": payload.get("created_at"),
        "finished_at": timestamps.get("finished_at"),
        "chat_text": chat_text,
    }


def build_openclaw_wake_text(project_root: Path, payload: dict) -> str:
    claw_path = (REPO_ROOT / "scripts" / "claw.py").resolve()
    run_id = payload.get("run_id", "")
    hook_id = payload.get("hook_id", "")
    project_name = payload.get("project") or project_root.name
    event_name = payload.get("event") or payload.get("event_type") or "run.completed"
    return (
        f"Claw hook ready for {project_name}: {event_name} "
        f"(run={run_id or 'unknown'}, hook={hook_id or 'unknown'}). "
        f"Run: python3 {claw_path} openclaw wake {project_root} --mode event"
    )


def emit_openclaw_system_event(project_root: Path, payload: dict) -> None:
    raw_command = openclaw_system_event_command()
    if not raw_command:
        return

    timeout_seconds = openclaw_system_event_timeout_seconds_from_env()
    argv = parse_trusted_argv(raw_command, env_name="CLAW_OPENCLAW_SYSTEM_EVENT_COMMAND")
    if argv is None:
        return
    argv = [*argv, "--text", build_openclaw_wake_text(project_root, payload), "--mode", "now"]
    subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )


def normalize_process_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def dispatch_hook_file(hook_path: Path) -> dict:
    payload = read_json(hook_path)
    project_root = project_root_from_hook_path(hook_path)
    command = hook_command()
    timeout_seconds = hook_timeout_seconds_from_env()
    current_status = hook_path.parent.name
    hook_id = payload.get("hook_id") or hook_path.stem
    now = utc_now()

    payload["hook_id"] = hook_id
    payload.setdefault("delivery_attempt_log", [])
    # Migrate legacy list-style delivery_attempts to delivery_attempt_log
    if isinstance(payload.get("delivery_attempts"), list):
        payload["delivery_attempt_log"] = payload.pop("delivery_attempts")
        payload["delivery_attempts"] = 0
    payload.setdefault("delivery_attempts", 0)
    payload.setdefault("max_delivery_attempts", 3)
    delivery = payload.setdefault(
        "delivery",
        {
            "status": current_status,
            "attempt_count": 0,
            "last_attempt_at": None,
            "sent_at": None,
            "last_error": "",
        },
    )

    if not command:
        delivery["status"] = current_status
        delivery["last_error"] = ""
        target_path = write_hook_payload(project_root, payload, current_status)
        persist_run_delivery_state(project_root, target_path, payload)
        try:
            emit_openclaw_system_event(project_root, payload)
        except (ValueError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
            print(f"Warning: failed to emit OpenClaw system event for hook {hook_id}: {exc}", file=sys.stderr)
        return {
            "hook_id": hook_id,
            "status": current_status,
            "path": target_path,
            "outcome": "skipped",
            "exit_code": None,
        }

    attempt_count = int(delivery.get("attempt_count", 0)) + 1
    exit_code = None
    stdout_text = ""
    stderr_text = ""
    try:
        argv = parse_trusted_argv(command, env_name="CLAW_HOOK_COMMAND")
        completed = subprocess.run(
            argv,
            input=json.dumps(payload, indent=2) + "\n",
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        exit_code = completed.returncode
        stdout_text = normalize_process_output(completed.stdout)
        stderr_text = normalize_process_output(completed.stderr)
    except ValueError as exc:
        exit_code = 126
        stderr_text = str(exc)
    except FileNotFoundError as exc:
        exit_code = 127
        stderr_text = f"Command not found: {exc.filename}"
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout_text = normalize_process_output(exc.stdout)
        stderr_text = normalize_process_output(exc.stderr).strip()
        timeout_message = f"Timed out after {timeout_seconds} seconds"
        stderr_text = f"{stderr_text}\n{timeout_message}".strip() if stderr_text else timeout_message

    attempt = {
        "attempt": attempt_count,
        "attempted_at": now,
        "command": command,
        "timeout_seconds": timeout_seconds,
        "exit_code": exit_code,
        "stdout": trim_text(stdout_text),
        "stderr": trim_text(stderr_text),
    }

    delivery["attempt_count"] = attempt_count
    delivery["last_attempt_at"] = now

    if exit_code == 0:
        delivery["status"] = "sent"
        delivery["sent_at"] = now
        delivery["last_error"] = ""
        attempt["outcome"] = "sent"
        payload["delivery_attempts"] = int(payload.get("delivery_attempts", 0)) + 1
        payload["delivery_attempt_log"].append(attempt)
        target_path = write_hook_payload(project_root, payload, "sent")
        persist_run_delivery_state(project_root, target_path, payload)
        return {
            "hook_id": hook_id,
            "status": "sent",
            "path": target_path,
            "outcome": "sent",
            "exit_code": 0,
        }

    delivery["status"] = "failed"
    delivery["sent_at"] = None
    delivery["last_error"] = trim_text(
        stderr_text or stdout_text or f"Hook command exited with status {exit_code}",
        400,
    )
    attempt["outcome"] = "failed"
    payload["delivery_attempts"] = int(payload.get("delivery_attempts", 0)) + 1
    payload["delivery_attempt_log"].append(attempt)
    max_attempts = int(payload.get("max_delivery_attempts", 3))
    if int(payload["delivery_attempts"]) >= max_attempts:
        payload["dead_letter"] = True
    target_path = write_hook_payload(project_root, payload, "failed")
    persist_run_delivery_state(project_root, target_path, payload)
    return {
        "hook_id": hook_id,
        "status": "failed",
        "path": target_path,
        "outcome": "failed",
        "exit_code": exit_code,
    }


def deliver_hook_via_callback_bridge(hook_path: Path) -> dict:
    payload = read_json(hook_path)
    project_root = project_root_from_hook_path(hook_path)
    current_status = hook_path.parent.name
    hook_id = payload.get("hook_id") or hook_path.stem
    now = utc_now()

    payload["hook_id"] = hook_id
    payload.setdefault("delivery_attempt_log", [])
    if isinstance(payload.get("delivery_attempts"), list):
        payload["delivery_attempt_log"] = payload.pop("delivery_attempts")
        payload["delivery_attempts"] = 0
    payload.setdefault("delivery_attempts", 0)
    payload.setdefault("max_delivery_attempts", 3)
    delivery = payload.setdefault(
        "delivery",
        {
            "status": current_status,
            "attempt_count": 0,
            "last_attempt_at": None,
            "sent_at": None,
            "last_error": "",
        },
    )

    callback_payload = build_callback_payload(payload)
    attempt_count = int(delivery.get("attempt_count", 0)) + 1
    attempt = {
        "attempt": attempt_count,
        "attempted_at": now,
        "command": "openclaw-callback-bridge",
        "timeout_seconds": 0,
        "exit_code": 0,
        "stdout": trim_text(json.dumps(callback_payload, ensure_ascii=False)),
        "stderr": "",
        "outcome": "sent",
    }

    delivery["attempt_count"] = attempt_count
    delivery["last_attempt_at"] = now
    delivery["status"] = "sent"
    delivery["sent_at"] = now
    delivery["last_error"] = ""
    payload["delivery_attempts"] = int(payload.get("delivery_attempts", 0)) + 1
    payload["delivery_attempt_log"].append(attempt)
    payload.pop("dead_letter", None)
    target_path = write_hook_payload(project_root, payload, "sent")
    persist_run_delivery_state(project_root, target_path, payload)
    return {
        "hook_id": hook_id,
        "status": "sent",
        "path": target_path,
        "outcome": "sent",
        "exit_code": 0,
        "callback": callback_payload,
    }


def stale_seconds_from_env() -> int:
    raw_value = os.environ.get("CLAW_HOOK_STALE_SECONDS", "").strip()
    if not raw_value:
        return DEFAULT_STALE_SECONDS
    try:
        return max(0, int(raw_value))
    except ValueError:
        return DEFAULT_STALE_SECONDS


def is_stale_pending_hook(hook_path: Path, stale_after_seconds: int) -> bool:
    payload = read_json(hook_path)
    reference = parse_timestamp(payload.get("delivery", {}).get("last_attempt_at")) or parse_timestamp(payload.get("created_at"))
    if reference is None:
        return True
    age_seconds = (datetime.now(timezone.utc) - reference).total_seconds()
    return age_seconds >= stale_after_seconds


def iter_hook_files(project_root: Path, status: str) -> list[Path]:
    if status not in HOOK_STATUSES:
        raise ValueError(f"Unsupported hook status: {status}")
    return sorted((hook_root(project_root) / status).glob("*.json"))


def resolve_project_roots(repo_root: Path, target: str | None) -> list[Path]:
    if target:
        candidate = Path(target).expanduser().resolve()
        if not candidate.is_dir():
            raise FileNotFoundError(f"Project path not found: {candidate}")
        return [candidate]

    projects_root = repo_root / "projects"
    if not projects_root.is_dir():
        return []

    project_roots = []
    for child in sorted(projects_root.iterdir()):
        if child.is_dir() and not child.name.startswith("_"):
            project_roots.append(child.resolve())
    return project_roots
