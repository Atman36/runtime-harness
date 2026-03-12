---
id: TASK-002
title: "Fix RUN-XXXX race condition in run_task.sh"
status: todo
spec: ../specs/SPEC-002.md
preferred_agent: codex
review_policy: standard
priority: high
project: demo-project
needs_review: false
risk_flags: []
---

# Task

## Goal
Apply the atomic-mkdir fix described in SPEC-002 to `scripts/run_task.sh`.
Replace the non-atomic `mkdir -p "$run_dir"` block with a retry loop that
uses `mkdir` (no `-p`) to exclusively claim the run directory.

## Done Definition
- `scripts/run_task.sh` uses atomic `mkdir` in a retry loop for `run_dir`
- The fix handles concurrent invocations without directory collisions
- Existing behavior (path format, artifacts, variable names) is preserved
