---
id: TASK-017
title: "Heartbeat wake queue and coalescing"
status: ready
spec: ../specs/SPEC-017.md
preferred_agent: codex
review_policy: standard
priority: high
project: _claw-dev
needs_review: true
risk_flags: [queue, wakeup, concurrency, orchestration]
tags: [paperclip-import, heartbeat, wake-queue, epic-14]
dependencies: [TASK-013]
epic: 14
---

# Task

## Goal
Add a filesystem-backed wake queue for agent heartbeats with typed wake reasons
and coalescing semantics, so `claw` can trigger short-burst work loops without
starting duplicate runs for the same agent/task context.

## Notes
- Adapt the PaperClip heartbeat model, not its DB/UI architecture.
- Wake reasons should at minimum cover `timer`, `assignment`, `mention`,
  `manual`, and `approval`.
- Duplicate wakes for the same agent/task should merge into one pending wake,
  not create fan-out run spam.
- The queue must stay inspectable from disk and debuggable by CLI.
