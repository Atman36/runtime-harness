---
id: TASK-008
title: "Project command registry in WORKFLOW.md"
status: done
spec: ../specs/SPEC-008.md
preferred_agent: codex
review_policy: standard
priority: medium
project: _claw-dev
needs_review: false
risk_flags: []
tags: [workflow, commands, registry, epic-12]
dependencies: [TASK-006]
epic: 12
---

# Task

## Goal
Add a `commands` section to WORKFLOW.md template and teach the orchestrator/worker
to use `commands.test` instead of the hardcoded `bash tests/run_all.sh`.
Add `claw run-checks --project slug` command.

## Notes
- WORKFLOW.md gains `commands: {test, lint, build, smoke}` block
- workflow_contract.py reads and exposes commands registry
- `claw run-checks --project slug [--type test|lint|build|smoke]`
- In this slice, orchestrate surfaces `commands.test` and `claw run-checks` executes it; full worker-loop integration remains a follow-up
- Graceful fallback to `bash tests/run_all.sh` when no commands block is present
