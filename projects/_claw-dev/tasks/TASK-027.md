---
id: TASK-027
title: "Shared session files for cross-agent handoff"
status: done
spec: ../specs/SPEC-027.md
preferred_agent: codex
review_policy: standard
priority: medium
project: _claw-dev
needs_review: true
risk_flags: [sessions, handoff, files, continuity]
tags: [operator-ux, sessions, handoff, epic-17]
dependencies: [TASK-019, TASK-025]
epic: 17
---

# Task

## Goal
Add a task-scoped shared session-files contract, so Claude/Codex can exchange
implementation notes, plans, and other handoff artifacts through deterministic
files instead of hidden chat/runtime memory.

## Notes
- Keep the storage task-scoped and engine-neutral.
- Track author/note metadata in a manifest for inspectability.
- Reuse safe relative-path handling and atomic writes.
- Make discovery easy from `session-status`.
