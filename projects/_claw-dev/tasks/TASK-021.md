---
id: TASK-021
title: "Budget and governance guardrails"
status: done
spec: ../specs/SPEC-021.md
preferred_agent: codex
review_policy: standard
priority: medium
project: _claw-dev
needs_review: true
risk_flags: [budget, approvals, governance, guardrails]
tags: [paperclip-import, budgets, approvals, epic-14]
dependencies: [TASK-019, TASK-020]
epic: 14
---

# Task

## Goal
Add file-backed budget and governance guardrails for agent runs, so `claw` can
soft-stop or require approval on risky/expensive execution paths without losing
artifact-level auditability.

## Notes
- Budget state should be snapshot-based and inspectable from disk.
- Soft-limit warnings and hard-stop pause semantics must be deterministic.
- Approval-required actions should integrate with existing approval artifacts,
  not invent a second parallel mechanism.
- Keep the slice policy-first; no UI or billing backend work here.
