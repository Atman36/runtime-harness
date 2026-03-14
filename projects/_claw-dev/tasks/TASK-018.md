---
id: TASK-018
title: "Agent inbox and atomic task claim/release"
status: ready
spec: ../specs/SPEC-018.md
preferred_agent: codex
review_policy: standard
priority: high
project: _claw-dev
needs_review: true
risk_flags: [tasks, locking, conflict-semantics, orchestration]
tags: [paperclip-import, inbox, task-claim, epic-14]
dependencies: [TASK-017]
epic: 14
---

# Task

## Goal
Add an agent-scoped inbox and atomic claim/release semantics for tasks, so work
ownership is explicit and conflicts are surfaced as structured outcomes instead
of accidental overlapping execution.

## Notes
- Inbox should be derivable from project files; no hidden assignment DB.
- Claim must be idempotent for the current owner and conflict for others.
- Release should preserve audit context about why ownership was given up.
- Blocked/in-progress transitions must remain visible in task artifacts.
