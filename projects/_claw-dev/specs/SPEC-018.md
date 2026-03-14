# SPEC-018 — Agent inbox and atomic task claim/release

## Context

The claw project is at `/Users/Apple/progect/claw`.

Once heartbeat wakes exist, `claw` needs a clear notion of what work belongs to
which agent and how that work is claimed. PaperClip solves this with an inbox
and atomic checkout semantics. `claw` already has tasks, queue items, and run
artifacts, but task ownership is still too implicit for multi-agent
coordination.

## Goal

Add an agent-scoped inbox and atomic claim/release lifecycle for tasks, so
ownership is explicit, conflicts are structured, and blocked or released work
remains visible in file-backed artifacts.

## Scope

- Add agent inbox projection from existing project artifacts
- Add claim/release contract for tasks with idempotent same-owner behavior
- Surface conflict outcomes in structured CLI/JSON form
- Preserve history for `in_progress`, `blocked`, and `released` states
- Integrate claim/release with wake queue and worker selection

## Constraints

- No hidden assignment database
- Task ownership must be derivable from files on disk
- Claim conflicts must not silently overwrite current ownership
- Existing task/spec schema should evolve minimally and compatibly

## Acceptance Criteria

- Agent inbox can be materialized from project artifacts
- Claim succeeds once, is idempotent for current owner, and conflicts for others
- Release writes a visible reason trail
- Blocked tasks stay visible and do not disappear from coordination state
- `bash tests/run_all.sh` passes

## Notes

- This slice should feel like PaperClip checkout semantics adapted to `claw`.
- It is a coordination primitive, not a UI feature.
