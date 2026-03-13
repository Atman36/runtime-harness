# SPEC-011 — Mandatory orchestrator completion signal

## Context

The claw project is at `/Users/Apple/progect/claw`.

A recent Codex run completed successfully but did not execute the prompt footer
`openclaw system event --text ... --mode now`. As a result, the operator saw no
completion signal even though the task itself finished.

This proved that prompt-level completion notifications are **best-effort** and
must not be treated as the delivery contract between an agent run and the
orchestrator.

The project already has:
- file-backed completion hooks in `state/hooks/{pending,sent,failed}`
- `claw openclaw wake` for reconcile/event delivery
- an OpenClaw system-event bridge layered on top of hook payloads

What is missing is an explicit runtime-level guarantee that a completed run either:
1. emits a delivery signal through the orchestrator path, or
2. is marked in state as missing mandatory delivery and surfaced as such.

## Goal

Introduce a mandatory completion-delivery contract for orchestrated runs so the
orchestrator can reliably detect whether completion was actually signaled.

## Desired outcome

1. A run has explicit delivery status in artifacts/state (not implicit in prompt text)
2. Successful execution without delivered signal is visible as a contract violation or recoverable pending state
3. `claw openclaw status` (or equivalent status surface) exposes runs waiting for mandatory delivery
4. Tests cover the case where the agent succeeds but no prompt-footer notify happens

## Scope

### In scope
- Add a runtime/artifact field that records mandatory completion signal state
- Ensure the execution path records whether delivery happened via orchestrator-managed mechanism
- Expose undelivered-completion state in CLI status output
- Add tests for success-without-agent-footer-notify
- Update docs explaining that prompt footer is advisory, not the contract

### Out of scope
- Full external webhook transport redesign
- Multi-project scheduler changes
- Replacing file-backed hooks as source of truth
- Browser/UI work

## Suggested implementation direction

Prefer a design like this:

- Extend run result/meta with a `delivery` section, for example:
  - `required: true`
  - `hook_written: true`
  - `system_event_emitted: true|false`
  - `callback_available: true|false`
  - `status: pending|delivered|failed|missing`
  - `last_error: ...`
- Define the runtime contract so completion is considered deliverable when the orchestrator-managed hook/callback path has enough information on disk, even if the nested agent never ran the footer command.
- If the hook exists but chat wake has not happened yet, surface it as `pending_delivery`, not silent success.
- If some path is truly mandatory and missing, mark it loudly in state/status.

## Files to modify / create

### MODIFY: `scripts/execute_job.py`
Record mandatory delivery state into result/meta artifacts when the run completes.

### MODIFY: `scripts/hooklib.py`
Provide helper(s) for computing delivery contract state from hook payload/status.

### MODIFY: `scripts/claw.py`
Extend `openclaw status` (or status-adjacent command) to expose pending/missing mandatory delivery.

### MODIFY: tests
Add or update tests covering:
- successful run where nested agent never executes footer notify
- hook exists on disk and orchestrator still reports pending delivery
- delivery becomes visible as completed after wake/bridge path runs

### MODIFY: docs
Document that:
- prompt footer notification is advisory only
- mandatory delivery is owned by orchestrator artifacts/state
- operators should inspect runtime delivery status, not assume chat notify happened

## Acceptance Criteria

- A successful run without agent-issued footer notify is still represented as `pending_delivery` or equivalent in orchestrator-managed state
- `claw openclaw status` (or equivalent) exposes at least one machine-readable field for undelivered mandatory completion
- Delivery state changes after `claw openclaw wake` / callback bridge reconciliation
- Existing hook/source-of-truth behavior remains intact
- Tests reproduce and protect against the original failure mode

## Constraints

- Do not rely on "agent promised to run command at end" as the primary mechanism
- Keep file-backed hooks as source of truth
- Keep the slice narrow and locally testable
- Avoid broad architecture rewrites
