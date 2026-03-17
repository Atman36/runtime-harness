# Native Subagents in `claw`

## Why `claw` still matters

Tool-native subagents do not replace `claw`.

- **Codex / Claude subagents** are short-lived delegation inside one parent session.
- **`claw`** is the durable orchestration layer: filesystem queue, run artifacts, hooks, approvals, review cadence, retries, scheduler, and cross-run state.

The practical split is:

- use a native subagent when the parent agent needs a bounded helper now
- use `claw` when the work must survive process exit, be inspectable on disk, or move through queue/review/approval hooks

## What is checked into this repo

Repo-scoped starter packs:

- `.codex/config.toml`
- `.codex/agents/claw-explorer.toml`
- `.codex/agents/claw-reviewer.toml`
- `.codex/agents/claw-worker.toml`
- `.claude/agents/claw-explorer.md`
- `.claude/agents/claw-reviewer.md`
- `.claude/agents/claw-implementer.md`

New project scaffolds also inherit generic starter packs through `projects/_template/.codex/` and `projects/_template/.claude/`.

## Decision rule

### Use Codex subagents when

- you are already in Codex and need explicit fan-out for parallel review points or targeted implementation slices
- the job fits inside one parent run
- you want a read-only explorer or reviewer that does not pollute the parent context

Codex subagents are explicit: the parent must ask Codex to spawn them.

### Use Claude subagents when

- you are already in Claude Code and want automatic delegation from a focused description
- you need a read-only explorer/reviewer or a bounded implementer with restricted tools
- you want isolation inside one Claude session without promoting the work to `claw`

Claude subagents stay inside one session. If you need multiple Claude sessions working in parallel and coordinating, use Claude agent teams instead of plain subagents.

### Use `claw` orchestration when

- the task needs queueing, retries, approvals, review cadence, or callback delivery
- the result must be visible as run artifacts on disk
- the work should continue across process boundaries or be picked up later by another agent
- you need deterministic task selection from `docs/PLAN.md` and project workflow contracts

## Suggested first-run onboarding in chat

This should stay conversational, not hidden behind brittle auto-detection only.

1. Ask which CLI the user already uses: `codex`, `claude`, both, or neither.
2. Verify local binaries with `command -v codex` and `command -v claude`.
3. If only one CLI is available or authenticated, route to that tool and narrow `allowed_agents` / policy accordingly.
4. If both are available, ask which one should be primary for implementation and which for research/review.
5. If the user is unsure about subscription or auth state, do a cheap capability probe such as `codex --help` / `claude --help`, then ask one confirmation question in chat instead of guessing.
6. Explain the fallback clearly:
   - only Codex available: implementation-first workflow, Claude-specific review steps disabled
   - only Claude available: research/review-heavy workflow, Codex implementation bias disabled
   - both available: keep `preferred_agent: auto`, use Codex for clear implementation and Claude for ambiguity/review

Recommended first message:

```text
Какие CLI у вас реально есть и используются каждый день: codex, claude, оба или пока ни одного?
Если не уверены, я могу быстро проверить наличие бинарей и дальше предложить безопасный дефолт для этого проекта.
```

## Practical prompt examples

### Codex

```text
Spawn claw_explorer to map the queue/runtime path for this bug, claw_reviewer to look for regression risk, and claw_worker only after the failure mode is clear.
```

### Claude

```text
Use the claw-reviewer subagent to review this diff, then use claw-implementer only for the smallest safe fix.
```
