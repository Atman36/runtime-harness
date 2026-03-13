---
id: TASK-001
title: Reason codes registry and structured error envelope
status: in_progress
spec: ../specs/SPEC-001.md
preferred_agent: claude
review_policy: standard
priority: high
project: _claw-dev
needs_review: false
risk_flags: []
tags:
- diagnostics
- error-handling
---

# Task

## Goal
Formalize the existing ad-hoc error codes in `scripts/claw.py` into a proper
registry with structured guidance (likely_cause, next_action). Upgrade the
JSON error envelope emitted by all openclaw/orchestrate/validate commands.

## Notes
- The skeleton already exists: `_openclaw_error()` at line 1794 of scripts/claw.py
- Do not break existing callers — only add fields to the envelope
- Reason codes must be stable strings (snake_case)
