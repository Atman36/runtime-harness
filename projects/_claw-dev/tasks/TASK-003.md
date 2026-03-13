---
id: TASK-003
title: "Workflow contract schema and validator"
status: todo
spec: ../specs/SPEC-003.md
preferred_agent: claude
review_policy: standard
priority: medium
project: _claw-dev
needs_review: false
risk_flags: []
tags: [contracts, workflow]
dependencies: [TASK-002]
---

# Task

## Goal
Add a minimal but explicit workflow contract to each project. The contract
defines approval gates, retry policy, timeout policy, and edit scope.
A validator checks it at orchestrate/status time and surfaces violations.

## Notes
- Contract is optional: if absent, defaults apply silently
- Schema lives in `_system/contracts/workflow.schema.json`
- Validator module lives in `_system/engine/workflow_contract.py`
- Template contract goes into `projects/_template/docs/WORKFLOW.md`
