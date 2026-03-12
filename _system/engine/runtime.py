from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from _system.engine.file_queue import DuplicateJobError, FileQueue


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


def execute_run_task(repo_root: Path, task_path: str, *, execute: bool = False) -> Path:
    command = ["bash", str(repo_root / "scripts" / "run_task.sh")]
    if execute:
        command.append("--execute")
    command.append(task_path)
    completed = run_command(command, cwd=repo_root)
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


def project_root_from_run_dir(run_dir: Path) -> Path:
    project_root = run_dir
    while project_root.name != "runs":
        if project_root.parent == project_root:
            raise RuntimeError(f"Could not resolve project root from run dir: {run_dir}")
        project_root = project_root.parent
    return project_root.parent


def enqueue_run(run_dir: Path, *, state: str = "pending") -> dict:
    project_root = project_root_from_run_dir(run_dir)
    queue = FileQueue(queue_root_for_project(project_root))
    payload = build_queue_payload(project_root, run_dir)
    try:
        queue.enqueue(payload, state=state)
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
