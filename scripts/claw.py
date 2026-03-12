#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


REPO_ROOT = repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _system.engine.file_queue import DuplicateJobError, FileQueue, QueueEmpty  # noqa: E402


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def run_command(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)


def resolve_project_root(argument: str) -> Path:
    path = Path(argument).expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"Project root not found: {argument}")
    if not (path / "state" / "project.yaml").is_file():
        raise FileNotFoundError(f"Expected project root with state/project.yaml: {argument}")
    return path


def queue_root_for_project(project_root: Path) -> Path:
    return project_root / "state" / "queue"


def execute_run_task(task_path: str, *, execute: bool = False) -> Path:
    command = ["bash", str(REPO_ROOT / "scripts" / "run_task.sh")]
    if execute:
        command.append("--execute")
    command.append(task_path)
    completed = run_command(command, cwd=REPO_ROOT)
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)

    created_line = ""
    for line in (completed.stdout or "").splitlines():
        if line.startswith("Created task run: "):
            created_line = line
    if not created_line:
        raise RuntimeError("Could not determine created run directory from run_task.sh output")

    return Path(created_line.removeprefix("Created task run: ").strip()).resolve()


def build_queue_payload(project_root: Path, run_dir: Path) -> dict:
    job = read_json(run_dir / "job.json")
    task = job.get("task", {})
    relative_run_path = run_dir.relative_to(project_root).as_posix()
    return {
        "job_id": job["run_id"],
        "job_version": 1,
        "run_id": job["run_id"],
        "run_path": relative_run_path,
        "project": job.get("project"),
        "preferred_agent": job.get("preferred_agent"),
        "review_policy": job.get("review_policy"),
        "created_at": job.get("created_at"),
        "task": {
            "id": task.get("id"),
            "title": task.get("title"),
            "priority": task.get("priority"),
        },
    }


def enqueue_run(run_dir: Path) -> dict:
    project_root = run_dir
    while project_root.name != "runs":
        if project_root.parent == project_root:
            raise RuntimeError(f"Could not resolve project root from run dir: {run_dir}")
        project_root = project_root.parent
    project_root = project_root.parent

    queue = FileQueue(queue_root_for_project(project_root))
    payload = build_queue_payload(project_root, run_dir)
    try:
        queue.enqueue(payload)
    except DuplicateJobError as exc:
        raise RuntimeError(str(exc)) from exc
    return payload


def find_run_dir(project_root: Path, run_id: str) -> Path | None:
    queue = FileQueue(queue_root_for_project(project_root))
    queued_path = queue.find_job(run_id)
    if queued_path is not None:
        payload = read_json(queued_path)
        run_path = payload.get("run_path")
        if run_path:
            return (project_root / run_path).resolve()

    runs_root = project_root / "runs"
    for meta_path in runs_root.rglob("meta.json"):
        meta = read_json(meta_path)
        if meta.get("run_id") == run_id:
            return meta_path.parent.resolve()
    return None


def cmd_create_project(args: argparse.Namespace) -> int:
    command = ["bash", str(REPO_ROOT / "scripts" / "create_project.sh"), args.project_slug]
    if args.destination_root:
        command.append(args.destination_root)
    completed = run_command(command, cwd=REPO_ROOT)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    return completed.returncode


def cmd_run(args: argparse.Namespace) -> int:
    run_dir = execute_run_task(args.task_path, execute=args.execute)
    if args.enqueue:
        payload = enqueue_run(run_dir)
        print(json.dumps({"status": "queued", "job_id": payload["job_id"], "run_path": payload["run_path"]}, ensure_ascii=False))
        return 0
    print(json.dumps({"status": "created", "run_dir": str(run_dir)}, ensure_ascii=False))
    return 0


def cmd_enqueue(args: argparse.Namespace) -> int:
    run_dir = execute_run_task(args.task_path, execute=False)
    payload = enqueue_run(run_dir)
    print(json.dumps({"status": "queued", "job_id": payload["job_id"], "run_path": payload["run_path"]}, ensure_ascii=False))
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    queue = FileQueue(queue_root_for_project(project_root))
    claimed_count = 0

    while True:
        try:
            claimed = queue.claim()
        except QueueEmpty:
            if claimed_count == 0:
                print(json.dumps({"status": "idle"}, ensure_ascii=False))
            return 0

        claimed_count += 1
        payload = queue.read_claimed(claimed)
        run_dir = (project_root / payload["run_path"]).resolve()
        completed = run_command(["python3", str(REPO_ROOT / "scripts" / "execute_job.py"), str(run_dir)], cwd=REPO_ROOT)
        if completed.returncode == 0:
            queue.ack(claimed)
            status = "done"
        else:
            queue.fail(claimed)
            status = "failed"

        sys.stdout.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        print(
            json.dumps(
                {
                    "job_id": payload["job_id"],
                    "run_path": payload["run_path"],
                    "queue_state": status,
                    "exit_code": completed.returncode,
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
    run.set_defaults(func=cmd_run)

    enqueue = subcommands.add_parser("enqueue", help="Create a run and place it into the queue")
    enqueue.add_argument("task_path")
    enqueue.set_defaults(func=cmd_enqueue)

    worker = subcommands.add_parser("worker", help="Claim queued jobs for one project")
    worker.add_argument("project_root")
    worker.add_argument("--once", action="store_true")
    worker.set_defaults(func=cmd_worker)

    dispatch = subcommands.add_parser("dispatch", help="Dispatch pending hooks for a project")
    dispatch.add_argument("project_root")
    dispatch.set_defaults(func=cmd_dispatch)

    reconcile = subcommands.add_parser("reconcile", help="Retry stale or failed hooks for a project")
    reconcile.add_argument("project_root")
    reconcile.set_defaults(func=cmd_reconcile)

    status = subcommands.add_parser("status", help="Show queue and run status for one run")
    status.add_argument("project_root")
    status.add_argument("run_id")
    status.set_defaults(func=cmd_status)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
