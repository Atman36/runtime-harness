#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import yaml

def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


REPO_ROOT = repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _system.engine import FileExchangeError, FileQueue, OperatorSessionStore, OrgGraphError, QueueEmpty, SessionDocsStore, SessionStore, TaskClaimStore, TransportConfigError, VALID_WAKE_REASONS, WakeQueue, bind_operator_context, build_agent_command, claims_root_for_project, delegation_targets, describe_transport_backends, enqueue_run, escalation_chain, execute_run_task, fetch_path, find_run_dir, load_file_exchange_policy, load_org_graph, operator_sessions_root_for_repo, plan_task_run, plan_to_dict, put_file, queue_root_for_project, read_json, resolve_project_root, run_command, run_transport_doctor, session_docs_root_for_project, sessions_root_for_project, validate_delegation, wake_root_for_project  # noqa: E402
from _system.engine.budget_guardrails import evaluate_guardrails, extract_referenced_paths, summarize_project_guardrails  # noqa: E402
from _system.engine.decision_log import append_decision, format_decision_for_display, read_decisions  # noqa: E402
from _system.engine.event_log import append_run_event, build_run_event_snapshot, load_run_events  # noqa: E402
from _system.engine.error_codes import build_error_envelope  # noqa: E402
from _system.engine.guardrails import run_guardrails  # noqa: E402
from _system.engine.listener_dispatch import dispatch_event_listeners  # noqa: E402
from _system.engine.workflow_contract import contract_summary, load_workflow_contract  # noqa: E402
from _system.engine.trusted_command import command_display, parse_trusted_argv  # noqa: E402
from _system.engine.decomposer import decompose_epic as _decompose_epic  # noqa: E402
from generate_review_batch import POLICY_PATH, classify_run, generate_batches, load_policy, load_run, resolve_cadence_batch_size  # noqa: E402
from hooklib import (
    build_callback_payload,
    build_delivery_snapshot,
    deliver_hook_via_callback_bridge,
    dispatch_hook_file,
    has_pending_approval_checkpoint,
    hook_command,
    iter_hook_files,
    trim_text,
    utc_now,
)  # noqa: E402


CADENCE_STATE_FILE = "review_cadence.json"
METRICS_SNAPSHOT_FILE = "metrics_snapshot.json"
ORCHESTRATION_STATE_FILE = "orchestration_state.json"
LISTENER_LOG_FILE = "listener_log.jsonl"
GUARDRAIL_SNAPSHOT_FILE = "guardrail_snapshot.json"
GUARDRAIL_STATE_DIR = "guardrails"
FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
TASK_ID_RE = re.compile(r"^TASK-(\d+)$")
DEFAULT_WORKER_LEASE_SECONDS = 600
DEFAULT_RETRY_BACKOFF_BASE_SECONDS = 30
DEFAULT_RETRY_BACKOFF_MAX_SECONDS = 300
DEFAULT_ORCHESTRATE_FAILURE_BUDGET = 3
TASK_DONE_STATUSES = {"done", "completed", "accepted"}
TASK_ACTIVE_STATUSES = {"in_progress", "running", "queued", "awaiting_review", "awaiting_approval"}
TASK_BLOCKED_STATUSES = {"blocked", "cancelled"}
PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def utc_after_seconds(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=max(1, int(seconds)))).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def duration_between(started_at: str | None, finished_at: str | None) -> float | None:
    start = parse_iso_timestamp(started_at)
    finish = parse_iso_timestamp(finished_at)
    if start is None or finish is None:
        return None
    return round((finish - start).total_seconds(), 1)

def listener_log_path(project_root: Path) -> Path:
    return project_root / "state" / LISTENER_LOG_FILE


def dispatch_registered_listeners(
    project_root: Path,
    event_type: str,
    *,
    run_id: str,
    status: str | None = None,
    task_id: str | None = None,
    ts: str | None = None,
) -> None:
    dispatch_event_listeners(
        REPO_ROOT / "_system" / "registry" / "listeners.yaml",
        event_type,
        {
            "run_id": run_id,
            "project_root": str(project_root),
            "status": status or "",
            "task_id": task_id or "",
            "ts": ts or utc_now(),
        },
        listener_log_path(project_root),
        cwd=REPO_ROOT,
    )


def emit_review_created_events(project_root: Path, batches: list[dict]) -> None:
    for batch in batches:
        runs = batch.get("runs") if isinstance(batch.get("runs"), list) else []
        for run in runs:
            run_id = str(run.get("run_id") or "").strip()
            if not run_id:
                continue
            run_path = run.get("run_path")
            run_dir: Path | None = None
            if isinstance(run_path, str) and run_path.strip():
                candidate = (project_root / run_path).resolve()
                if candidate.is_dir():
                    run_dir = candidate
            if run_dir is None:
                run_dir = find_run_dir(project_root, run_id)
            if run_dir is None:
                continue
            event = append_run_event(
                run_dir,
                "review_created",
                project_root=project_root,
                payload={
                    "batch_id": batch.get("batch_id"),
                    "reviewer": batch.get("reviewer"),
                    "trigger_type": batch.get("trigger_type"),
                    "run_count": len(runs),
                },
            )
            dispatch_registered_listeners(
                project_root,
                "review_created",
                run_id=run_id,
                status="created",
                task_id=str(run.get("task_id") or ""),
                ts=event.get("recorded_at"),
            )


def load_stream_tail(run_dir: Path, limit: int = 10) -> list[dict]:
    stream_path = run_dir / "agent_stream.jsonl"
    job_path = run_dir / "job.json"

    if job_path.is_file():
        try:
            job = read_json(job_path)
            artifacts = job.get("artifacts") or {}
            relative_path = artifacts.get("stream_path")
            if isinstance(relative_path, str) and relative_path.strip():
                stream_path = run_dir / relative_path
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    if not stream_path.is_file():
        return []

    tail: deque[dict] = deque(maxlen=max(1, limit))
    try:
        with stream_path.open(encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(record, dict):
                    tail.append(record)
    except OSError:
        return []

    return list(tail)


def load_cadence_state(project_root: Path) -> dict:
    path = project_root / "state" / CADENCE_STATE_FILE
    default_state = {
        "successful_since_last_batch": 0,
        "last_batch_generated_at": None,
    }
    try:
        payload = read_json(path)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(default_state)

    if not isinstance(payload, dict):
        return dict(default_state)

    try:
        success_count = int(payload.get("successful_since_last_batch", 0))
    except (TypeError, ValueError):
        success_count = 0

    last_batch_generated_at = payload.get("last_batch_generated_at")
    if not isinstance(last_batch_generated_at, str):
        last_batch_generated_at = None

    return {
        "successful_since_last_batch": max(0, success_count),
        "last_batch_generated_at": last_batch_generated_at,
    }


def save_cadence_state(project_root: Path, state: dict) -> None:
    path = project_root / "state" / CADENCE_STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    payload = {
        "successful_since_last_batch": max(0, int(state.get("successful_since_last_batch", 0))),
        "last_batch_generated_at": state.get("last_batch_generated_at"),
    }

    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


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


def append_routing_decision(project_root: Path, run_dir: Path, *, source: str) -> None:
    try:
        job = read_json(run_dir / "job.json")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return

    routing = job.get("routing") if isinstance(job.get("routing"), dict) else {}
    execution = job.get("execution") if isinstance(job.get("execution"), dict) else {}
    task = job.get("task") if isinstance(job.get("task"), dict) else {}

    append_decision(
        project_root,
        "routing",
        run_id=run_dir.name,
        task_id=str(task.get("id") or ""),
        reason_code=str(routing.get("selection_source") or "unknown"),
        details={
            "source": source,
            "selected_agent": routing.get("selected_agent") or job.get("preferred_agent"),
            "selection_source": routing.get("selection_source"),
            "routing_rule": routing.get("routing_rule"),
            "workspace_mode": execution.get("workspace_mode"),
        },
        outcome="dispatched",
    )


def append_approval_requested_decision(project_root: Path, approval: dict) -> None:
    append_decision(
        project_root,
        "approval_requested",
        run_id=str(approval.get("run_id") or ""),
        task_id=str(approval.get("task_id") or ""),
        reason_code=str(approval.get("reason") or ""),
        details={
            "source": approval.get("source"),
            "requested_action": approval.get("requested_action"),
            "approval_id": approval.get("approval_id"),
        },
        outcome="waiting",
    )


def load_orchestration_state(project_root: Path) -> dict:
    path = project_root / "state" / ORCHESTRATION_STATE_FILE
    default_state = {
        "consecutive_failures": 0,
        "last_run_id": None,
        "last_decision": None,
        "last_updated_at": None,
    }
    try:
        payload = read_json(path)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return dict(default_state)
    if not isinstance(payload, dict):
        return dict(default_state)
    try:
        consecutive_failures = max(0, int(payload.get("consecutive_failures", 0)))
    except (TypeError, ValueError):
        consecutive_failures = 0
    return {
        "consecutive_failures": consecutive_failures,
        "last_run_id": payload.get("last_run_id"),
        "last_decision": payload.get("last_decision"),
        "last_updated_at": payload.get("last_updated_at"),
    }


def save_orchestration_state(project_root: Path, state: dict) -> None:
    write_json_atomic(
        project_root / "state" / ORCHESTRATION_STATE_FILE,
        {
            "consecutive_failures": max(0, int(state.get("consecutive_failures", 0))),
            "last_run_id": state.get("last_run_id"),
            "last_decision": state.get("last_decision"),
            "last_updated_at": state.get("last_updated_at"),
        },
    )


def hook_counts(project_root: Path) -> dict[str, int]:
    hooks_root = project_root / "state" / "hooks"
    counts: dict[str, int] = {}
    for status in ("pending", "sent", "failed"):
        status_dir = hooks_root / status
        counts[status] = len(list(status_dir.glob("*.json"))) if status_dir.is_dir() else 0
    return counts


def queue_counts(project_root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    queue_root = queue_root_for_project(project_root)
    for state in ("pending", "running", "awaiting_approval", "done", "failed", "dead_letter"):
        state_dir = queue_root / state
        counts[state] = len(list(state_dir.glob("*.json"))) if state_dir.is_dir() else 0
    return counts


def wake_snapshot(project_root: Path, *, limit: int = 5) -> dict:
    queue = WakeQueue(wake_root_for_project(project_root))
    snapshot = queue.snapshot(limit=limit)
    sessions = load_session_summary_map(project_root)
    for item in snapshot.get("pending", []):
        scope_key = str(item.get("scope_key") or "").strip()
        session = sessions.get(scope_key)
        if session:
            item["session"] = session
    return snapshot


def default_heartbeat_interval(lease_seconds: int) -> float:
    return max(1.0, min(float(max(1, int(lease_seconds))) / 3.0, 60.0))


def compute_retry_backoff(attempt_count: int, *, base_seconds: int, max_seconds: int) -> int:
    exponent = max(0, int(attempt_count) - 1)
    return min(max(1, int(max_seconds)), max(1, int(base_seconds)) * (2 ** exponent))


def summarize_worker_error(completed: subprocess.CompletedProcess[str], heartbeat_warnings: list[str]) -> str:
    parts: list[str] = []
    stderr_text = (completed.stderr or "").strip()
    if stderr_text:
        parts.append(trim_text(stderr_text, limit=4000))
    if completed.returncode != 0 and not parts:
        parts.append(f"execute_job.py exited with code {completed.returncode}")
    if heartbeat_warnings:
        parts.extend(heartbeat_warnings)
    return "\n".join(part for part in parts if part)


def run_job_with_lease_heartbeat(
    queue: FileQueue,
    claimed,
    run_dir: Path,
    *,
    lease_seconds: int,
    heartbeat_interval_seconds: float,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    command = ["python3", str(REPO_ROOT / "scripts" / "execute_job.py"), str(run_dir)]
    process = subprocess.Popen(
        command,
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stop_event = threading.Event()
    heartbeat_warnings: list[str] = []

    def heartbeat_loop() -> None:
        while not stop_event.wait(heartbeat_interval_seconds):
            try:
                renewed = queue.renew_lease(claimed, lease_seconds=lease_seconds)
            except Exception as exc:  # pragma: no cover - defensive logging path
                message = f"Lease heartbeat error for {claimed.job_id}: {exc}"
                heartbeat_warnings.append(message)
                print(message, file=sys.stderr)
                continue
            if renewed:
                continue
            message = f"Lease heartbeat stopped for {claimed.job_id}: lease could not be renewed"
            heartbeat_warnings.append(message)
            print(message, file=sys.stderr)
            return

    heartbeat_thread = threading.Thread(target=heartbeat_loop, name=f"lease-heartbeat-{claimed.job_id}", daemon=True)
    heartbeat_thread.start()
    try:
        stdout, stderr = process.communicate()
    finally:
        stop_event.set()
        heartbeat_thread.join(timeout=max(heartbeat_interval_seconds, 1.0))

    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr), heartbeat_warnings


def summarize_hook_outcomes(outcomes: list[dict]) -> dict[str, int]:
    summary = {
        "attempted": len(outcomes),
        "sent": 0,
        "failed": 0,
        "skipped": 0,
    }
    for outcome in outcomes:
        label = outcome.get("outcome")
        if label in ("sent", "failed", "skipped"):
            summary[label] += 1
    return summary


def iter_projects_root() -> list[Path]:
    projects_root = REPO_ROOT / "projects"
    if not projects_root.is_dir():
        raise FileNotFoundError(f"Projects directory not found: {projects_root}")
    return sorted(path for path in projects_root.iterdir() if path.is_dir() and not path.name.startswith("_"))


def read_front_matter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    match = FRONT_MATTER_RE.match(text)
    if match is None:
        return {}, text
    loaded = yaml.safe_load(match.group(1)) or {}
    front_matter = dict(loaded) if isinstance(loaded, dict) else {}
    body = text[match.end():]
    return front_matter, body


def write_front_matter(path: Path, front_matter: dict, body: str) -> None:
    rendered = "---\n" + yaml.safe_dump(front_matter, sort_keys=False, allow_unicode=False).strip() + "\n---\n"
    if body and not body.startswith("\n"):
        rendered += "\n"
    rendered += body
    path.write_text(rendered, encoding="utf-8")


def update_task_status(task_path: Path, status: str) -> None:
    front_matter, body = read_front_matter(task_path)
    front_matter["status"] = status
    write_front_matter(task_path, front_matter, body)


def resolve_project_root_or_slug(argument: str) -> Path:
    project_slug_root = REPO_ROOT / "projects" / argument
    if project_slug_root.is_dir() and (project_slug_root / "state" / "project.yaml").is_file():
        return project_slug_root.resolve()
    return resolve_project_root(argument)


def next_task_id(project_root: Path) -> str:
    tasks_root = project_root / "tasks"
    max_index = 0
    for task_path in tasks_root.glob("TASK-*.md"):
        match = TASK_ID_RE.match(task_path.stem)
        if match is None:
            continue
        max_index = max(max_index, int(match.group(1)))
    return f"TASK-{max_index + 1:03d}"


def resolve_follow_up_spec_reference(project_root: Path, meta: dict) -> str | None:
    task_rel_path = str(meta.get("task_path") or "").strip()
    if task_rel_path:
        source_task = (project_root / task_rel_path).resolve()
        if source_task.is_file():
            front_matter, _body = read_front_matter(source_task)
            spec_reference = str(front_matter.get("spec") or "").strip()
            if spec_reference:
                return spec_reference

    spec_rel_path = str(meta.get("spec_path") or "").strip()
    if spec_rel_path:
        tasks_root = project_root / "tasks"
        return os.path.relpath(project_root / spec_rel_path, tasks_root)
    return None


def find_follow_up_task(project_root: Path, *, review_id: str, action_id: str) -> Path | None:
    tasks_root = project_root / "tasks"
    for task_path in sorted(tasks_root.glob("TASK-*.md")):
        front_matter, _body = read_front_matter(task_path)
        if str(front_matter.get("source_review_id") or "").strip() != review_id:
            continue
        if str(front_matter.get("follow_up_action_id") or "").strip() != action_id:
            continue
        return task_path
    return None


def create_follow_up_task(
    project_root: Path,
    *,
    meta: dict,
    decision: dict,
    action: dict,
) -> Path:
    task_id = next_task_id(project_root)
    description = str(action.get("description") or "").strip()
    source_task_id = str(meta.get("task_id") or "").strip()
    assigned_agent = str(action.get("assigned_agent") or meta.get("preferred_agent") or "auto").strip() or "auto"
    task_path = project_root / "tasks" / f"{task_id}.md"
    title = f"Follow-up: {description}"
    front_matter = {
        "id": task_id,
        "title": title,
        "status": "todo",
        "spec": resolve_follow_up_spec_reference(project_root, meta),
        "preferred_agent": assigned_agent,
        "review_policy": str(meta.get("review_policy") or "standard").strip() or "standard",
        "priority": str(meta.get("priority") or "medium").strip() or "medium",
        "project": project_root.name,
        "needs_review": False,
        "risk_flags": [],
        "dependencies": [source_task_id] if source_task_id else [],
        "tags": ["follow_up"],
        "source_run_id": str(meta.get("run_id") or "").strip(),
        "source_review_id": str(decision.get("review_id") or "").strip(),
        "follow_up_action_id": str(action.get("action_id") or "").strip(),
    }
    body = "\n".join(
        [
            "# Task",
            "",
            "## Goal",
            description,
            "",
            "## Context",
            f"- Generated from review decision `{decision.get('review_id', '')}` for run `{meta.get('run_id', '')}`.",
            f"- Source task: `{source_task_id or 'unknown'}`.",
            f"- Follow-up action: `{action.get('action_id', '')}`.",
            "",
            "## Notes",
            "- Auto-generated by `claw orchestrate` from reviewer follow-up actions.",
            "- Keep scope limited to the reviewer-requested delta.",
            "",
        ]
    )
    write_front_matter(task_path, front_matter, body)
    return task_path


def create_delegated_task(
    project_root: Path,
    *,
    parent_task_path: Path,
    delegated_by: str,
    delegated_to: str,
    reason: str,
    note: str | None = None,
    title: str | None = None,
    spec_ref: str | None = None,
    priority: str | None = None,
    review_policy: str | None = None,
    needs_review: bool | None = None,
    tags: list[str] | None = None,
    delegation_type: str = "delegation",
    extra_front_matter: dict | None = None,
) -> Path:
    task_id = next_task_id(project_root)
    front_matter_parent, _body_parent = read_front_matter(parent_task_path)
    parent_task_id = str(front_matter_parent.get("id") or parent_task_path.stem).strip()
    parent_title = str(front_matter_parent.get("title") or "").strip()
    parent_spec = str(front_matter_parent.get("spec") or "../specs/SPEC-001.md").strip()
    parent_review_policy = str(front_matter_parent.get("review_policy") or "standard").strip() or "standard"
    parent_priority = str(front_matter_parent.get("priority") or "medium").strip() or "medium"
    parent_needs_review = bool(front_matter_parent.get("needs_review", False))

    task_path = project_root / "tasks" / f"{task_id}.md"
    resolved_title = title or f"{delegation_type.title()}: {parent_title or parent_task_id}"
    resolved_spec = spec_ref or parent_spec
    resolved_priority = priority or parent_priority
    resolved_review_policy = review_policy or parent_review_policy
    resolved_needs_review = parent_needs_review if needs_review is None else bool(needs_review)
    resolved_tags = tags if tags is not None else [delegation_type]

    front_matter = {
        "id": task_id,
        "title": resolved_title,
        "status": "todo",
        "spec": resolved_spec,
        "preferred_agent": delegated_to,
        "review_policy": resolved_review_policy,
        "priority": resolved_priority,
        "project": project_root.name,
        "needs_review": resolved_needs_review,
        "risk_flags": [],
        "dependencies": [],
        "tags": resolved_tags,
        "parent_task_id": parent_task_id,
        "parent_task_path": parent_task_path.relative_to(project_root).as_posix(),
        "delegated_by": delegated_by,
        "delegated_to": delegated_to,
        "delegation_reason": reason,
        "delegation_note": note,
        "delegation_type": delegation_type,
        "delegation_created_at": utc_now(),
    }
    if extra_front_matter:
        front_matter.update(extra_front_matter)

    action_label = "task-delegate" if delegation_type == "delegation" else "task-escalate" if delegation_type == "escalation" else f"task-{delegation_type}"
    body = "\n".join(
        [
            "# Task",
            "",
            "## Goal",
            resolved_title,
            "",
            "## Context",
            f"- Parent task: `{parent_task_id}` ({parent_title or 'untitled'}).",
            f"- Delegated by: `{delegated_by}`.",
            f"- Delegated to: `{delegated_to}`.",
            f"- Reason: {reason}.",
            "",
            "## Notes",
            f"- Auto-generated by `claw {action_label}`.",
            "- Update parent linkage or dependencies as needed once delegated work is scoped.",
            "",
        ]
    )
    write_front_matter(task_path, front_matter, body)
    return task_path


def approvals_root(project_root: Path) -> Path:
    return project_root / "state" / "approvals"


def ensure_approval_dirs(project_root: Path) -> dict[str, Path]:
    root = approvals_root(project_root)
    directories = {
        "pending": root / "pending",
        "resolved": root / "resolved",
    }
    root.mkdir(parents=True, exist_ok=True)
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
    return directories


def approval_counts(project_root: Path) -> dict[str, int]:
    directories = ensure_approval_dirs(project_root)
    return {name: len(list(path.glob("*.json"))) for name, path in directories.items()}


def drop_queue_job(project_root: Path, job_id: str) -> bool:
    queue = FileQueue(queue_root_for_project(project_root))
    path = queue.find_job(job_id)
    if path is None:
        return False
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


def load_approval_requests(project_root: Path, *, state: str = "pending") -> list[dict]:
    directories = ensure_approval_dirs(project_root)
    target_dir = directories[state]
    requests: list[dict] = []
    for path in sorted(target_dir.glob("*.json")):
        payload, error = load_json_status(path)
        if error is not None:
            continue
        payload["approval_file"] = path.relative_to(project_root).as_posix()
        requests.append(payload)
    return requests


def create_approval_request(
    project_root: Path,
    *,
    run_id: str,
    task_id: str,
    task_path: str,
    source: str,
    reason: str,
    requested_action: str,
) -> dict:
    directories = ensure_approval_dirs(project_root)
    for payload in load_approval_requests(project_root, state="pending"):
        if (
            payload.get("run_id") == run_id
            and payload.get("source") == source
            and payload.get("reason") == reason
            and payload.get("requested_action") == requested_action
        ):
            return payload

    approval_id = f"APPROVAL-{uuid4().hex[:10]}"
    payload = {
        "approval_id": approval_id,
        "project": project_root.name,
        "run_id": run_id,
        "task_id": task_id,
        "task_path": task_path,
        "source": source,
        "reason": reason,
        "requested_action": requested_action,
        "status": "pending",
        "created_at": utc_now(),
        "resolved_at": None,
        "decision": None,
        "notes": "",
    }
    write_json_atomic(directories["pending"] / f"{approval_id}.json", payload)
    return payload


def resolve_approval_request(project_root: Path, approval_id: str, *, decision: str, notes: str) -> dict | None:
    directories = ensure_approval_dirs(project_root)
    pending_path = directories["pending"] / f"{approval_id}.json"
    if not pending_path.is_file():
        return None

    payload, error = load_json_status(pending_path)
    if error is not None:
        return None

    payload["status"] = "resolved"
    payload["decision"] = decision
    payload["notes"] = notes
    payload["resolved_at"] = utc_now()

    task_rel_path = payload.get("task_path")
    if isinstance(task_rel_path, str) and task_rel_path:
        task_path = (project_root / task_rel_path).resolve()
        if task_path.is_file():
            if decision == "approved" and payload.get("requested_action") == "retry":
                drop_queue_job(project_root, str(payload.get("run_id") or ""))
                update_task_status(task_path, "todo")
            elif decision == "approved" and payload.get("requested_action") == "accept":
                update_task_status(task_path, "done")
            elif payload.get("requested_action") == "run":
                if decision == "approved":
                    queue = FileQueue(queue_root_for_project(project_root))
                    run_id = str(payload.get("run_id") or "")
                    if not queue.approve(run_id):
                        run_dir = find_run_dir(project_root, run_id)
                        if run_dir is not None:
                            enqueue_run(run_dir, state="pending")
                    update_task_status(task_path, "queued")
                elif decision == "rejected":
                    update_task_status(task_path, "blocked")

    target_path = directories["resolved"] / pending_path.name
    write_json_atomic(target_path, payload)
    pending_path.unlink()
    return payload


def guardrails_root(project_root: Path) -> Path:
    return project_root / "state" / GUARDRAIL_STATE_DIR


def guardrail_state_path(project_root: Path) -> Path:
    return guardrails_root(project_root) / "budget_snapshot.json"


def run_guardrail_path(run_dir: Path) -> Path:
    return run_dir / GUARDRAIL_SNAPSHOT_FILE


def load_guardrail_snapshot(run_dir: Path) -> dict | None:
    payload, error = load_json_status(run_guardrail_path(run_dir))
    if error is not None:
        return None
    return payload


def collect_guardrail_snapshots(project_root: Path) -> list[dict]:
    snapshots: list[dict] = []
    runs_root = project_root / "runs"
    if not runs_root.is_dir():
        return snapshots
    for date_dir in sorted(runs_root.iterdir()):
        if not date_dir.is_dir():
            continue
        for run_dir in sorted(date_dir.iterdir()):
            if not run_dir.is_dir() or not run_dir.name.startswith("RUN-"):
                continue
            snapshot = load_guardrail_snapshot(run_dir)
            if snapshot is not None:
                snapshots.append(snapshot)
    return snapshots


def load_guardrail_project_snapshot(project_root: Path) -> dict:
    payload, error = load_json_status(guardrail_state_path(project_root))
    if error is not None:
        return {
            "snapshot_version": 1,
            "budget": {
                "enabled": False,
                "warning_limit": 0,
                "hard_limit": 0,
                "consumed_units": 0,
                "warning_runs": 0,
                "soft_limit_reached": False,
                "hard_limit_reached": False,
                "policy": {},
            },
            "pending_runs": 0,
            "last_run_id": None,
            "governance": {"policy": {}},
            "updated_at": None,
        }
    return payload


def refresh_guardrail_snapshot(project_root: Path) -> dict:
    contract = load_workflow_contract(project_root)
    snapshots = collect_guardrail_snapshots(project_root)
    summary = summarize_project_guardrails(contract.guardrails, snapshots)
    summary["updated_at"] = utc_now()
    write_json_atomic(guardrail_state_path(project_root), summary)
    return summary


def load_resolved_guardrail_override(project_root: Path, run_id: str) -> dict | None:
    for payload in load_approval_requests(project_root, state="resolved"):
        if payload.get("source") != "guardrail":
            continue
        if payload.get("requested_action") != "run":
            continue
        if payload.get("decision") != "approved":
            continue
        if payload.get("run_id") != run_id:
            continue
        return payload
    return None


def write_guardrail_snapshot(run_dir: Path, snapshot: dict) -> None:
    write_json_atomic(run_guardrail_path(run_dir), snapshot)


def preflight_guardrails(
    project_root: Path,
    run_dir: Path,
    *,
    source: str,
) -> dict:
    refresh_guardrail_snapshot(project_root)
    project_snapshot = load_guardrail_project_snapshot(project_root)
    contract = load_workflow_contract(project_root)
    job, _job_error = load_json_status(run_dir / "job.json")
    meta, _meta_error = load_json_status(run_dir / "meta.json")

    task = job.get("task") if isinstance(job.get("task"), dict) else {}
    execution = job.get("execution") if isinstance(job.get("execution"), dict) else {}
    routing = job.get("routing") if isinstance(job.get("routing"), dict) else {}
    spec_text = ""
    spec_path = run_dir / "spec.md"
    if spec_path.is_file():
        spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
    override = load_resolved_guardrail_override(project_root, run_dir.name)
    snapshot = evaluate_guardrails(
        contract.guardrails,
        current_consumed_units=int(((project_snapshot.get("budget") or {}).get("consumed_units") or 0)),
        run_id=run_dir.name,
        task_id=str(task.get("id") or meta.get("task_id") or ""),
        task_title=str(task.get("title") or meta.get("task_title") or ""),
        selected_agent=str(routing.get("selected_agent") or job.get("preferred_agent") or meta.get("preferred_agent") or "codex"),
        workspace_mode=str(execution.get("workspace_mode") or "project_root"),
        risk_flags=[str(item).strip() for item in (task.get("risk_flags") or []) if str(item).strip()],
        referenced_paths=extract_referenced_paths(spec_text),
        approval_override=override is not None,
        approval_id=str(override.get("approval_id") or "") if override else None,
    )
    snapshot["source"] = source
    snapshot["recorded_at"] = utc_now()
    write_guardrail_snapshot(run_dir, snapshot)
    refresh_guardrail_snapshot(project_root)
    return snapshot


def finalize_guardrails(project_root: Path, run_dir: Path, *, executed: bool) -> dict | None:
    snapshot = load_guardrail_snapshot(run_dir)
    if snapshot is None:
        return None
    budget = snapshot.get("budget") if isinstance(snapshot.get("budget"), dict) else {}
    if executed:
        budget["consumed_units"] = int(budget.get("estimated_units", 0) or 0)
        snapshot["accounted_at"] = utc_now()
    snapshot["budget"] = budget
    write_guardrail_snapshot(run_dir, snapshot)
    refresh_guardrail_snapshot(project_root)
    return snapshot


def guardrail_task_path(project_root: Path, meta: dict) -> Path | None:
    task_rel_path = str(meta.get("task_path") or "").strip()
    if not task_rel_path:
        return None
    task_path = (project_root / task_rel_path).resolve()
    if not task_path.is_file():
        return None
    return task_path


def pause_run_for_guardrail(
    project_root: Path,
    run_dir: Path,
    snapshot: dict,
    *,
    source: str,
    claimed=None,
) -> dict:
    meta, _meta_error = load_json_status(run_dir / "meta.json")
    task_path = guardrail_task_path(project_root, meta)
    if task_path is not None:
        update_task_status(task_path, "awaiting_approval")

    primary_reason = str((snapshot.get("reason_codes") or ["guardrail_pause"])[0])
    approval = create_approval_request(
        project_root,
        run_id=run_dir.name,
        task_id=str(meta.get("task_id") or ""),
        task_path=str(meta.get("task_path") or ""),
        source="guardrail",
        reason=primary_reason,
        requested_action="run",
    )
    append_approval_requested_decision(project_root, approval)
    if claimed is not None:
        FileQueue(queue_root_for_project(project_root)).await_approval(claimed)

    append_run_event(
        run_dir,
        "guardrail_paused",
        project_root=project_root,
        payload={
            "queue_state": "awaiting_approval",
            "reason_codes": snapshot.get("reason_codes") or [],
            "source": source,
            "approval_id": approval.get("approval_id"),
        },
    )
    refresh_guardrail_snapshot(project_root)
    refresh_metrics_snapshot(project_root)
    return approval


def parse_task_dependencies(front_matter: dict) -> list[str]:
    raw = front_matter.get("dependencies", front_matter.get("depends_on", []))
    if isinstance(raw, str):
        return [item.strip() for item in raw.split(",") if item.strip()]
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def task_priority_value(priority: str | None) -> int:
    return PRIORITY_ORDER.get(str(priority or "").strip().lower(), len(PRIORITY_ORDER) + 1)


def collect_active_task_ids(queue: FileQueue) -> set[str]:
    active_ids: set[str] = set()
    for state in ("pending", "running", "awaiting_approval"):
        for payload in queue.list_jobs(state):
            task_id = payload.get("task", {}).get("id")
            if isinstance(task_id, str) and task_id:
                active_ids.add(task_id)
    return active_ids


def collect_task_records(project_root: Path) -> list[dict]:
    tasks_root = project_root / "tasks"
    queue = FileQueue(queue_root_for_project(project_root))
    active_task_ids = collect_active_task_ids(queue)
    records: list[dict] = []

    for task_path in sorted(tasks_root.glob("TASK-*.md")):
        try:
            front_matter, _body = read_front_matter(task_path)
        except yaml.YAMLError as exc:
            records.append({
                "task_id": task_path.stem,
                "title": "",
                "task_path": task_path,
                "task_path_rel": task_path.relative_to(project_root).as_posix(),
                "status": "todo",
                "priority": "",
                "dependencies": [],
                "preferred_agent": "auto",
                "needs_review": False,
                "active": False,
                "_parse_error": str(exc),
            })
            continue
        task_id = str(front_matter.get("id") or task_path.stem).strip()
        status = str(front_matter.get("status") or "todo").strip().lower()
        dependencies = parse_task_dependencies(front_matter)
        spec_ref = str(front_matter.get("spec") or "").strip()
        spec_path = None
        spec_path_rel = None
        if spec_ref:
            candidate = (task_path.parent / spec_ref).resolve()
            if candidate.is_file():
                spec_path = candidate
                try:
                    spec_path_rel = candidate.relative_to(project_root).as_posix()
                except ValueError:
                    spec_path_rel = str(candidate)
        records.append(
            {
                "task_id": task_id,
                "title": str(front_matter.get("title") or "").strip(),
                "task_path": task_path,
                "task_path_rel": task_path.relative_to(project_root).as_posix(),
                "spec": spec_ref,
                "spec_path": spec_path,
                "spec_path_rel": spec_path_rel,
                "status": status,
                "priority": str(front_matter.get("priority") or "").strip().lower(),
                "dependencies": dependencies,
                "preferred_agent": str(front_matter.get("preferred_agent") or "auto").strip(),
                "needs_review": bool(front_matter.get("needs_review", False)),
                "shared_files": front_matter.get("shared_files", False),
                "active": task_id in active_task_ids,
            }
        )

    done_ids = {record["task_id"] for record in records if record["status"] in TASK_DONE_STATUSES}
    for record in records:
        if record.get("_parse_error"):
            record["dependency_blockers"] = []
            record["ready"] = False
            record["selected_agent"] = "auto"
            continue
        status = record["status"]
        dependency_blockers = [item for item in record["dependencies"] if item not in done_ids]
        is_ready = (
            status not in TASK_DONE_STATUSES
            and status not in TASK_ACTIVE_STATUSES
            and status not in TASK_BLOCKED_STATUSES
            and not record["active"]
            and not dependency_blockers
        )
        record["dependency_blockers"] = dependency_blockers
        record["ready"] = is_ready
        try:
            plan = plan_task_run(REPO_ROOT, record["task_path"])
            record["selected_agent"] = plan.routing.selected_agent
        except Exception:
            record["selected_agent"] = record["preferred_agent"]

    return records


def resolve_task_path_for_id(project_root: Path, task_id: str) -> Path | None:
    candidate = project_root / "tasks" / f"{task_id}.md"
    if candidate.is_file():
        return candidate
    return None


def load_task_claims_map(project_root: Path) -> dict[str, dict]:
    store = TaskClaimStore(claims_root_for_project(project_root))
    claims: dict[str, dict] = {}
    for payload in store.list_claims():
        task_id = str(payload.get("task_id") or "").strip()
        if not task_id:
            continue
        claims[task_id] = payload
    return claims


def _session_summary(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    return {
        "session_id": payload.get("session_id"),
        "status": payload.get("status"),
        "updated_at": payload.get("updated_at"),
        "resume": payload.get("resume"),
        "handoff": payload.get("handoff") if isinstance(payload.get("handoff"), dict) else {},
        "reset_count": payload.get("reset_count"),
        "rotation_count": payload.get("rotation_count"),
        "session_file": payload.get("session_file"),
    }


def _session_docs_summary(project_root: Path, task_id: str) -> dict | None:
    store = SessionDocsStore(session_docs_root_for_project(project_root))
    payload = store.load_manifest(task_id=task_id)
    if not isinstance(payload, dict):
        return None
    return {
        "task_id": payload.get("task_id"),
        "document_count": payload.get("document_count"),
        "updated_at": payload.get("updated_at"),
        "manifest_file": payload.get("manifest_file"),
        "files_root": payload.get("files_root"),
        "documents": payload.get("documents") if isinstance(payload.get("documents"), list) else [],
    }


def _operator_session_summary(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    store = OperatorSessionStore(operator_sessions_root_for_repo(REPO_ROOT), repo_root=REPO_ROOT)
    return {
        "session_id": payload.get("session_id"),
        "status": payload.get("status"),
        "scope": payload.get("scope") if isinstance(payload.get("scope"), dict) else {},
        "scope_key": payload.get("scope_key"),
        "engine": payload.get("engine"),
        "binding": payload.get("binding") if isinstance(payload.get("binding"), dict) else {},
        "updated_at": payload.get("updated_at"),
        "resume": payload.get("resume"),
        "resume_line": store.derive_resume_line(
            engine=str(payload.get("engine") or ""),
            resume=payload.get("resume") if isinstance(payload.get("resume"), dict) else None,
        ),
        "handoff": payload.get("handoff") if isinstance(payload.get("handoff"), dict) else {},
        "reset_count": payload.get("reset_count"),
        "rotation_count": payload.get("rotation_count"),
        "session_file": payload.get("session_file"),
    }


def load_session_summary_map(project_root: Path) -> dict[str, dict]:
    store = SessionStore(sessions_root_for_project(project_root))
    sessions: dict[str, dict] = {}
    for payload in store.list_sessions():
        scope_key = str(payload.get("scope_key") or "").strip()
        if not scope_key:
            continue
        summary = _session_summary(payload)
        if summary:
            sessions[scope_key] = summary
    return sessions


def _claim_summary(payload: dict | None) -> dict | None:
    if not isinstance(payload, dict):
        return None
    return {
        "status": payload.get("status"),
        "owner": payload.get("owner"),
        "updated_at": payload.get("updated_at"),
        "reason": payload.get("reason"),
        "note": payload.get("note"),
        "claim_file": payload.get("claim_file"),
    }


def _task_inbox_entry(record: dict, claim: dict | None, session: dict | None = None) -> dict:
    entry = {
        "task_id": record.get("task_id"),
        "title": record.get("title"),
        "status": record.get("status"),
        "priority": record.get("priority"),
        "ready": record.get("ready"),
        "active": record.get("active"),
        "task_path": record.get("task_path_rel"),
        "selected_agent": record.get("selected_agent"),
        "preferred_agent": record.get("preferred_agent"),
        "dependency_blockers": record.get("dependency_blockers"),
    }
    claim_summary = _claim_summary(claim)
    if claim_summary:
        entry["claim"] = claim_summary
    if session:
        entry["session"] = session
    return entry


def build_agent_inbox(project_root: Path, *, agent: str, limit: int = 20) -> dict:
    normalized_agent = str(agent or "").strip()
    records = collect_task_records(project_root)
    claims = load_task_claims_map(project_root)
    sessions = load_session_summary_map(project_root)
    claimed: list[dict] = []
    blocked: list[dict] = []
    released: list[dict] = []
    available: list[dict] = []
    conflicts: list[dict] = []

    for record in records:
        task_id = record.get("task_id")
        claim = claims.get(task_id) if task_id else None
        claim_status = str(claim.get("status") or "").strip() if isinstance(claim, dict) else ""
        claim_owner = str(claim.get("owner") or "").strip() if isinstance(claim, dict) else ""
        selected_agent = str(record.get("selected_agent") or record.get("preferred_agent") or "").strip()
        eligible = selected_agent == normalized_agent

        session = sessions.get(f"{normalized_agent}::{task_id}") if task_id else None

        if claim_status == "claimed":
            if claim_owner == normalized_agent:
                claimed.append(_task_inbox_entry(record, claim, session))
            elif eligible:
                conflicts.append(_task_inbox_entry(record, claim, session))
            continue

        if claim_status == "blocked" and claim_owner == normalized_agent:
            blocked.append(_task_inbox_entry(record, claim, session))
        if claim_status == "released" and claim_owner == normalized_agent:
            released.append(_task_inbox_entry(record, claim, session))

        if record.get("ready") and eligible:
            available.append(_task_inbox_entry(record, claim, session))

    payload = {
        "project": project_root.name,
        "agent": normalized_agent,
        "generated_at": utc_now(),
        "counts": {
            "claimed": len(claimed),
            "blocked": len(blocked),
            "released": len(released),
            "available": len(available),
            "conflicts": len(conflicts),
        },
        "claimed": claimed[: max(1, int(limit))],
        "blocked": blocked[: max(1, int(limit))],
        "released": released[: max(1, int(limit))],
        "available": available[: max(1, int(limit))],
        "conflicts": conflicts[: max(1, int(limit))],
    }
    return payload


def _task_declares_shared_files(record: dict) -> bool:
    shared_files = record.get("shared_files")
    return bool(shared_files)


def _task_spec_files(record: dict) -> set[str]:
    spec_path = record.get("spec_path")
    if not isinstance(spec_path, Path) or not spec_path.is_file():
        return set()
    spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
    return set(re.findall(r"`([a-z_][a-zA-Z0-9_/.-]+\.[a-z]+)`", spec_text))


def _tasks_overlap_files(record: dict, other_record: dict, task_files: dict[str, set[str]]) -> set[str]:
    if _task_declares_shared_files(record) or _task_declares_shared_files(other_record):
        return set()
    return task_files.get(record["task_id"], set()) & task_files.get(other_record["task_id"], set())


def check_file_overlap(records: list[dict]) -> list[dict]:
    """Check for file-overlap between tasks that don't declare shared_files."""
    relevant_records = [record for record in records if record.get("status") not in TASK_DONE_STATUSES]
    task_files = {record["task_id"]: _task_spec_files(record) for record in relevant_records}
    issues: list[dict] = []

    for index, record in enumerate(relevant_records):
        for other_record in relevant_records[index + 1:]:
            overlap = _tasks_overlap_files(record, other_record, task_files)
            if not overlap:
                continue
            issues.append(
                {
                    "code": "file_overlap",
                    "task_id": record["task_id"],
                    "other_task_id": other_record["task_id"],
                    "severity": "warning",
                    "message": (
                        f"Tasks {record['task_id']} and {other_record['task_id']} both reference files: "
                        + ", ".join(sorted(overlap))
                        + " - do not run in parallel"
                    ),
                }
            )

    return issues


def select_ready_tasks(project_root: Path, *, limit: int = 3) -> list[dict]:
    records = collect_task_records(project_root)
    task_files = {record["task_id"]: _task_spec_files(record) for record in records}
    occupied_records = [
        record
        for record in records
        if record.get("status") in TASK_ACTIVE_STATUSES or bool(record.get("active"))
    ]
    ready = [record for record in records if record["ready"]]
    ready.sort(key=lambda record: (task_priority_value(record["priority"]), record["task_id"]))
    selected: list[dict] = []
    reserved = list(occupied_records)

    for record in ready:
        has_overlap = any(_tasks_overlap_files(record, other_record, task_files) for other_record in reserved)
        if has_overlap:
            continue
        selected.append(record)
        reserved.append(record)
        if len(selected) >= limit:
            break

    return selected


def count_retry_backlog(project_root: Path) -> int:
    queue = FileQueue(queue_root_for_project(project_root))
    waiting = 0
    now = datetime.now(timezone.utc)
    for payload in queue.list_jobs("pending"):
        next_retry_at = parse_iso_timestamp(payload.get("queue", {}).get("next_retry_at"))
        if next_retry_at is not None and next_retry_at > now:
            waiting += 1
    return waiting


def load_pending_review_decisions(project_root: Path, *, run_id: str | None = None) -> list[dict]:
    decisions_dir = project_root / "reviews" / "decisions"
    pending: list[dict] = []
    if not decisions_dir.is_dir():
        return pending

    for path in sorted(decisions_dir.glob("*.json")):
        payload, error = load_json_status(path)
        if error is not None or payload.get("decision") != "pending":
            continue
        if run_id and payload.get("run_id") != run_id:
            continue
        payload["decision_file"] = path.relative_to(project_root).as_posix()
        pending.append(payload)
    return pending


def load_resolved_review_decision(project_root: Path, run_id: str) -> dict | None:
    decisions_dir = project_root / "reviews" / "decisions"
    if not decisions_dir.is_dir():
        return None

    resolved: list[dict] = []
    for path in sorted(decisions_dir.glob("*.json")):
        payload, error = load_json_status(path)
        if error is not None or payload.get("run_id") != run_id:
            continue
        if payload.get("decision") in {"pending", None}:
            continue
        payload["decision_file"] = path.relative_to(project_root).as_posix()
        resolved.append(payload)

    if not resolved:
        return None
    resolved.sort(key=lambda payload: str(payload.get("decided_at") or payload.get("review_id") or ""))
    return resolved[-1]


def record_orchestration_decision(project_root: Path, *, run_id: str, decision: dict, failure_budget: int) -> dict:
    state = load_orchestration_state(project_root)
    outcome = str(decision.get("decision") or "unknown")
    reason = str(decision.get("reason") or outcome)
    if outcome == "accept":
        state["consecutive_failures"] = 0
    elif outcome == "ask_human" and reason in {"run_failed", "rejected"}:
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
    state["last_run_id"] = run_id
    state["last_decision"] = reason
    state["last_updated_at"] = utc_now()
    save_orchestration_state(project_root, state)
    return {
        "consecutive_failures": int(state.get("consecutive_failures", 0)),
        "failure_budget": max(1, int(failure_budget)),
        "failure_budget_exhausted": int(state.get("consecutive_failures", 0)) >= max(1, int(failure_budget)),
        "last_run_id": state.get("last_run_id"),
        "last_decision": state.get("last_decision"),
        "last_updated_at": state.get("last_updated_at"),
    }


def materialize_follow_up_tasks(project_root: Path, run_dir: Path, decision: dict) -> dict:
    decision_file = decision.get("decision_file")
    decision_path = (project_root / str(decision_file)).resolve() if isinstance(decision_file, str) and decision_file else None
    actions = decision.get("follow_up_actions")
    if not isinstance(actions, list) or not actions:
        return {"status": "missing_follow_up_actions", "tasks": [], "runs": []}

    meta, _meta_error = load_json_status(run_dir / "meta.json")
    if not meta:
        return {"status": "missing_run_meta", "tasks": [], "runs": []}

    created_tasks: list[dict] = []
    enqueued_runs: list[dict] = []
    updated_actions: list[dict] = []

    for raw_action in actions:
        action = dict(raw_action) if isinstance(raw_action, dict) else {}
        action_id = str(action.get("action_id") or "").strip()
        description = str(action.get("description") or "").strip()
        action_status = str(action.get("status") or "pending").strip() or "pending"
        if not action_id or not description:
            updated_actions.append(action)
            continue

        existing_task: Path | None = None
        existing_task_path = str(action.get("task_path") or "").strip()
        if existing_task_path:
            candidate = (project_root / existing_task_path).resolve()
            if candidate.is_file():
                existing_task = candidate
        if existing_task is None:
            existing_task = find_follow_up_task(project_root, review_id=str(decision.get("review_id") or "").strip(), action_id=action_id)

        if existing_task is None and action_status == "pending":
            existing_task = create_follow_up_task(project_root, meta=meta, decision=decision, action=action)
            created_tasks.append(
                {
                    "task_id": existing_task.stem,
                    "task_path": existing_task.relative_to(project_root).as_posix(),
                }
            )
            append_decision(
                project_root,
                "follow_up_created",
                run_id=str(meta.get("run_id") or run_dir.name),
                task_id=str(meta.get("task_id") or ""),
                reason_code="needs_follow_up",
                details={
                    "review_id": decision.get("review_id"),
                    "action_id": action_id,
                    "assigned_agent": action.get("assigned_agent"),
                    "created_task_id": existing_task.stem,
                    "created_task_path": existing_task.relative_to(project_root).as_posix(),
                },
                outcome="created",
            )

        if existing_task is not None:
            action["task_id"] = existing_task.stem
            action["task_path"] = existing_task.relative_to(project_root).as_posix()
            task_front_matter, _body = read_front_matter(existing_task)
            task_status = str(task_front_matter.get("status") or "todo").strip().lower()
            if task_status in TASK_DONE_STATUSES:
                action["status"] = "done"
                updated_actions.append(action)
                continue
            if task_status in TASK_ACTIVE_STATUSES:
                action["status"] = "in_progress"
                updated_actions.append(action)
                continue

        if existing_task is not None and action_status == "pending":
            previous_status = str(read_front_matter(existing_task)[0].get("status") or "todo").strip().lower() or "todo"
            update_task_status(existing_task, "queued")
            try:
                follow_up_run_dir, _payload = enqueue_task_path(existing_task)
            except Exception:
                update_task_status(existing_task, previous_status)
                raise
            action["status"] = "in_progress"
            action["materialized_at"] = utc_now()
            action["enqueued_run_id"] = follow_up_run_dir.name
            action["enqueued_run_path"] = follow_up_run_dir.relative_to(project_root).as_posix()
            enqueued_runs.append(
                {
                    "run_id": follow_up_run_dir.name,
                    "run_path": follow_up_run_dir.relative_to(project_root).as_posix(),
                    "task_id": existing_task.stem,
                }
            )

        updated_actions.append(action)

    if decision_path is not None:
        updated_decision = dict(decision)
        updated_decision.pop("decision_file", None)
        updated_decision["follow_up_actions"] = updated_actions
        if created_tasks or enqueued_runs:
            updated_decision["follow_up_materialized_at"] = utc_now()
        write_json_atomic(decision_path, updated_decision)

    if not created_tasks and not enqueued_runs and not any(str(action.get("task_path") or "").strip() for action in updated_actions):
        return {"status": "missing_follow_up_actions", "tasks": [], "runs": []}

    return {"status": "materialized", "tasks": created_tasks, "runs": enqueued_runs}


def recent_failure_records(project_root: Path, *, limit: int = 3) -> list[dict]:
    queue = FileQueue(queue_root_for_project(project_root))
    failures: list[dict] = []
    for state in ("failed", "dead_letter"):
        for payload in queue.list_jobs(state):
            queue_payload = payload.get("queue", {})
            failures.append(
                {
                    "job_id": payload.get("job_id"),
                    "state": state,
                    "task_id": payload.get("task", {}).get("id"),
                    "error": trim_text(str(queue_payload.get("last_error") or ""), 200),
                    "updated_at": queue_payload.get("updated_at"),
                }
            )
    failures.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return failures[:limit]


def current_running_job(project_root: Path) -> dict | None:
    queue = FileQueue(queue_root_for_project(project_root))
    running = queue.list_jobs("running")
    if not running:
        return None
    payload = running[0]
    return {
        "job_id": payload.get("job_id"),
        "task_id": payload.get("task", {}).get("id"),
        "task_title": payload.get("task", {}).get("title"),
        "worker_id": payload.get("queue", {}).get("worker_id"),
        "attempt_count": payload.get("queue", {}).get("attempt_count"),
    }


def build_project_dashboard(project_root: Path, *, recent_limit: int = 5, ready_limit: int = 3) -> dict:
    snapshot = refresh_metrics_snapshot(project_root, recent_limit=max(recent_limit, 20))
    guardrails = refresh_guardrail_snapshot(project_root)
    approvals = approval_counts(project_root)
    ready_tasks = select_ready_tasks(project_root, limit=ready_limit)

    return {
        "project": project_root.name,
        "queue": snapshot["queue"],
        "wakes": snapshot.get("wakes") or wake_snapshot(project_root, limit=ready_limit)["counts"],
        "pending_reviews": snapshot["reviews"]["pending_decisions"],
        "pending_hooks": snapshot["hooks"]["pending"],
        "failed_hooks": snapshot["hooks"]["failed"],
        "pending_approvals": approvals["pending"],
        "resolved_approvals": approvals["resolved"],
        "retry_backlog": count_retry_backlog(project_root),
        "current_run": current_running_job(project_root),
        "recent_runs": snapshot["recent_runs"][:recent_limit],
        "recent_failures": recent_failure_records(project_root, limit=ready_limit),
        "ready_tasks": [
            {
                "task_id": task["task_id"],
                "title": task["title"],
                "priority": task["priority"],
                "selected_agent": task.get("selected_agent"),
                "task_path_rel": task["task_path_rel"],
            }
            for task in ready_tasks
        ],
        "approvals": load_approval_requests(project_root, state="pending")[:ready_limit],
        "guardrails": guardrails,
        "metrics": {
            "updated_at": snapshot["updated_at"],
            "runs": snapshot["runs"],
            "reviews": snapshot["reviews"],
        },
    }


def parse_last_json_line(text: str) -> dict | None:
    for line in reversed((text or "").splitlines()):
        candidate = line.strip()
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def run_worker_once(project_root: Path, *, skip_review: bool = False) -> tuple[subprocess.CompletedProcess[str], dict | None]:
    command = [sys.executable, str(REPO_ROOT / "scripts" / "claw.py"), "worker", str(project_root), "--once"]
    if skip_review:
        command.append("--skip-review")
    completed = run_command(command, cwd=REPO_ROOT)
    return completed, parse_last_json_line(completed.stdout)


def enqueue_task_path(task_path: Path) -> tuple[Path, dict]:
    run_dir = execute_run_task(REPO_ROOT, str(task_path), execute=False)
    payload = enqueue_run(run_dir, state="pending")
    project_root = run_dir.parent.parent.parent
    append_routing_decision(project_root, run_dir, source="orchestrate")
    refresh_metrics_snapshot(project_root)
    return run_dir, payload


def accept_run(project_root: Path, run_dir: Path) -> None:
    meta, _error = load_json_status(run_dir / "meta.json")
    task_rel_path = meta.get("task_path")
    if isinstance(task_rel_path, str) and task_rel_path:
        task_path = (project_root / task_rel_path).resolve()
        if task_path.is_file():
            update_task_status(task_path, "done")


def evaluate_run_decision(project_root: Path, run_dir: Path, *, result_status: str) -> dict:
    meta, _meta_error = load_json_status(run_dir / "meta.json")
    run_id = str(meta.get("run_id") or run_dir.name)
    task_id = str(meta.get("task_id") or "")
    task_path = str(meta.get("task_path") or "")

    if result_status != "success":
        approval = create_approval_request(
            project_root,
            run_id=run_id,
            task_id=task_id,
            task_path=task_path,
            source="runtime",
            reason="run_failed",
            requested_action="retry",
        )
        append_approval_requested_decision(project_root, approval)
        return {"decision": "ask_human", "approval_id": approval["approval_id"], "reason": "run_failed"}

    pending_reviews = load_pending_review_decisions(project_root, run_id=run_id)
    if pending_reviews:
        return {"decision": "awaiting_review", "pending_reviews": len(pending_reviews), "reason": "pending_review"}

    resolved_decision = load_resolved_review_decision(project_root, run_id)
    if resolved_decision is not None:
        decision = str(resolved_decision.get("decision") or "pending")
        if decision in {"approved", "approved_with_notes", "waived"}:
            accept_run(project_root, run_dir)
            return {"decision": "accept", "reason": decision}
        if decision == "needs_follow_up":
            follow_up = materialize_follow_up_tasks(project_root, run_dir, resolved_decision)
            if follow_up["status"] == "materialized":
                accept_run(project_root, run_dir)
                return {
                    "decision": "accept",
                    "reason": "needs_follow_up",
                    "follow_up_tasks": follow_up["tasks"],
                    "follow_up_runs": follow_up["runs"],
                }
        approval = create_approval_request(
            project_root,
            run_id=run_id,
            task_id=task_id,
            task_path=task_path,
            source="review",
            reason=decision,
            requested_action="follow_up" if decision == "needs_follow_up" else "retry",
        )
        append_approval_requested_decision(project_root, approval)
        return {"decision": "ask_human", "approval_id": approval["approval_id"], "reason": decision}

    accept_run(project_root, run_dir)
    return {"decision": "accept", "reason": "no_review_required"}


def collect_run_results(project_root: Path) -> list[tuple[Path, dict]]:
    runs_root = project_root / "runs"
    results: list[tuple[Path, dict]] = []
    if not runs_root.is_dir():
        return results

    for date_dir in sorted(runs_root.iterdir()):
        if not date_dir.is_dir():
            continue
        for run_dir in sorted(date_dir.iterdir()):
            if not run_dir.is_dir() or not run_dir.name.startswith("RUN-"):
                continue
            result_path = run_dir / "result.json"
            if not result_path.is_file():
                continue
            try:
                results.append((result_path, read_json(result_path)))
            except (json.JSONDecodeError, OSError):
                continue
    return results


def count_pending_review_decisions(project_root: Path) -> int:
    decisions_dir = project_root / "reviews" / "decisions"
    pending_reviews = 0
    if not decisions_dir.is_dir():
        return pending_reviews

    for stub_path in decisions_dir.glob("*.json"):
        try:
            stub = read_json(stub_path)
        except (json.JSONDecodeError, OSError):
            continue
        if stub.get("decision") == "pending":
            pending_reviews += 1
    return pending_reviews


def build_metrics_snapshot(project_root: Path, *, recent_limit: int = 20) -> dict:
    queue_snapshot = queue_counts(project_root)
    wakes_snapshot = wake_snapshot(project_root, limit=recent_limit)
    hook_snapshot = hook_counts(project_root)
    reviewed_batches = len(list((project_root / "reviews").glob("REVIEW-*.json")))
    result_files = collect_run_results(project_root)

    run_statuses = {
        "pending": 0,
        "running": 0,
        "success": 0,
        "failed": 0,
        "unknown": 0,
    }
    delivery_counts = {
        "pending": 0,
        "delivered": 0,
        "failed": 0,
        "missing": 0,
    }
    recent_runs: list[dict] = []

    for result_path, result in result_files:
        status = str(result.get("status") or "unknown")
        run_statuses[status if status in run_statuses else "unknown"] += 1
        run_id = result.get("run_id") or result_path.parent.name
        finished_at = result.get("finished_at") or result.get("completed_at") or result.get("created_at")
        meta, _meta_error = load_json_status(result_path.parent / "meta.json")
        delivery = resolve_run_delivery(project_root, result_path.parent, meta=meta, result=result)
        delivery_status = str(delivery.get("status") or "")
        if delivery_status == "pending_delivery":
            delivery_counts["pending"] += 1
        elif delivery_status == "delivered":
            delivery_counts["delivered"] += 1
        elif delivery_status == "failed":
            delivery_counts["failed"] += 1
        elif delivery_status == "missing":
            delivery_counts["missing"] += 1
        recent_runs.append({
            "run_id": run_id,
            "status": status,
            "agent": result.get("agent", ""),
            "finished_at": finished_at,
            "task_id": meta.get("task_id"),
            "delivery": delivery,
        })

    snapshot = {
        "snapshot_version": 1,
        "project": project_root.name,
        "updated_at": utc_now(),
        "queue": queue_snapshot,
        "wakes": wakes_snapshot["counts"],
        "hooks": hook_snapshot,
        "runs": {
            "total": len(result_files),
            "by_status": run_statuses,
        },
        "delivery": delivery_counts,
        "reviews": {
            "batch_count": reviewed_batches,
            "pending_decisions": count_pending_review_decisions(project_root),
        },
        "recent_runs": list(reversed(recent_runs[-recent_limit:])),
    }
    return snapshot


def load_json_status(path: Path) -> tuple[dict, str | None]:
    try:
        payload = read_json(path)
    except FileNotFoundError:
        return {}, "missing"
    except (json.JSONDecodeError, OSError) as exc:
        return {}, str(exc)
    if not isinstance(payload, dict):
        return {}, "invalid_json_type"
    return payload, None


def empty_event_snapshot(project_root: Path, run_dir: Path) -> dict:
    return {
        "snapshot_version": 1,
        "project": project_root.name,
        "run_id": run_dir.name,
        "run_path": run_dir.relative_to(project_root).as_posix(),
        "updated_at": None,
        "event_count": 0,
        "last_event_type": None,
        "last_event_at": None,
        "queue_state": None,
        "run_status": None,
        "delivery_status": None,
        "attempt_count": 0,
    }


def load_event_snapshot_status(project_root: Path, run_dir: Path) -> tuple[dict, dict[str, str]]:
    snapshot_path = run_dir / "event_snapshot.json"
    snapshot, snapshot_error = load_json_status(snapshot_path)
    errors: dict[str, str] = {}

    if snapshot_error is None:
        return {**empty_event_snapshot(project_root, run_dir), **snapshot}, errors

    errors["event_snapshot"] = snapshot_error
    try:
        derived = build_run_event_snapshot(project_root, run_dir)
    except Exception as exc:
        errors["events"] = str(exc)
        derived = empty_event_snapshot(project_root, run_dir)
    return {**empty_event_snapshot(project_root, run_dir), **derived}, errors


def load_queue_projection(project_root: Path, run_id: str) -> tuple[dict, dict, dict[str, str]]:
    queue = FileQueue(queue_root_for_project(project_root))
    queue_state = queue.queue_state(run_id) or "unknown"
    queue_projection = {"state": queue_state}
    payload: dict = {}
    errors: dict[str, str] = {}

    queue_path = queue.find_job(run_id)
    if queue_path is None:
        return queue_projection, payload, errors

    payload, queue_error = load_json_status(queue_path)
    if queue_error is not None:
        errors["queue"] = queue_error
        return queue_projection, {}, errors

    queue_payload = payload.get("queue") if isinstance(payload.get("queue"), dict) else {}
    queue_projection.update(
        {
            "attempt_count": queue_payload.get("attempt_count"),
            "max_attempts": queue_payload.get("max_attempts"),
            "next_retry_at": queue_payload.get("next_retry_at"),
            "retry_backoff_seconds": queue_payload.get("retry_backoff_seconds"),
            "worker_id": queue_payload.get("worker_id"),
            "last_worker_id": queue_payload.get("last_worker_id"),
            "last_error": queue_payload.get("last_error"),
            "updated_at": queue_payload.get("updated_at"),
            "completed_at": queue_payload.get("completed_at"),
            "history_length": len(queue_payload.get("history", [])) if isinstance(queue_payload.get("history"), list) else 0,
        }
    )
    return queue_projection, payload, errors


def load_checkpoint_status(run_dir: Path) -> tuple[dict | None, str | None]:
    checkpoint_path = run_dir / "approval_checkpoint.json"
    if not checkpoint_path.exists():
        return None, None

    payload, error = load_json_status(checkpoint_path)
    if error is not None:
        return None, error
    return payload, None


def latest_timestamp(*values: str | None) -> str | None:
    latest_value: str | None = None
    latest_parsed: datetime | None = None
    for value in values:
        parsed = parse_iso_timestamp(value)
        if parsed is None:
            continue
        if latest_parsed is None or parsed > latest_parsed:
            latest_parsed = parsed
            latest_value = value
    return latest_value


def normalize_artifact_status(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized == "completed":
        return "success"
    return normalized


def resolve_live_run_status(
    *,
    queue_state: str,
    result_status: str | None,
    meta_status: str | None,
    event_snapshot: dict,
    checkpoint: dict | None,
) -> str:
    if checkpoint is not None and str(checkpoint.get("status") or "").strip().lower() == "pending":
        return "awaiting_approval"
    if queue_state == "awaiting_approval":
        return "awaiting_approval"
    if queue_state == "running":
        return "running"
    if queue_state == "pending":
        return normalize_artifact_status(event_snapshot.get("run_status")) or "queued"
    if queue_state == "dead_letter":
        return "dead_letter"
    if queue_state == "failed":
        return "failed"
    if queue_state == "done":
        return (
            normalize_artifact_status(result_status)
            or normalize_artifact_status(event_snapshot.get("run_status"))
            or "success"
        )
    return (
        normalize_artifact_status(event_snapshot.get("run_status"))
        or normalize_artifact_status(result_status)
        or normalize_artifact_status(meta_status)
        or "unknown"
    )


def build_current_step(
    *,
    queue_state: str,
    run_status: str,
    event_snapshot: dict,
    delivery: dict,
    checkpoint: dict | None,
    queue_projection: dict,
) -> dict:
    last_event_type = str(event_snapshot.get("last_event_type") or "").strip()
    last_event_at = event_snapshot.get("last_event_at") or event_snapshot.get("updated_at")
    step = {
        "key": last_event_type or queue_state or run_status or "unknown",
        "label": last_event_type or run_status or queue_state or "unknown",
        "status": "unknown",
        "updated_at": last_event_at,
        "source": "event_snapshot" if last_event_type else "derived",
    }

    if checkpoint is not None and str(checkpoint.get("status") or "").strip().lower() == "pending":
        context = checkpoint.get("context") if isinstance(checkpoint.get("context"), dict) else {}
        step.update(
            {
                "key": "approval_checkpoint",
                "label": str(context.get("step") or checkpoint.get("reason") or "Awaiting approval"),
                "status": "blocked",
                "updated_at": checkpoint.get("created_at") or last_event_at,
                "source": "approval_checkpoint",
                "checkpoint_id": checkpoint.get("checkpoint_id"),
                "context": context,
            }
        )
        return step

    if queue_state == "pending":
        label = "Queued"
        if last_event_type == "job_retried":
            label = "Queued for retry"
        elif last_event_type == "approval_granted":
            label = "Re-queued after approval"
        step.update({"key": last_event_type or "run_enqueued", "label": label, "status": "pending"})
        if queue_projection.get("next_retry_at"):
            step["next_retry_at"] = queue_projection.get("next_retry_at")
        return step

    if queue_state == "running":
        step.update({"key": last_event_type or "run_started", "label": "Agent running", "status": "active"})
        return step

    if queue_state == "awaiting_approval":
        step.update({"key": "awaiting_approval", "label": "Awaiting approval", "status": "blocked", "source": "queue"})
        return step

    if queue_state == "dead_letter":
        step.update({"key": "job_dead_letter", "label": "Moved to dead letter", "status": "failed"})
        return step

    delivery_status = str(delivery.get("status") or "").strip()
    if delivery_status == "pending_delivery":
        step.update({"key": "delivery_pending", "label": "Awaiting delivery", "status": "complete", "source": "delivery"})
        return step
    if delivery_status == "failed":
        step.update({"key": "delivery_failed", "label": "Delivery failed", "status": "failed", "source": "delivery"})
        return step
    if delivery_status == "delivered":
        step.update({"key": "delivery_sent", "label": "Delivered", "status": "complete", "source": "delivery"})
        return step

    if run_status == "success":
        step.update({"key": last_event_type or "run_finished", "label": "Run finished", "status": "complete"})
    elif run_status == "failed":
        step.update({"key": last_event_type or "run_finished", "label": "Run failed", "status": "failed"})
    return step


def build_live_status_feed(project_root: Path, run_dir: Path, *, stream_limit: int = 10) -> dict:
    meta, meta_error = load_json_status(run_dir / "meta.json")
    result, result_error = load_json_status(run_dir / "result.json")
    event_snapshot, snapshot_errors = load_event_snapshot_status(project_root, run_dir)
    queue_projection, queue_payload, queue_errors = load_queue_projection(project_root, run_dir.name)
    checkpoint, checkpoint_error = load_checkpoint_status(run_dir)

    stream_tail = load_stream_tail(run_dir, limit=stream_limit)
    result_status = normalize_artifact_status(result.get("status"))
    meta_status = normalize_artifact_status(meta.get("status"))
    run_status = resolve_live_run_status(
        queue_state=str(queue_projection.get("state") or "unknown"),
        result_status=result_status,
        meta_status=meta_status,
        event_snapshot=event_snapshot,
        checkpoint=checkpoint,
    )

    started_at = result.get("started_at") or meta.get("started_at")
    finished_at = result.get("finished_at") or result.get("completed_at") or meta.get("finished_at")
    delivery = resolve_run_delivery(project_root, run_dir, meta=meta, result=result)
    current_step = build_current_step(
        queue_state=str(queue_projection.get("state") or "unknown"),
        run_status=run_status,
        event_snapshot=event_snapshot,
        delivery=delivery,
        checkpoint=checkpoint,
        queue_projection=queue_projection,
    )

    report_path_abs = run_dir / "report.md"
    report_path = ""
    if report_path_abs.is_file():
        try:
            report_path = report_path_abs.relative_to(project_root).as_posix()
        except ValueError:
            report_path = str(report_path_abs)

    job_task = queue_payload.get("task") if isinstance(queue_payload.get("task"), dict) else {}
    artifact_errors: dict[str, str] = {}
    if meta_error is not None:
        artifact_errors["meta"] = meta_error
    if result_error is not None:
        artifact_errors["result"] = result_error
    artifact_errors.update(snapshot_errors)
    artifact_errors.update(queue_errors)
    if checkpoint_error is not None:
        artifact_errors["approval_checkpoint"] = checkpoint_error

    payload = {
        "feed_version": 1,
        "project": meta.get("project") or project_root.name,
        "run_id": result.get("run_id") or meta.get("run_id") or run_dir.name,
        "run_path": run_dir.relative_to(project_root).as_posix(),
        "status": run_status,
        "run_status": run_status,
        "meta_status": meta.get("status") or "unknown",
        "result_status": result_status or "unknown",
        "queue_state": queue_projection.get("state") or "unknown",
        "queue": queue_projection,
        "agent": result.get("agent") or meta.get("preferred_agent") or queue_payload.get("preferred_agent") or "",
        "task_id": meta.get("task_id") or job_task.get("id"),
        "task_title": meta.get("task_title") or job_task.get("title"),
        "summary": result.get("summary") or meta.get("summary") or "",
        "duration_seconds": duration_between(started_at, finished_at),
        "current_step": current_step,
        "stream_tail": stream_tail,
        "validation": result.get("validation") or meta.get("validation") or {},
        "hook": result.get("hook") or meta.get("hook") or {},
        "delivery": delivery,
        "guardrails": load_guardrail_snapshot(run_dir),
        "event_snapshot": event_snapshot,
        "checkpoint": checkpoint,
        "report_path": report_path,
        "updated_at": latest_timestamp(
            current_step.get("updated_at"),
            event_snapshot.get("updated_at"),
            finished_at,
            started_at,
            stream_tail[-1]["ts"] if stream_tail else None,
        ),
    }
    if artifact_errors:
        payload["artifact_errors"] = artifact_errors
        payload["errors"] = artifact_errors
    return payload


def resolve_run_delivery(project_root: Path, run_dir: Path, *, meta: dict | None = None, result: dict | None = None) -> dict:
    resolved_meta = meta or {}
    resolved_result = result or {}
    run_id = str(resolved_result.get("run_id") or resolved_meta.get("run_id") or run_dir.name)
    run_date = str(resolved_meta.get("run_date") or run_dir.parent.name or "").strip() or None
    return build_delivery_snapshot(
        project_root,
        run_id=run_id,
        run_date=run_date,
        meta=resolved_meta,
        result=resolved_result,
    )


def refresh_metrics_snapshot(project_root: Path, *, recent_limit: int = 20) -> dict:
    snapshot = build_metrics_snapshot(project_root, recent_limit=recent_limit)
    write_json_atomic(project_root / "state" / METRICS_SNAPSHOT_FILE, snapshot)
    return snapshot


TASK_SNAPSHOT_FILE = "tasks_snapshot.json"
WORKFLOW_GRAPH_FILE = "workflow_graph.json"


def build_task_snapshot(project_root: Path, *, records: list[dict] | None = None) -> dict:
    """Build a structural snapshot of the task graph for lint and selector use."""
    records = records if records is not None else collect_task_records(project_root)
    tasks = [
        {
            "task_id": r["task_id"],
            "title": r["title"],
            "status": r["status"],
            "priority": r["priority"],
            "dependencies": r["dependencies"],
            "dependency_blockers": r["dependency_blockers"],
            "preferred_agent": r["preferred_agent"],
            "needs_review": r["needs_review"],
            "ready": r["ready"],
            "active": r["active"],
            "task_path": r["task_path_rel"],
        }
        for r in records
    ]
    canonical = json.dumps(tasks, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    checksum = hashlib.sha256(canonical.encode()).hexdigest()
    return {
        "snapshot_version": 1,
        "project": project_root.name,
        "updated_at": utc_now(),
        "task_count": len(tasks),
        "tasks": tasks,
        "checksum": checksum,
    }


def build_workflow_graph(project_root: Path, *, records: list[dict] | None = None) -> dict:
    records = records if records is not None else collect_task_records(project_root)
    nodes = []
    for record in records:
        node = {
            "node_id": record["task_id"],
            "node_type": "task",
            "title": record["title"],
            "status": record["status"],
            "priority": record["priority"],
            "preferred_agent": record["preferred_agent"],
            "selected_agent": record.get("selected_agent") or record["preferred_agent"],
            "needs_review": bool(record["needs_review"]),
            "ready": bool(record["ready"]),
            "active": bool(record["active"]),
            "dependencies": list(record["dependencies"]),
            "dependency_blockers": list(record["dependency_blockers"]),
            "task_path": record["task_path_rel"],
        }
        if record.get("_parse_error"):
            node["parse_error"] = str(record["_parse_error"])
        nodes.append(node)

    edges = [
        {
            "from": record["task_id"],
            "to": dependency,
            "edge_type": "sequence",
            "trigger": "dependency_resolved",
            "reason_code": "dependency",
            "approval_gate": False,
        }
        for record in records
        for dependency in record["dependencies"]
    ]
    canonical = json.dumps({"nodes": nodes, "edges": edges}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    checksum = hashlib.sha256(canonical.encode()).hexdigest()
    return {
        "artifact_version": 1,
        "project": project_root.name,
        "generated_at": utc_now(),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
        "checksum": checksum,
    }


def refresh_workflow_graph(project_root: Path, *, records: list[dict] | None = None) -> dict:
    artifact = build_workflow_graph(project_root, records=records)
    write_json_atomic(project_root / "state" / WORKFLOW_GRAPH_FILE, artifact)
    return artifact


def refresh_task_snapshot(project_root: Path) -> dict:
    """Build and atomically write the task snapshot to state/tasks_snapshot.json."""
    records = collect_task_records(project_root)
    snapshot = build_task_snapshot(project_root, records=records)
    write_json_atomic(project_root / "state" / TASK_SNAPSHOT_FILE, snapshot)
    refresh_workflow_graph(project_root, records=records)
    return snapshot


def detect_task_cycles(records: list[dict]) -> list[list[str]]:
    """Return list of cycle paths detected in the dependency graph.

    Each cycle is a list of task_ids forming the loop (last element repeats first).
    Handles disconnected graphs correctly.
    """
    graph: dict[str, list[str]] = {r["task_id"]: r["dependencies"] for r in records}
    all_ids = set(graph)
    color: dict[str, str] = {}  # absent=white, grey=in-progress, black=done
    cycles: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        if color.get(node) == "black":
            return
        if color.get(node) == "grey":
            loop_start = path.index(node)
            cycles.append(path[loop_start:] + [node])
            return
        color[node] = "grey"
        for dep in graph.get(node, []):
            if dep in all_ids:
                dfs(dep, path + [node])
        color[node] = "black"

    for task_id in sorted(all_ids):
        if task_id not in color:
            dfs(task_id, [])

    return cycles


def lint_task_graph(project_root: Path) -> list[dict]:
    """Lint the task dependency graph. Return list of issue dicts.

    Each issue has keys: code, task_id, message.
    """
    records = collect_task_records(project_root)
    all_ids = {r["task_id"] for r in records}
    issues: list[dict] = []

    for r in records:
        if r.get("_parse_error"):
            issues.append({
                "code": "task_parse_failed",
                "task_id": r["task_id"],
                "message": f"Failed to parse front matter for {r['task_id']}: {r['_parse_error']}",
            })

    for r in records:
        if r.get("_parse_error"):
            continue
        for dep in r["dependencies"]:
            if dep not in all_ids:
                issues.append({
                    "code": "unknown_dependency",
                    "task_id": r["task_id"],
                    "message": f"Task {r['task_id']} depends on unknown task {dep!r}",
                })

    for cycle in detect_task_cycles(records):
        cycle_str = " -> ".join(cycle)
        issues.append({
            "code": "task_graph_cycle",
            "task_id": cycle[0],
            "message": f"Dependency cycle detected: {cycle_str}",
        })

    return issues


def generate_review_batches_with_policy(project_root: Path, *, dry_run: bool, capture_stdout: bool = False) -> list[dict]:
    try:
        policy = load_policy(POLICY_PATH)
    except (FileNotFoundError, ValueError) as exc:
        raise RuntimeError(f"Failed to load reviewer policy: {exc}") from exc

    if capture_stdout:
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            batches = generate_batches(project_root, policy, dry_run=dry_run)
        log_output = buffer.getvalue().strip()
        if log_output:
            print(log_output, file=sys.stderr)
        return batches

    return generate_batches(project_root, policy, dry_run=dry_run)


def maybe_trigger_review(project_root: Path, run_dir: Path, result_status: str, policy: dict) -> list[dict]:
    try:
        run = load_run(run_dir, project_root)
        if run is None:
            return []

        cadence_state = load_cadence_state(project_root)
        trigger = classify_run(run, policy)
        batches: list[dict] = []

        if trigger is not None:
            batches = generate_batches(project_root, policy)
        elif result_status == "success":
            cadence_state["successful_since_last_batch"] += 1
            cadence_batch_size = resolve_cadence_batch_size(policy)
            if cadence_state["successful_since_last_batch"] >= cadence_batch_size:
                batches = generate_batches(project_root, policy)

        if any(batch.get("trigger_type") == "cadence" for batch in batches):
            cadence_state["successful_since_last_batch"] = 0
            cadence_state["last_batch_generated_at"] = utc_now()

        emit_review_created_events(project_root, batches)
        save_cadence_state(project_root, cadence_state)
        return batches
    except Exception as exc:  # pragma: no cover - review generation must not fail worker loop
        print(f"Review trigger error for {run_dir}: {exc}", file=sys.stderr)
        return []


def build_review_prompt(project_root: Path, *, batch_id: str, reviewer: str, stubs: list[dict]) -> str:
    lines = [
        f"Review batch `{batch_id}` for project `{project_root.name}`.",
        "Inspect the listed runs and update each pending review decision stub in place.",
        "Only edit the decision stub JSON files listed below. Do not modify source code, task files, or run artifacts.",
        "Each stub must remain valid against `_system/contracts/review_decision.schema.json`.",
        "Set `decided_at` to the current UTC timestamp and replace `decision: pending` with one of:",
        "- approved",
        "- approved_with_notes",
        "- waived",
        "- rejected",
        "- needs_follow_up",
        "Populate `findings` for every stub. If you choose `needs_follow_up`, also populate `follow_up_actions` with actionable steps.",
        "",
        "Pending stubs:",
    ]

    for stub in stubs:
        run_dir = find_run_dir(project_root, str(stub.get("run_id") or ""))
        run_rel = run_dir.relative_to(project_root).as_posix() if run_dir is not None else "<missing-run>"
        lines.extend(
            [
                f"- Stub: `{stub['decision_file']}`",
                f"  Run: `{run_rel}`",
                f"  Batch manifest: `reviews/{batch_id}.json`",
                f"  Batch brief: `reviews/{batch_id}.md`",
                "  Inspect:",
                f"  - `{run_rel}/result.json`",
                f"  - `{run_rel}/report.md`",
                f"  - `{run_rel}/stdout.log`",
                f"  - `{run_rel}/stderr.log`",
            ]
        )

    lines.extend(
        [
            "",
            f"Use reviewer agent `{reviewer}` judgement conservatively. When in doubt, prefer `approved_with_notes` over `approved`.",
        ]
    )
    return "\n".join(lines)


def run_agent_prompt(project_root: Path, *, agent: str, prompt: str) -> tuple[subprocess.CompletedProcess[str], str, int]:
    agent_command = build_agent_command(REPO_ROOT, agent=agent, project_root=project_root, prompt=prompt)
    override_env = f"CLAW_AGENT_COMMAND_{agent.upper()}"
    override_raw = os.environ.get(override_env) or os.environ.get("CLAW_AGENT_COMMAND")
    timeout_seconds = max(1, int(os.environ.get("CLAW_AGENT_TIMEOUT_SECONDS") or agent_command.timeout_seconds))
    command = list(agent_command.command)
    prompt_input: str | None = None
    display = command_display(command)
    cwd = agent_command.cwd

    if override_raw:
        env_name = override_env if os.environ.get(override_env) else "CLAW_AGENT_COMMAND"
        override = parse_trusted_argv(override_raw, env_name=env_name)
        if override is None:
            raise ValueError(f"{env_name} must define a trusted argv command")
        command = override
        display = command_display(command)
        prompt_input = prompt
    elif agent_command.prompt_mode == "stdin":
        prompt_input = prompt

    try:
        completed = subprocess.run(
            command,
            input=prompt_input,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        completed = subprocess.CompletedProcess(
            command,
            124,
            exc.stdout or "",
            (exc.stderr or "") + f"\nTimed out after {timeout_seconds} seconds\n",
        )
    return completed, display, timeout_seconds


def maybe_execute_pending_reviews(project_root: Path) -> list[dict]:
    pending = load_pending_review_decisions(project_root)
    if not pending:
        return []

    grouped: dict[tuple[str, str], list[dict]] = {}
    for stub in pending:
        batch_id = str(stub.get("batch_id") or "ad-hoc")
        reviewer = str(stub.get("reviewer_agent") or "claude")
        grouped.setdefault((batch_id, reviewer), []).append(stub)

    executions: list[dict] = []
    for (batch_id, reviewer), stubs in sorted(grouped.items()):
        prompt = build_review_prompt(project_root, batch_id=batch_id, reviewer=reviewer, stubs=stubs)
        try:
            completed, command, timeout_seconds = run_agent_prompt(project_root, agent=reviewer, prompt=prompt)
        except Exception as exc:
            executions.append(
                {
                    "batch_id": batch_id,
                    "reviewer": reviewer,
                    "status": "error",
                    "error": str(exc),
                }
            )
            continue

        if completed.stdout:
            sys.stdout.write(completed.stdout)
        if completed.stderr:
            sys.stderr.write(completed.stderr)

        remaining = [
            stub
            for stub in load_pending_review_decisions(project_root)
            if str(stub.get("batch_id") or "ad-hoc") == batch_id and str(stub.get("reviewer_agent") or "claude") == reviewer
        ]
        executions.append(
            {
                "batch_id": batch_id,
                "reviewer": reviewer,
                "status": "resolved" if completed.returncode == 0 and not remaining else "pending",
                "command": command,
                "timeout_seconds": timeout_seconds,
                "returncode": completed.returncode,
                "resolved_count": len(stubs) - len(remaining),
                "remaining_count": len(remaining),
            }
        )

    return executions


def cmd_create_project(args: argparse.Namespace) -> int:
    command = ["bash", str(REPO_ROOT / "scripts" / "create_project.sh"), args.project_slug]
    if args.destination_root:
        command.append(args.destination_root)
    completed = run_command(command, cwd=REPO_ROOT)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


def substitute_project_slug_placeholders(project_root: Path, slug: str) -> None:
    for file_path in sorted(project_root.rglob("*")):
        if not file_path.is_file() or file_path.name == ".gitkeep":
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "{{PROJECT_SLUG}}" not in content:
            continue
        file_path.write_text(content.replace("{{PROJECT_SLUG}}", slug), encoding="utf-8")


def cmd_import_project(args: argparse.Namespace) -> int:
    slug = args.slug
    source_path = Path(args.path).expanduser().resolve()

    if not re.match(r"^[a-z0-9][a-z0-9-]*$", slug):
        print(
            json.dumps({"error": "Invalid slug. Use lowercase letters, digits, hyphens only."}),
            file=sys.stderr,
        )
        return 1

    project_root = REPO_ROOT / "projects" / slug
    if project_root.exists():
        print(
            json.dumps({"error": f"Project '{slug}' already exists at {project_root}"}),
            file=sys.stderr,
        )
        return 1

    excluded_dirs = {
        ".git",
        ".github",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        "dist",
        "build",
    }
    if source_path.is_dir():
        edit_scope = sorted(
            entry.name
            for entry in source_path.iterdir()
            if entry.is_dir() and entry.name not in excluded_dirs and not entry.name.startswith(".")
        )
    else:
        edit_scope = []

    template_root = REPO_ROOT / "projects" / "_template"
    shutil.copytree(str(template_root), str(project_root))
    substitute_project_slug_placeholders(project_root, slug)

    project_yaml = {
        "slug": slug,
        "source_path": str(source_path),
        "created_at": utc_now(),
    }
    state_dir = project_root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "project.yaml").write_text(
        yaml.safe_dump(project_yaml, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    workflow_path = project_root / "docs" / "WORKFLOW.md"
    workflow_content = workflow_path.read_text(encoding="utf-8").replace("{{PROJECT_SLUG}}", slug)
    if edit_scope:
        scope_yaml_lines = "\n".join(f"    - {directory}" for directory in edit_scope)
        workflow_content = workflow_content.replace(
            "  edit_scope: []",
            "  edit_scope:\n" + scope_yaml_lines,
        )
    workflow_path.write_text(workflow_content, encoding="utf-8")

    payload = {
        "status": "created",
        "slug": slug,
        "project_root": str(project_root),
        "source_path": str(source_path),
        "edit_scope": edit_scope,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    if args.awaiting_approval and not args.enqueue:
        raise SystemExit("--awaiting-approval requires --enqueue")

    run_dir = execute_run_task(REPO_ROOT, args.task_path, execute=False)
    project_root = run_dir.parent.parent.parent
    append_run_event(
        run_dir,
        "run_created",
        project_root=project_root,
        payload={
            "run_status": "created",
            "source": "cmd.run",
            "task_path": str(args.task_path),
            "executed": bool(args.execute),
        },
    )
    if args.enqueue:
        queue_state = "awaiting_approval" if args.awaiting_approval else "pending"
        payload = enqueue_run(run_dir, state=queue_state)
        append_routing_decision(project_root, run_dir, source="cmd.run")
        append_run_event(
            run_dir,
            "run_enqueued",
            project_root=project_root,
            payload={
                "queue_state": queue_state,
                "run_status": "queued",
                "source": "cmd.run",
            },
        )
        refresh_metrics_snapshot(project_root)
        print(
            json.dumps(
                {"status": "queued", "job_id": payload["job_id"], "run_path": payload["run_path"], "queue_state": queue_state},
                ensure_ascii=False,
            )
        )
        return 0
    if args.execute:
        guardrail_snapshot = preflight_guardrails(project_root, run_dir, source="cmd.run")
        if guardrail_snapshot.get("decision") == "pause":
            approval = pause_run_for_guardrail(project_root, run_dir, guardrail_snapshot, source="cmd.run")
            print(
                json.dumps(
                    {
                        "status": "awaiting_approval",
                        "run_dir": str(run_dir),
                        "approval_id": approval.get("approval_id"),
                        "guardrails": guardrail_snapshot,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        completed = run_command(["python3", str(REPO_ROOT / "scripts" / "execute_job.py"), str(run_dir)], cwd=REPO_ROOT)
        finalize_guardrails(project_root, run_dir, executed=completed.returncode in (0, 2))
        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        result, _result_error = load_json_status(run_dir / "result.json")
        event = append_run_event(
            run_dir,
            "run_finished",
            project_root=project_root,
            payload={
                "queue_state": "done",
                "run_status": result.get("status") or "success",
                "exit_code": 0 if (result.get("status") or "success") == "success" else 1,
                "source": "cmd.run",
            },
        )
        task_id = ""
        try:
            task_id = str(read_json(run_dir / "job.json").get("task", {}).get("id") or "")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            task_id = ""
        dispatch_registered_listeners(
            project_root,
            "run_finished",
            run_id=run_dir.name,
            status=str(result.get("status") or "success"),
            task_id=task_id,
            ts=event.get("recorded_at"),
        )
    refresh_metrics_snapshot(project_root)
    print(json.dumps({"status": "created", "run_dir": str(run_dir)}, ensure_ascii=False))
    return 0


def cmd_enqueue(args: argparse.Namespace) -> int:
    queue_state = "awaiting_approval" if args.awaiting_approval else "pending"
    run_dir = execute_run_task(REPO_ROOT, args.task_path, execute=False)
    payload = enqueue_run(run_dir, state=queue_state)
    project_root = run_dir.parent.parent.parent
    append_routing_decision(project_root, run_dir, source="cmd.enqueue")
    append_run_event(
        run_dir,
        "run_created",
        project_root=project_root,
        payload={"run_status": "created", "source": "cmd.enqueue", "task_path": str(args.task_path)},
    )
    append_run_event(
        run_dir,
        "run_enqueued",
        project_root=project_root,
        payload={"queue_state": queue_state, "run_status": "queued", "source": "cmd.enqueue"},
    )
    refresh_metrics_snapshot(project_root)
    print(json.dumps({"status": "queued", "job_id": payload["job_id"], "run_path": payload["run_path"], "queue_state": queue_state}, ensure_ascii=False))
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    queue = FileQueue(queue_root_for_project(project_root))
    claimed_count = 0
    review_policy = None
    lease_seconds = max(1, int(args.lease_seconds))
    heartbeat_interval_seconds = args.heartbeat_interval_seconds
    if heartbeat_interval_seconds is None or heartbeat_interval_seconds <= 0:
        heartbeat_interval_seconds = default_heartbeat_interval(lease_seconds)

    if not args.skip_review:
        try:
            review_policy = load_policy(POLICY_PATH)
        except (FileNotFoundError, ValueError) as exc:
            print(f"Review policy unavailable: {exc}", file=sys.stderr)

    while True:
        reclaimed = 0
        if args.stale_after_seconds is not None:
            reclaimed = queue.reclaim_stale_running(args.stale_after_seconds)

        try:
            claimed = queue.claim(lease_seconds=lease_seconds)
        except QueueEmpty:
            if claimed_count == 0:
                print(json.dumps({"status": "idle", "reclaimed": reclaimed}, ensure_ascii=False))
            return 0

        claimed_count += 1
        payload = queue.read_claimed(claimed)
        run_dir = (project_root / payload["run_path"]).resolve()
        append_run_event(
            run_dir,
            "job_claimed",
            project_root=project_root,
            payload={
                "queue_state": "running",
                "run_status": "running",
                "attempt_count": claimed.attempt_count,
                "worker_id": claimed.worker_id,
            },
        )
        guardrail_snapshot = preflight_guardrails(project_root, run_dir, source="worker")
        if guardrail_snapshot.get("decision") == "pause":
            approval = pause_run_for_guardrail(project_root, run_dir, guardrail_snapshot, source="worker", claimed=claimed)
            print(
                json.dumps(
                    {
                        "job_id": payload["job_id"],
                        "run_path": payload["run_path"],
                        "queue_state": "awaiting_approval",
                        "result_status": "guardrail_blocked",
                        "approval_id": approval.get("approval_id"),
                        "guardrails": guardrail_snapshot,
                        "reclaimed": reclaimed,
                    },
                    ensure_ascii=False,
                )
            )
            if args.once:
                return 0
            continue
        started_event = append_run_event(
            run_dir,
            "run_started",
            project_root=project_root,
            payload={
                "queue_state": "running",
                "run_status": "running",
                "attempt_count": claimed.attempt_count,
                "worker_id": claimed.worker_id,
            },
        )
        dispatch_registered_listeners(
            project_root,
            "run_started",
            run_id=run_dir.name,
            status="running",
            task_id=str(payload.get("task", {}).get("id") or ""),
            ts=started_event.get("recorded_at"),
        )
        completed, heartbeat_warnings = run_job_with_lease_heartbeat(
            queue,
            claimed,
            run_dir,
            lease_seconds=lease_seconds,
            heartbeat_interval_seconds=float(heartbeat_interval_seconds),
        )
        result_status = "failed"
        try:
            result_status = read_json(run_dir / "result.json").get("status") or result_status
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            result_status = "success" if completed.returncode == 0 else "failed"
        finalize_guardrails(project_root, run_dir, executed=completed.returncode in (0, 2))

        next_retry_at = None
        retry_backoff_seconds = None
        review_execution: list[dict] = []
        if completed.returncode == 0:
            queue.ack(claimed, result_status=result_status, exit_code=completed.returncode)
            queue_state = "done"
        elif completed.returncode == 2 and has_pending_approval_checkpoint(run_dir):
            queue.await_approval(claimed)
            queue_state = "awaiting_approval"
        else:
            error_text = summarize_worker_error(completed, heartbeat_warnings)
            if claimed.attempt_count < claimed.max_attempts:
                retry_backoff_seconds = compute_retry_backoff(
                    claimed.attempt_count,
                    base_seconds=args.retry_backoff_base_seconds,
                    max_seconds=args.retry_backoff_max_seconds,
                )
                next_retry_at = utc_after_seconds(retry_backoff_seconds)
                queue.fail(
                    claimed,
                    result_status=result_status,
                    exit_code=completed.returncode,
                    error=error_text,
                )
                queue.retry(
                    claimed.job_id,
                    next_retry_at=next_retry_at,
                    backoff_seconds=retry_backoff_seconds,
                )
                append_decision(
                    project_root,
                    "retry",
                    run_id=run_dir.name,
                    task_id=str(payload.get("task", {}).get("id") or ""),
                    reason_code="run_failed",
                    details={
                        "attempt_count": claimed.attempt_count,
                        "max_attempts": claimed.max_attempts,
                        "next_retry_at": next_retry_at,
                        "retry_backoff_seconds": retry_backoff_seconds,
                        "exit_code": completed.returncode,
                    },
                    outcome="queued",
                )
                queue_state = "retried"
            else:
                queue.dead_letter(
                    claimed,
                    result_status=result_status,
                    exit_code=completed.returncode,
                    error=error_text,
                )
                queue_state = "dead_letter"

        result_payload, _result_error = load_json_status(run_dir / "result.json")
        meta_payload, _meta_error = load_json_status(run_dir / "meta.json")
        delivery_snapshot = resolve_run_delivery(project_root, run_dir, meta=meta_payload, result=result_payload)
        finished_event = append_run_event(
            run_dir,
            "run_finished",
            project_root=project_root,
            payload={
                "queue_state": "pending" if queue_state == "retried" else queue_state,
                "result_status": result_status,
                "run_status": result_status,
                "exit_code": completed.returncode,
                "delivery_status": delivery_snapshot.get("status"),
            },
        )
        dispatch_registered_listeners(
            project_root,
            "run_finished",
            run_id=run_dir.name,
            status=result_status,
            task_id=str(payload.get("task", {}).get("id") or ""),
            ts=finished_event.get("recorded_at"),
        )
        if queue_state == "retried":
            append_run_event(
                run_dir,
                "job_retried",
                project_root=project_root,
                payload={
                    "queue_state": "pending",
                    "result_status": result_status,
                    "attempt_count": claimed.attempt_count,
                    "next_retry_at": next_retry_at,
                    "retry_backoff_seconds": retry_backoff_seconds,
                },
            )
        elif queue_state == "dead_letter":
            append_run_event(
                run_dir,
                "job_dead_letter",
                project_root=project_root,
                payload={
                    "queue_state": "dead_letter",
                    "result_status": result_status,
                    "attempt_count": claimed.attempt_count,
                },
            )

        if review_policy is not None and queue_state != "awaiting_approval":
            maybe_trigger_review(project_root, run_dir, result_status, review_policy)
            review_execution = maybe_execute_pending_reviews(project_root)

        refresh_metrics_snapshot(project_root)

        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        print(
            json.dumps(
                {
                    "job_id": payload["job_id"],
                    "run_path": payload["run_path"],
                    "queue_state": queue_state,
                    "result_status": result_status,
                    "exit_code": completed.returncode,
                    "attempt_count": claimed.attempt_count,
                    "max_attempts": claimed.max_attempts,
                    "next_retry_at": next_retry_at,
                    "retry_backoff_seconds": retry_backoff_seconds,
                    "heartbeat_warnings": heartbeat_warnings,
                    "review_execution": review_execution,
                    "reclaimed": reclaimed,
                },
                ensure_ascii=False,
            )
        )

        if args.once:
            return completed.returncode


def cmd_dispatch(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    completed = run_command(["python3", str(REPO_ROOT / "scripts" / "dispatch_hooks.py"), str(project_root)], cwd=REPO_ROOT)
    refresh_metrics_snapshot(project_root)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


def cmd_reconcile(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    completed = run_command(["python3", str(REPO_ROOT / "scripts" / "reconcile_hooks.py"), str(project_root)], cwd=REPO_ROOT)
    refresh_metrics_snapshot(project_root)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


def cmd_approve(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    queue = FileQueue(queue_root_for_project(project_root))
    if not queue.approve(args.run_id):
        print(json.dumps({"status": "not_found", "job_id": args.run_id}, ensure_ascii=False))
        return 1
    run_dir = find_run_dir(project_root, args.run_id)
    if run_dir is not None:
        append_run_event(
            run_dir,
            "approval_granted",
            project_root=project_root,
            payload={"queue_state": "pending", "run_status": "queued"},
        )
    refresh_metrics_snapshot(project_root)
    print(json.dumps({"status": "approved", "job_id": args.run_id, "queue_state": "pending"}, ensure_ascii=False))
    return 0


def cmd_resolve_checkpoint(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    run_dir = find_run_dir(project_root, args.run_id)
    if run_dir is None:
        print(f"Run not found: {args.run_id}", file=sys.stderr)
        return 1

    checkpoint_path = run_dir / "approval_checkpoint.json"
    if not checkpoint_path.is_file():
        print(f"Checkpoint not found: {checkpoint_path}", file=sys.stderr)
        return 1

    try:
        payload = read_json(checkpoint_path)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        print(f"Failed to read checkpoint: {exc}", file=sys.stderr)
        return 1

    if not isinstance(payload, dict):
        print("Checkpoint payload must be a JSON object.", file=sys.stderr)
        return 1

    decision = str(args.decision).strip().lower()
    if decision not in {"accept", "reject"}:
        print("Decision must be accept or reject.", file=sys.stderr)
        return 1

    notes = str(args.notes or "").strip() or None
    now = utc_now()

    payload["status"] = "resolved"
    payload["decision"] = decision
    payload["decision_notes"] = notes
    payload["resolved_at"] = now

    checkpoint_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    append_run_event(
        run_dir,
        "checkpoint_resolved",
        project_root=project_root,
        payload={
            "decision": decision,
            "notes": notes or "",
            "checkpoint_id": payload.get("checkpoint_id"),
        },
    )

    queue = FileQueue(queue_root_for_project(project_root))
    queue_action = "none"
    if decision == "accept":
        queue_action = "approved" if queue.approve(args.run_id) else "not_found"
    else:
        queue_action = "rejected" if queue.reject(args.run_id, error="checkpoint rejected") else "not_found"

    refresh_metrics_snapshot(project_root)
    queue_state = queue.queue_state(args.run_id)
    print(
        json.dumps(
            {
                "status": "resolved",
                "job_id": args.run_id,
                "decision": decision,
                "queue_action": queue_action,
                "queue_state": queue_state,
                "checkpoint_path": checkpoint_path.relative_to(run_dir).as_posix(),
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_reclaim(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    queue = FileQueue(queue_root_for_project(project_root))
    reclaimed = queue.reclaim_stale_running(args.stale_after_seconds)
    refresh_metrics_snapshot(project_root)
    print(json.dumps({"status": "reclaimed", "reclaimed": reclaimed, "queue_state": "pending"}, ensure_ascii=False))
    return 0


def resolve_run_dir(project_root: Path, run_id_or_path: str) -> Path | None:
    candidate = Path(run_id_or_path)
    if candidate.is_absolute() and candidate.is_dir():
        return candidate.resolve()
    if (project_root / run_id_or_path).is_dir():
        return (project_root / run_id_or_path).resolve()
    return find_run_dir(project_root, run_id_or_path)


def load_review_findings(path: Path) -> dict:
    try:
        payload = read_json(path)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def cmd_apply_patch(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    run_dir = resolve_run_dir(project_root, args.run_id_or_path)
    if run_dir is None:
        print(f"Run not found: {args.run_id_or_path}", file=sys.stderr)
        return 1

    patch_path = run_dir / "patch.diff"
    if not patch_path.is_file():
        print(f"patch.diff not found for run {run_dir.name}: {patch_path}", file=sys.stderr)
        return 1

    findings_path = run_dir / "review_findings.json"
    findings_payload = load_review_findings(findings_path)
    patch_text = patch_path.read_text(encoding="utf-8")
    findings = findings_payload.get("findings") if isinstance(findings_payload.get("findings"), list) else []
    severity = findings_payload.get("severity")
    recommendation = findings_payload.get("recommendation")
    warnings: list[str] = []
    if not findings_path.is_file():
        warnings.append("review_findings.json missing; severity and recommendation unavailable")

    payload = {
        "status": "dry_run",
        "project": project_root.name,
        "run_id": run_dir.name,
        "run_path": run_dir.relative_to(project_root).as_posix(),
        "patch_path": patch_path.relative_to(project_root).as_posix(),
        "severity": severity,
        "recommendation": recommendation,
        "findings_count": len(findings),
        "confirm_required": True,
    }
    if warnings:
        payload["warnings"] = warnings

    if not args.confirm:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if patch_text:
            if not patch_text.endswith("\n"):
                patch_text += "\n"
            sys.stdout.write(patch_text)
        return 0

    completed = subprocess.run(
        ["git", "-C", str(project_root), "apply", str(patch_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        error_text = (completed.stderr or completed.stdout or "").strip() or "git apply failed"
        print(error_text, file=sys.stderr)
        return completed.returncode or 1

    event = append_run_event(
        run_dir,
        "patch_applied",
        project_root=project_root,
        payload={
            "severity": severity,
            "recommendation": recommendation,
            "findings_count": len(findings),
            "patch_path": patch_path.relative_to(project_root).as_posix(),
        },
    )
    payload.update(
        {
            "status": "applied",
            "confirm_required": False,
            "event_id": event.get("event_id"),
            "recorded_at": event.get("recorded_at"),
        }
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    run_dir = find_run_dir(project_root, args.run_id)
    if run_dir is None:
        print(f"Run not found: {args.run_id}", file=sys.stderr)
        return 1

    payload = build_live_status_feed(project_root, run_dir, stream_limit=args.stream_limit)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    if args.all:
        project_roots = iter_projects_root()
    elif args.project_root:
        project_roots = [resolve_project_root(args.project_root)]
    else:
        project_roots = iter_projects_root()

    projects = [build_project_dashboard(project_root, recent_limit=args.recent, ready_limit=args.ready_limit) for project_root in project_roots]
    payload = {
        "projects": projects,
        "summary": {
            "project_count": len(projects),
            "pending_reviews": sum(project["pending_reviews"] for project in projects),
            "pending_approvals": sum(project["pending_approvals"] for project in projects),
            "pending_hooks": sum(project["pending_hooks"] for project in projects),
            "failed_hooks": sum(project["failed_hooks"] for project in projects),
            "pending_wakes": sum(int((project.get("wakes") or {}).get("pending", 0)) for project in projects),
            "retry_backlog": sum(project["retry_backlog"] for project in projects),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_scheduler(args: argparse.Namespace) -> int:
    if args.projects:
        project_roots = [resolve_project_root(project_root) for project_root in args.projects]
    else:
        project_roots = iter_projects_root()

    max_jobs = max(1, int(args.max_jobs))
    processed_jobs: list[dict] = []

    while len(processed_jobs) < max_jobs:
        progress = False
        for project_root in project_roots:
            if len(processed_jobs) >= max_jobs:
                break
            counts = queue_counts(project_root)
            if counts["pending"] == 0 and counts["running"] == 0:
                continue

            completed, worker_payload = run_worker_once(project_root, skip_review=args.skip_review)
            if worker_payload is None or worker_payload.get("status") == "idle":
                continue
            processed_jobs.append(
                {
                    "project": project_root.name,
                    "returncode": completed.returncode,
                    "job": worker_payload,
                }
            )
            progress = True

        if args.once or not progress:
            break

    payload = {
        "status": "processed" if processed_jobs else "idle",
        "processed_jobs": processed_jobs,
        "remaining_projects": [
            project_root.name
            for project_root in project_roots
            if queue_counts(project_root)["pending"] > 0 or queue_counts(project_root)["running"] > 0
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _parse_context_json(raw_value: str | None) -> dict | None:
    if not raw_value:
        return None
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"context-json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("context-json must be a JSON object")
    return payload


def _parse_resume_handle(raw_value: str | None, raw_json: str | None) -> dict | None:
    if raw_value and raw_json:
        raise ValueError("Use only one of --resume-handle or --resume-handle-json")
    if raw_json:
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"resume-handle-json is not valid JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("resume-handle-json must be a JSON object")
        return payload
    if raw_value:
        return {"handle": str(raw_value), "kind": "text"}
    return None


def _load_summary_text(summary: str | None, summary_file: str | None) -> str | None:
    if summary and summary_file:
        raise ValueError("Use only one of --summary or --summary-file")
    if summary_file:
        path = Path(summary_file).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"summary-file not found: {summary_file}")
        return path.read_text(encoding="utf-8")
    if summary is not None:
        return str(summary)
    return None


def _load_text_option(value: str | None, file_path: str | None, *, option_name: str) -> str | None:
    if value and file_path:
        raise ValueError(f"Use only one of --{option_name} or --{option_name}-file")
    if file_path:
        path = Path(file_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"{option_name}-file not found: {file_path}")
        return path.read_text(encoding="utf-8")
    if value is not None:
        return str(value)
    return None


def _read_project_state(project_root: Path) -> dict[str, Any]:
    state_path = project_root / "state" / "project.yaml"
    if not state_path.is_file():
        return {}
    loaded = yaml.safe_load(state_path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return {}
    return dict(loaded)


def _load_context_json_option(raw_value: str | None, file_path: str | None) -> dict | None:
    return _parse_context_json(_load_text_option(raw_value, file_path, option_name="context-json"))


def _resolve_openclaw_run_dir(project_root: Path, run_id_or_path: str | None) -> Path | None:
    raw_value = str(run_id_or_path or "").strip()
    if not raw_value:
        return None

    run_dir = resolve_run_dir(project_root, raw_value)
    if run_dir is None:
        raise FileExchangeError(f"Run not found: {raw_value}")

    resolved_run_dir = run_dir.resolve()
    if project_root not in resolved_run_dir.parents:
        raise FileExchangeError(f"Run does not belong to project root: {raw_value}")
    return resolved_run_dir


def _file_exchange_workspace_mode(project_root: Path, context_payload: dict | None, run_dir: Path | None) -> str:
    resolved = context_payload.get("resolved") if isinstance(context_payload, dict) and isinstance(context_payload.get("resolved"), dict) else {}
    context_mode = str(resolved.get("workspace_mode") or "").strip()
    if context_mode:
        return "project_root" if context_mode in {"shared_project", "project_root"} else context_mode

    if run_dir is not None:
        try:
            job = read_json(run_dir / "job.json")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            job = {}
        execution = job.get("execution") if isinstance(job.get("execution"), dict) else {}
        run_mode = str(execution.get("workspace_mode") or "").strip()
        if run_mode:
            return "project_root" if run_mode in {"shared_project", "project_root"} else run_mode

    project_state = _read_project_state(project_root)
    execution = project_state.get("execution") if isinstance(project_state.get("execution"), dict) else {}
    project_mode = str(execution.get("workspace_mode") or "").strip()
    if project_mode:
        return "project_root" if project_mode in {"shared_project", "project_root"} else project_mode
    return "project_root"


def _resolve_file_exchange_target(project_root: Path, context_payload: dict | None, run_id_or_path: str | None) -> tuple[Path, str, Path | None]:
    resolved = context_payload.get("resolved") if isinstance(context_payload, dict) and isinstance(context_payload.get("resolved"), dict) else {}
    context_project_root = str(resolved.get("project_root") or "").strip()
    if context_project_root and Path(context_project_root).expanduser().resolve() != project_root:
        raise FileExchangeError("Context project_root does not match the requested project path")

    run_dir = _resolve_openclaw_run_dir(project_root, run_id_or_path)
    workspace_mode = _file_exchange_workspace_mode(project_root, context_payload, run_dir)
    if workspace_mode == "project_root":
        return project_root, workspace_mode, run_dir

    if run_dir is None:
        raise FileExchangeError(
            f"Workspace mode '{workspace_mode}' requires --run so file exchange can target the active worktree"
        )

    if workspace_mode == "git_worktree":
        from execute_job import ensure_git_worktree  # noqa: PLC0415

        workspace = ensure_git_worktree(project_root, run_dir)
        return workspace.project_root, workspace.mode, run_dir

    if workspace_mode == "isolated_checkout":
        from execute_job import ensure_isolated_checkout  # noqa: PLC0415

        workspace = ensure_isolated_checkout(project_root, run_dir)
        return workspace.project_root, workspace.mode, run_dir

    raise FileExchangeError(f"Unsupported workspace mode for file exchange: {workspace_mode}")


def cmd_wake_enqueue(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    try:
        context = _parse_context_json(getattr(args, "context_json", None))
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    queue = WakeQueue(wake_root_for_project(project_root))
    try:
        payload = queue.enqueue(
            agent=args.agent,
            task_id=args.task_id,
            reason=args.reason,
            run_id=args.run_id,
            source=args.source,
            note=args.note,
            context=context,
        )
    except (TimeoutError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    session_summary = None
    session_store = SessionStore(sessions_root_for_project(project_root))
    session_payload = session_store.load_session(agent=args.agent, task_id=args.task_id)
    if session_payload:
        session_summary = _session_summary(session_payload)
        if session_summary:
            payload["session"] = session_summary

    refresh_metrics_snapshot(project_root)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_wake_status(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    payload = wake_snapshot(project_root, limit=args.limit)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_ask_human(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    run_dir = find_run_dir(project_root, args.run_id)
    if run_dir is None:
        print(f"Run not found: {args.run_id}", file=sys.stderr)
        return 1

    meta, _error = load_json_status(run_dir / "meta.json")
    approval = create_approval_request(
        project_root,
        run_id=args.run_id,
        task_id=str(meta.get("task_id") or ""),
        task_path=str(meta.get("task_path") or ""),
        source=args.source,
        reason=args.reason,
        requested_action=args.action,
    )
    append_approval_requested_decision(project_root, approval)
    event = append_run_event(
        run_dir,
        "approval_requested",
        project_root=project_root,
        payload={
            "approval_id": approval.get("approval_id"),
            "approval_status": approval.get("status"),
            "requested_action": approval.get("requested_action"),
            "source": approval.get("source"),
            "reason": approval.get("reason"),
        },
    )
    dispatch_registered_listeners(
        project_root,
        "approval_requested",
        run_id=args.run_id,
        status=str(approval.get("status") or ""),
        task_id=str(approval.get("task_id") or ""),
        ts=event.get("recorded_at"),
    )
    refresh_metrics_snapshot(project_root)
    print(json.dumps(approval, ensure_ascii=False, indent=2))
    return 0


def cmd_resolve_approval(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    payload = resolve_approval_request(project_root, args.approval_id, decision=args.decision, notes=args.notes or "")
    if payload is None:
        print(json.dumps({"status": "not_found", "approval_id": args.approval_id}, ensure_ascii=False))
        return 1
    refresh_metrics_snapshot(project_root)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_task_snapshot(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    snapshot = refresh_task_snapshot(project_root)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0


def cmd_task_claim(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    task_id = str(args.task_id).strip()
    agent = str(args.agent).strip()
    task_path = resolve_task_path_for_id(project_root, task_id)
    if task_path is None:
        print(json.dumps({"status": "not_found", "task_id": task_id, "agent": agent}, ensure_ascii=False))
        return 1

    front_matter, _body = read_front_matter(task_path)
    task_status = str(front_matter.get("status") or "todo").strip().lower()
    if task_status in TASK_DONE_STATUSES:
        print(
            json.dumps(
                {
                    "status": "task_done",
                    "task_id": task_id,
                    "agent": agent,
                    "task_status": task_status,
                },
                ensure_ascii=False,
            )
        )
        return 1

    store = TaskClaimStore(claims_root_for_project(project_root))
    task_rel = task_path.relative_to(project_root).as_posix()
    result = store.claim(
        task_id=task_id,
        agent=agent,
        reason=args.reason,
        note=args.note,
        task_path=task_rel,
        project=project_root.name,
    )
    status = result.get("status")
    if status not in {"claimed", "already_claimed"}:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    if task_status != "in_progress":
        update_task_status(task_path, "in_progress")

    wake_payload = None
    wake_error = None
    if not args.no_wake and status == "claimed":
        try:
            queue = WakeQueue(wake_root_for_project(project_root))
            wake_payload = queue.enqueue(
                agent=agent,
                task_id=task_id,
                reason="assignment",
                source="task_claim",
                note=args.note,
                context={"claim_status": status, "task_path": task_rel},
            )
        except Exception as exc:
            wake_error = str(exc)

    refresh_task_snapshot(project_root)

    claim_file = None
    if result.get("claim_file"):
        claim_file = (claims_root_for_project(project_root) / str(result["claim_file"]).strip()).relative_to(project_root).as_posix()

    session_summary = None
    if status in {"claimed", "already_claimed"}:
        session_store = SessionStore(sessions_root_for_project(project_root))
        session_payload = session_store.get_or_create(
            agent=agent,
            task_id=task_id,
            project=project_root.name,
            task_path=task_rel,
        )
        session_summary = _session_summary(session_payload)

    payload = {
        "status": status,
        "task_id": task_id,
        "agent": agent,
        "task_path": task_rel,
        "claim": result.get("claim"),
        "claim_file": claim_file,
        "wake": wake_payload,
        "wake_error": wake_error,
    }
    if session_summary:
        payload["session"] = session_summary
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_task_release(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    task_id = str(args.task_id).strip()
    agent = str(args.agent).strip()
    task_path = resolve_task_path_for_id(project_root, task_id)
    task_rel = task_path.relative_to(project_root).as_posix() if task_path else ""

    store = TaskClaimStore(claims_root_for_project(project_root))
    result = store.release(
        task_id=task_id,
        agent=agent,
        status=args.status,
        reason=args.reason,
        note=args.note,
        task_path=task_rel or None,
        project=project_root.name,
    )
    status = result.get("status")
    if status not in {"released", "blocked"}:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1

    if task_path is not None:
        front_matter, _body = read_front_matter(task_path)
        task_status = str(front_matter.get("status") or "todo").strip().lower()
        if task_status not in TASK_DONE_STATUSES:
            update_task_status(task_path, status)

    refresh_task_snapshot(project_root)

    claim_file = None
    if result.get("claim_file"):
        claim_file = (claims_root_for_project(project_root) / str(result["claim_file"]).strip()).relative_to(project_root).as_posix()

    payload = {
        "status": status,
        "task_id": task_id,
        "agent": agent,
        "task_path": task_rel,
        "claim": result.get("claim"),
        "claim_file": claim_file,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_session_status(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    store = SessionStore(sessions_root_for_project(project_root))
    payload = store.load_session(agent=args.agent, task_id=args.task_id)
    if payload is None:
        print(json.dumps({"status": "not_found", "agent": args.agent, "task_id": args.task_id}, ensure_ascii=False))
        return 1
    payload["shared_files"] = _session_docs_summary(project_root, str(args.task_id).strip())
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_session_update(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    task_id = str(args.task_id).strip()
    agent = str(args.agent).strip()
    task_path = resolve_task_path_for_id(project_root, task_id)
    task_rel = task_path.relative_to(project_root).as_posix() if task_path else ""

    try:
        resume_handle = _parse_resume_handle(args.resume_handle, args.resume_handle_json)
        summary_text = _load_summary_text(args.summary, args.summary_file)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    if resume_handle is None and summary_text is None and not args.note and not args.run_id and not args.run_path:
        print(json.dumps({"error": "No session updates provided"}, ensure_ascii=False), file=sys.stderr)
        return 1

    store = SessionStore(sessions_root_for_project(project_root))
    payload = store.update(
        agent=agent,
        task_id=task_id,
        resume=resume_handle,
        summary=summary_text,
        note=args.note,
        run_id=args.run_id,
        run_path=args.run_path,
        project=project_root.name,
        task_path=task_rel,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_session_reset(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    task_id = str(args.task_id).strip()
    agent = str(args.agent).strip()
    task_path = resolve_task_path_for_id(project_root, task_id)
    task_rel = task_path.relative_to(project_root).as_posix() if task_path else ""

    store = SessionStore(sessions_root_for_project(project_root))
    payload = store.reset(
        agent=agent,
        task_id=task_id,
        note=args.note,
        project=project_root.name,
        task_path=task_rel,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_session_rotate(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    task_id = str(args.task_id).strip()
    agent = str(args.agent).strip()
    task_path = resolve_task_path_for_id(project_root, task_id)
    task_rel = task_path.relative_to(project_root).as_posix() if task_path else ""

    store = SessionStore(sessions_root_for_project(project_root))
    payload = store.rotate(
        agent=agent,
        task_id=task_id,
        note=args.note,
        project=project_root.name,
        task_path=task_rel,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_session_files(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    store = SessionDocsStore(session_docs_root_for_project(project_root))
    try:
        payload = store.list_documents(task_id=args.task_id)
    except ValueError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    if payload is None:
        print(json.dumps({"status": "not_found", "task_id": args.task_id}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_session_file_put(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    store = SessionDocsStore(session_docs_root_for_project(project_root))
    try:
        payload = store.put_document(
            task_id=args.task_id,
            relative_path=args.relative_path,
            source_file=Path(args.source_file),
            project=project_root.name,
            author=args.author,
            note=args.note,
        )
    except (FileNotFoundError, FileExchangeError, ValueError, TimeoutError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_session_file_fetch(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    store = SessionDocsStore(session_docs_root_for_project(project_root))
    try:
        payload = store.fetch_document(
            task_id=args.task_id,
            relative_path=args.relative_path,
            output_file=Path(args.output_file),
        )
    except (FileNotFoundError, FileExchangeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _org_graph_error_payload(exc: OrgGraphError) -> dict:
    return {
        "status": "error",
        "reason_code": exc.code,
        "message": str(exc),
        "details": exc.details,
    }


def cmd_org_graph(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    try:
        graph = load_org_graph(REPO_ROOT, project_root)
    except OrgGraphError as exc:
        print(json.dumps(_org_graph_error_payload(exc), ensure_ascii=False), file=sys.stderr)
        return 1
    payload = {"status": "ok", "org_graph": graph}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_task_delegate(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    parent_task_path = resolve_task_path_for_id(project_root, args.task_id)
    if parent_task_path is None:
        print(json.dumps({"status": "not_found", "task_id": args.task_id}, ensure_ascii=False))
        return 1

    reason = str(args.reason or "").strip()
    if not reason:
        print(json.dumps({"status": "invalid", "reason_code": "missing_reason"}, ensure_ascii=False), file=sys.stderr)
        return 1

    delegator = str(args.agent).strip()
    delegatee = str(args.assignee).strip()
    try:
        graph = load_org_graph(REPO_ROOT, project_root)
    except OrgGraphError as exc:
        print(json.dumps(_org_graph_error_payload(exc), ensure_ascii=False), file=sys.stderr)
        return 1

    check = validate_delegation(graph, delegator=delegator, delegatee=delegatee)
    if not check.allowed:
        payload = {
            "status": "forbidden",
            "reason_code": check.reason_code,
            "details": check.details or {},
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    tags = [tag for tag in (args.tag or []) if str(tag).strip()]
    task_path = create_delegated_task(
        project_root,
        parent_task_path=parent_task_path,
        delegated_by=delegator,
        delegated_to=delegatee,
        reason=reason,
        note=args.note,
        title=args.title,
        spec_ref=args.spec,
        priority=args.priority,
        review_policy=args.review_policy,
        needs_review=True if args.needs_review else None,
        tags=tags or None,
        delegation_type="delegation",
    )
    refresh_task_snapshot(project_root)

    payload = {
        "status": "delegated",
        "task_id": task_path.stem,
        "task_path": task_path.relative_to(project_root).as_posix(),
        "parent_task_id": str(args.task_id),
        "delegated_by": delegator,
        "delegated_to": delegatee,
        "reason": reason,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_task_escalate(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    parent_task_path = resolve_task_path_for_id(project_root, args.task_id)
    if parent_task_path is None:
        print(json.dumps({"status": "not_found", "task_id": args.task_id}, ensure_ascii=False))
        return 1

    reason = str(args.reason or "").strip()
    if not reason:
        print(json.dumps({"status": "invalid", "reason_code": "missing_reason"}, ensure_ascii=False), file=sys.stderr)
        return 1

    front_matter, _body = read_front_matter(parent_task_path)
    task_status = str(front_matter.get("status") or "todo").strip().lower()
    if task_status not in TASK_BLOCKED_STATUSES:
        payload = {
            "status": "invalid",
            "reason_code": "task_not_blocked",
            "task_status": task_status,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    agent = str(args.agent).strip()
    store = TaskClaimStore(claims_root_for_project(project_root))
    claim = store.load_claim(str(args.task_id))
    if isinstance(claim, dict):
        owner = str(claim.get("owner") or "").strip()
        if owner and owner != agent:
            payload = {
                "status": "forbidden",
                "reason_code": "claim_owner_mismatch",
                "owner": owner,
                "agent": agent,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1

    try:
        graph = load_org_graph(REPO_ROOT, project_root)
    except OrgGraphError as exc:
        print(json.dumps(_org_graph_error_payload(exc), ensure_ascii=False), file=sys.stderr)
        return 1

    try:
        chain = escalation_chain(graph, agent=agent)
    except OrgGraphError as exc:
        print(json.dumps(_org_graph_error_payload(exc), ensure_ascii=False), file=sys.stderr)
        return 1

    if not chain:
        payload = {
            "status": "invalid",
            "reason_code": "no_manager",
            "agent": agent,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1

    manager = chain[0]
    task_path = create_delegated_task(
        project_root,
        parent_task_path=parent_task_path,
        delegated_by=agent,
        delegated_to=manager,
        reason=reason,
        note=args.note,
        title=args.title,
        spec_ref=args.spec,
        priority=args.priority,
        review_policy=args.review_policy,
        needs_review=True if args.needs_review else None,
        tags=["escalation"],
        delegation_type="escalation",
        extra_front_matter={"escalation_chain": chain},
    )
    refresh_task_snapshot(project_root)

    payload = {
        "status": "escalated",
        "task_id": task_path.stem,
        "task_path": task_path.relative_to(project_root).as_posix(),
        "parent_task_id": str(args.task_id),
        "agent": agent,
        "manager": manager,
        "chain": chain,
        "reason": reason,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_inbox(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    payload = build_agent_inbox(project_root, agent=args.agent, limit=args.limit)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_workflow_graph(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    artifact = refresh_workflow_graph(project_root)
    print(json.dumps(artifact, ensure_ascii=False, indent=2))
    return 0


def cmd_decision_log(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    records = read_decisions(project_root, last_n=args.last)
    for record in records:
        print(format_decision_for_display(record))
    return 0


def cmd_task_lint(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    issues = lint_task_graph(project_root)
    payload = {
        "project": project_root.name,
        "issue_count": len(issues),
        "issues": issues,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if issues else 0


def cmd_task_graph_lint(args: argparse.Namespace) -> int:
    """Extended task graph lint: cycles, unknown deps, parse errors, and file-overlap warnings."""
    project_root = resolve_project_root(args.project_root)
    records = collect_task_records(project_root)
    issues = lint_task_graph(project_root)
    issues.extend(check_file_overlap(records))
    blocking_count = sum(
        1
        for issue in issues
        if issue.get("severity", "fail") == "fail" or issue.get("code") in ("task_graph_cycle", "unknown_dependency")
    )
    warning_count = sum(1 for issue in issues if issue.get("severity") == "warning")
    payload = {
        "project": project_root.name,
        "issue_count": len(issues),
        "blocking_count": blocking_count,
        "warning_count": warning_count,
        "issues": issues,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if blocking_count else 0


def get_epic_tasks(project_root: Path, epic_tag: str | None) -> list[dict]:
    """Return task records filtered by epic tag.

    If epic_tag is None or empty, returns all tasks.
    """
    records = collect_task_records(project_root)
    if not epic_tag:
        return records

    filtered = []
    for r in records:
        task_path = Path(r.get("task_path", ""))
        if not task_path.is_file():
            continue
        try:
            text = task_path.read_text(encoding="utf-8")
            fm_match = re.match(r'\A---\n(.*?)\n---\n?', text, re.DOTALL)
            if fm_match:
                fm = yaml.safe_load(fm_match.group(1))
                if isinstance(fm, dict):
                    task_epic = str(fm.get("epic", ""))
                    if task_epic == str(epic_tag):
                        filtered.append(r)
        except Exception:
            continue
    return filtered


def _epic_completion_summary(project_root: Path, epic_tag: str) -> dict:
    tasks = get_epic_tasks(project_root, epic_tag)
    total = len(tasks)
    done = sum(1 for t in tasks if t.get("status") in ("done", "accepted"))
    return {"total": total, "done": done, "complete": done == total and total > 0}


def cmd_orchestrate(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    max_steps = max(1, int(args.max_steps))
    scope = getattr(args, "scope", None)
    epic_scope: str | None = None
    if scope and scope.startswith("epic:"):
        epic_scope = scope[len("epic:"):]
    failure_budget = max(1, int(args.failure_budget))
    steps = 0
    accepted_runs: list[str] = []
    last_status = "idle"
    budget_state = load_orchestration_state(project_root)

    # Load workflow contract (optional — missing contract is not an error)
    workflow_contract = load_workflow_contract(project_root)

    # Refresh task snapshot and abort on blocking task graph issues.
    refresh_task_snapshot(project_root)
    graph_issues = lint_task_graph(project_root)
    blocking_issues = [i for i in graph_issues if i["code"] in ("task_graph_cycle", "unknown_dependency")]
    if blocking_issues:
        _openclaw_error(blocking_issues[0]["message"], blocking_issues[0]["code"])
        return 1

    while steps < max_steps:
        # Check epic scope completion
        if epic_scope:
            scope_tasks = get_epic_tasks(project_root, epic_scope)
            scope_done = all(t.get("status") in ("done", "accepted") for t in scope_tasks)
            if scope_tasks and scope_done:
                last_status = "scope_complete"
                break

        dashboard = build_project_dashboard(project_root, recent_limit=args.recent, ready_limit=args.ready_limit)
        if dashboard["pending_approvals"] > 0 or dashboard["queue"]["awaiting_approval"] > 0:
            last_status = "awaiting_approval"
            break
        if dashboard["pending_reviews"] > 0:
            last_status = "awaiting_review"
            break

        queue_snapshot = dashboard["queue"]
        if queue_snapshot["pending"] == 0 and queue_snapshot["running"] == 0:
            ready_tasks = select_ready_tasks(project_root, limit=1)
            if not ready_tasks:
                last_status = "idle"
                state = load_orchestration_state(project_root)
                if state.get("consecutive_failures", 0) > 0:
                    state["consecutive_failures"] = 0
                    state["last_decision"] = "all_done"
                    state["last_updated_at"] = utc_now()
                    save_orchestration_state(project_root, state)
                break
            next_task = ready_tasks[0]

            # Enforce allowed_agents from workflow contract
            if workflow_contract and workflow_contract.scope.allowed_agents:
                task_agent = next_task.get("preferred_agent", "auto")
                allowed = set(workflow_contract.scope.allowed_agents)
                if task_agent not in allowed and task_agent != "auto":
                    payload = {
                        "status": "contract_violation",
                        "reason_code": "contract_violation",
                        "project": project_root.name,
                        "steps": steps,
                        "accepted_runs": accepted_runs,
                        "task_id": next_task["task_id"],
                        "agent": task_agent,
                        "allowed_agents": sorted(allowed),
                        "message": (
                            f"Task {next_task['task_id']} requires agent '{task_agent}' "
                            f"but WORKFLOW.md only allows {sorted(allowed)}"
                        ),
                    }
                    print(json.dumps(payload, ensure_ascii=False, indent=2))
                    return 1

            update_task_status(next_task["task_path"], "in_progress")
            enqueue_task_path(next_task["task_path"])

        completed, worker_payload = run_worker_once(project_root, skip_review=args.skip_review)
        if worker_payload is None or worker_payload.get("status") == "idle":
            last_status = "idle"
            break

        run_path = worker_payload.get("run_path")
        run_dir = (project_root / str(run_path)).resolve() if run_path else None
        result_status = str(worker_payload.get("result_status") or "unknown")
        steps += 1

        if run_dir is None or not run_dir.is_dir():
            last_status = "error"
            break
        if worker_payload.get("queue_state") == "awaiting_approval":
            last_status = "awaiting_approval"
            break

        decision = evaluate_run_decision(project_root, run_dir, result_status=result_status)
        budget_state = record_orchestration_decision(project_root, run_id=run_dir.name, decision=decision, failure_budget=failure_budget)
        if decision["decision"] == "accept":
            accepted_runs.append(run_dir.name)
            last_status = "accepted"
            continue
        if decision["decision"] == "awaiting_review":
            last_status = "awaiting_review"
            break
        if decision["decision"] == "ask_human":
            last_status = "failure_budget_exhausted" if budget_state["failure_budget_exhausted"] else "awaiting_approval"
            break

    _STATUS_REASON_CODE: dict[str, str | None] = {
        "idle": "queue_empty",
        "awaiting_approval": "approval_pending",
        "awaiting_review": "review_pending",
        "failure_budget_exhausted": "failure_budget_exhausted",
        "contract_violation": "contract_violation",
        "scope_complete": None,
        "error": "ERROR",
        "accepted": None,
    }
    payload = {
        "status": last_status,
        "reason_code": _STATUS_REASON_CODE.get(last_status),
        "project": project_root.name,
        "steps": steps,
        "accepted_runs": accepted_runs,
        "orchestration": budget_state,
        "scope": scope,
        "scope_completion": (
            {
                "epic": epic_scope,
                **_epic_completion_summary(project_root, epic_scope),
            }
            if epic_scope else None
        ),
        "pending_reviews": load_pending_review_decisions(project_root),
        "pending_approvals": load_approval_requests(project_root, state="pending"),
        "ready_tasks": [
            {
                "task_id": task["task_id"],
                "title": task["title"],
                "selected_agent": task.get("selected_agent"),
                "task_path": task["task_path_rel"],
            }
            for task in select_ready_tasks(project_root, limit=args.ready_limit)
        ],
        "task_graph_issues": graph_issues,
        "contract": contract_summary(workflow_contract),
        "test_command": workflow_contract.commands.test,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if last_status != "error" else 1


def cmd_run_checks(args: argparse.Namespace) -> int:
    """Run a registered project command from the WORKFLOW.md commands registry."""
    project_argument = getattr(args, "project", None) or getattr(args, "project_root", None)
    if not project_argument:
        print(json.dumps({"error": "project is required"}), file=sys.stderr)
        return 1

    try:
        project_root = resolve_project_root_or_slug(project_argument)
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    contract = load_workflow_contract(project_root)
    check_type = getattr(args, "type", "test") or "test"
    command_map = {
        "test": contract.commands.test,
        "lint": contract.commands.lint,
        "build": contract.commands.build,
        "smoke": contract.commands.smoke,
    }
    command = command_map.get(check_type, "")

    if not command:
        print(
            json.dumps(
                {
                    "status": "skipped",
                    "type": check_type,
                    "message": f"No '{check_type}' command registered in WORKFLOW.md commands",
                },
                ensure_ascii=False,
            )
        )
        return 0

    command_cwd = project_root.parent.parent
    result = subprocess.run(
        command,
        shell=True,
        cwd=str(command_cwd),
        capture_output=False,
        check=False,
    )
    print(
        json.dumps(
            {
                "status": "success" if result.returncode == 0 else "failed",
                "type": check_type,
                "command": command,
                "returncode": result.returncode,
            },
            ensure_ascii=False,
        )
    )
    return result.returncode


def cmd_review_batch(args: argparse.Namespace) -> int:
    if args.all:
        try:
            projects = iter_projects_root()
        except FileNotFoundError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        if not projects:
            print("No projects found.")
            return 0

        try:
            for project_root in projects:
                print(f"Project: {project_root.name}")
                generate_review_batches_with_policy(project_root, dry_run=args.dry_run)
                refresh_metrics_snapshot(project_root)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        return 0

    if not args.project_root:
        print("usage: claw review-batch [--dry-run] [--all] PROJECT_ROOT", file=sys.stderr)
        return 2

    try:
        project_root = resolve_project_root(args.project_root)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        print(f"Project: {project_root.name}")
        generate_review_batches_with_policy(project_root, dry_run=args.dry_run)
        refresh_metrics_snapshot(project_root)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _preview_agent_command(repo_root: Path, agent: str, project_root: Path, workspace_mode: str) -> dict:
    """Build a representative command preview without actually running anything."""
    import shlex as _shlex

    import yaml as _yaml

    _AGENT_DEFAULTS: dict[str, dict] = {
        "codex": {
            "command": "codex",
            "args": "exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -C {project_root}",
            "prompt_mode": "arg",
            "cwd": "project_root",
            "default_timeout_seconds": 3600,
        },
        "claude": {
            "command": "claude",
            "args": "-p --permission-mode bypassPermissions --output-format text",
            "prompt_mode": "arg",
            "cwd": "project_root",
            "default_timeout_seconds": 3600,
        },
    }

    registry: dict = {}
    agents_registry_path = repo_root / "_system" / "registry" / "agents.yaml"
    if agents_registry_path.is_file():
        try:
            loaded = _yaml.safe_load(agents_registry_path.read_text(encoding="utf-8")) or {}
            agents_raw = loaded.get("agents", {}) if isinstance(loaded, dict) else {}
            registry = {str(k): v for k, v in agents_raw.items() if isinstance(v, dict)}
        except Exception:
            pass

    agent_config = dict(_AGENT_DEFAULTS.get(agent, {"command": agent, "args": "", "prompt_mode": "arg", "cwd": "project_root", "default_timeout_seconds": 3600}))
    agent_config.update(registry.get(agent, {}))

    executable = str(agent_config.get("command") or agent).strip() or agent
    args_template = str(agent_config.get("args") or "").strip()
    prompt_mode = str(agent_config.get("prompt_mode") or "arg").strip().lower() or "arg"
    cwd_mode = str(agent_config.get("cwd") or "project_root").strip().lower() or "project_root"
    timeout_seconds = int(agent_config.get("default_timeout_seconds") or 3600)

    workspace_root_preview = "<worktree_root>" if workspace_mode in {"git_worktree", "isolated_checkout"} else str(project_root)

    args_list: list[str] = []
    if args_template:
        try:
            rendered = args_template.format(
                project_root=project_root,
                source_project_root=project_root,
                run_dir="<run_dir>",
                workspace_root=workspace_root_preview,
            )
            args_list = _shlex.split(rendered)
        except (KeyError, ValueError):
            args_list = _shlex.split(args_template)

    if cwd_mode == "workspace_root":
        cwd_preview = workspace_root_preview
    elif cwd_mode == "run_dir":
        cwd_preview = "<run_dir>"
    else:
        cwd_preview = str(project_root)

    parts = [executable, *args_list]
    if prompt_mode == "arg":
        parts.append("<prompt>")
        command_str = " ".join(parts)
    else:
        command_str = " ".join(parts) + " <<< <prompt_file>"

    return {
        "command": command_str,
        "cwd": cwd_preview,
        "prompt_mode": prompt_mode,
        "timeout_seconds": timeout_seconds,
    }


def cmd_launch_plan(args: argparse.Namespace) -> int:
    try:
        plan = plan_task_run(REPO_ROOT, args.task_path)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    plan_dict = plan_to_dict(plan)
    plan_dict["command_preview"] = _preview_agent_command(
        REPO_ROOT,
        agent=plan.routing.selected_agent,
        project_root=plan.project_root,
        workspace_mode=plan.execution.workspace_mode,
    )

    # Warn if spec references files outside workflow edit_scope
    contract = load_workflow_contract(plan.project_root)
    scope_warnings: list[str] = []
    if contract and contract.scope.edit_scope:
        spec_path_str = plan_dict.get("spec_path", "")
        spec_path = Path(spec_path_str) if spec_path_str else None
        if spec_path and spec_path.is_file():
            spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
            path_re = re.compile(r'`([^`]+/[^`]+\.[a-z]+)`')
            for m in path_re.finditer(spec_text):
                file_path = m.group(1)
                top_dir = Path(file_path).parts[0] if Path(file_path).parts else None
                if top_dir and top_dir not in contract.scope.edit_scope:
                    scope_warnings.append(
                        f"Spec references '{file_path}' outside edit_scope {sorted(contract.scope.edit_scope)}"
                    )
    if scope_warnings:
        plan_dict["scope_warnings"] = scope_warnings

    print(json.dumps(plan_dict, ensure_ascii=False, indent=2))
    return 0


def cmd_workflow_validate(args: argparse.Namespace) -> int:
    """Validate the workflow contract for a project."""
    try:
        project_root = resolve_project_root(args.project_root)
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    contract = load_workflow_contract(project_root)
    if contract.source == "defaults":
        payload = {
            "status": "no_contract",
            "project": project_root.name,
            "message": "No docs/WORKFLOW.md found — defaults apply",
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    summary = contract_summary(contract)
    errors = list(summary.get("contract_errors", []))

    if contract.scope.allowed_agents:
        from _system.engine.workflow_contract import VALID_AGENTS
        unknown = set(contract.scope.allowed_agents) - VALID_AGENTS
        if unknown:
            errors.append(f"Unknown agent(s) in allowed_agents: {sorted(unknown)}")

    payload = {
        "status": "valid" if not errors else "invalid",
        "project": project_root.name,
        "contract_version": contract.contract_version,
        "allowed_agents": sorted(contract.scope.allowed_agents),
        "edit_scope": sorted(contract.scope.edit_scope),
        "failure_budget": contract.retry_policy.failure_budget,
        "commands": {
            "test": contract.commands.test,
            "lint": contract.commands.lint,
            "build": contract.commands.build,
            "smoke": contract.commands.smoke,
        },
        "guardrails": contract_summary(contract).get("guardrails"),
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def _resolve_guardrail_project(project_argument: str) -> Path:
    project_root = REPO_ROOT / "projects" / project_argument
    if project_root.is_dir() and (project_root / "state" / "project.yaml").is_file():
        return project_root.resolve()
    return resolve_project_root(project_argument)


def cmd_guardrail_check(args: argparse.Namespace) -> int:
    diff_path = Path(args.diff_path).expanduser().resolve()
    if not diff_path.is_file():
        print(json.dumps({"error": f"Diff file not found: {diff_path}"}), file=sys.stderr)
        return 1

    try:
        project_root = _resolve_guardrail_project(args.project)
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    diff_text = diff_path.read_text(encoding="utf-8", errors="replace")
    workflow_contract = load_workflow_contract(project_root)
    edit_scope = list(workflow_contract.scope.edit_scope)
    allowed_slugs = sorted(
        entry.name
        for entry in (REPO_ROOT / "projects").iterdir()
        if entry.is_dir()
    )

    result = run_guardrails(
        diff_text=diff_text,
        allowed_project_slugs=allowed_slugs,
        edit_scope=edit_scope,
        project_root_name=project_root.name,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


# ── openclaw subcommands ──────────────────────────────────────────────────────


def _openclaw_error(message: str, code: str = "ERROR") -> None:
    """Write a structured JSON error envelope to stderr."""
    json.dump(build_error_envelope(code, message), sys.stderr, ensure_ascii=False)
    sys.stderr.write("\n")


def _binding_resume_matches(session_payload: dict | None, resolved: dict[str, Any] | None) -> bool:
    if not isinstance(session_payload, dict) or not isinstance(resolved, dict):
        return False
    binding = session_payload.get("binding") if isinstance(session_payload.get("binding"), dict) else {}
    stored_project = str(binding.get("project") or "").strip()
    stored_agent = str(binding.get("agent") or "").strip()
    stored_branch = str(binding.get("branch") or "").strip()
    current_project = str(resolved.get("project") or "").strip()
    current_agent = str(resolved.get("agent") or "").strip()
    current_branch = str(resolved.get("branch") or "").strip()
    return (
        stored_project == current_project
        and stored_agent == current_agent
        and stored_branch == current_branch
    )


def _resolve_operator_continuation(
    context_payload: dict[str, Any],
    session_payload: dict[str, Any] | None,
    *,
    session_mode: str,
) -> dict[str, Any]:
    payload = {
        "mode": "fresh",
        "source": "missing_session",
        "resume": None,
        "resume_line": None,
        "session_id": session_payload.get("session_id") if isinstance(session_payload, dict) else None,
    }
    if session_mode == "reset":
        payload["source"] = "explicit_reset"
        return payload
    if session_mode == "new-thread":
        payload["source"] = "explicit_new_thread"
        return payload
    if not isinstance(session_payload, dict):
        return payload

    resume = session_payload.get("resume") if isinstance(session_payload.get("resume"), dict) else None
    if str(session_payload.get("status") or "").strip() != "active":
        payload["source"] = "session_reset"
        return payload
    if not resume or not str(resume.get("handle") or "").strip():
        payload["source"] = "missing_resume"
        return payload
    if not _binding_resume_matches(session_payload, context_payload.get("resolved")):
        payload["source"] = "context_changed"
        return payload

    session_summary = _operator_session_summary(session_payload) or {}
    payload["mode"] = "resume"
    payload["source"] = "stored_session"
    payload["resume"] = resume
    payload["resume_line"] = session_summary.get("resume_line")
    return payload


def cmd_openclaw_status(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.project_path)
    except FileNotFoundError as exc:
        _openclaw_error(str(exc), "NOT_FOUND")
        return 1

    max_recent = getattr(args, "recent", 5)
    snapshot = refresh_metrics_snapshot(project_root, recent_limit=max(max_recent, 20))
    dashboard = build_project_dashboard(project_root, recent_limit=max_recent, ready_limit=3)
    wakes = wake_snapshot(project_root, limit=3)

    payload = {
        "project": project_root.name,
        "queue": snapshot["queue"],
        "wakes": {
            **wakes["counts"],
            "pending_items": wakes["pending"],
        },
        "recent_runs": snapshot["recent_runs"][:max_recent],
        "delivery": {
            "pending": snapshot["delivery"]["pending"],
            "delivered": snapshot["delivery"]["delivered"],
            "failed": snapshot["delivery"]["failed"],
            "missing": snapshot["delivery"]["missing"],
            "runs": [
                {
                    "run_id": run["run_id"],
                    "status": run.get("delivery", {}).get("status"),
                    "hook_status": run.get("delivery", {}).get("hook_status"),
                    "task_id": run.get("task_id"),
                }
                for run in snapshot["recent_runs"][:max_recent]
                if (run.get("delivery") or {}).get("required")
            ],
        },
        "pending_reviews": snapshot["reviews"]["pending_decisions"],
        "pending_hooks": snapshot["hooks"]["pending"],
        "failed_hooks": snapshot["hooks"]["failed"],
        "pending_approvals": dashboard["pending_approvals"],
        "retry_backlog": dashboard["retry_backlog"],
        "current_run": dashboard["current_run"],
        "guardrails": dashboard.get("guardrails"),
        "recent_failures": dashboard["recent_failures"],
        "ready_tasks": dashboard["ready_tasks"],
        "metrics": {
            "updated_at": snapshot["updated_at"],
            "runs": snapshot["runs"],
            "reviews": snapshot["reviews"],
        },
        "contract": contract_summary(load_workflow_contract(project_root)),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


_VALID_TRIGGER_TYPES = {"manual", "schedule", "webhook"}


def _parse_trigger_envelope(trigger_json: str) -> tuple[dict, str]:
    """Parse and minimally validate a trigger envelope JSON string."""
    try:
        envelope = json.loads(trigger_json)
    except json.JSONDecodeError as exc:
        return {}, f"trigger-json is not valid JSON: {exc}"
    if not isinstance(envelope, dict):
        return {}, "trigger-json must be a JSON object"
    trigger_type = envelope.get("trigger_type")
    if not trigger_type:
        return {}, "trigger-json missing required field 'trigger_type'"
    if trigger_type not in _VALID_TRIGGER_TYPES:
        return {}, f"trigger_type {trigger_type!r} not in {sorted(_VALID_TRIGGER_TYPES)}"
    return envelope, ""


def cmd_openclaw_enqueue(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.project_path)
    except FileNotFoundError as exc:
        _openclaw_error(str(exc), "NOT_FOUND")
        return 1

    trigger_envelope: dict = {}
    trigger_json_raw: str = getattr(args, "trigger_json", None) or ""
    if trigger_json_raw:
        trigger_envelope, parse_err = _parse_trigger_envelope(trigger_json_raw)
        if parse_err:
            _openclaw_error(parse_err, "TRIGGER_INVALID")
            return 1

    task_path = args.task_path
    try:
        run_dir = execute_run_task(REPO_ROOT, task_path, execute=False)
    except SystemExit as exc:
        _openclaw_error(f"Failed to build run from task: {task_path}", "BUILD_FAILED")
        return int(exc.code) if exc.code else 1

    try:
        payload = enqueue_run(run_dir, state="pending")
    except RuntimeError as exc:
        _openclaw_error(str(exc), "ENQUEUE_FAILED")
        return 1

    run_id = payload["job_id"]
    run_path = payload["run_path"]

    if trigger_envelope:
        try:
            trigger_path = run_dir / "trigger.json"
            trigger_path.write_text(json.dumps(trigger_envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            _openclaw_error(f"Failed to write trigger.json: {exc}", "TRIGGER_WRITE_FAILED")
            return 1

    # Read agent and workspace_mode from job.json
    agent = ""
    workspace_mode = ""
    try:
        job = read_json(run_dir / "job.json")
        agent = job.get("preferred_agent") or ""
        workspace_mode = (job.get("execution") or {}).get("workspace_mode") or ""
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # Build launch-plan preview
    preview: dict = {}
    try:
        plan = plan_task_run(REPO_ROOT, task_path)
        plan_dict = plan_to_dict(plan)
        plan_dict["command_preview"] = _preview_agent_command(
            REPO_ROOT,
            agent=plan.routing.selected_agent,
            project_root=plan.project_root,
            workspace_mode=plan.execution.workspace_mode,
        )
        preview = plan_dict
    except Exception:
        pass

    result = {
        "status": "queued",
        "run_id": run_id,
        "run_path": run_path,
        "agent": agent,
        "workspace_mode": workspace_mode,
        "preview": preview,
    }
    if trigger_envelope:
        result["trigger"] = {
            "trigger_type": trigger_envelope.get("trigger_type"),
            "triggered_by": trigger_envelope.get("triggered_by"),
            "idempotency_key": trigger_envelope.get("idempotency_key"),
            "reason_code": trigger_envelope.get("reason_code"),
        }
    append_routing_decision(project_root, run_dir, source="openclaw.enqueue")
    append_run_event(
        run_dir,
        "run_created",
        project_root=project_root,
        payload={
            "run_status": "created",
            "source": "openclaw.enqueue",
            "task_path": str(task_path),
            "agent": agent,
        },
    )
    append_run_event(
        run_dir,
        "run_enqueued",
        project_root=project_root,
        payload={
            "queue_state": "pending",
            "run_status": "queued",
            "source": "openclaw.enqueue",
            "trigger_type": trigger_envelope.get("trigger_type") if trigger_envelope else None,
        },
    )
    refresh_metrics_snapshot(project_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_bind_context(args: argparse.Namespace) -> int:
    try:
        message_text = _load_text_option(args.message, args.message_file, option_name="message")
        reply_text = _load_text_option(args.reply_message, args.reply_message_file, option_name="reply-message")
    except ValueError as exc:
        _openclaw_error(str(exc), "CONTEXT_INVALID")
        return 1

    if message_text is None:
        _openclaw_error("One of --message or --message-file is required", "CONTEXT_INVALID")
        return 1

    defaults = {
        "project": getattr(args, "default_project", None),
        "agent": getattr(args, "default_agent", None),
        "branch": getattr(args, "default_branch", None),
    }

    try:
        payload = bind_operator_context(
            REPO_ROOT,
            message_text,
            reply_message_text=reply_text,
            defaults=defaults,
        )
    except (FileNotFoundError, ValueError) as exc:
        _openclaw_error(str(exc), "CONTEXT_INVALID")
        return 1

    session_scope = str(getattr(args, "session_scope", "") or "").strip()
    if session_scope:
        resolved = payload.get("resolved") if isinstance(payload.get("resolved"), dict) else {}
        resolved_agent = str(resolved.get("agent") or "").strip()
        session_payload = None
        if resolved_agent:
            store = OperatorSessionStore(operator_sessions_root_for_repo(REPO_ROOT), repo_root=REPO_ROOT)
            session_payload = store.load_session(
                scope_id=session_scope,
                scope_kind=getattr(args, "session_scope_kind", "thread"),
                engine=resolved_agent,
            )
        payload["operator_session"] = _operator_session_summary(session_payload)
        payload["continuation"] = _resolve_operator_continuation(
            payload,
            session_payload,
            session_mode=getattr(args, "session_mode", "auto"),
        )

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_file_put(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.project_path)
        context_payload = _load_context_json_option(getattr(args, "context_json", None), getattr(args, "context_json_file", None))
        target_root, workspace_mode, run_dir = _resolve_file_exchange_target(project_root, context_payload, getattr(args, "run", None))
        policy = load_file_exchange_policy(project_root, REPO_ROOT)
        result = put_file(
            target_root,
            args.relative_path,
            Path(args.source_file),
            deny_globs=policy["deny_globs"],
        )
    except FileNotFoundError as exc:
        _openclaw_error(str(exc), "NOT_FOUND")
        return 1
    except TransportConfigError as exc:
        _openclaw_error(str(exc), exc.code)
        return 1
    except FileExchangeError as exc:
        _openclaw_error(str(exc), exc.code)
        return 1
    except ValueError as exc:
        _openclaw_error(str(exc), "FILE_EXCHANGE_INVALID")
        return 1

    payload = {
        "status": "ok",
        "project": project_root.name,
        "workspace_mode": workspace_mode,
        "target_root": str(target_root),
        "run_id": run_dir.name if run_dir is not None else None,
        **result,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_file_fetch(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.project_path)
        context_payload = _load_context_json_option(getattr(args, "context_json", None), getattr(args, "context_json_file", None))
        target_root, workspace_mode, run_dir = _resolve_file_exchange_target(project_root, context_payload, getattr(args, "run", None))
        policy = load_file_exchange_policy(project_root, REPO_ROOT)
        result = fetch_path(
            target_root,
            args.relative_path,
            Path(args.output_file),
            deny_globs=policy["deny_globs"],
        )
    except FileNotFoundError as exc:
        _openclaw_error(str(exc), "NOT_FOUND")
        return 1
    except TransportConfigError as exc:
        _openclaw_error(str(exc), exc.code)
        return 1
    except FileExchangeError as exc:
        _openclaw_error(str(exc), exc.code)
        return 1
    except ValueError as exc:
        _openclaw_error(str(exc), "FILE_EXCHANGE_INVALID")
        return 1

    payload = {
        "status": "ok",
        "project": project_root.name,
        "workspace_mode": workspace_mode,
        "target_root": str(target_root),
        "run_id": run_dir.name if run_dir is not None else None,
        **result,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_session_status(args: argparse.Namespace) -> int:
    store = OperatorSessionStore(operator_sessions_root_for_repo(REPO_ROOT), repo_root=REPO_ROOT)
    payload = store.load_session(
        scope_id=args.scope,
        scope_kind=args.scope_kind,
        engine=args.engine,
    )
    if payload is None:
        print(
            json.dumps(
                {
                    "status": "not_found",
                    "scope": {"kind": args.scope_kind, "id": args.scope},
                    "engine": args.engine,
                },
                ensure_ascii=False,
            )
        )
        return 1
    summary = _operator_session_summary(payload) or payload
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_session_update(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.project_path)
        resume_handle = _parse_resume_handle(args.resume_handle, args.resume_handle_json)
        summary_text = _load_summary_text(args.summary, args.summary_file)
    except (FileNotFoundError, ValueError) as exc:
        _openclaw_error(str(exc), "SESSION_INVALID")
        return 1

    if resume_handle is None and summary_text is None and not args.note and not args.run_id and not args.run_path:
        _openclaw_error("No operator session updates provided", "SESSION_INVALID")
        return 1

    store = OperatorSessionStore(operator_sessions_root_for_repo(REPO_ROOT), repo_root=REPO_ROOT)
    payload = store.update(
        scope_id=args.scope,
        scope_kind=args.scope_kind,
        engine=args.engine,
        resume=resume_handle,
        summary=summary_text,
        note=args.note,
        run_id=args.run_id,
        run_path=args.run_path,
        project=args.project or project_root.name,
        project_root=str(project_root),
        branch=args.branch,
        workspace_mode=args.workspace_mode,
    )
    summary = _operator_session_summary(payload) or payload
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_session_reset(args: argparse.Namespace) -> int:
    store = OperatorSessionStore(operator_sessions_root_for_repo(REPO_ROOT), repo_root=REPO_ROOT)
    try:
        payload = store.reset(
            scope_id=args.scope,
            scope_kind=args.scope_kind,
            engine=args.engine,
            note=args.note,
        )
    except ValueError as exc:
        _openclaw_error(str(exc), "SESSION_INVALID")
        return 1
    summary = _operator_session_summary(payload) or payload
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_session_new_thread(args: argparse.Namespace) -> int:
    store = OperatorSessionStore(operator_sessions_root_for_repo(REPO_ROOT), repo_root=REPO_ROOT)
    try:
        payload = store.rotate(
            scope_id=args.scope,
            scope_kind=args.scope_kind,
            engine=args.engine,
            note=args.note,
        )
    except ValueError as exc:
        _openclaw_error(str(exc), "SESSION_INVALID")
        return 1
    summary = _operator_session_summary(payload) or payload
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_review_batch(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.project_path)
    except FileNotFoundError as exc:
        _openclaw_error(str(exc), "NOT_FOUND")
        return 1

    dry_run: bool = getattr(args, "dry_run", False)

    try:
        batches = generate_review_batches_with_policy(project_root, dry_run=dry_run, capture_stdout=True)
    except RuntimeError as exc:
        _openclaw_error(str(exc), "POLICY_ERROR")
        return 1
    except Exception as exc:
        _openclaw_error(f"Review batch generation failed: {exc}", "BATCH_FAILED")
        return 1

    candidates = []
    for batch in batches:
        for run in batch.get("runs", []):
            candidates.append({
                "run_id": run.get("run_id"),
                "trigger": run.get("trigger"),
                "reviewer": batch.get("reviewer"),
            })

    if not dry_run:
        emit_review_created_events(project_root, batches)

    result = {
        "batches_created": len(batches) if not dry_run else 0,
        "candidates": candidates,
        "dry_run": dry_run,
    }
    refresh_metrics_snapshot(project_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_summary(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.project_path)
    except FileNotFoundError as exc:
        _openclaw_error(str(exc), "NOT_FOUND")
        return 1

    run_dir = resolve_run_dir(project_root, args.run_id_or_path)
    if run_dir is None:
        _openclaw_error(f"Run not found: {args.run_id_or_path}", "NOT_FOUND")
        return 1

    payload = build_live_status_feed(project_root, run_dir, stream_limit=args.stream_limit)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_replay_events(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.project_path)
    except FileNotFoundError as exc:
        _openclaw_error(str(exc), "NOT_FOUND")
        return 1

    run_dir = resolve_run_dir(project_root, args.run_id_or_path)
    if run_dir is None:
        _openclaw_error(f"Run not found: {args.run_id_or_path}", "NOT_FOUND")
        return 1

    events = load_run_events(run_dir)
    payload = {
        "project": project_root.name,
        "run_id": run_dir.name,
        "run_path": run_dir.relative_to(project_root).as_posix(),
        "snapshot": build_run_event_snapshot(project_root, run_dir, events=events),
        "events": events,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_callback(args: argparse.Namespace) -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _openclaw_error(f"Invalid hook payload JSON: {exc}", "INVALID_JSON")
        return 1

    if not isinstance(payload, dict):
        _openclaw_error("Hook payload must be a JSON object", "INVALID_PAYLOAD")
        return 1

    callback_payload = build_callback_payload(payload)
    print(json.dumps(callback_payload, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_wake(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.project_path)
    except FileNotFoundError as exc:
        _openclaw_error(str(exc), "NOT_FOUND")
        return 1

    pending_hook_paths = iter_hook_files(project_root, "pending")
    failed_hook_paths = iter_hook_files(project_root, "failed")
    before_hooks = hook_counts(project_root)
    use_callback_bridge = hook_command() is None

    dispatch_outcomes = [
        deliver_hook_via_callback_bridge(hook_path) if use_callback_bridge else dispatch_hook_file(hook_path)
        for hook_path in pending_hook_paths
    ]
    reconcile_outcomes = []
    for hook_path in failed_hook_paths:
        if hook_path.exists():
            outcome = deliver_hook_via_callback_bridge(hook_path) if use_callback_bridge else dispatch_hook_file(hook_path)
            reconcile_outcomes.append(outcome)

    after_hooks = hook_counts(project_root)
    all_outcomes = dispatch_outcomes + reconcile_outcomes
    has_dispatch_failure = any(o.get("status") == "failed" for o in all_outcomes)
    for outcome in all_outcomes:
        outcome_path = outcome.get("path")
        if outcome_path is None:
            continue
        hook_path = Path(outcome_path)
        try:
            hook_payload = read_json(hook_path)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        run_dir = resolve_run_dir(project_root, str(hook_payload.get("run_id") or ""))
        if run_dir is None:
            continue
        delivery = hook_payload.get("delivery") if isinstance(hook_payload.get("delivery"), dict) else {}
        append_run_event(
            run_dir,
            "delivery_sent" if outcome.get("status") == "sent" else "delivery_failed",
            project_root=project_root,
            payload={
                "delivery_status": "delivered" if outcome.get("status") == "sent" else "failed",
                "attempt_count": int(delivery.get("attempt_count", 0)),
                "hook_id": hook_payload.get("hook_id"),
                "hook_status": outcome.get("status"),
                "last_error": delivery.get("last_error"),
            },
        )
    callbacks = []
    for outcome in all_outcomes:
        if outcome.get("status") != "sent":
            continue
        callback_payload = outcome.get("callback")
        if callback_payload is None:
            callback_payload = build_callback_payload(read_json(outcome["path"]))
        callbacks.append(callback_payload)
    payload = {
        "project": project_root.name,
        "mode": args.mode,
        "reason_code": "hook_dispatch_failed" if has_dispatch_failure else None,
        "schedule": {
            "interval_seconds": 900,
            "kind": "cron" if args.mode == "cron" else "event",
        },
        "queue": queue_counts(project_root),
        "wakes": wake_snapshot(project_root, limit=3),
        "hooks": {
            "before": before_hooks,
            "after": after_hooks,
        },
        "callbacks": callbacks,
        "dispatch": summarize_hook_outcomes(dispatch_outcomes),
        "reconcile": summarize_hook_outcomes(reconcile_outcomes),
    }
    refresh_metrics_snapshot(project_root)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_transports(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.project_path)
        payload = {
            "status": "ok",
            "project": project_root.name,
            "backends": describe_transport_backends(REPO_ROOT, project_root),
        }
    except FileNotFoundError as exc:
        _openclaw_error(str(exc), "NOT_FOUND")
        return 1
    except TransportConfigError as exc:
        _openclaw_error(str(exc), exc.code)
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_doctor(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.project_path)
        payload = run_transport_doctor(REPO_ROOT, project_root)
    except FileNotFoundError as exc:
        _openclaw_error(str(exc), "NOT_FOUND")
        return 1

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") == "ok" else 1


def cmd_decompose_epic(args: argparse.Namespace) -> int:
    """Decompose an epic/roadmap file into TASK + SPEC pairs via LLM."""
    try:
        project_root = resolve_project_root(args.project)
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    input_path = Path(args.input)
    if not input_path.is_file():
        print(json.dumps({"error": f"Input file not found: {input_path}"}), file=sys.stderr)
        return 1

    input_text = input_path.read_text(encoding="utf-8")
    dry_run = not getattr(args, "write", False)

    try:
        result = _decompose_epic(project_root, input_text, dry_run=dry_run)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in ("created", "dry_run") else 1


def cmd_epic_status(args: argparse.Namespace) -> int:
    """Show completion status for a specific epic or all epics."""
    try:
        project_root = resolve_project_root(args.project_root)
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    epic_tag = getattr(args, "epic", None)
    records = collect_task_records(project_root)

    # Group by epic
    epic_groups: dict[str, list[dict]] = {}
    for r in records:
        task_path = Path(r.get("task_path", ""))
        task_epic = "untagged"
        if task_path.is_file():
            try:
                text = task_path.read_text(encoding="utf-8")
                fm_match = re.match(r'\A---\n(.*?)\n---\n?', text, re.DOTALL)
                if fm_match:
                    fm = yaml.safe_load(fm_match.group(1))
                    if isinstance(fm, dict) and fm.get("epic"):
                        task_epic = str(fm["epic"])
            except Exception:
                pass
        epic_groups.setdefault(task_epic, []).append(r)

    def _epic_summary(tasks: list[dict]) -> dict:
        total = len(tasks)
        done = sum(1 for t in tasks if t.get("status") in ("done", "accepted"))
        blocked = sum(1 for t in tasks if t.get("dependency_blockers"))
        pending = total - done - blocked
        pct = round(done / total * 100) if total > 0 else 0
        return {
            "total": total,
            "done": done,
            "blocked": blocked,
            "pending": pending,
            "completion_pct": pct,
            "complete": done == total,
        }

    if epic_tag:
        tasks = epic_groups.get(str(epic_tag), [])
        payload = {
            "project": project_root.name,
            "epic": epic_tag,
            **_epic_summary(tasks),
            "tasks": [{"task_id": t["task_id"], "status": t["status"]} for t in tasks],
        }
    else:
        epics_out = {}
        for tag, tasks in sorted(epic_groups.items()):
            epics_out[tag] = _epic_summary(tasks)
        payload = {
            "project": project_root.name,
            "epics": epics_out,
        }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claw")
    subcommands = parser.add_subparsers(dest="command", required=True)

    create_project = subcommands.add_parser("create-project", help="Create a project scaffold")
    create_project.add_argument("project_slug")
    create_project.add_argument("destination_root", nargs="?")
    create_project.set_defaults(func=cmd_create_project)

    import_project = subcommands.add_parser(
        "import-project",
        help="Bootstrap a new project from an existing external repository",
    )
    import_project.add_argument("--slug", required=True, help="Project slug (lowercase, hyphens)")
    import_project.add_argument("--path", required=True, help="Path to the external repository")
    import_project.set_defaults(func=cmd_import_project)

    run = subcommands.add_parser("run", help="Create a run from a task")
    run.add_argument("task_path")
    run.add_argument("--execute", action="store_true")
    run.add_argument("--enqueue", action="store_true")
    run.add_argument("--awaiting-approval", action="store_true")
    run.set_defaults(func=cmd_run)

    enqueue = subcommands.add_parser("enqueue", help="Create a run and place it into the queue")
    enqueue.add_argument("task_path")
    enqueue.add_argument("--awaiting-approval", action="store_true")
    enqueue.set_defaults(func=cmd_enqueue)

    worker = subcommands.add_parser("worker", help="Claim queued jobs for one project")
    worker.add_argument("project_root")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--skip-review", action="store_true")
    worker.add_argument("--stale-after-seconds", type=int)
    worker.add_argument("--lease-seconds", type=int, default=DEFAULT_WORKER_LEASE_SECONDS)
    worker.add_argument("--heartbeat-interval-seconds", type=float)
    worker.add_argument("--retry-backoff-base-seconds", type=int, default=DEFAULT_RETRY_BACKOFF_BASE_SECONDS)
    worker.add_argument("--retry-backoff-max-seconds", type=int, default=DEFAULT_RETRY_BACKOFF_MAX_SECONDS)
    worker.set_defaults(func=cmd_worker)

    dispatch = subcommands.add_parser("dispatch", help="Dispatch pending hooks for a project")
    dispatch.add_argument("project_root")
    dispatch.set_defaults(func=cmd_dispatch)

    reconcile = subcommands.add_parser("reconcile", help="Retry stale or failed hooks for a project")
    reconcile.add_argument("project_root")
    reconcile.set_defaults(func=cmd_reconcile)

    approve = subcommands.add_parser("approve", help="Move a queued job from awaiting approval back to pending")
    approve.add_argument("project_root")
    approve.add_argument("run_id")
    approve.set_defaults(func=cmd_approve)

    resolve_checkpoint = subcommands.add_parser("resolve-checkpoint", help="Resolve a step-level approval checkpoint")
    resolve_checkpoint.add_argument("project_root")
    resolve_checkpoint.add_argument("run_id")
    resolve_checkpoint.add_argument("--decision", required=True, choices=("accept", "reject"))
    resolve_checkpoint.add_argument("--notes")
    resolve_checkpoint.set_defaults(func=cmd_resolve_checkpoint)

    reclaim = subcommands.add_parser("reclaim", help="Move stale running jobs back to pending")
    reclaim.add_argument("project_root")
    reclaim.add_argument("--stale-after-seconds", type=int, required=True)
    reclaim.set_defaults(func=cmd_reclaim)

    apply_patch = subcommands.add_parser("apply-patch", help="Preview or apply an advisory patch artifact")
    apply_patch.add_argument("project_root")
    apply_patch.add_argument("run_id_or_path")
    apply_patch.add_argument("--confirm", action="store_true", help="Actually apply patch.diff via git apply")
    apply_patch.set_defaults(func=cmd_apply_patch)

    status = subcommands.add_parser("status", help="Show live status feed for one run")
    status.add_argument("project_root")
    status.add_argument("run_id")
    status.add_argument("--stream-limit", type=int, default=10, help="Number of stream records to include (default: 10)")
    status.set_defaults(func=cmd_status)

    dashboard = subcommands.add_parser("dashboard", help="Show richer status for one or all projects")
    dashboard.add_argument("project_root", nargs="?")
    dashboard.add_argument("--all", action="store_true")
    dashboard.add_argument("--recent", type=int, default=5)
    dashboard.add_argument("--ready-limit", type=int, default=3)
    dashboard.set_defaults(func=cmd_dashboard)

    wake_enqueue = subcommands.add_parser("wake-enqueue", help="Create or coalesce a pending wake artifact for an agent/task scope")
    wake_enqueue.add_argument("project_root")
    wake_enqueue.add_argument("--agent", required=True, help="Agent id, e.g. codex")
    wake_enqueue.add_argument("--task-id", required=True, help="Task id, e.g. TASK-001")
    wake_enqueue.add_argument("--reason", required=True, choices=sorted(VALID_WAKE_REASONS))
    wake_enqueue.add_argument("--run-id", help="Optional run id associated with the wake")
    wake_enqueue.add_argument("--source", help="Optional source marker for debugging")
    wake_enqueue.add_argument("--note", help="Optional human-readable note")
    wake_enqueue.add_argument("--context-json", help="Optional JSON object with extra wake context")
    wake_enqueue.set_defaults(func=cmd_wake_enqueue)

    wake_status = subcommands.add_parser("wake-status", help="Inspect pending wake artifacts as JSON")
    wake_status.add_argument("project_root")
    wake_status.add_argument("--limit", type=int, default=20)
    wake_status.set_defaults(func=cmd_wake_status)

    scheduler = subcommands.add_parser("scheduler", help="Run fair multi-project worker scheduling")
    scheduler.add_argument("projects", nargs="*")
    scheduler.add_argument("--once", action="store_true")
    scheduler.add_argument("--max-jobs", type=int, default=1)
    scheduler.add_argument("--skip-review", action="store_true")
    scheduler.set_defaults(func=cmd_scheduler)

    ask_human = subcommands.add_parser("ask-human", help="Create a pending human approval request for a run")
    ask_human.add_argument("project_root")
    ask_human.add_argument("run_id")
    ask_human.add_argument("--reason", required=True)
    ask_human.add_argument("--action", choices=("retry", "accept", "follow_up"), default="retry")
    ask_human.add_argument("--source", choices=("runtime", "review", "manual"), default="manual")
    ask_human.set_defaults(func=cmd_ask_human)

    resolve_approval = subcommands.add_parser("resolve-approval", help="Resolve a pending approval request")
    resolve_approval.add_argument("project_root")
    resolve_approval.add_argument("approval_id")
    resolve_approval.add_argument("--decision", choices=("approved", "rejected"), required=True)
    resolve_approval.add_argument("--notes", default="")
    resolve_approval.set_defaults(func=cmd_resolve_approval)

    orchestrate = subcommands.add_parser("orchestrate", help="Run a task->queue->worker decision loop for one project")
    orchestrate.add_argument("project_root")
    orchestrate.add_argument("--max-steps", type=int, default=1)
    orchestrate.add_argument("--failure-budget", type=int, default=DEFAULT_ORCHESTRATE_FAILURE_BUDGET)
    orchestrate.add_argument("--skip-review", action="store_true")
    orchestrate.add_argument("--recent", type=int, default=5)
    orchestrate.add_argument("--ready-limit", type=int, default=3)
    orchestrate.add_argument("--scope", default=None,
                              help="Stop when scope is complete (e.g. 'epic:12')")
    orchestrate.set_defaults(func=cmd_orchestrate)

    epic_status = subcommands.add_parser(
        "epic-status",
        help="Show completion status for tasks grouped by epic tag"
    )
    epic_status.add_argument("project_root", help="Project root path or slug")
    epic_status.add_argument("--epic", default=None, help="Filter to specific epic tag (e.g. '12')")
    epic_status.set_defaults(func=cmd_epic_status)

    task_snapshot = subcommands.add_parser("task-snapshot", help="Generate and write task graph snapshot")
    task_snapshot.add_argument("project_root")
    task_snapshot.set_defaults(func=cmd_task_snapshot)

    task_claim = subcommands.add_parser("task-claim", help="Claim a task for an agent")
    task_claim.add_argument("project_root")
    task_claim.add_argument("--task-id", required=True, help="Task id (e.g. TASK-001)")
    task_claim.add_argument("--agent", required=True, help="Agent id (e.g. codex)")
    task_claim.add_argument("--reason", help="Optional claim reason")
    task_claim.add_argument("--note", help="Optional claim note")
    task_claim.add_argument("--no-wake", action="store_true", help="Do not enqueue assignment wake")
    task_claim.set_defaults(func=cmd_task_claim)

    task_release = subcommands.add_parser("task-release", help="Release a claimed task")
    task_release.add_argument("project_root")
    task_release.add_argument("--task-id", required=True, help="Task id (e.g. TASK-001)")
    task_release.add_argument("--agent", required=True, help="Agent id (e.g. codex)")
    task_release.add_argument("--status", choices=("released", "blocked"), default="released")
    task_release.add_argument("--reason", help="Optional release reason")
    task_release.add_argument("--note", help="Optional release note")
    task_release.set_defaults(func=cmd_task_release)

    task_delegate = subcommands.add_parser("task-delegate", help="Delegate a task to another agent")
    task_delegate.add_argument("project_root")
    task_delegate.add_argument("--task-id", required=True, help="Parent task id (e.g. TASK-001)")
    task_delegate.add_argument("--agent", required=True, help="Delegating agent id")
    task_delegate.add_argument("--assignee", required=True, help="Delegate agent id")
    task_delegate.add_argument("--reason", required=True, help="Delegation reason")
    task_delegate.add_argument("--note", help="Optional delegation note")
    task_delegate.add_argument("--title", help="Optional delegated task title")
    task_delegate.add_argument("--spec", help="Optional spec reference for the delegated task")
    task_delegate.add_argument("--priority", help="Optional priority override")
    task_delegate.add_argument("--review-policy", help="Optional review policy override")
    task_delegate.add_argument("--needs-review", action="store_true", help="Mark delegated task as needing review")
    task_delegate.add_argument("--tag", action="append", help="Optional extra tag (repeatable)")
    task_delegate.set_defaults(func=cmd_task_delegate)

    task_escalate = subcommands.add_parser("task-escalate", help="Escalate a blocked task to the manager chain")
    task_escalate.add_argument("project_root")
    task_escalate.add_argument("--task-id", required=True, help="Blocked task id (e.g. TASK-001)")
    task_escalate.add_argument("--agent", required=True, help="Escalating agent id")
    task_escalate.add_argument("--reason", required=True, help="Escalation reason")
    task_escalate.add_argument("--note", help="Optional escalation note")
    task_escalate.add_argument("--title", help="Optional escalation task title")
    task_escalate.add_argument("--spec", help="Optional spec reference for the escalation task")
    task_escalate.add_argument("--priority", help="Optional priority override")
    task_escalate.add_argument("--review-policy", help="Optional review policy override")
    task_escalate.add_argument("--needs-review", action="store_true", help="Mark escalation task as needing review")
    task_escalate.set_defaults(func=cmd_task_escalate)

    inbox = subcommands.add_parser("inbox", help="Materialize an agent inbox projection as JSON")
    inbox.add_argument("project_root")
    inbox.add_argument("--agent", required=True, help="Agent id (e.g. codex)")
    inbox.add_argument("--limit", type=int, default=20)
    inbox.set_defaults(func=cmd_inbox)

    org_graph = subcommands.add_parser("org-graph", help="Show org graph and delegation policy")
    org_graph.add_argument("project_root")
    org_graph.set_defaults(func=cmd_org_graph)

    session_status = subcommands.add_parser("session-status", help="Show session continuity for an agent/task scope")
    session_status.add_argument("project_root")
    session_status.add_argument("--agent", required=True, help="Agent id (e.g. codex)")
    session_status.add_argument("--task-id", required=True, help="Task id (e.g. TASK-001)")
    session_status.set_defaults(func=cmd_session_status)

    session_update = subcommands.add_parser("session-update", help="Update session resume handle or handoff summary")
    session_update.add_argument("project_root")
    session_update.add_argument("--agent", required=True, help="Agent id (e.g. codex)")
    session_update.add_argument("--task-id", required=True, help="Task id (e.g. TASK-001)")
    session_update.add_argument("--resume-handle", help="Opaque resume handle string")
    session_update.add_argument("--resume-handle-json", help="JSON object for provider-neutral resume handle")
    session_update.add_argument("--summary", help="Handoff summary text")
    session_update.add_argument("--summary-file", help="Read handoff summary from a file path")
    session_update.add_argument("--note", help="Optional update note")
    session_update.add_argument("--run-id", help="Optional run id associated with the update")
    session_update.add_argument("--run-path", help="Optional run path associated with the update")
    session_update.set_defaults(func=cmd_session_update)

    session_reset = subcommands.add_parser("session-reset", help="Reset session continuity for an agent/task scope")
    session_reset.add_argument("project_root")
    session_reset.add_argument("--agent", required=True, help="Agent id (e.g. codex)")
    session_reset.add_argument("--task-id", required=True, help="Task id (e.g. TASK-001)")
    session_reset.add_argument("--note", help="Optional reset note")
    session_reset.set_defaults(func=cmd_session_reset)

    session_rotate = subcommands.add_parser("session-rotate", help="Rotate session id and clear continuity state")
    session_rotate.add_argument("project_root")
    session_rotate.add_argument("--agent", required=True, help="Agent id (e.g. codex)")
    session_rotate.add_argument("--task-id", required=True, help="Task id (e.g. TASK-001)")
    session_rotate.add_argument("--note", help="Optional rotation note")
    session_rotate.set_defaults(func=cmd_session_rotate)

    session_files = subcommands.add_parser("session-files", help="List shared session files for a task scope")
    session_files.add_argument("project_root")
    session_files.add_argument("--task-id", required=True, help="Task id (e.g. TASK-001)")
    session_files.set_defaults(func=cmd_session_files)

    session_file_put = subcommands.add_parser("session-file-put", help="Write a shared session file for a task scope")
    session_file_put.add_argument("project_root")
    session_file_put.add_argument("--task-id", required=True, help="Task id (e.g. TASK-001)")
    session_file_put.add_argument("relative_path", help="Relative path inside the task-scoped shared session files root")
    session_file_put.add_argument("--source-file", required=True, help="Local source file path to store")
    session_file_put.add_argument("--author", help="Optional author/agent label")
    session_file_put.add_argument("--note", help="Optional note attached to the file entry")
    session_file_put.set_defaults(func=cmd_session_file_put)

    session_file_fetch = subcommands.add_parser("session-file-fetch", help="Fetch a shared session file for a task scope")
    session_file_fetch.add_argument("project_root")
    session_file_fetch.add_argument("--task-id", required=True, help="Task id (e.g. TASK-001)")
    session_file_fetch.add_argument("relative_path", help="Relative path inside the task-scoped shared session files root")
    session_file_fetch.add_argument("--output-file", required=True, help="Local output file path")
    session_file_fetch.set_defaults(func=cmd_session_file_fetch)

    workflow_graph = subcommands.add_parser("workflow-graph", help="Generate and write portable workflow graph artifact")
    workflow_graph.add_argument("project_root")
    workflow_graph.set_defaults(func=cmd_workflow_graph)

    decision_log = subcommands.add_parser("decision-log", help="Show recent orchestrator decisions for a project")
    decision_log.add_argument("project_root")
    decision_log.add_argument("--last", type=int, default=20)
    decision_log.set_defaults(func=cmd_decision_log)

    task_lint = subcommands.add_parser("task-lint", help="Lint the task dependency graph for cycles and invalid refs")
    task_lint.add_argument("project_root")
    task_lint.set_defaults(func=cmd_task_lint)

    task_graph_lint = subcommands.add_parser(
        "task-graph-lint",
        help="Extended task graph lint: cycles, unknown deps, and file-overlap",
    )
    task_graph_lint.add_argument("project_root")
    task_graph_lint.set_defaults(func=cmd_task_graph_lint)

    launch_plan = subcommands.add_parser("launch-plan", help="Preview execution plan for a task without running it")
    launch_plan.add_argument("task_path")
    launch_plan.set_defaults(func=cmd_launch_plan)

    workflow_validate = subcommands.add_parser(
        "workflow-validate",
        help="Validate the WORKFLOW.md contract for a project",
    )
    workflow_validate.add_argument("project_root", help="Project root path or slug")
    workflow_validate.set_defaults(func=cmd_workflow_validate)

    run_checks = subcommands.add_parser(
        "run-checks",
        help="Run registered project commands (test/lint/build/smoke) from WORKFLOW.md",
    )
    run_checks.add_argument("project_root", nargs="?", help="Project root path or slug")
    run_checks.add_argument("--project", help="Project root path or slug")
    run_checks.add_argument(
        "--type",
        default="test",
        choices=["test", "lint", "build", "smoke"],
        help="Which command to run (default: test)",
    )
    run_checks.set_defaults(func=cmd_run_checks)

    guardrail_check = subcommands.add_parser("guardrail-check", help="Run standalone structural guardrails against a diff file")
    guardrail_check.add_argument("--project", required=True, help="Project slug or path used to load edit_scope")
    guardrail_check.add_argument("--diff-path", required=True, help="Path to a unified diff file")
    guardrail_check.set_defaults(func=cmd_guardrail_check)

    review_batch = subcommands.add_parser("review-batch", help="Generate review batch artifacts for one project or all projects")
    review_batch.add_argument("project_root", nargs="?")
    review_batch.add_argument("--all", action="store_true", help="Process all projects in the repo")
    review_batch.add_argument("--dry-run", action="store_true", help="Print what would be written without creating files")
    review_batch.set_defaults(func=cmd_review_batch)

    decompose_epic = subcommands.add_parser(
        "decompose-epic",
        help="Decompose an epic/roadmap into TASK + SPEC file pairs via LLM",
    )
    decompose_epic.add_argument("--project", required=True, help="Project slug or path")
    decompose_epic.add_argument("--input", required=True, help="Path to roadmap/epic markdown file")
    decompose_epic.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Preview tasks without writing files (default behavior unless --write is passed)",
    )
    decompose_epic.add_argument(
        "--write", action="store_true", default=False,
        help="Actually write TASK + SPEC files (required to materialize)",
    )
    decompose_epic.set_defaults(func=cmd_decompose_epic)

    # ── openclaw ──────────────────────────────────────────────────────────────
    openclaw = subcommands.add_parser("openclaw", help="OpenClaw agent-facing project management commands")
    openclaw_sub = openclaw.add_subparsers(dest="openclaw_command", required=True)

    oc_status = openclaw_sub.add_parser("status", help="Show project status as JSON for agent consumption")
    oc_status.add_argument("project_path")
    oc_status.add_argument("--recent", type=int, default=5, help="Number of recent runs to include (default: 5)")
    oc_status.set_defaults(func=cmd_openclaw_status)

    oc_enqueue = openclaw_sub.add_parser("enqueue", help="Build a run and enqueue it, returning JSON")
    oc_enqueue.add_argument("project_path")
    oc_enqueue.add_argument("task_path")
    oc_enqueue.add_argument(
        "--trigger-json",
        metavar="JSON",
        default=None,
        help=(
            "Optional typed trigger envelope as a JSON string "
            "(trigger_type: manual|schedule|webhook). "
            "Written as trigger.json alongside job.json in the run directory."
        ),
    )
    oc_enqueue.set_defaults(func=cmd_openclaw_enqueue)

    oc_bind_context = openclaw_sub.add_parser("bind-context", help="Normalize operator directives and ctx footer into routing context JSON")
    oc_bind_context.add_argument("--message", help="Operator message text")
    oc_bind_context.add_argument("--message-file", help="Read operator message text from a file")
    oc_bind_context.add_argument("--reply-message", help="Optional replied-to message text carrying a ctx footer")
    oc_bind_context.add_argument("--reply-message-file", help="Read replied-to message text from a file")
    oc_bind_context.add_argument("--default-project", help="Ambient default project slug or path")
    oc_bind_context.add_argument("--default-agent", help="Ambient default agent id")
    oc_bind_context.add_argument("--default-branch", help="Ambient default branch name")
    oc_bind_context.add_argument("--session-scope", help="Optional operator scope id for auto-resume lookup")
    oc_bind_context.add_argument("--session-scope-kind", default="thread", help="Operator scope kind (default: thread)")
    oc_bind_context.add_argument(
        "--session-mode",
        choices=("auto", "new-thread", "reset"),
        default="auto",
        help="Override auto-resume behavior for this bind result",
    )
    oc_bind_context.set_defaults(func=cmd_openclaw_bind_context)

    oc_file_put = openclaw_sub.add_parser("file-put", help="Safely upload a file into a project root or active worktree")
    oc_file_put.add_argument("project_path")
    oc_file_put.add_argument("relative_path", help="Relative destination path inside the active project/worktree root")
    oc_file_put.add_argument("--source-file", required=True, help="Local source file to upload")
    oc_file_put.add_argument("--context-json", help="Optional bind-context JSON payload as a string")
    oc_file_put.add_argument("--context-json-file", help="Read bind-context JSON payload from a file")
    oc_file_put.add_argument("--run", help="Optional run id or run path used to resolve worktree-backed targets")
    oc_file_put.set_defaults(func=cmd_openclaw_file_put)

    oc_file_fetch = openclaw_sub.add_parser("file-fetch", help="Safely fetch a file or directory from a project root or active worktree")
    oc_file_fetch.add_argument("project_path")
    oc_file_fetch.add_argument("relative_path", help="Relative source path inside the active project/worktree root")
    oc_file_fetch.add_argument("--output-file", required=True, help="Local file path to write the fetched payload")
    oc_file_fetch.add_argument("--context-json", help="Optional bind-context JSON payload as a string")
    oc_file_fetch.add_argument("--context-json-file", help="Read bind-context JSON payload from a file")
    oc_file_fetch.add_argument("--run", help="Optional run id or run path used to resolve worktree-backed targets")
    oc_file_fetch.set_defaults(func=cmd_openclaw_file_fetch)

    oc_transports = openclaw_sub.add_parser("transports", help="List discovered operator transport backends")
    oc_transports.add_argument("project_path")
    oc_transports.set_defaults(func=cmd_openclaw_transports)

    oc_doctor = openclaw_sub.add_parser("doctor", help="Run setup checks for configured operator transport backends")
    oc_doctor.add_argument("project_path")
    oc_doctor.set_defaults(func=cmd_openclaw_doctor)

    oc_session_status = openclaw_sub.add_parser("session-status", help="Show operator session continuity for one scope/engine")
    oc_session_status.add_argument("project_path")
    oc_session_status.add_argument("--scope", required=True, help="Operator scope id (e.g. transport/thread-42)")
    oc_session_status.add_argument("--scope-kind", default="thread", help="Operator scope kind (default: thread)")
    oc_session_status.add_argument("--engine", required=True, help="Engine/agent id (e.g. codex)")
    oc_session_status.set_defaults(func=cmd_openclaw_session_status)

    oc_session_update = openclaw_sub.add_parser("session-update", help="Update operator session resume handle or handoff summary")
    oc_session_update.add_argument("project_path")
    oc_session_update.add_argument("--scope", required=True, help="Operator scope id (e.g. transport/thread-42)")
    oc_session_update.add_argument("--scope-kind", default="thread", help="Operator scope kind (default: thread)")
    oc_session_update.add_argument("--engine", required=True, help="Engine/agent id (e.g. codex)")
    oc_session_update.add_argument("--project", help="Bound project slug (defaults to the provided project path)")
    oc_session_update.add_argument("--branch", help="Bound branch name for this operator session")
    oc_session_update.add_argument("--workspace-mode", help="Optional workspace mode bound to this session")
    oc_session_update.add_argument("--resume-handle", help="Opaque resume handle string")
    oc_session_update.add_argument("--resume-handle-json", help="JSON object for provider-neutral resume handle")
    oc_session_update.add_argument("--summary", help="Handoff summary text")
    oc_session_update.add_argument("--summary-file", help="Read handoff summary from a file path")
    oc_session_update.add_argument("--note", help="Optional update note")
    oc_session_update.add_argument("--run-id", help="Optional run id associated with the update")
    oc_session_update.add_argument("--run-path", help="Optional run path associated with the update")
    oc_session_update.set_defaults(func=cmd_openclaw_session_update)

    oc_session_reset = openclaw_sub.add_parser("session-reset", help="Reset stored continuity for one operator scope")
    oc_session_reset.add_argument("project_path")
    oc_session_reset.add_argument("--scope", required=True, help="Operator scope id (e.g. transport/thread-42)")
    oc_session_reset.add_argument("--scope-kind", default="thread", help="Operator scope kind (default: thread)")
    oc_session_reset.add_argument("--engine", required=True, help="Engine/agent id (e.g. codex)")
    oc_session_reset.add_argument("--note", help="Optional reset note")
    oc_session_reset.set_defaults(func=cmd_openclaw_session_reset)

    oc_session_new_thread = openclaw_sub.add_parser("session-new-thread", help="Rotate operator session id and clear stored continuity")
    oc_session_new_thread.add_argument("project_path")
    oc_session_new_thread.add_argument("--scope", required=True, help="Operator scope id (e.g. transport/thread-42)")
    oc_session_new_thread.add_argument("--scope-kind", default="thread", help="Operator scope kind (default: thread)")
    oc_session_new_thread.add_argument("--engine", required=True, help="Engine/agent id (e.g. codex)")
    oc_session_new_thread.add_argument("--note", help="Optional note for the new thread rotation")
    oc_session_new_thread.set_defaults(func=cmd_openclaw_session_new_thread)

    oc_review_batch = openclaw_sub.add_parser("review-batch", help="Generate review batches and return JSON summary")
    oc_review_batch.add_argument("project_path")
    oc_review_batch.add_argument("--dry-run", dest="dry_run", action="store_true")
    oc_review_batch.set_defaults(func=cmd_openclaw_review_batch)

    oc_summary = openclaw_sub.add_parser("summary", help="Show live status feed for a run as JSON")
    oc_summary.add_argument("project_path")
    oc_summary.add_argument("run_id_or_path")
    oc_summary.add_argument("--stream-limit", type=int, default=10, help="Number of stream records to include (default: 10)")
    oc_summary.set_defaults(func=cmd_openclaw_summary)

    oc_replay_events = openclaw_sub.add_parser("replay-events", help="Replay append-only run events as JSON")
    oc_replay_events.add_argument("project_path")
    oc_replay_events.add_argument("run_id_or_path")
    oc_replay_events.set_defaults(func=cmd_openclaw_replay_events)

    oc_callback = openclaw_sub.add_parser("callback", help="Convert hook payload from stdin into chat callback JSON")
    oc_callback.set_defaults(func=cmd_openclaw_callback)

    oc_wake = openclaw_sub.add_parser("wake", help="Dispatch pending hooks and reconcile failed ones for chat bridge wake-ups")
    oc_wake.add_argument("project_path")
    oc_wake.add_argument("--mode", choices=("event", "cron"), default="event")
    oc_wake.set_defaults(func=cmd_openclaw_wake)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
