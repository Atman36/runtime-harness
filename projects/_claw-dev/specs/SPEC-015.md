# SPEC-015 — Advisory patch-only review mode

## Context

The claw project is at `/Users/Apple/progect/claw`.

The existing review cycle lets an agent make direct writes to the workspace.
For risky changes (security patches, schema migrations, config changes) it is
safer to let a reviewer/advisor agent produce **findings and a diff** that the
orchestrator then decides whether to apply.

Inspired by the ccg-workflow `review.md` / `codex-exec.md` pattern: the advisor
run has no direct write access; it returns `advice.md`, `patch.diff`, and
`review_findings.json`. The orchestrator separately applies or discards the patch.

## Goal

Add a **best-effort advisory** run mode where an agent is expected to produce
findings and a patch instead of directly modifying the workspace. Add an
`apply-patch` command that the operator uses to apply the diff after reviewing.

**Important caveat:** `CLAW_ADVISORY=1` is a signal to a well-behaved agent,
not an enforced sandbox. The current runner (`execute_job.py` + `agent_exec.py`)
does not prevent the agent process from writing to the workspace. For hard
isolation use the existing `git_worktree` workspace mode. The advisory mode's
value is the artifact contract and the `apply-patch` gate — not a security
boundary. Spec text and docs must say this explicitly.

## Desired outcome

1. A task can declare `mode: advisory` in its TASK.md front matter
2. Runner sets `CLAW_ADVISORY=1` in the agent subprocess environment as a
   best-effort signal; well-behaved agents respect it, others do not
3. After the run, `advice.md`, `patch.diff`, `review_findings.json` are expected
   in the run dir; missing files produce warnings, not run failure
4. `claw apply-patch <project_root> <run_id>` applies `patch.diff` to the workspace
5. Apply-patch is guarded: dry-run by default, `--confirm` required to apply
6. Tests: advisory env var set; artifact validation warns on missing; dry-run
   and confirm paths work

## Artifact formats

`review_findings.json`:
```json
{
  "severity": "medium",
  "findings": [
    { "file": "scripts/foo.py", "line": 42, "issue": "...", "suggestion": "..." }
  ],
  "recommendation": "apply_patch | discard | needs_discussion"
}
```

`advice.md` — freeform markdown summary for human review.

`patch.diff` — unified diff format, applicable with `git apply`.

## Scope

### In scope
- `mode: advisory` recognised in TASK.md front matter
- `execute_job.py`: detect advisory mode, set `CLAW_ADVISORY=1` in subprocess
  env; do **not** attempt to pass `--read-only` flags to the agent binary (not
  supported universally and provides no real enforcement)
- Add a note to run meta that advisory mode was requested (`meta["advisory"] = true`)
- Validate presence of three advisory artifacts post-run; warn if missing
- `claw apply-patch <project_root> <run_id> [--confirm]`:
  - Without `--confirm`: print diff, report findings severity, exit 0
  - With `--confirm`: run `git apply` on `patch.diff`, append `patch_applied` event to events.jsonl
- Tests for env var set, artifact validation warns on missing, dry-run, apply path

### Out of scope
- Changing the default run mode for any existing tasks
- Full sandboxing / containerisation of the agent process
- Automatic application without human confirmation
- Multi-patch stacking / conflict resolution

## Files to modify / create

### MODIFY: `scripts/execute_job.py`
Detect `mode: advisory` in job config. Pass `CLAW_ADVISORY=1` env var to
subprocess. After run, call `validate_advisory_artifacts(run_dir)` — log
warnings for missing files, do not fail run.

### MODIFY: `scripts/claw.py`
- `cmd_apply_patch`: read `patch.diff` and `review_findings.json`, dry-run or apply.
- Register `apply-patch` subcommand: `project_root`, `run_id`, `--confirm` flag.

### MODIFY / CREATE: `tests/`
- `test_advisory_mode.py`:
  - advisory job config sets env var
  - post-run artifact validation warns on missing files
  - apply-patch dry-run prints diff without modifying files
  - apply-patch --confirm calls git apply

### UPDATE: `projects/_template/tasks/TASK-001.md` or docs
Document `mode: advisory` option in task front matter comments. **Include an
explicit note** that advisory mode is best-effort: the env var signals intent
to a cooperative agent but does not prevent filesystem writes. For enforced
isolation use `workspace_mode: git_worktree`.

## Acceptance Criteria

- A task with `mode: advisory` sets `CLAW_ADVISORY=1` in subprocess env
- Missing advisory artifacts generate logged warnings, not run failure
- `claw apply-patch ... ` (no `--confirm`) prints diff + findings, modifies nothing
- `claw apply-patch ... --confirm` applies the patch via `git apply`
- `bash tests/run_all.sh` passes

## Constraints

- Default run mode unchanged — existing tasks unaffected
- `--confirm` is the only way to apply; no implicit application
- Advisory enforcement is **best-effort only via env var** — docs must say this;
  do not promise or imply read-only enforcement in any user-facing text
- Do not add new CLI dependencies (git is already assumed available)
- Do not add `--read-only` or `--no-git-write` flags to agent invocations
