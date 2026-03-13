---
id: TASK-011
title: "Mandatory orchestrator completion signal"
status: done
spec: ../specs/SPEC-011.md
preferred_agent: codex
review_policy: standard
priority: high
project: _claw-dev
needs_review: false
risk_flags: [orchestration, delivery-contract]
tags: [hooks, callbacks, orchestration, epic-12]
dependencies: [TASK-005]
epic: 12
---

# Task

## Goal
Make completion signaling to the orchestrator mandatory and machine-verifiable, so a run cannot be considered fully delivered based only on an agent prompt footer.

## Notes
- The current prompt-level `openclaw system event ...` footer is best-effort and was skipped by Codex in a successful run.
- The fix should move the guarantee into the orchestration/runtime path, not rely on agent memory.
- Delivery must be observable from orchestrator artifacts/state, not just chat logs.
- Preserve file-backed hooks as source of truth.
- Standalone implementation slice is preferred before any broader multi-project scheduler work.
