---
contract_version: 1
project: "_claw-dev"
approval_gates:
  require_human_approval_on_failure: true
  require_approval_before_first_run: false
retry_policy:
  failure_budget: 3
  backoff_base_seconds: 30
  backoff_max_seconds: 300
timeout_policy:
  worker_lease_seconds: 600
  run_timeout_seconds: 3600
scope:
  edit_scope:
    - docs
    - scripts
    - tests
  allowed_agents:
    - claude
    - codex
    - auto
notes: "Demo project workflow contract for local orchestration and validation examples."
---

# Workflow Contract — demo-project

This file defines the orchestration policy for the demo project.
Edit the YAML front matter above to change runtime behavior.

## Approval Gates

- `require_human_approval_on_failure`: When true, the orchestrator pauses and
  requests human approval after the configured failure budget is exhausted.
- `require_approval_before_first_run`: When true, every new task requires prior
  approval before the first execution.

## Retry Policy

- `failure_budget`: Number of consecutive failed decisions allowed before the
  orchestrator stops and asks for human input.
- `backoff_base_seconds` / `backoff_max_seconds`: Exponential retry bounds for
  queue retries.

## Timeout Policy

- `worker_lease_seconds`: How long a worker may hold a queue item before it can
  be reclaimed.
- `run_timeout_seconds`: Maximum wall-clock time per agent run.

## Scope

- `edit_scope`: Directories that are expected to be in-bounds for this project.
- `allowed_agents`: Agent keys that may be selected by routing or task policy.
