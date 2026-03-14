# SPEC-013 — Step-level HITL checkpoint: `approval_checkpoint.json`

## Context

The claw project is at `/Users/Apple/progect/claw`.

`claw` already has project/queue-level approvals (`ask-human`, `resolve-approval`
commands, `state/approvals/` dir). These are triggered after a run finishes or
when routing decides to escalate.

What is missing is a **within-run, step-level** pause/resume primitive that
an agent can emit mid-run. Inspired by crewAI's `human_feedback.py` pattern
but adapted to claw's filesystem-first model: no SQLite, no Flow DSL.

## Goal

Allow an agent to write an `approval_checkpoint.json` into the run dir at any
point. The runner detects this file, exits with code 2, and moves the queue job
to `awaiting_approval` — the existing queue state in `FileQueue`. When a human
resolves the checkpoint the run resumes deterministically from filesystem state.

## Lifecycle mapping

This feature must not introduce new status values in `result.json` or
`meta.json`. Instead it maps to existing contracts:

| Layer | Value | Notes |
|-------|-------|-------|
| `result.json` `status` | `failed` | exit code 2 is not success |
| `meta.json` `status` | `failed` | same |
| queue `state` | `awaiting_approval` | already in `STATE_ORDER` and `FileQueue.await_approval()` |
| distinguishing signal | `approval_checkpoint.json` exists + `status: pending` | separates checkpoint-wait from real failure |

The worker interprets exit code 2 as "step checkpoint" and calls
`queue.await_approval()` instead of the normal fail/retry path.

## Desired outcome

1. An agent can signal "I need human input here" by writing `approval_checkpoint.json`
2. Runner detects the file post-run, exits with code **2**; worker calls
   `queue.await_approval()` (existing `FileQueue` method)
3. `claw resolve-checkpoint <project_root> <run_id> --decision <accept|reject> [--notes ...]`
   writes the human decision to the checkpoint file, appends a
   `checkpoint_resolved` event, then calls `queue.approve()` (accept) or
   leaves the queue entry in `failed` (reject)
4. `openclaw status` surfaces `awaiting_approval` count — this already works;
   no new status field needed
5. Tests cover: exit code 2 → `awaiting_approval` transition; resolution writes
   decision; accept re-queues; reject leaves `failed`

## Artifact format

`runs/<id>/approval_checkpoint.json`:
```json
{
  "checkpoint_id": "ckpt-<uuid>",
  "created_at": "2026-03-14T10:00:00Z",
  "reason": "human readable explanation",
  "context": { "step": "...", "question": "..." },
  "status": "pending",
  "decision": null,
  "decision_notes": null,
  "resolved_at": null
}
```

After resolution, `status` → `"resolved"`, `decision` → `"accept"` or `"reject"`.

## Scope

### In scope
- Detect `approval_checkpoint.json` in `execute_job.py` post-run (check after
  subprocess exits — no polling required for initial slice)
- Exit runner with **code 2** when checkpoint detected and `status == "pending"`
- Worker (`cmd_worker`) interprets exit code 2 as "checkpoint" and calls
  `queue.await_approval()` instead of fail/retry path
- `claw resolve-checkpoint <project_root> <run_id> --decision accept|reject [--notes]`
  command: writes decision to checkpoint file, appends `checkpoint_resolved`
  event to `events.jsonl`, calls `queue.approve()` (accept) or leaves `failed` (reject)
- `openclaw status` already shows `awaiting_approval` count — no new field needed
- Tests: exit code 2 → `awaiting_approval`; resolution writes decision; accept re-queues

### Out of scope
- Agent-SDK-level step framework or Flow DSL
- SQLite or external persistence
- Mid-run streaming of partial state (see SPEC-012)
- Multi-step checkpoint chains

## Files to modify / create

### MODIFY: `scripts/execute_job.py`
After the agent subprocess exits, check for `approval_checkpoint.json`. If
found and `status == "pending"`, sys.exit(2). Do not modify `result.json` or
`meta.json` status fields — they remain `failed` (exit code 2 is non-zero).

### MODIFY: `scripts/claw.py`
- In `cmd_worker`, after `completed = subprocess.run(...)`: if
  `completed.returncode == 2` and `approval_checkpoint.json` exists in run_dir,
  call `queue.await_approval(claimed)` instead of the normal fail/retry path.
- Add `cmd_resolve_checkpoint`: reads checkpoint file, writes decision + notes +
  `resolved_at`, appends `checkpoint_resolved` event to `events.jsonl`, calls
  `queue.approve(job_id)` for accept or no queue change for reject.
- Register `resolve-checkpoint` subcommand: `project_root`, `run_id`,
  `--decision` (required, choices: accept|reject), `--notes`.

### MODIFY: `tests/`
- `test_checkpoint.py`:
  - runner exits with code 2 when checkpoint file present and pending
  - worker calls `queue.await_approval()` on exit code 2 + checkpoint present
  - resolve-checkpoint accept → `queue.approve()` called, checkpoint `status: resolved`
  - resolve-checkpoint reject → checkpoint `status: resolved`, queue stays `failed`

## Acceptance Criteria

- Exit code 2 + `approval_checkpoint.json` (status: pending) → queue state
  becomes `awaiting_approval`; `result.json` status remains `failed`
- `claw resolve-checkpoint ... --decision accept` → checkpoint resolved,
  job moved back to `pending` via `queue.approve()`
- `claw resolve-checkpoint ... --decision reject` → checkpoint resolved,
  queue entry stays in its current state
- `openclaw status` shows `awaiting_approval` count correctly (already works)
- Tests reproduce pause and resolution path
- `bash tests/run_all.sh` passes

## Constraints

- Do not add `waiting_for_human` or any new value to `result.json` / `meta.json`
  status enums — schemas only allow existing values
- Do not add new queue states — use existing `awaiting_approval`
- Exit code 2 is the sole distinguishing signal; `approval_checkpoint.json`
  presence is the confirmation
- No change to project-level approval flow (ask-human / resolve-approval)
