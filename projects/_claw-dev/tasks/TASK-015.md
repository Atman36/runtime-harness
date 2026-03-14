---
id: TASK-015
title: "Advisory patch-only review mode"
status: done
spec: ../specs/SPEC-015.md
preferred_agent: codex
review_policy: standard
priority: medium
project: _claw-dev
needs_review: true
risk_flags: [git-apply, advisory-mode, artifacts]
tags: [review, advisory, patch, epic-13]
dependencies: [TASK-012]
epic: 13
---

# Task

## Goal
Add an `advisory` run mode where an agent produces `advice.md`, `patch.diff`,
and `review_findings.json` instead of directly writing to the workspace. Add
`claw apply-patch` for the operator to apply the diff after review, with
`--confirm` required to actually modify files.

## Notes
- Inspired by ccg-workflow review.md / codex-exec.md pattern.
- Default mode for all existing tasks is unchanged.
- Advisory enforcement is best-effort only: `CLAW_ADVISORY=1` signals intent to a
  cooperative agent but does NOT prevent filesystem writes. Docs must say this.
  For enforced isolation use `workspace_mode: git_worktree`.
- Do NOT add `--read-only` flags to agent CLI invocations.
- Missing advisory artifacts → logged warnings, not run failure.
- `git apply` is the apply mechanism; git is already assumed available.
