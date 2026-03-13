---
name: claw-task-spec
description: Create a TASK + SPEC file pair for the claw orchestration system following the spec-driven development methodology. Use whenever the user wants to create a new task, write a spec, decompose work into a claw-compatible unit, add something to the project backlog, or says phrases like "create task", "make spec", "write spec for", "create SPEC-N", "создай задачу", "напиши спеку", "добавь задачу в claw". Also trigger when the user describes work that should be done by an agent — the right response is usually a spec, not a free-form description.
---

# claw-task-spec

A spec-file is a contract between the user and the agent: goal, scope, acceptance criteria, test cases. A good spec = predictable output. Your job is to produce exactly this contract.

## What to create

Two files per task:

**`projects/<project>/tasks/TASK-NNN.md`** — routing + metadata
**`projects/<project>/specs/SPEC-NNN.md`** — the actual contract

---

## Step 1: Gather info

Before writing anything, collect:
1. **Project** — which project directory (e.g. `demo-project`). If unclear, check `projects/` for existing ones.
2. **Task number** — next available NNN (scan existing `tasks/` and `specs/` to find the gap).
3. **Scope** — what needs to be done, what's explicitly excluded.
4. **Files/zones** — which files the agent should touch. If the user doesn't know, suggest based on the claw codebase.
5. **Agent routing** — see routing rules below.

Ask for missing info if the user's description is too vague to write verifiable acceptance criteria.

---

## Step 2: Determine preferred_agent

Read `_system/registry/routing_rules.yaml`. Short version:
- **codex** → tags include `implementation`, `tests`, `fixes`, `shell` + spec is clear → codex
- **claude** → tags include `design`, `architecture`, `research`, `ux` + high ambiguity → claude
- **auto** → let the planner decide (safe default)

Add matching tags to the TASK frontmatter.

---

## Step 3: Write TASK-NNN.md

```markdown
---
id: TASK-NNN
title: "<Short imperative title>"
status: todo
spec: ../specs/SPEC-NNN.md
preferred_agent: <codex|claude|auto>
review_policy: standard
priority: <high|medium|low>
project: <project-slug>
needs_review: <true|false>
risk_flags: []
tags: [<implementation|tests|fixes|design|architecture|research|shell>]
---

# Task

## Goal
<One sentence: what this task achieves.>

## Notes
- <Any routing hints, dependencies on other tasks, or context the agent needs.>
```

---

## Step 4: Write SPEC-NNN.md

Use the 8-section format. Every section must be concrete — no vague language.

```markdown
# SPEC-NNN — <Short name>
**Estimate:** 2–3 hours

## Goal
<One sentence: the outcome when this spec is done.>

## Why
<Business or technical reason this matters. Gives the agent context so it can make sensible decisions at the margin.>

## In scope
- <Specific change 1>
- <Specific change 2>

## Out of scope
- <What explicitly must NOT be touched. This is the most important section after acceptance criteria.>

## Files / zones
- `path/to/file.py` — <what to do there>
- `path/to/other.sh` — <what to do there>

## Steps
1. <Concrete step>
2. <Concrete step>
3. <Concrete step>

## Acceptance criteria
- <Specific, verifiable claim. "Route returns 403 for non-owner" — good. "Works correctly" — not acceptable.>
- <Another verifiable claim>

## Test cases
- Happy path: <describe>
- Edge case: <describe>
- Negative case: <describe>

## Notes
<Any additional context, gotchas, or decisions the agent should be aware of.>

---
*After completion: update task status to `done`, add commit hash to the execution log.*
```

---

## Key principles (why each section exists)

- **Out of scope** — agents tend to "improve things along the way". An explicit boundary prevents unexpected changes to unrelated files.
- **Files / zones** — without this, agents search and sometimes find the wrong files.
- **Acceptance criteria** — defines "done" unambiguously. Without it, the agent decides when it's satisfied.
- **Test cases** — without explicit test cases, agents write only happy-path tests.
- **Estimate 2–3 hours** — if the spec looks bigger, split it. One agent session = one spec.

---

## After writing

Tell the user:
1. The paths to both files
2. The routing decision and why
3. The command to run it: `python scripts/claw.py run --execute TASK-NNN` (direct) or `--enqueue TASK-NNN` (queue)
4. Preview with: `python scripts/claw.py launch-plan TASK-NNN`
