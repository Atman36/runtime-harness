---
id: TASK-010
title: "Epic/roadmap completion criteria"
status: done
spec: ../specs/SPEC-010.md
preferred_agent: claude
review_policy: standard
priority: medium
project: _claw-dev
needs_review: false
risk_flags: []
tags: [orchestration, epic-completion, epic-12]
dependencies: [TASK-007, TASK-009]
epic: 12
---

# Task

## Goal
Add epic-level completion awareness: `claw epic-status` shows progress by epic tag,
and `claw orchestrate --scope epic:N` stops when all tasks for that epic are done.

## Notes
- Reads `epic` frontmatter tag from task files (already present)
- `claw epic-status --project slug --epic 12` → total/done/blocked/pending + % completion
- `claw orchestrate --scope epic:12` stops when all epic:12 tasks are done
- `state/orchestration_state.json` gains `scope_completion` field
