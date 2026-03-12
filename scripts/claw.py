#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


REPO_ROOT = repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _system.engine import FileQueue, QueueEmpty, enqueue_run, execute_run_task, find_run_dir, plan_task_run, plan_to_dict, queue_root_for_project, read_json, resolve_project_root, run_command  # noqa: E402
from generate_review_batch import POLICY_PATH, classify_run, generate_batches, load_policy, load_run  # noqa: E402


CADENCE_STATE_FILE = "review_cadence.json"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    if args.enqueue:
        queue_state = "awaiting_approval" if args.awaiting_approval else "pending"
        payload = enqueue_run(run_dir, state=queue_state)
        print(
            json.dumps(
                {"status": "queued", "job_id": payload["job_id"], "run_path": payload["run_path"], "queue_state": queue_state},
                ensure_ascii=False,
            )
        )
        return 0
    print(json.dumps({"status": "created", "run_dir": str(run_dir)}, ensure_ascii=False))
    return 0


def cmd_enqueue(args: argparse.Namespace) -> int:
    queue_state = "awaiting_approval" if args.awaiting_approval else "pending"
    run_dir = execute_run_task(REPO_ROOT, args.task_path, execute=False)
    payload = enqueue_run(run_dir, state=queue_state)
    print(json.dumps({"status": "queued", "job_id": payload["job_id"], "run_path": payload["run_path"], "queue_state": queue_state}, ensure_ascii=False))
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    queue = FileQueue(queue_root_for_project(project_root))
    claimed_count = 0
    review_policy = None

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
            claimed = queue.claim()
        except QueueEmpty:
            if claimed_count == 0:
                print(json.dumps({"status": "idle", "reclaimed": reclaimed}, ensure_ascii=False))
            return 0

        claimed_count += 1
        payload = queue.read_claimed(claimed)
        run_dir = (project_root / payload["run_path"]).resolve()
        completed = run_command(["python3", str(REPO_ROOT / "scripts" / "execute_job.py"), str(run_dir)], cwd=REPO_ROOT)
        result_status = "failed"
        try:
            result_status = read_json(run_dir / "result.json").get("status") or result_status
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            result_status = "success" if completed.returncode == 0 else "failed"

        if completed.returncode == 0:
            queue.ack(claimed)
            status = "done"
        else:
            queue.fail(claimed)
            status = "failed"

        if review_policy is not None:
            maybe_trigger_review(project_root, run_dir, result_status, review_policy)

        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        print(
            json.dumps(
                {
                    "job_id": payload["job_id"],
                    "run_path": payload["run_path"],
                    "queue_state": status,
                    "exit_code": completed.returncode,
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
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


def cmd_reconcile(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    completed = run_command(["python3", str(REPO_ROOT / "scripts" / "reconcile_hooks.py"), str(project_root)], cwd=REPO_ROOT)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


def cmd_approve(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    queue = FileQueue(queue_root_for_project(project_root))
    if not queue.approve(args.run_id):
        print(json.dumps({"status": "not_found", "job_id": args.run_id}, ensure_ascii=False))
        return 1
    print(json.dumps({"status": "approved", "job_id": args.run_id, "queue_state": "pending"}, ensure_ascii=False))
    return 0


def cmd_reclaim(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    queue = FileQueue(queue_root_for_project(project_root))
    reclaimed = queue.reclaim_stale_running(args.stale_after_seconds)
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

    queue = FileQueue(queue_root_for_project(project_root))

    queue_counts: dict[str, int] = {}
    for state in ("pending", "running", "awaiting_approval", "done", "failed"):
        state_dir = queue_root_for_project(project_root) / state
        if state_dir.is_dir():
            queue_counts[state] = len(list(state_dir.glob("*.json")))
        else:
            queue_counts[state] = 0

    # Recent runs: collect result.json from runs/ sorted by path (RUN number)
    runs_root = project_root / "runs"
    result_files: list[Path] = []
    if runs_root.is_dir():
        for date_dir in sorted(runs_root.iterdir()):
            if not date_dir.is_dir():
                continue
            for run_dir in sorted(date_dir.iterdir()):
                if run_dir.is_dir() and run_dir.name.startswith("RUN-"):
                    result_path = run_dir / "result.json"
                    if result_path.is_file():
                        result_files.append(result_path)

    max_recent = getattr(args, "recent", 5)
    recent_result_files = result_files[-max_recent:]

    recent_runs = []
    for result_path in reversed(recent_result_files):
        try:
            result = read_json(result_path)
        except (json.JSONDecodeError, OSError):
            continue
        run_id = result.get("run_id") or result_path.parent.name
        finished_at = result.get("finished_at") or result.get("completed_at") or result.get("created_at")
        recent_runs.append({
            "run_id": run_id,
            "status": result.get("status", "unknown"),
            "agent": result.get("agent", ""),
            "finished_at": finished_at,
        })

    # pending_reviews: count from review_cadence.json or reviews dir
    pending_reviews = 0
    cadence_path = project_root / "state" / CADENCE_STATE_FILE
    reviews_dir = project_root / "reviews"
    decisions_dir = reviews_dir / "decisions"
    if decisions_dir.is_dir():
        for stub_path in decisions_dir.glob("*.json"):
            try:
                stub = read_json(stub_path)
                if stub.get("decision") == "pending":
                    pending_reviews += 1
            except (json.JSONDecodeError, OSError):
                pass

    # Hook counts
    hooks_root = project_root / "state" / "hooks"
    pending_hooks = 0
    failed_hooks = 0
    if hooks_root.is_dir():
        pending_dir = hooks_root / "pending"
        failed_dir = hooks_root / "failed"
        if pending_dir.is_dir():
            pending_hooks = len(list(pending_dir.iterdir()))
        if failed_dir.is_dir():
            failed_hooks = len(list(failed_dir.iterdir()))

    payload = {
        "project": project_root.name,
        "queue": queue_counts,
        "recent_runs": recent_runs,
        "pending_reviews": pending_reviews,
        "pending_hooks": pending_hooks,
        "failed_hooks": failed_hooks,
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
        from generate_review_batch import POLICY_PATH as _POLICY_PATH, generate_batches as _generate_batches, load_policy as _load_policy
        policy = _load_policy(_POLICY_PATH)
    except (FileNotFoundError, ValueError) as exc:
        _openclaw_error(f"Failed to load reviewer policy: {exc}", "POLICY_ERROR")
        return 1

    try:
        import io
        _buf = io.StringIO()
        import contextlib
        with contextlib.redirect_stdout(_buf):
            batches = _generate_batches(project_root, policy, dry_run=dry_run)
        _log = _buf.getvalue()
        if _log.strip():
            print(_log.rstrip(), file=sys.stderr)
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
    if started_at and finished_at:
        try:
            from datetime import datetime as _dt
            start = _dt.fromisoformat(started_at.replace("Z", "+00:00"))
            end = _dt.fromisoformat(finished_at.replace("Z", "+00:00"))
            duration_seconds = round((end - start).total_seconds(), 1)
        except Exception:
            pass

    # Validation
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

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
