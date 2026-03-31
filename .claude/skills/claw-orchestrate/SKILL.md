---
name: claw-orchestrate
description: Orchestrate a complete spec-driven agent workflow end-to-end: pick task → preview routing → run agent CLI → check result → review → validate → commit. Use when the user wants to execute a task or series of tasks through the claw system, run the continuous loop, coordinate multiple agent CLIs (codex + claude), or says phrases like "запусти задачу", "выполни спеку", "run task", "execute SPEC-N", "запусти агента", "прогони через claw", "run the loop", "оркестрируй", "выполни по плану", "take the next task", "start worker". This skill is the operational brain of the claw system.
---

# claw-orchestrate

This skill is the coordinator contract for claw. It owns the plan, decomposes work into bounded execution slices, synthesizes results itself, and only then sends implementation or verification work forward. Workers do not see the coordinator's private reasoning, so every worker prompt must be self-contained.

## Coordinator rules

1. Work in four phases: `Research -> Synthesis -> Implementation -> Verification`.
2. Do not say "based on your findings" to a worker. Read worker outputs, synthesize them yourself, then issue the next prompt with concrete files, commands, and acceptance criteria.
3. Prefer `continue` when the same worker/session already has the right context. Prefer spawning a new worker only when the task is isolated, parallel-safe, or needs a different tool/agent.
4. Use `projects/<slug>/state/session_docs/<TASK>/` as the durable scratchpad for cross-worker knowledge. Put handoff notes in `handoff/summary.md` using the compact 9-section format.
5. Parallelize read-only research freely. Serialize writes per overlapping file set.
6. The coordinator is responsible for the final verdict. Workers can gather evidence; they do not close the task for you.

## Four phases

### Phase 1: Research

Use this phase to gather only the context needed for the current task.

- Read `docs/PLAN.md`, `docs/STATUS.md`, the target `TASK-XXX.md`, and linked `SPEC-XXX.md`.
- Run `python3 scripts/claw.py session-files <project_root> --task-id TASK-XXX` first. If shared files exist, fetch the relevant ones before planning.
- Use `python3 scripts/claw.py launch-plan <project_root>/tasks/TASK-XXX.md` to preview routing and workspace policy.
- If multiple read-only questions are independent, parallelize them. Do not overlap writes.

### Phase 2: Synthesis

The coordinator must produce an explicit execution brief before implementation.

- Summarize the exact scope: files, invariants, validations, and stop conditions.
- Decide `continue` vs `spawn`:
  - `continue`: the active worker already holds the necessary local context and the next slice touches the same files.
  - `spawn`: the slice is isolated, uses a disjoint file set, or can run in parallel as read-only work.
- Write a compact handoff note if another worker will need durable context:
  - Path: `handoff/summary.md`
  - Contract: 9 sections, no `<analysis>` block, concrete file paths and next actions

### Phase 3: Implementation

Choose the execution path that fits the task.

Direct execution for one bounded task:

```bash
python3 scripts/claw.py run --execute <project_root> TASK-XXX
```

Queue plus worker for longer or multiple queued tasks:

```bash
python3 scripts/claw.py run --enqueue <project_root> TASK-XXX
python3 scripts/claw.py worker --once <project_root>
```

During execution, track:

- `projects/<slug>/runs/<date>/RUN-XXXX/result.json`
- `projects/<slug>/runs/<date>/RUN-XXXX/report.md`
- `projects/<slug>/state/session_docs/<TASK>/files/handoff/summary.md`

### Phase 4: Verification

Verification is a separate pass, not an afterthought.

Required checks:

```bash
python3 scripts/validate_artifacts.py projects/<slug>/runs/<date>/RUN-XXXX
bash tests/run_all.sh
```

Also verify:

- acceptance criteria from the spec
- review artifacts and follow-up tasks
- no drift in task status, session notes, or shared files

If verification fails, return to synthesis with the concrete failure, not a vague retry request.

## Worker prompt contracts

### Research worker prompt

Use when a bounded read-only question can be delegated.

```text
Task: answer the research question below and return only concrete evidence.
Scope:
- Project root: <project_root>
- Task id: TASK-XXX
- Files to inspect: <paths>
- Commands allowed: read-only only
Output:
- Findings with file paths and line references
- Risks or unknowns
- No implementation changes
```

### Implementation worker prompt

Use when the scope is fixed and the target file set is known.

```text
Task: implement the requested change.
Scope:
- Project root: <project_root>
- Task id: TASK-XXX
- Files you may edit: <explicit file list>
- Constraints: preserve existing behavior outside this slice
Validation:
- <exact commands>
Return:
- changed files
- validation results
- remaining risks
```

### Verification worker prompt

Use when you need an independent validation pass.

```text
Task: verify the completed slice without broadening scope.
Inputs:
- run artifacts: <paths>
- changed files: <paths>
- spec acceptance criteria: <quoted summary>
Checks:
- artifact schema
- tests
- obvious regressions
Return:
- pass/fail
- concrete findings with file paths
```

## Task notifications

When a worker writes back into shared session docs, keep the feedback structured and concise:

```xml
<task-notification>
  <task-id>TASK-XXX</task-id>
  <status>completed|failed|blocked</status>
  <summary>One concrete paragraph with file paths and result.</summary>
  <next-action>Exact next coordinator action.</next-action>
</task-notification>
```

Mirror the durable form into `handoff/summary.md` when another worker or later session must continue.

## Concurrency rules

- Parallel-safe: read-only discovery, grep/search, spec review, artifact inspection.
- Serialize: edits to the same file, the same directory subtree, or merge-sensitive planning docs.
- Treat `docs/PLAN.md`, `docs/STATUS.md`, `docs/BACKLOG.md`, and `scripts/claw.py` as high-conflict files unless a single owner is assigned.

## Failure handling

- Non-zero agent exit: inspect `result.json`, repair the underlying issue, and rerun only the bounded slice.
- Invalid handoff note: rewrite `handoff/summary.md` in the compact format before continuing.
- Review failure: convert findings into a new bounded implementation brief or follow-up task.
- Validation failure: do not mark the task done and do not advance the plan.

## Quick reference

```bash
python3 scripts/claw.py session-files <project_root> --task-id TASK-XXX
python3 scripts/claw.py launch-plan <project_root>/tasks/TASK-XXX.md
python3 scripts/claw.py run --execute <project_root> TASK-XXX
python3 scripts/validate_artifacts.py projects/<slug>/runs/<date>/RUN-XXXX
bash tests/run_all.sh
python3 scripts/claw.py session-file-put <project_root> --task-id TASK-XXX handoff/summary.md --source-file /tmp/handoff.md --author codex
```
