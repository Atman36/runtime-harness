---
id: TASK-014
title: "Simple listener registry for orchestrator events"
status: ready
spec: ../specs/SPEC-014.md
preferred_agent: codex
review_policy: standard
priority: medium
project: _claw-dev
needs_review: true
risk_flags: [trusted-command, event-dispatch]
tags: [listeners, events, registry, epic-13]
dependencies: [TASK-012]
epic: 13
---

# Task

## Goal
Add a declarative `_system/registry/listeners.yaml` and a matching dispatch
engine so orchestrator events (run_started, run_finished, review_created,
approval_requested) can trigger trusted commands without ad-hoc wiring in claw.py.

## Notes
- Inspired by crewAI listener/event model; adapted to claw's filesystem-first approach.
- Listener failures must not abort the primary event path — log and continue.
- Use existing `trusted_command.py` for security — do not bypass it.
- All example listeners in registry should be `enabled: false` by default.
