# claw

Orchestration workspace for spec-driven agent tasks.

## Stack
- Python (`scripts/claw.py`) — main CLI
- Shell scripts in `scripts/` — task execution, dispatch, hooks
- `projects/` — per-project workspaces with specs, tasks, runs

## Key commands
```
python scripts/claw.py --help
scripts/run_task.sh <project> <task>
scripts/create_project.sh <name>
```

## Native subagents

This repo now ships project-scoped starter subagents in `.claude/agents/`:

- `claw-explorer` — read-only codebase and runtime explorer
- `claw-reviewer` — read-only regression and contract reviewer
- `claw-implementer` — bounded implementation helper

Use Claude subagents for isolated work inside one Claude session. If the task
needs durable queue/artifact/review lifecycle, keep it in `claw` instead of
pushing it down into subagents.

Codex starter agents also exist in `.codex/agents/` for the same repo.

## First-run onboarding

Before assuming Claude is available, ask which CLI the user actually has:
`claude`, `codex`, both, or neither. Confirm local availability with
`command -v claude` / `command -v codex`; if only one exists, route to that tool
instead of treating the other as required.

## Rules
- Tests live in `tests/` — run `bash tests/run_all.sh` before committing
- New projects must be created from `projects/_template/`
- Never edit files inside `runs/` — they are generated artifacts
