---
id: TASK-022
title: "Live status feed for operators"
status: done
spec: ../specs/SPEC-022.md
preferred_agent: codex
review_policy: standard
priority: high
project: _claw-dev
needs_review: true
risk_flags: [status-feed, events, polling, observability]
tags: [operator-ux, live-status, transport, epic-15]
dependencies: [TASK-015]
epic: 15
---

# Task

## Goal
Add a transport-friendly live status feed on top of existing event artifacts, so
an operator can watch a run progress without manually tailing files in the run
directory.

## Notes
- Reuse `events.jsonl`, `event_snapshot.json`, and `agent_stream.jsonl` instead
  of inventing a second event model.
- First slice should be CLI/polling friendly; no SSE or websocket dependency.
- Feed output should surface queue state, run status, current step, and recent
  stream tail in one stable contract.
- Keep the transport layer read-only with respect to run state.
