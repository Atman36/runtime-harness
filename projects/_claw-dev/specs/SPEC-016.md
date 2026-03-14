# SPEC-016 — Orchestrator decision log and richer workflow graph metadata

## Context

The claw project is at `/Users/Apple/progect/claw`.

Runtime rationale is currently spread across `STATUS.md`, run reports, and
diffs. There is no single append-only record of *why* the orchestrator made a
decision: why it routed a task to codex vs claude, why it created a follow-up,
why it retried, why it requested approval.

Separately, `workflow_graph.json` stores nodes and edges but no transition
semantics — making it a picture rather than a debug artifact.

Both gaps come from the same root cause: orchestrator decisions happen
implicitly in code and are not persisted as typed records.

## Goal

1. Add `state/decision_log.jsonl` — an append-only record of orchestrator
   decisions with typed reason codes.
2. Extend `workflow_graph.json` edges with `edge_type`, `trigger`, `reason_code`,
   `approval_gate` fields.

## Desired outcome

1. Every orchestrator routing/retry/approval/follow-up decision appends one
   record to `state/decision_log.jsonl`
2. `workflow_graph.json` edges carry semantics, not just source/target
3. `claw decision-log <project_root> [--last N]` prints recent decisions
4. Tests verify records written at known decision points

## Decision log record format

`state/decision_log.jsonl` — one JSON object per line:
```json
{
  "ts": "2026-03-14T10:00:00Z",
  "decision_id": "dec-<uuid>",
  "kind": "routing | retry | approval_requested | follow_up_created | run_skipped",
  "run_id": "...",
  "task_id": "...",
  "reason_code": "run_failed | review_rejected | manual | dependency_unmet | ...",
  "details": { "agent": "codex", "attempt": 2 },
  "outcome": "dispatched | queued | waiting | skipped"
}
```

## Workflow graph edge enrichment

Add to each edge in `workflow_graph.json`:
```json
{
  "source": "TASK-001",
  "target": "TASK-002",
  "edge_type": "sequence | conditional | approval_gate | listener",
  "trigger": "run_finished | review_approved | manual",
  "reason_code": "dependency | routing_rule | human_decision",
  "approval_gate": false
}
```

Existing edges without these fields should be treated as `edge_type: sequence`.

## Scope

### In scope
- `_system/engine/decision_log.py`: `append_decision(project_root, kind, ...)` helper
- Call `append_decision` at four existing decision points in `claw.py`:
  routing dispatch, retry, approval_requested, follow_up creation
- `claw decision-log <project_root> [--last N]` command: reads and pretty-prints log
- Extend workflow graph builder / writer to include edge metadata fields
- Tests: decision appended at each of the four points; graph edges have new fields

### Out of scope
- Replacing STATUS.md or existing event_log with decision_log
- Visual graph UI changes
- Querying / filtering decision log beyond `--last N`
- Removing old edge format (must be backwards-compatible)

## Files to modify / create

### CREATE: `_system/engine/decision_log.py`
- `append_decision(project_root, kind, run_id, task_id, reason_code, details, outcome)`
- `read_decisions(project_root, last_n) -> list[dict]`
- Atomic append (same pattern as event_log.py)

### MODIFY: `scripts/claw.py`
- Call `append_decision` at routing dispatch, retry, approval_requested,
  follow_up creation.
- Add `cmd_decision_log` subcommand with `project_root` and `--last N` (default 20).

### MODIFY: `_system/engine/workflow_contract.py` (or wherever graph is written)
Extend edge dicts with the four new fields when generating/updating
`workflow_graph.json`. Existing edges get `edge_type: sequence` as default.

### MODIFY / CREATE: `tests/`
- `test_decision_log.py`: append and read helpers, atomic writes
- Integration: four decision points each write a record

## Acceptance Criteria

- `state/decision_log.jsonl` is created and grows at the four decision points
- Each record has at minimum: `ts`, `kind`, `run_id`, `reason_code`, `outcome`
- `claw decision-log <project_root> --last 5` prints 5 most recent decisions
- `workflow_graph.json` edges include `edge_type` and `trigger` fields
- Existing edges without new fields do not cause errors
- `bash tests/run_all.sh` passes

## Constraints

- Append-only — do not mutate existing log records
- Do not replace `events.jsonl` or `STATUS.md`; decision_log is additive
- Graph edge enrichment must be backwards-compatible
- Atomic writes (same pattern as event_log.py) — no partial records
