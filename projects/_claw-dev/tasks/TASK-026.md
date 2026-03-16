---
id: TASK-026
title: "Transport plugin surface and setup checks"
status: done
spec: ../specs/SPEC-026.md
preferred_agent: codex
review_policy: standard
priority: medium
project: _claw-dev
needs_review: true
risk_flags: [plugins, config, setup, transport]
tags: [operator-ux, plugins, doctor, epic-15]
dependencies: [TASK-024, TASK-025]
epic: 15
---

# Task

## Goal
Add a narrow plugin surface and setup checks for transport backends, so new
operator ingress paths can be added without turning `scripts/claw.py` into a
hardcoded monolith.

## Notes
- Discover transport or command backends through an explicit registry contract.
- Validate plugin ids, config shape, and duplicate providers deterministically.
- Add doctor/setup checks for missing binaries, malformed transport config, and
  unsupported combinations.
- Keep plugin loading lazy and failure modes user-readable.
