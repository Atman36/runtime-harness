---
id: TASK-007
title: "Task graph lint as mandatory pre-orchestrate gate"
status: done
spec: ../specs/SPEC-007.md
preferred_agent: codex
review_policy: standard
priority: medium
project: _claw-dev
needs_review: false
risk_flags: []
tags: [task-graph, lint, gate, epic-12]
dependencies: [TASK-002, TASK-006]
epic: 12
---

# Task

## Goal
Make `claw orchestrate` abort on broken task graph: cycles or unresolvable
dependency references. Expose `claw task-graph-lint` as standalone command.
Parallel task selection respects file-overlap rules.

## Notes
- Extends TASK-002 (task-snapshot/lint already exists)
- `claw task-graph-lint` → adds file-overlap check on top of cycle/ref checks
- `claw orchestrate` aborts with reason_code: task_graph_cycle or unknown_dependency
- Parallel slots only for tasks without file-overlap in their specs
