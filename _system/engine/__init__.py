"""Minimal filesystem-backed engine primitives for claw."""

from _system.engine.file_queue import ClaimedJob, DuplicateJobError, FileQueue, QueueEmpty
from _system.engine.runtime import (
    build_queue_payload,
    enqueue_run,
    execute_run_task,
    find_run_dir,
    project_root_from_run_dir,
    queue_root_for_project,
    read_json,
    resolve_project_root,
    run_command,
)

__all__ = [
    "ClaimedJob",
    "DuplicateJobError",
    "FileQueue",
    "QueueEmpty",
    "build_queue_payload",
    "enqueue_run",
    "execute_run_task",
    "find_run_dir",
    "project_root_from_run_dir",
    "queue_root_for_project",
    "read_json",
    "resolve_project_root",
    "run_command",
]
