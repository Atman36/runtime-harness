# SPEC-014 — Simple listener registry for orchestrator events

## Context

The claw project is at `/Users/Apple/progect/claw`.

The project already fires events into `events.jsonl` (orchestrator-level) and
has a hook system in `state/hooks/`. Currently, reacting to specific run events
(e.g. "run finished → notify Slack", "review created → auto-assign") requires
ad-hoc code scattered across `claw.py` or external cron jobs.

Inspired by crewAI's listener/event pattern: a declarative registry that maps
event types to trusted commands, with side effects written back to artifacts.

## Goal

Add a `_system/registry/listeners.yaml` file that defines listeners for four
orchestrator events. When claw processes one of these events (run_started,
run_finished, review_created, approval_requested), it reads the registry and
dispatches matching listeners as trusted commands.

## Desired outcome

1. `_system/registry/listeners.yaml` defines event → trusted command mappings
2. Listener dispatch is called from the relevant claw.py event-processing paths
3. Listener output / side effects are written to `state/listener_log.jsonl`
4. Failed listeners are logged but do not abort the primary event path
5. Tests cover: registry loaded, matching listener called, non-matching skipped,
   failure logged without crash

## Registry format

`_system/registry/listeners.yaml`:
```yaml
listeners:
  - id: notify-on-finish
    event: run_finished
    condition:                 # optional; all conditions must match
      status: success
    command: openclaw system event --text "Run {run_id} finished" --mode now
    enabled: true

  - id: log-review-created
    event: review_created
    command: openclaw summary {project_root} {run_id}
    enabled: false
```

Template variables available: `{run_id}`, `{project_root}`, `{status}`,
`{task_id}`, `{ts}`.

## Scope

### In scope
- `_system/registry/listeners.yaml` with the four event types documented
- `_system/engine/listener_dispatch.py`: load registry, match event, render
  template vars, invoke via `trusted_command`, append to `listener_log.jsonl`
- Dispatch called from `claw.py` at the correct event emission points:
  - `run_finished` → inside `cmd_worker` after `append_run_event(..., "run_finished", ...)`
    (around line 1778 in `claw.py`) and after the same event in `cmd_run`
  - `review_created` → inside `maybe_trigger_review` or its call site
  - `approval_requested` → inside `cmd_ask_human` after approval record is written
  - `run_started` → inside `cmd_worker` after job is claimed (optional, lower priority)
- `listener_log.jsonl` format: `{ts, listener_id, event, run_id, status, error}`
- Example listeners in registry for the four event types (all `enabled: false` by default)
- Tests: dispatch called on match, skipped on mismatch, error path logs to file

### Out of scope
- Global async event bus
- External webhook transports
- Per-project listener overrides (single global registry for now)
- UI for managing listeners

## Files to modify / create

### CREATE: `_system/registry/listeners.yaml`
Four example listeners (all `enabled: false` by default) covering the four
event types. Document the format in comments.

### CREATE: `_system/engine/listener_dispatch.py`
- `load_listeners(registry_path) -> list[dict]`
- `match_listeners(listeners, event_type, context) -> list[dict]`
- `dispatch_listeners(matched, context, log_path)` — calls trusted_command,
  appends to log, catches exceptions

### MODIFY: `scripts/claw.py`
Call `dispatch_listeners` at the four event emission points listed above.
**Do not call from `cmd_openclaw_summary`** — that is a read path and would
cause listeners to fire on every status check. Pass minimal context dict:
`{run_id, project_root, status, ts}`.

### MODIFY / CREATE: `tests/`
- `test_listener_dispatch.py`: unit tests for load, match, dispatch
- Integration check: listener_log.jsonl written after a mocked run_finished

## Acceptance Criteria

- Registry file exists and loads without error
- A listener with matching event type is dispatched; non-matching is skipped
- `listener_log.jsonl` has one entry per dispatch attempt with status
- A listener that raises an exception does not abort the primary claw event path
- All four event types have at least one disabled example in registry
- `bash tests/run_all.sh` passes

## Constraints

- Listener dispatch must be fire-and-forget / non-blocking relative to claw flow
- Trusted command security model must not be bypassed (use existing `trusted_command.py`)
- Do not add external dependencies
- Keep registry YAML minimal — no complex DSL
