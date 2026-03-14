# SPEC-017 — Heartbeat wake queue and coalescing

## Context

The claw project is at `/Users/Apple/progect/claw`.

`claw` already has a file-backed run queue and orchestration loop, but it does
not yet model short-burst heartbeat wakes as first-class artifacts. A local
analysis of `PaperClip` showed a useful pattern: agents wake on typed triggers
(`timer`, `assignment`, `mention`, `manual`, `approval`) and duplicate wakeups
are coalesced rather than spawning duplicate execution windows.

In `claw`, the same idea should be adapted to filesystem artifacts instead of a
database queue.

## Goal

Add a file-backed wake queue with typed wake reasons and deterministic
coalescing rules, so heartbeat-style agent execution can be layered on top of
the existing run queue without hidden runtime state.

## Scope

- Add a wake artifact contract under project `state/`
- Support wake reasons: `timer`, `assignment`, `mention`, `manual`, `approval`
- Coalesce duplicate pending wakes for the same agent/task scope
- Add CLI inspection/debug output for pending and coalesced wakes
- Keep wake handling compatible with current `worker` / `orchestrate` flow

## Constraints

- Filesystem remains source of truth
- No database, daemon-only memory, or external scheduler dependency
- Coalescing must be deterministic and auditable from artifacts
- Wake queue must not replace the existing run queue; it feeds it

## Acceptance Criteria

- Wake files are written to disk with typed reason and context
- Duplicate wakes merge into a single pending wake with coalesced metadata
- CLI can show pending/coalesced wake state without reading internal Python objects
- Existing run queue behavior remains valid
- `bash tests/run_all.sh` passes

## Notes

- This is the foundation for later inbox/session/delegation work.
- Adapt the pattern, not the PaperClip implementation details.
