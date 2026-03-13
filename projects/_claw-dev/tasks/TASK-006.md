---
id: TASK-006
title: "WORKFLOW.md enforcement in orchestrate"
status: done
spec: ../specs/SPEC-006.md
preferred_agent: claude
review_policy: standard
priority: medium
project: _claw-dev
needs_review: false
risk_flags: []
tags: [workflow, enforcement, contracts, epic-12]
dependencies: [TASK-003, TASK-004]
epic: 12
---

# Task

## Goal
Make `allowed_agents` and `edit_scope` from WORKFLOW.md actually block or warn
during orchestration. Add `claw workflow-validate --project slug` as standalone check.

## Notes
- Extends TASK-003 (workflow_contract.py already exists)
- `allowed_agents` check: if task preferred_agent not in list → reason_code: contract_violation
- `edit_scope` check: warn in launch-plan, fail in orchestrate
- `claw workflow-validate --project slug` — standalone validation command
