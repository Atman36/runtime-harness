---
id: TASK-025
title: "Safe file exchange for project roots"
status: done
spec: ../specs/SPEC-025.md
preferred_agent: codex
review_policy: standard
priority: medium
project: _claw-dev
needs_review: true
risk_flags: [file-transfer, security, path-normalization, transport]
tags: [operator-ux, file-exchange, transport, epic-15]
dependencies: [TASK-023]
epic: 15
---

# Task

## Goal
Add a safe file exchange contract for active project roots and worktrees, so an
operator transport can upload inputs or fetch outputs without exposing raw
filesystem access.

## Notes
- Normalize relative paths, forbid escaping project roots, and block sensitive
  patterns via deny-globs.
- Writes should be atomic; directory fetches may be zipped on demand.
- File exchange should respect active context so uploads land in the intended
  project/worktree.
- This slice is transport-facing infrastructure, not a UI workflow by itself.
