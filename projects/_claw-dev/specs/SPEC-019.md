# SPEC-019 — Resumable agent session state

## Context

The claw project is at `/Users/Apple/progect/claw`.

PaperClip persists resumable session handles so the next heartbeat can continue
the same work context. `claw` already stores immutable run artifacts, but it
does not yet persist a dedicated per-agent/per-task continuity layer that can
be intentionally resumed or rotated.

## Goal

Persist resumable session state plus a compact handoff summary per agent/task,
so the next wake can continue prior work without replaying the full history.

## Scope

- Add a file-backed session-state artifact for agent/task continuity
- Persist both provider-neutral resume handle data and compact handoff summary
- Support explicit session reset/rotate for stale or confused state
- Integrate continuity lookup into the wake/claim execution path

## Constraints

- State must remain inspectable from disk
- Resume contract must not be hard-coded to one CLI provider
- Reset/rotate must be explicit and auditable
- Immutable run artifacts stay immutable; continuity lives alongside them

## Acceptance Criteria

- Session state can be resumed across multiple wakes for the same work item
- Handoff summary is human-readable and stored in artifacts
- Reset/rotate path clears stale continuity without damaging past runs
- Existing runs remain readable without the new session artifacts
- `bash tests/run_all.sh` passes

## Notes

- Favor a thin continuity contract over a heavyweight conversation database.
