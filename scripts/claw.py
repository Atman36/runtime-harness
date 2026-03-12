#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import yaml

def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


REPO_ROOT = repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _system.engine import FileQueue, QueueEmpty, enqueue_run, execute_run_task, find_run_dir, plan_task_run, plan_to_dict, queue_root_for_project, read_json, resolve_project_root, run_command  # noqa: E402
from generate_review_batch import POLICY_PATH, classify_run, generate_batches, load_policy, load_run  # noqa: E402
from hooklib import dispatch_hook_file, iter_hook_files, trim_text  # noqa: E402


CADENCE_STATE_FILE = "review_cadence.json"
METRICS_SNAPSHOT_FILE = "metrics_snapshot.json"
FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
DEFAULT_WORKER_LEASE_SECONDS = 600
DEFAULT_RETRY_BACKOFF_BASE_SECONDS = 30
DEFAULT_RETRY_BACKOFF_MAX_SECONDS = 300
TASK_DONE_STATUSES = {"done", "completed", "accepted"}
TASK_ACTIVE_STATUSES = {"in_progress", "running", "queued", "awaiting_review", "awaiting_approval"}
TASK_BLOCKED_STATUSES = {"blocked", "cancelled"}
PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
                update_task_status(task_path, "todo")
            elif decision == "approved" and payload.get("requested_action") == "accept":
                update_task_status(task_path, "done")

    target_path = directories["resolved"] / pending_path.name
    write_json_atomic(target_path, payload)
    pending_path.unlink()
    return payload


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
        front_matter, _body = read_front_matter(task_path)
        task_id = str(front_matter.get("id") or task_path.stem).strip()
        status = str(front_matter.get("status") or "todo").strip().lower()
        dependencies = parse_task_dependencies(front_matter)
        records.append(
            {
                "task_id": task_id,
                "title": str(front_matter.get("title") or "").strip(),
                "task_path": task_path,
                "task_path_rel": task_path.relative_to(project_root).as_posix(),
                "status": status,
                "priority": str(front_matter.get("priority") or "").strip().lower(),
                "dependencies": dependencies,
                "preferred_agent": str(front_matter.get("preferred_agent") or "auto").strip(),
                "needs_review": bool(front_matter.get("needs_review", False)),
                "active": task_id in active_task_ids,
            }
        )

    done_ids = {record["task_id"] for record in records if record["status"] in TASK_DONE_STATUSES}
    for record in records:
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


def select_ready_tasks(project_root: Path, *, limit: int = 3) -> list[dict]:
    ready = [record for record in collect_task_records(project_root) if record["ready"]]
    ready.sort(key=lambda record: (task_priority_value(record["priority"]), record["task_id"]))
    return ready[:limit]


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
        resolved.append(payload)

    if not resolved:
        return None
    resolved.sort(key=lambda payload: str(payload.get("decided_at") or payload.get("review_id") or ""))
    return resolved[-1]


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
    approvals = approval_counts(project_root)
    ready_tasks = select_ready_tasks(project_root, limit=ready_limit)

    return {
        "project": project_root.name,
        "queue": snapshot["queue"],
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
                "task_path": task["task_path_rel"],
            }
            for task in ready_tasks
        ],
        "approvals": load_approval_requests(project_root, state="pending")[:ready_limit],
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
    refresh_metrics_snapshot(run_dir.parent.parent.parent)
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
        return {"decision": "ask_human", "approval_id": approval["approval_id"]}

    pending_reviews = load_pending_review_decisions(project_root, run_id=run_id)
    if pending_reviews:
        return {"decision": "awaiting_review", "pending_reviews": len(pending_reviews)}

    resolved_decision = load_resolved_review_decision(project_root, run_id)
    if resolved_decision is not None:
        decision = str(resolved_decision.get("decision") or "pending")
        if decision in {"approved", "approved_with_notes", "waived"}:
            accept_run(project_root, run_dir)
            return {"decision": "accept"}
        approval = create_approval_request(
            project_root,
            run_id=run_id,
            task_id=task_id,
            task_path=task_path,
            source="review",
            reason=decision,
            requested_action="follow_up" if decision == "needs_follow_up" else "retry",
        )
        return {"decision": "ask_human", "approval_id": approval["approval_id"]}

    accept_run(project_root, run_dir)
    return {"decision": "accept"}


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
    recent_runs: list[dict] = []

    for result_path, result in result_files:
        status = str(result.get("status") or "unknown")
        run_statuses[status if status in run_statuses else "unknown"] += 1
        run_id = result.get("run_id") or result_path.parent.name
        finished_at = result.get("finished_at") or result.get("completed_at") or result.get("created_at")
        recent_runs.append({
            "run_id": run_id,
            "status": status,
            "agent": result.get("agent", ""),
            "finished_at": finished_at,
        })

    snapshot = {
        "snapshot_version": 1,
        "project": project_root.name,
        "updated_at": utc_now(),
        "queue": queue_snapshot,
        "hooks": hook_snapshot,
        "runs": {
            "total": len(result_files),
            "by_status": run_statuses,
        },
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


def refresh_metrics_snapshot(project_root: Path, *, recent_limit: int = 20) -> dict:
    snapshot = build_metrics_snapshot(project_root, recent_limit=recent_limit)
    write_json_atomic(project_root / "state" / METRICS_SNAPSHOT_FILE, snapshot)
    return snapshot


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


def maybe_trigger_review(project_root: Path, run_dir: Path, result_status: str, policy: dict) -> list[dict]:
    try:
        run = load_run(run_dir, project_root)
        if run is None:
            return []

        cadence_state = load_cadence_state(project_root)
        trigger = classify_run(run)
        batches: list[dict] = []

        if trigger is not None:
            batches = generate_batches(project_root, policy)
        elif result_status == "success":
            cadence_state["successful_since_last_batch"] += 1
            cadence_batch_size = int(policy.get("cadence", {}).get("successful_runs_batch", 5))
            if cadence_state["successful_since_last_batch"] >= cadence_batch_size:
                batches = generate_batches(project_root, policy)

        if any(batch.get("trigger_type") == "cadence" for batch in batches):
            cadence_state["successful_since_last_batch"] = 0
            cadence_state["last_batch_generated_at"] = utc_now()

        save_cadence_state(project_root, cadence_state)
        return batches
    except Exception as exc:  # pragma: no cover - review generation must not fail worker loop
        print(f"Review trigger error for {run_dir}: {exc}", file=sys.stderr)
        return []


def cmd_create_project(args: argparse.Namespace) -> int:
    command = ["bash", str(REPO_ROOT / "scripts" / "create_project.sh"), args.project_slug]
    if args.destination_root:
        command.append(args.destination_root)
    completed = run_command(command, cwd=REPO_ROOT)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


def cmd_run(args: argparse.Namespace) -> int:
    if args.awaiting_approval and not args.enqueue:
        raise SystemExit("--awaiting-approval requires --enqueue")

    run_dir = execute_run_task(REPO_ROOT, args.task_path, execute=args.execute)
    project_root = run_dir.parent.parent.parent
    if args.enqueue:
        queue_state = "awaiting_approval" if args.awaiting_approval else "pending"
        payload = enqueue_run(run_dir, state=queue_state)
        refresh_metrics_snapshot(project_root)
        print(
            json.dumps(
                {"status": "queued", "job_id": payload["job_id"], "run_path": payload["run_path"], "queue_state": queue_state},
                ensure_ascii=False,
            )
        )
        return 0
    refresh_metrics_snapshot(project_root)
    print(json.dumps({"status": "created", "run_dir": str(run_dir)}, ensure_ascii=False))
    return 0


def cmd_enqueue(args: argparse.Namespace) -> int:
    queue_state = "awaiting_approval" if args.awaiting_approval else "pending"
    run_dir = execute_run_task(REPO_ROOT, args.task_path, execute=False)
    payload = enqueue_run(run_dir, state=queue_state)
    refresh_metrics_snapshot(run_dir.parent.parent.parent)
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

        next_retry_at = None
        retry_backoff_seconds = None
        if completed.returncode == 0:
            queue.ack(claimed, result_status=result_status, exit_code=completed.returncode)
            queue_state = "done"
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
                queue_state = "retried"
            else:
                queue.dead_letter(
                    claimed,
                    result_status=result_status,
                    exit_code=completed.returncode,
                    error=error_text,
                )
                queue_state = "dead_letter"

        if review_policy is not None:
            maybe_trigger_review(project_root, run_dir, result_status, review_policy)

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
    refresh_metrics_snapshot(project_root)
    print(json.dumps({"status": "approved", "job_id": args.run_id, "queue_state": "pending"}, ensure_ascii=False))
    return 0


def cmd_reclaim(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    queue = FileQueue(queue_root_for_project(project_root))
    reclaimed = queue.reclaim_stale_running(args.stale_after_seconds)
    refresh_metrics_snapshot(project_root)
    print(json.dumps({"status": "reclaimed", "reclaimed": reclaimed, "queue_state": "pending"}, ensure_ascii=False))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    run_dir = find_run_dir(project_root, args.run_id)
    if run_dir is None:
        print(f"Run not found: {args.run_id}", file=sys.stderr)
        return 1

    queue = FileQueue(queue_root_for_project(project_root))
    queue_state = queue.queue_state(args.run_id)
    meta, meta_error = load_json_status(run_dir / "meta.json")
    result, result_error = load_json_status(run_dir / "result.json")
    payload = {
        "run_id": args.run_id,
        "run_path": run_dir.relative_to(project_root).as_posix(),
        "queue_state": queue_state,
        "run_status": meta.get("status") or "unknown",
        "result_status": result.get("status") or "unknown",
        "agent": result.get("agent") or meta.get("preferred_agent"),
        "project": meta.get("project"),
        "task_id": meta.get("task_id"),
    }
    errors = {}
    if meta_error is not None:
        errors["meta"] = meta_error
    if result_error is not None:
        errors["result"] = result_error
    if errors:
        payload["errors"] = errors
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


def cmd_orchestrate(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    max_steps = max(1, int(args.max_steps))
    steps = 0
    accepted_runs: list[str] = []
    last_status = "idle"

    while steps < max_steps:
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
                break
            next_task = ready_tasks[0]
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

        decision = evaluate_run_decision(project_root, run_dir, result_status=result_status)
        if decision["decision"] == "accept":
            accepted_runs.append(run_dir.name)
            last_status = "accepted"
            continue
        if decision["decision"] == "awaiting_review":
            last_status = "awaiting_review"
            break
        if decision["decision"] == "ask_human":
            last_status = "awaiting_approval"
            break

    payload = {
        "status": last_status,
        "project": project_root.name,
        "steps": steps,
        "accepted_runs": accepted_runs,
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
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if last_status != "error" else 1


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
    print(json.dumps(plan_dict, ensure_ascii=False, indent=2))
    return 0


# ── openclaw subcommands ──────────────────────────────────────────────────────


def _openclaw_error(message: str, code: str = "ERROR") -> None:
    """Write a JSON error to stderr."""
    json.dump({"error": message, "code": code}, sys.stderr, ensure_ascii=False)
    sys.stderr.write("\n")


def cmd_openclaw_status(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.project_path)
    except FileNotFoundError as exc:
        _openclaw_error(str(exc), "NOT_FOUND")
        return 1

    max_recent = getattr(args, "recent", 5)
    snapshot = refresh_metrics_snapshot(project_root, recent_limit=max(max_recent, 20))
    dashboard = build_project_dashboard(project_root, recent_limit=max_recent, ready_limit=3)

    payload = {
        "project": project_root.name,
        "queue": snapshot["queue"],
        "recent_runs": snapshot["recent_runs"][:max_recent],
        "pending_reviews": snapshot["reviews"]["pending_decisions"],
        "pending_hooks": snapshot["hooks"]["pending"],
        "failed_hooks": snapshot["hooks"]["failed"],
        "pending_approvals": dashboard["pending_approvals"],
        "retry_backlog": dashboard["retry_backlog"],
        "current_run": dashboard["current_run"],
        "recent_failures": dashboard["recent_failures"],
        "ready_tasks": dashboard["ready_tasks"],
        "metrics": {
            "updated_at": snapshot["updated_at"],
            "runs": snapshot["runs"],
            "reviews": snapshot["reviews"],
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_openclaw_enqueue(args: argparse.Namespace) -> int:
    try:
        project_root = resolve_project_root(args.project_path)
    except FileNotFoundError as exc:
        _openclaw_error(str(exc), "NOT_FOUND")
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
    refresh_metrics_snapshot(project_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
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

    run_id_or_path = args.run_id_or_path

    # Resolve run_dir: could be a run_id like RUN-0005 or a path
    run_dir: Path | None = None
    candidate = Path(run_id_or_path)
    if candidate.is_absolute() and candidate.is_dir():
        run_dir = candidate
    elif (project_root / run_id_or_path).is_dir():
        run_dir = (project_root / run_id_or_path).resolve()
    else:
        run_dir = find_run_dir(project_root, run_id_or_path)

    if run_dir is None:
        _openclaw_error(f"Run not found: {run_id_or_path}", "NOT_FOUND")
        return 1

    try:
        result = read_json(run_dir / "result.json")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        result = {}

    try:
        meta = read_json(run_dir / "meta.json")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        meta = {}

    run_id = result.get("run_id") or meta.get("run_id") or run_dir.name
    status = result.get("status") or meta.get("status") or "unknown"
    agent = result.get("agent") or meta.get("preferred_agent") or ""
    summary_text = result.get("summary") or meta.get("summary") or ""

    # Duration
    duration_seconds: float | None = None
    started_at = result.get("started_at") or meta.get("started_at")
    finished_at = result.get("finished_at") or result.get("completed_at")
    duration_seconds = duration_between(started_at, finished_at)
    validation = result.get("validation") or {}

    # Hook delivery status
    hook_status: dict = {}
    hook_path = run_dir / "hook.json"
    if hook_path.is_file():
        try:
            hook_data = read_json(hook_path)
            hook_status = {"delivery_status": hook_data.get("delivery_status")}
        except (json.JSONDecodeError, OSError):
            pass

    # Report path
    report_path_abs = run_dir / "report.md"
    report_path = ""
    if report_path_abs.is_file():
        try:
            report_path = report_path_abs.relative_to(project_root).as_posix()
        except ValueError:
            report_path = str(report_path_abs)

    payload = {
        "run_id": run_id,
        "status": status,
        "agent": agent,
        "duration_seconds": duration_seconds,
        "summary": summary_text,
        "validation": validation,
        "hook": hook_status,
        "report_path": report_path,
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

    dispatch_outcomes = [dispatch_hook_file(hook_path) for hook_path in pending_hook_paths]
    reconcile_outcomes = []
    for hook_path in failed_hook_paths:
        if hook_path.exists():
            reconcile_outcomes.append(dispatch_hook_file(hook_path))

    after_hooks = hook_counts(project_root)
    payload = {
        "project": project_root.name,
        "mode": args.mode,
        "schedule": {
            "interval_seconds": 900,
            "kind": "cron" if args.mode == "cron" else "event",
        },
        "queue": queue_counts(project_root),
        "hooks": {
            "before": before_hooks,
            "after": after_hooks,
        },
        "dispatch": summarize_hook_outcomes(dispatch_outcomes),
        "reconcile": summarize_hook_outcomes(reconcile_outcomes),
    }
    refresh_metrics_snapshot(project_root)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claw")
    subcommands = parser.add_subparsers(dest="command", required=True)

    create_project = subcommands.add_parser("create-project", help="Create a project scaffold")
    create_project.add_argument("project_slug")
    create_project.add_argument("destination_root", nargs="?")
    create_project.set_defaults(func=cmd_create_project)

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

    reclaim = subcommands.add_parser("reclaim", help="Move stale running jobs back to pending")
    reclaim.add_argument("project_root")
    reclaim.add_argument("--stale-after-seconds", type=int, required=True)
    reclaim.set_defaults(func=cmd_reclaim)

    status = subcommands.add_parser("status", help="Show queue and run status for one run")
    status.add_argument("project_root")
    status.add_argument("run_id")
    status.set_defaults(func=cmd_status)

    dashboard = subcommands.add_parser("dashboard", help="Show richer status for one or all projects")
    dashboard.add_argument("project_root", nargs="?")
    dashboard.add_argument("--all", action="store_true")
    dashboard.add_argument("--recent", type=int, default=5)
    dashboard.add_argument("--ready-limit", type=int, default=3)
    dashboard.set_defaults(func=cmd_dashboard)

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
    orchestrate.add_argument("--skip-review", action="store_true")
    orchestrate.add_argument("--recent", type=int, default=5)
    orchestrate.add_argument("--ready-limit", type=int, default=3)
    orchestrate.set_defaults(func=cmd_orchestrate)

    launch_plan = subcommands.add_parser("launch-plan", help="Preview execution plan for a task without running it")
    launch_plan.add_argument("task_path")
    launch_plan.set_defaults(func=cmd_launch_plan)

    review_batch = subcommands.add_parser("review-batch", help="Generate review batch artifacts for one project or all projects")
    review_batch.add_argument("project_root", nargs="?")
    review_batch.add_argument("--all", action="store_true", help="Process all projects in the repo")
    review_batch.add_argument("--dry-run", action="store_true", help="Print what would be written without creating files")
    review_batch.set_defaults(func=cmd_review_batch)

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
    oc_enqueue.set_defaults(func=cmd_openclaw_enqueue)

    oc_review_batch = openclaw_sub.add_parser("review-batch", help="Generate review batches and return JSON summary")
    oc_review_batch.add_argument("project_path")
    oc_review_batch.add_argument("--dry-run", dest="dry_run", action="store_true")
    oc_review_batch.set_defaults(func=cmd_openclaw_review_batch)

    oc_summary = openclaw_sub.add_parser("summary", help="Return structured summary of a run as JSON")
    oc_summary.add_argument("project_path")
    oc_summary.add_argument("run_id_or_path")
    oc_summary.set_defaults(func=cmd_openclaw_summary)

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
