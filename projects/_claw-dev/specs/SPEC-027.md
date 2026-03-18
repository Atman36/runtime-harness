# SPEC-027 — Shared session files for cross-agent handoff

## Context

The claw project is at `/Users/Apple/progect/claw`.

`claw` already persists session continuity (`state/sessions/`) and safe project
file exchange, but it still lacks a narrow shared file contract for task-scoped
handoff between agents. That forces Claude/Codex collaboration to either rely
on chat history or to scatter ad-hoc notes in arbitrary project paths.

## Goal

Add a task-scoped shared session-files contract, so multiple agents can write
and read the same handoff artifacts from disk without introducing hidden state.

## Scope

- Persist task-scoped shared files under a deterministic state path
- Maintain a manifest with author/note metadata for inspectable handoff state
- Expose CLI commands to put, list, and fetch shared session files
- Surface manifest visibility from `session-status`

## Constraints

- The contract must stay filesystem-first and restart-safe
- Shared files must remain task-scoped, not tied to one engine/runtime
- Relative paths must stay inside the session-files root
- Existing session continuity and file-exchange flows must not regress

## Acceptance Criteria

- Claude and Codex can exchange task-scoped files through a stable contract
- Shared session files have a machine-readable manifest and schema validation
- `session-status` exposes the current shared-files summary for discovery
- `bash tests/run_all.sh` passes

## Notes

- This is a narrow handoff layer, not a replacement for specs/PRDs committed in
  normal project directories.
