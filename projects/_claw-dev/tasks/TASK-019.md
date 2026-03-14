---
id: TASK-019
title: "Resumable agent session state"
status: ready
spec: ../specs/SPEC-019.md
preferred_agent: codex
review_policy: standard
priority: medium
project: _claw-dev
needs_review: true
risk_flags: [sessions, runtime-state, continuity]
tags: [paperclip-import, sessions, continuity, epic-14]
dependencies: [TASK-018]
epic: 14
---

# Task

## Goal
Persist resumable session state and a compact handoff summary per agent/task,
so the next wake can continue the same slice of work without replaying the full
context from scratch.

## Notes
- Session continuity must be file-backed and resettable.
- Store both machine-resumable handle and human-readable handoff summary.
- Provide explicit session reset/rotate to recover from drift or stale context.
- Avoid coupling continuity to a single provider-specific CLI format.
