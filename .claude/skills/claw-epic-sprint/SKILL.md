---
name: claw-epic-sprint
description: Decompose a claw epic or large feature from PLAN.md/BACKLOG.md into concrete TASK + SPEC file pairs ready for agent execution. Use when the user wants to plan the next development sprint, break down an epic into tasks, create a batch of specs for an upcoming feature, or says phrases like "разбей эпик", "распланируй эпик 9.6", "создай задачи для эпика", "plan epic", "sprint planning", "decompose epic", "что делать дальше по плану". Also trigger when the user says "начнём эпик X" or "take on epic X".
---

# claw-epic-sprint

Convert an epic from the roadmap into a set of concrete, agent-ready task + spec pairs. Each spec must be completable in one agent session (2–3 hours of work).

## Step 1: Read the roadmap

Read these files to understand the current state:
- `docs/PLAN.md` — epics, decisions, active work
- `docs/BACKLOG.md` — detailed epic descriptions with acceptance criteria
- `docs/STATUS.md` — what's already done (avoid duplication)

If the user specifies an epic number (e.g. "9.6"), find it in BACKLOG.md.

## Step 2: Understand the epic

Extract from the epic description:
1. **Goal** — what outcome does this epic achieve?
2. **Deliverables** — concrete artifacts expected
3. **Dependencies** — what must exist first?
4. **Risks** — what's uncertain or potentially breaking?
5. **Affected files** — scan the codebase to identify actual files

## Step 3: Decompose into specs

**Split criteria:**
- Each spec = one agent session (2–3 hours)
- One spec should not modify more than ~5 files
- If a spec would require both design work and implementation, split it
- Tests for a feature = separate spec from the feature itself (or combined if small)

**Ordering rules:**
1. Read-only / design specs first (no risk)
2. Core implementation second
3. Tests third (can reference implementation)
4. Integration/wiring last

**Typical decomposition patterns:**

| Epic type | Split into |
|-----------|-----------|
| New feature | design → implement core → add tests → wire into CLI |
| Hardening | identify gaps → fix each gap (1 spec per file/module) → regression tests |
| Refactor | map current state → change module A → change module B → update tests |
| Bug fix | reproduce + diagnose spec → fix spec → test spec |

## Step 4: Write the task + spec files

For each spec, create:
- `projects/<project>/tasks/TASK-NNN.md`
- `projects/<project>/specs/SPEC-NNN.md`

Follow the format from **claw-task-spec** (read it if needed):
- TASK frontmatter: id, title, status, spec, preferred_agent, tags, priority
- SPEC: 8 sections (Goal, Why, In scope, Out of scope, Files/zones, Steps, Acceptance criteria, Test cases)

**Agent routing for typical epic tasks:**
- Stress tests, shell hardening, concurrency → `codex` + tags: `tests`, `implementation`
- Architecture design, design docs → `claude` + tags: `design`, `architecture`
- Python implementation → `codex` + tags: `implementation`

## Step 5: Create an index (if 3+ specs)

If the epic generates 3 or more specs, create a sprint index:

```markdown
# Epic NNN Sprint — <Epic title>
> Created: YYYY-MM-DD

## Goal
<One sentence summary of what this sprint achieves>

## Current Pointer
- **Current task:** SPEC-NNN
- **Next task:** SPEC-NNN+1
- **Last completed:** —

## Task Board
| ID | Title | Status | Agent | Review | Commit | Notes |
|----|-------|--------|-------|--------|--------|-------|
| SPEC-NNN | ... | todo | codex | — | — | |
| SPEC-NNN+1 | ... | todo | codex | — | — | |

## Execution Log
| Date | Task | Event | Result | Next |
|------|------|-------|--------|------|
```

Save to: `projects/<project>/docs/SPRINT-epic-NNN.md`

## Step 6: Present the plan

Show the user:
1. **Decomposition summary** — N specs, estimated total effort
2. **Dependency order** — which to run first
3. **The spec list** with one-line descriptions
4. **Launch command** for the first task:
   ```bash
   python scripts/claw.py launch-plan TASK-NNN   # preview
   python scripts/claw.py run --execute TASK-NNN  # run
   ```

Ask for approval before creating the files if the decomposition is non-obvious — it's cheaper to adjust the plan than to rewrite specs.

---

## Current active epics (as of project state)

From PLAN.md, the active epics are in the 9.x and 8.x series:
- **9.6** — Concurrency/stress/failure-injection tests
- **9.7** — Shell command trust boundary hardening
- **9.8** — Execution robustness fixes
- **9.9** — Latent edge case cleanup
- **8.4** — Continuous orchestration loop
