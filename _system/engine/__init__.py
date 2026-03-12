"""Minimal filesystem-backed engine primitives for claw."""

from _system.engine.file_queue import ClaimedJob, DuplicateJobError, FileQueue, QueueEmpty
from _system.engine.task_planner import ExecutionPlan, RoutingDecision, TaskRunPlan, plan_task_run, plan_to_dict
from _system.engine.agent_exec import AgentCommand, build_agent_command
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
    "AgentCommand",
    "RoutingDecision",
    "ExecutionPlan",
    "TaskRunPlan",
    "build_queue_payload",
    "enqueue_run",
    "execute_run_task",
    "find_run_dir",
    "project_root_from_run_dir",
    "queue_root_for_project",
    "read_json",
    "resolve_project_root",
    "build_agent_command",
    "plan_task_run",
    "plan_to_dict",
    "run_command",
]
