"""Minimal filesystem-backed engine primitives for claw."""

from _system.engine.error_codes import REASON_CODES, build_error_envelope
from _system.engine.workflow_contract import Commands, WorkflowContract, WorkflowLoadError, contract_summary, load_workflow_contract, validate_workflow_contract
from _system.engine.file_queue import ClaimedJob, DuplicateJobError, FileQueue, QueueEmpty
from _system.engine.wake_queue import VALID_WAKE_REASONS, WakeQueue, wake_root_for_project
from _system.engine.task_claims import TaskClaimStore, claims_root_for_project
from _system.engine.session_store import SessionStore, sessions_root_for_project
from _system.engine.org_graph import DelegationCheck, OrgGraphError, delegation_targets, escalation_chain, load_org_graph, validate_delegation
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
    "REASON_CODES",
    "build_error_envelope",
    "Commands",
    "WorkflowContract",
    "WorkflowLoadError",
    "contract_summary",
    "load_workflow_contract",
    "validate_workflow_contract",
    "ClaimedJob",
    "DuplicateJobError",
    "FileQueue",
    "QueueEmpty",
    "WakeQueue",
    "VALID_WAKE_REASONS",
    "TaskClaimStore",
    "SessionStore",
    "OrgGraphError",
    "DelegationCheck",
    "load_org_graph",
    "validate_delegation",
    "delegation_targets",
    "escalation_chain",
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
    "wake_root_for_project",
    "claims_root_for_project",
    "sessions_root_for_project",
]
