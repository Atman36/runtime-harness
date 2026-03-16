---
id: TASK-023
title: "Message directives and context binding"
status: done
spec: ../specs/SPEC-023.md
preferred_agent: codex
review_policy: standard
priority: high
project: _claw-dev
needs_review: true
risk_flags: [routing, context, worktree, transport]
tags: [operator-ux, directives, context, epic-15]
dependencies: [TASK-022]
epic: 15
---

# Task

## Goal
Add a normalized directive parser and context binding contract for operator
messages, so project/agent/branch targeting stays deterministic across replies,
defaults, and transport adapters.

## Notes
- Support `/agent`, `/project`, and `@branch` style directives plus a `ctx:`
  footer for reply-based carry-over.
- Precedence should be explicit and testable: reply context first, then direct
  directives, then ambient defaults.
- Context binding must remain filesystem-backed or artifact-derived; no hidden
  in-memory routing state.
- Branch targeting should align with existing worktree execution policy.
