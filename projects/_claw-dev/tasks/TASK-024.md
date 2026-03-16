---
id: TASK-024
title: "Operator session memory and resume handles"
status: done
spec: ../specs/SPEC-024.md
preferred_agent: codex
review_policy: standard
priority: medium
project: _claw-dev
needs_review: true
risk_flags: [sessions, continuity, resume, transport]
tags: [operator-ux, sessions, continuity, epic-15]
dependencies: [TASK-023]
epic: 15
---

# Task

## Goal
Persist operator-scoped session memory and resumable handles, so follow-up
messages can continue the same thread deliberately instead of replaying full
context or relying on hidden transport state.

## Notes
- Store one resumable handle per scope and engine, plus explicit reset/new-thread
  semantics.
- Keep the stored handle provider-neutral; adapter-specific resume lines can be
  derived separately.
- Reply-based continuation should still be possible even when auto-resume is
  enabled.
- Session artifacts should survive process restarts and be easy to inspect.
