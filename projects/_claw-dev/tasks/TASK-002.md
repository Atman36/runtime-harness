---
id: TASK-002
title: "Task graph snapshot and lint"
status: todo
spec: ../specs/SPEC-002.md
preferred_agent: claude
review_policy: standard
priority: high
project: _claw-dev
needs_review: false
risk_flags: []
tags: [diagnostics, task-graph]
dependencies: [TASK-001]
---

# Task

## Goal
Add a `build_task_snapshot()` function that serializes the task graph into
`state/tasks_snapshot.json`, adds a checksum, validates `depends_on`
references, and detects dependency cycles. Expose as `claw task-snapshot`
and `claw task-lint` commands. Hook into `claw orchestrate` start.

## Notes
- `collect_task_records()` already does most of the graph traversal — reuse it
- Cycle detection: DFS with grey/black coloring
- Snapshot file must be a derived artifact (never hand-edited)
