---
name: claw-status-report
description: Generate a human-readable status report of a claw project's current state — queue, runs, hooks, reviews. Use when the user wants to know what's happening in a project right now, check queue health, see recent run results, or says phrases like "покажи статус", "что в очереди", "status проекта", "что сейчас делает claw", "check queue", "what's running", "show status", "project health". Also trigger when starting a work session and the user wants context on where things stand.
---

# claw-status-report

Aggregate the current state of a claw project from filesystem artifacts into a single readable summary. This is especially useful at the start of a work session or when something seems off.

## Step 1: Find the project

If the user specifies a project name, use `projects/<name>/`.
If not, list `projects/` and pick the active one (skip `_template`).

## Step 2: Read queue state

Scan all JSON files in `projects/<project>/state/queue/`:

For each item, extract:
- `id`, `task_id`, `status`, `retry_count`, `created_at`, `updated_at`
- `lease_expires_at` (if running)
- `next_retry_at` (if in backoff)

Group by status: `pending | running | done | failed | awaiting_approval | dead_letter`

## Step 3: Find recent runs

List `projects/<project>/runs/` directories sorted by date descending.
For the 5 most recent runs, read `meta.json` and `result.json`:
- Run ID, task ID, agent used, exit code, duration, timestamp

## Step 4: Check hook health

Scan:
- `state/hooks/pending/` — count (should be 0 in healthy state)
- `state/hooks/sent/` — count delivered
- `state/hooks/failed/` — count (any here = problem)

## Step 5: Check for pending reviews

Scan `projects/<project>/reviews/` for:
- Review batch files awaiting decision
- Any `REVIEW-*.md` files that don't have a corresponding `DECISION-*.json`

## Step 6: Format the report

```
## claw status — <project-slug>
<timestamp>

### Queue
  pending:           N
  running:           N  [lease expires: HH:MM:SS]
  awaiting_approval: N  ← needs action
  failed (backoff):  N  [next retry: HH:MM:SS]
  dead_letter:       N  ← needs action
  done (total):      N

### Recent runs (last 5)
  RUN-xxxx  TASK-NNN  codex  ✓ done     45s   2026-03-13 14:22
  RUN-yyyy  TASK-NNN  claude ✗ failed   12s   2026-03-13 13:55
  ...

### Hooks
  pending:  N  (healthy: 0)
  sent:     N
  failed:   N  ← run: python scripts/claw.py reconcile <project>

### Reviews
  pending decisions: N
  <filename if any>

### Health: <HEALTHY | NEEDS ATTENTION | DEGRADED>
<One sentence summary of issues if any>
```

## Step 7: Suggest action

After the report, always suggest the most important next action:

| Condition | Suggestion |
|-----------|-----------|
| `dead_letter` items | Diagnose with claw-run-debugger, then fix and re-enqueue |
| `awaiting_approval` | `python scripts/claw.py approve <project> <run-id>` |
| hooks in `failed/` | `python scripts/claw.py reconcile <project>` |
| `running` with expired lease | `python scripts/claw.py reclaim <project>` |
| queue empty + no dead_letter | All good — pick next task from PLAN.md |

## Quick commands

```bash
# Built-in status command (machine-readable)
python scripts/claw.py status <project>

# Dispatch pending hooks
python scripts/claw.py dispatch <project>

# Reconcile failed hooks
python scripts/claw.py reconcile <project>

# Reclaim stale running jobs
python scripts/claw.py reclaim <project>
```
