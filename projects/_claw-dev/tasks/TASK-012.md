---
id: TASK-012
title: "Live agent stream: agent_stream.jsonl"
status: done
spec: ../specs/SPEC-012.md
preferred_agent: codex
review_policy: standard
priority: high
project: _claw-dev
needs_review: true
risk_flags: [subprocess, streaming, artifacts]
tags: [streaming, observability, execute-job, epic-13]
dependencies: [TASK-011]
epic: 13
---

# Task

## Goal
Add real-time per-line streaming of agent output to `agent_stream.jsonl` during
a run, so the orchestrator can observe live progress without waiting for the
full `stdout.log` to be written at completion.

## Notes
- Current `subprocess.run` must become `subprocess.Popen` with readline loop.
- `stdout.log` must still be written at end — backwards compatibility required.
- Line classification is heuristic only; keep it simple.
- `cmd_openclaw_summary` adds `stream_tail` JSON field (last 10 records as list) — not a text footer.
- Stderr must be drained on a background thread to avoid Popen deadlock.
- This is the highest-priority improvement from the ccg-workflow analysis.
