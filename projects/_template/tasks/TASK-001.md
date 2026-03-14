---
id: TASK-001
title: "Bootstrap the first slice"
status: todo
spec: ../specs/SPEC-001.md
preferred_agent: auto
review_policy: standard
priority: medium
project: {{PROJECT_SLUG}}
needs_review: false
risk_flags: []
tags: []
---

# Task

## Goal
Implement the smallest useful slice for `{{PROJECT_SLUG}}`.

## Notes
- Update `preferred_agent` if the work is design-heavy or ambiguous
- Optional: set `mode: advisory` to request `advice.md`, `patch.diff`, and `review_findings.json` instead of direct edits; this is best-effort only via `CLAW_ADVISORY=1`
- For enforced isolation, prefer `workspace_mode: git_worktree`
- Add run links and review context after execution
