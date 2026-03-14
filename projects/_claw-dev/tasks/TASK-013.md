---
id: TASK-013
title: "Step-level HITL checkpoint: approval_checkpoint.json"
status: done
spec: ../specs/SPEC-013.md
preferred_agent: codex
review_policy: standard
priority: medium
project: _claw-dev
needs_review: true
risk_flags: [hitl, pause-resume, run-status]
tags: [human-feedback, approvals, orchestration, epic-13]
dependencies: [TASK-012]
epic: 13
---

# Task

## Goal
Add a within-run pause/resume primitive: an agent writes `approval_checkpoint.json`,
the runner exits with code 2, the worker calls `queue.await_approval()` (existing
queue state), and `resolve-checkpoint` resumes or cancels via `queue.approve()`.

## Notes
- Do NOT add new status values to result.json or meta.json — schemas only allow
  existing enums. result.json stays `failed` (exit code 2 is non-zero).
- Queue state: use existing `awaiting_approval` + `FileQueue.await_approval()`.
- Distinguishing signal: exit code 2 + `approval_checkpoint.json` status: pending.
- `resolve-checkpoint accept` → `queue.approve()` re-queues; reject → stays failed.
- Project-level approval flow (ask-human / resolve-approval) must remain unchanged.
