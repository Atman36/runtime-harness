---
name: claw-orchestrate
description: Orchestrate a complete spec-driven agent workflow end-to-end: pick task → preview routing → run agent CLI → check result → review → validate → commit. Use when the user wants to execute a task or series of tasks through the claw system, run the continuous loop, coordinate multiple agent CLIs (codex + claude), or says phrases like "запусти задачу", "выполни спеку", "run task", "execute SPEC-N", "запусти агента", "прогони через claw", "run the loop", "оркестрируй", "выполни по плану", "take the next task", "start worker". This skill is the operational brain of the claw system.
---

# claw-orchestrate

This skill drives the full spec-driven execution loop. It knows how to use claw's CLI tools to move a task from "todo" to "committed and reviewed". Think of it as the human operator's role, systematized.

## The execution loop

```
1. SELECT → pick next task
2. PREVIEW → launch-plan (dry run)
3. EXECUTE → run agent CLI
4. VALIDATE → check artifacts + tests
5. REVIEW → codex review → claude preview
6. COMMIT → commit with context
7. NEXT → update index, pick next
```

---

## Phase 1: SELECT — pick next task

If the user specifies a task (e.g. "run TASK-007"), use that.

Otherwise, find the next task:
1. Read `docs/PLAN.md` → Current active epic
2. Read sprint index if present: `docs/SPRINT-epic-NNN.md`
3. Find the first task with `status: todo` in `projects/<project>/tasks/`
4. Confirm with the user before proceeding

---

## Phase 2: PREVIEW — dry run

Always run launch-plan before executing. This shows routing, command, workspace mode — no side effects.

```bash
python scripts/claw.py launch-plan <project> TASK-NNN
```

Read the output. Verify:
- Correct agent selected (matches task tags + routing rules)
- Correct workspace_mode (`project_root` | `git_worktree` | `isolated_checkout`)
- Command looks right

If routing is wrong, update the task's `preferred_agent` or `tags` before proceeding.

---

## Phase 3: EXECUTE — run the agent

**Option A: Direct execution** (synchronous, good for single tasks)
```bash
python scripts/claw.py run --execute <project> TASK-NNN
```

**Option B: Queue + worker** (for multiple tasks or background work)
```bash
python scripts/claw.py run --enqueue <project> TASK-NNN
python scripts/claw.py worker <project>         # run continuously
python scripts/claw.py worker --once <project>  # process one job
```

**Option C: OpenClaw JSON bridge** (when integrating with external systems)
```bash
python scripts/claw.py openclaw enqueue '{"project": "<slug>", "task_id": "TASK-NNN"}'
```

While the agent runs, watch for output in:
- `projects/<project>/runs/<date>/RUN-XXXX/result.json` — exit code + stdout/stderr
- `projects/<project>/runs/<date>/RUN-XXXX/report.md` — agent's summary

---

## Phase 4: VALIDATE — check the artifacts

After execution, verify:

1. **Result status**: Read `result.json` → `status` should be `success`
2. **Acceptance criteria**: Compare `result.json` output against SPEC acceptance criteria
3. **Artifact contracts**: Run schema validation
   ```bash
   python scripts/validate_artifacts.py projects/<project>/runs/<date>/RUN-XXXX/
   ```
4. **Tests**: If the task involved code changes, run the test suite
   ```bash
   bash tests/run_all.sh
   ```

If validation fails → use claw-run-debugger to diagnose before proceeding.

---

## Phase 5: REVIEW — two-pass review

The review workflow follows a two-pass model:

### Codex review (technical pass)
- Python correctness, shell safety, tests, schema compliance
- Trigger: `python scripts/claw.py review-batch <project>`
- Or direct: run codex on the review batch

### Claude preview (product pass)
- Does the output match the spec's goal and acceptance criteria?
- Are edge cases handled?
- Is the "Out of scope" boundary respected?

This pass is you (Claude) reading the output vs the spec and making a judgment call.

**Review workflow status progression:**
```
todo → in_progress → codex_review → claude_preview → done
```

A task is closed only when:
- ✓ Codex review passed
- ✓ Claude preview passed
- ✓ Commit exists

---

## Phase 6: COMMIT

After both review passes:

```bash
git add <specific changed files>
git commit -m "<type>(<scope>): <description>

TASK-NNN: <spec title>
Closes SPEC-NNN"
```

Then update the sprint index (if one exists):
- Set task status to `done`
- Add commit hash
- Add entry to Execution Log
- Update Current/Next task pointer

---

## Phase 7: NEXT — continue the loop

After committing:
1. Update `status: done` in `tasks/TASK-NNN.md`
2. Update sprint index
3. Ask the user: "Continue with TASK-NNN+1?" or surface the next pending task

---

## Multi-agent coordination

When running tasks in parallel (e.g. two independent specs):

**Safe to parallelize:**
- Tasks with `workspace_mode: git_worktree` or `isolated_checkout`
- Tasks that don't share files (check specs' Files/zones sections)

**Do NOT parallelize:**
- Tasks touching the same Python modules
- Planning docs (PLAN.md, BACKLOG.md, STATUS.md) — merge-sensitive
- Tasks where one depends on another's output

Parallel execution uses worktrees:
```bash
python scripts/claw.py run --execute <project> TASK-NNN  # terminal 1
python scripts/claw.py run --execute <project> TASK-MMM  # terminal 2
```

---

## Handling failures in the loop

| Failure | Action |
|---------|--------|
| Agent returned non-zero | Use claw-run-debugger, fix spec or code, re-run |
| Schema validation failed | Fix artifact structure, re-validate |
| Tests failed | Fix code, re-run tests, then continue to review |
| Codex review found issues | Create a follow-up spec for the issues, or fix inline if trivial |
| Spec acceptance criteria not met | The task is NOT done. Fix and re-run. |

---

## Quick reference

```bash
# Full pipeline
python scripts/claw.py launch-plan <project> TASK-NNN    # preview
python scripts/claw.py run --execute <project> TASK-NNN  # execute
python scripts/validate_artifacts.py runs/<date>/RUN-X/  # validate
bash tests/run_all.sh                                     # test
python scripts/claw.py review-batch <project>            # review

# Status
python scripts/claw.py status <project>

# Queue management
python scripts/claw.py worker <project>
python scripts/claw.py reclaim <project>
python scripts/claw.py approve <project> <run-id>
```
