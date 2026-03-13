---
id: TASK-005
title: "Structural guardrails against agent drift"
status: todo
spec: ../specs/SPEC-005.md
preferred_agent: codex
review_policy: standard
priority: high
project: _claw-dev
needs_review: false
risk_flags: [structural-safety]
tags: [guardrails, safety, epic-12]
dependencies: []
epic: 12
---

# Task

## Goal
Add `_system/engine/guardrails.py` with checks that catch structural drift
introduced by agents: unauthorized project scaffold creation, assertion
weakening in diffs, and edit-scope violations.

## Notes
- Standalone only in this slice: `claw guardrail-check --project slug --diff-path diff.txt`
- Orchestrate integration is explicitly out of scope for TASK-005; it should be a follow-up once diff semantics are stable
- Three checks: unauthorized scaffold, assert weakening, scope violation
- New test in `tests/guardrails_test.sh` using crafted bad diffs
