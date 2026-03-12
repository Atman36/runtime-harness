#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


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
DEFAULT_WORKER_LEASE_SECONDS = 600
DEFAULT_RETRY_BACKOFF_BASE_SECONDS = 30
DEFAULT_RETRY_BACKOFF_MAX_SECONDS = 300


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
    meta = read_json(run_dir / "meta.json")
    result = read_json(run_dir / "result.json")
    payload = {
        "run_id": args.run_id,
        "run_path": run_dir.relative_to(project_root).as_posix(),
        "queue_state": queue_state,
        "run_status": meta.get("status"),
        "result_status": result.get("status"),
        "agent": result.get("agent") or meta.get("preferred_agent"),
        "project": meta.get("project"),
        "task_id": meta.get("task_id"),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


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

    payload = {
        "project": project_root.name,
        "queue": snapshot["queue"],
        "recent_runs": snapshot["recent_runs"][:max_recent],
        "pending_reviews": snapshot["reviews"]["pending_decisions"],
        "pending_hooks": snapshot["hooks"]["pending"],
        "failed_hooks": snapshot["hooks"]["failed"],
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
