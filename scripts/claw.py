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

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
