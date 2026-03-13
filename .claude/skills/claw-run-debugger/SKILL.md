---
name: claw-run-debugger
description: Diagnose failed, stuck, or unexpected runs in the claw orchestration system by reading filesystem artifacts. Use when the user reports a run that failed, a task stuck in the queue, hooks not delivering, a dead-letter item, an agent that didn't do what was expected, or any runtime anomaly. Trigger on phrases like "run failed", "task stuck", "что пошло не так", "почему завис", "проверь ран", "debug run", "hook не дошёл", "dead letter", "worker завис".
---

# claw-run-debugger

The claw system is filesystem-first: every state transition, error, and decision is a file on disk. Debugging means reading the right files in the right order.

## Diagnostic path

### Step 1: Identify the run

Ask the user for one of:
- Run ID (e.g. `RUN-a1b2c3d4`)
- Task ID (e.g. `TASK-007`)
- "Latest run" — then find it yourself

Find run directories:
```
projects/<project>/runs/YYYY-MM-DD/RUN-*/
```

### Step 2: Read the core artifacts in order

Read these files from the run directory:

| File | What it tells you |
|------|-------------------|
| `meta.json` | Current status, attempt count, timestamps |
| `result.json` | Exit code, stdout/stderr, validation snapshot |
| `job.json` | Immutable job contract: agent, command, workspace_mode |
| `prompt.txt` | Exact prompt sent to the agent |
| `task.md` | Task as seen by the agent |
| `report.md` | Agent's own output/summary (if exists) |

### Step 3: Check queue state

```
projects/<project>/state/queue/
```

Files here are JSON queue items. Look for the relevant task:
- `status`: `pending | running | done | failed | awaiting_approval | dead_letter`
- `retry_count`, `next_retry_at` — is it in backoff?
- `lease_expires_at` — is the worker still holding a lease?

### Step 4: Check hooks (if delivery issue)

```
projects/<project>/state/hooks/pending/    ← not yet sent
projects/<project>/state/hooks/sent/       ← delivered
projects/<project>/state/hooks/failed/     ← delivery failed
```

### Step 5: Check review state (if `awaiting_approval`)

```
projects/<project>/reviews/
```

Look for a `REVIEW-*.md` or review batch JSON. Is there a pending decision?

---

## Failure patterns and fixes

### Pattern: `status: failed`, low retry count
The agent ran but returned a non-zero exit code.
→ Read `result.json` → `stderr` / `stdout` for the actual error.
→ Check `prompt.txt` — was the spec clear enough?
→ Fix: correct the spec, reset queue item to `pending`.

### Pattern: `status: running`, lease expired
Worker died mid-run without releasing the lease.
→ Run: `python scripts/claw.py reclaim <project>`
→ This returns stale `running` items to `pending`.

### Pattern: `status: dead_letter`
Retry limit exhausted.
→ Read `meta.json` → `attempt_log` for all failure reasons.
→ Fix the underlying cause, then manually reset to `pending` or create a new task.

### Pattern: `status: awaiting_approval`
Task needs human approval before re-queuing.
→ Run: `python scripts/claw.py approve <project> <run-id>`

### Pattern: hooks stuck in `pending/`
Hook payload written but not delivered.
→ Run: `python scripts/claw.py dispatch <project>` (attempt delivery)
→ Or: `python scripts/claw.py reconcile <project>` (retry failed + stale)

### Pattern: agent did the wrong thing
Spec was ambiguous, out-of-scope changes made.
→ Read `prompt.txt` — identify which section was unclear.
→ Improve the spec: strengthen "Out of scope" section.
→ Check `routing_rules.yaml` — was the right agent used for this task type?

---

## Output format

Present findings as:

```
## Run diagnosis: RUN-XXXX (TASK-NNN)

Status: <current status>
Agent: <codex|claude>
Attempt: N of M

### What happened
<1-3 sentences summarizing the failure>

### Root cause
<Specific finding from the artifacts>

### Fix
<Exact command or file change needed>
```

---

## Quick commands reference

```bash
# Reclaim stale running jobs
python scripts/claw.py reclaim <project>

# Approve a waiting job
python scripts/claw.py approve <project> <run-id>

# Retry hook delivery
python scripts/claw.py dispatch <project>
python scripts/claw.py reconcile <project>

# Check project status
python scripts/claw.py status <project>
```
