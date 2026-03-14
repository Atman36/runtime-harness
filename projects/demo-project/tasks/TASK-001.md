---
id: TASK-001
title: "Create the first task-to-job run"
status: todo
spec: ../specs/SPEC-001.md
preferred_agent: auto
review_policy: standard
priority: high
project: demo-project
needs_review: false
risk_flags: []
tags: []
---

# Task

## Goal
Use the demo project as the first concrete workspace for deterministic task launches.

## Notes
- Resolve `spec` from front matter and copy both inputs into the run directory
- `mode: advisory` is available for patch-only review runs; it only signals intent via `CLAW_ADVISORY=1` and does not enforce read-only execution
- For enforced isolation, use `workspace_mode: git_worktree`
- Keep run artifacts deterministic and ready for future engine execution
