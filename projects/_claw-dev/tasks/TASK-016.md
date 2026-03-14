---
id: TASK-016
title: "Orchestrator decision log and richer workflow graph metadata"
status: ready
spec: ../specs/SPEC-016.md
preferred_agent: codex
review_policy: standard
priority: low
project: _claw-dev
needs_review: true
risk_flags: [event-log, graph-schema]
tags: [observability, decision-log, workflow-graph, epic-13]
dependencies: [TASK-014]
epic: 13
---

# Task

## Goal
Add `state/decision_log.jsonl` (append-only typed record of orchestrator
decisions) and enrich `workflow_graph.json` edges with `edge_type`, `trigger`,
`reason_code`, `approval_gate` fields — turning the graph from a picture into
a debug artifact.

## Notes
- Inspired by crewAI flow visualization / context log analysis.
- Decision log is additive — does not replace events.jsonl or STATUS.md.
- Graph edge enrichment must be backwards-compatible (missing fields = sequence).
- Atomic write pattern same as event_log.py.
- Four decision points to instrument: routing dispatch, retry, approval_requested, follow_up creation.
