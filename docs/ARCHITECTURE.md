# Architecture — claw

Date: 2026-03-13
Status: authoritative / reflects code as of Epic 7 + 9.1–9.5 completion

---

## Overview

`claw` is a **local-first project orchestration shell** built around three layers:

| Layer | Purpose |
|-------|---------|
| **Project shell** | Structure, templates, registry, review policy |
| **Engine** | File queue, worker loop, run artifacts, hooks, planner |
| **OpenClaw integration** | Chat entrypoint, event bridge, cron reconcile |

The single architectural principle tying them together: **filesystem is the source of truth**. Every decision, state transition, and result is a file on disk. There is no hidden in-memory state.

---

## System Layers

### 1. Project Shell (`claw/` top level)

```
claw/
├── _system/
│   ├── registry/          # agents.yaml, routing_rules.yaml, reviewer_policy.yaml
│   ├── templates/         # task/spec/project templates
│   ├── contracts/         # JSON Schema for all artifact types
│   └── engine/            # Python engine modules
├── projects/
│   ├── _template/         # Canonical project scaffold
│   └── <project-slug>/
│       ├── docs/
│       ├── specs/
│       ├── tasks/
│       ├── runs/          # Run artifacts (source of truth for execution history)
│       ├── reviews/       # Review batches + decision stubs
│       └── state/         # Queue, hooks, cadence, metrics (mutable runtime state)
├── scripts/               # CLI entrypoints + adapters
└── tests/                 # Smoke + integration test suite
```

The `_system/registry/` directory is the single configuration source for:
- which agents exist and how to invoke them (`agents.yaml`)
- routing heuristics that map task properties to agents (`routing_rules.yaml`)
- reviewer assignment policy (`reviewer_policy.yaml`)

### 2. Engine Layer (`_system/engine/`)

| Module | Role |
|--------|------|
| `file_queue.py` | Atomic filesystem queue with state-machine transitions |
| `task_planner.py` | Routing + execution contract derivation from task front matter and registry |
| `agent_exec.py` | Registry-driven agent command builder |
| `runtime.py` | Shared runtime helpers (timestamps, slug validation, etc.) |

Key design decision: engine modules are pure Python with no side effects on import. They expose APIs that scripts call — they do not embed CLI parsing.

### 3. Integration Layer (OpenClaw / `scripts/claw.py openclaw *`)

OpenClaw commands expose a **clean JSON API** over the engine, designed to be called from Claude chat sessions via `openclaw system event` or cron hooks.

| Command | Purpose |
|---------|---------|
| `openclaw status` | JSON snapshot of queue + hooks + runs + reviews |
| `openclaw enqueue` | Submit a task to the project queue |
| `openclaw review-batch` | Generate + return review batch manifest |
| `openclaw summary` | Human-readable run summary |
| `openclaw callback` | Parse hook payload from stdin, return completion summary |
| `openclaw wake` | Reconcile pending/failed hooks; run retry cycle |

All `openclaw_*` commands write diagnostic output to **stderr** and return clean JSON on **stdout**. This is enforced because `callback` and `wake` outputs are consumed programmatically.

---

## Primary Entities

### Task (`tasks/TASK-NNN.md`)

A Markdown file with YAML front matter. The authoritative source of what needs to be done.

Required front-matter fields:
- `id` — e.g. `TASK-001`
- `title`
- `status` — `open | in_progress | done`
- `spec` — path to the associated spec file
- `preferred_agent` — `auto | codex | claude`
- `review_policy` — e.g. `standard`
- `priority` — `low | medium | high`
- `project` — project slug
- `needs_review` — boolean
- `risk_flags` — list: `risky_area`, `uncertainty`, `large_diff`

### Spec (`specs/SPEC-NNN.md`)

Acceptance criteria for a task. Required sections: `goal`, `scope`, `constraints`, `acceptance criteria`, `notes`.

### Run directory (`runs/YYYY-MM-DD/RUN-XXXX/`)

The run directory is the immutable artifact of a single task execution. Once created, its path never changes. It contains:

| File | Role | Mutable? |
|------|------|---------|
| `job.json` | Full execution contract (task + routing + execution + paths) | **Immutable** after creation |
| `meta.json` | Mutable execution status + validation + hook snapshot | Yes |
| `result.json` | Machine-readable execution outcome | Yes (written during/after execution) |
| `prompt.txt` | Rendered agent prompt | Immutable after creation |
| `task.md` | Snapshot copy of task at run creation time | Immutable |
| `spec.md` | Snapshot copy of spec at run creation time | Immutable |
| `stdout.log` | Agent stdout | Written during execution |
| `stderr.log` | Agent stderr | Written during execution |
| `report.md` | Human-readable run summary | Written after execution |

The `run_path` field in `job.json` and in the queue item is the stable pointer between the queue and the run artifacts.

### Job (`job.json`)

`job_version: 1`. Carries the full execution contract including:
- `routing` — planner decision: selected agent, selection source, matched routing rule
- `execution` — workspace mode, workspace root, edit scope, parallel-safe flag, concurrency group
- `run_path` — relative path from project root to run directory

`job.json` is written by `scripts/build_run.py` and is **immutable** after creation. It is the audit record of the build-time decision.

### Queue Item (`state/queue/<state>/RUN-XXXX.json`)

`queue_version: 1`. Tracks the runtime queue state for a job. Fields:
- `job_id`, `run_id`, `run_path`, `project`
- `queue.state` — `pending | running | awaiting_approval | done | failed | dead_letter`
- `queue.attempt_count`, `queue.max_attempts`
- `queue.lease_id`, `queue.last_claimed_at`, `queue.lease_heartbeat_at`, `queue.lease_expires_at`
- `queue.history` — full state transition log

Queue items live in:
```
projects/<slug>/state/queue/
├── pending/
├── running/
├── awaiting_approval/
├── done/
├── failed/
└── dead_letter/
```

All state transitions are atomic via `os.replace`.

### Hook Payload (`state/hooks/<state>/<hook_id>.json`)

`event_version: 1`. Carries a completion notification from the engine to an external consumer (e.g. OpenClaw callback). Key fields:
- `hook_id`, `idempotency_key`
- `event_type` — e.g. `run_complete`
- `delivery_attempts`, `max_delivery_attempts`
- `run_id`, `project`, `status`, `agent`, `summary`

Hook lifecycle:
```
state/hooks/pending/  →  sent/
                      ↘  failed/  →  (reconcile retry)  →  sent/ or dead-letter
```

### Review Decision (`reviews/decisions/<batch_id>-<run_id>-stub.json`)

`decision_version: 1`. Written as a stub by `generate_review_batch.py`; filled in by the reviewer agent. Fields:
- `run_id`, `batch_id`, `reviewer`, `reviewed_at`
- `findings[]` — severity, description, file, recommendation
- `approved` boolean
- `waivers[]`, `follow_up_actions[]`

---

## Run Lifecycle

```
TASK.md + SPEC.md
    │
    ▼
scripts/build_run.py
    │  • reads task front matter + spec
    │  • runs task_planner.py → routing + execution contract
    │  • creates run directory: runs/YYYY-MM-DD/RUN-XXXX/
    │  • writes: job.json (immutable), meta.json, result.json (pending),
    │            prompt.txt, task.md, spec.md
    │
    ├── Direct execution path ──────────────────────────────┐
    │   scripts/execute_job.py                              │
    │     • reads job.json                                  │
    │     • materializes workspace (shared_project /        │
    │       git_worktree / isolated_checkout)               │
    │     • builds agent command via agent_exec.py          │
    │     • runs agent CLI                                  │
    │     • writes stdout.log, stderr.log, report.md        │
    │     • writes result.json (success/failed)             │
    │     • runs post-artifact validation                   │
    │     • embeds validation snapshot in result.json +     │
    │       meta.json                                       │
    │     • writes hook to state/hooks/pending/             │
    │     • attempts immediate hook dispatch                │
    │                                                       │
    └── Queue path ─────────────────────────────────────────┘
        scripts/claw.py enqueue
          • writes queue item → state/queue/pending/RUN-XXXX.json

        scripts/claw.py worker <project>
          • claims pending item → state/queue/running/
          • starts lease heartbeat while execute_job.py is running
          • calls execute_job.py
          • on success → state/queue/done/
          • on failure below max attempts → state/queue/failed/ → pending with `next_retry_at` + `retry_backoff_seconds`
          • on exhausted attempts → state/queue/dead_letter/
          • updates state/review_cadence.json
          • triggers review batch generation if threshold met
```

### Queue State Machine

```
pending ──claim──► running ──success──► done
   ▲                   │
   │ (reclaim          │ failure
   │  stale)           ▼
   │              failed ──retry + backoff──► pending
   │                   │
   │                   └── max attempts reached ──► dead_letter
   │
awaiting_approval ──approve──► pending
```

While a job is in `running`, the worker renews the lease periodically. Retry scheduling is persisted in the queue item itself via `next_retry_at` / `retry_backoff_seconds`, so the backoff policy is also filesystem-visible.

---

## Agent Execution Backends

Workspace mode is determined by the planner and persisted in `job.execution.workspace_mode`. The executor reads it as first priority.

| Mode | Description | Use case |
|------|-------------|----------|
| `project_root` (alias: `shared_project`) | Agent runs in the live project directory | Default for most tasks |
| `git_worktree` | Agent runs in a temporary git worktree of the repo | Parallel-safe tasks that touch source files |
| `isolated_checkout` | Agent runs in a fully isolated clone | High-risk or cross-branch tasks |

### Routing

Routing is determined by `_system/engine/task_planner.py` at build time:

1. If `task.preferred_agent` is a concrete agent (not `auto`), use it directly (source: `task_front_matter`).
2. Evaluate `_system/registry/routing_rules.yaml` rules in priority order against task properties (`risk_flags`, `needs_review`, `title` patterns).
3. Fall through to the registry default rule (currently `default-codex`).

The routing decision is persisted in `job.json` and `meta.json` as `routing.selected_agent`, `routing.selection_source`, `routing.routing_rule`.

### Agent Invocation

`_system/engine/agent_exec.py` reads `_system/registry/agents.yaml` and renders the agent command. Template variables in `args`:
- `{project_root}` — live project directory
- `{workspace_root}` — materialized workspace (may equal `project_root`)
- `{run_dir}` — run directory

Runtime overrides (for tests and local stubs):
- `CLAW_AGENT_COMMAND` — override all agents
- `CLAW_AGENT_COMMAND_CODEX` / `CLAW_AGENT_COMMAND_CLAUDE` — per-agent override
- `CLAW_AGENT_TIMEOUT_SECONDS`

### Parallel Agent Execution Notes

Практика двух параллельных запусков подтвердила несколько operational rules:

1. **Давай агентам узкие, почти не пересекающиеся slices.** Надёжный split: Codex на implementation/runtime, Claude на docs/review/architecture.
2. **Передавай агенту путь к worktree, а не к main repo.** Иначе `git_worktree`-изоляция превращается в декорацию.
3. **Planning docs (`PLAN/BACKLOG/STATUS`) мерджатся выборочно.** Это самый merge-sensitive слой: roadmap может поменяться, пока агент пишет docs в своей ветке.
4. **Completion summary не заменяет diff review.** Перед интеграцией оркестратор обязан смотреть `git show` и прогонять тесты, иначе можно незаметно затащить устаревшую документацию.

`10.2` остаётся отдельной задачей: оформить эти правила как полноценный parallel execution guide.

---

## Review System

### Trigger Policy

Stored in `_system/registry/reviewer_policy.yaml`.

| Trigger | Condition |
|---------|-----------|
| **Immediate** | `result.status == "failed"` |
| **Immediate** | `task.needs_review == true` |
| **Immediate** | `task.risk_flags` contains `risky_area`, `uncertainty`, or `large_diff` |
| **Cadence** | Every `successful_runs_batch` (default: 5) successful runs |

Reviewer is always the opposite model (`codex` agent → `claude` reviewer, vice versa), per `reviewer_policy.yaml`.

### Cadence State

`projects/<slug>/state/review_cadence.json` tracks the successful run counter per project. Worker updates it after every completed run and resets it when a cadence batch is emitted.

---

## Filesystem as Source of Truth

This principle governs all design decisions:

| Concern | Location |
|---------|---------|
| Agent configuration | `_system/registry/agents.yaml` |
| Routing rules | `_system/registry/routing_rules.yaml` |
| Review policy | `_system/registry/reviewer_policy.yaml` |
| Run artifact history | `projects/<slug>/runs/` |
| Queue state | `projects/<slug>/state/queue/` |
| Hook state | `projects/<slug>/state/hooks/` |
| Cadence counter | `projects/<slug>/state/review_cadence.json` |
| Metrics snapshot | `projects/<slug>/state/metrics_snapshot.json` |
| Review batches + decisions | `projects/<slug>/reviews/` |
| Project identity | `projects/<slug>/state/project.yaml` |

**No hidden in-memory state.** Any tool that reads the filesystem will see the same picture as the running worker.

---

## CLI Reference

Unified entrypoint: `python3 scripts/claw.py <command>`

| Command | Description |
|---------|-------------|
| `create-project <slug>` | Scaffold a new project from `_template` |
| `run <task>` | Build run artifacts (no execution) |
| `run --execute <task>` | Build + execute directly |
| `run --enqueue <task>` | Build + add to queue |
| `launch-plan <task>` | Dry-run preview: routing, workspace mode, command (no files written) |
| `enqueue <task>` | Add task to project queue |
| `worker <project>` | Run worker loop (claim → execute → hooks → review) |
| `worker --once <project>` | Process one job then exit |
| `approve <project> <run-id>` | Move awaiting_approval item to pending |
| `reclaim <project>` | Return stale running items to pending |
| `status <project> <run-id>` | Safe single-run status |
| `dashboard [--all] [project]` | Rich status across queue/reviews/approvals/ready tasks |
| `scheduler [projects...]` | Fair multi-project worker scheduler |
| `ask-human <project> <run-id>` | Create pending approval request |
| `resolve-approval <project> <approval-id>` | Resolve approval request |
| `orchestrate <project>` | Ready-task selector → worker → decision loop |
| `dispatch <project>` | Attempt delivery of pending hooks |
| `reconcile <project>` | Retry failed + stale hooks |
| `review-batch <project>` | Generate review batch for project |
| `openclaw <subcommand>` | JSON bridge for chat / OpenClaw integration |

---

## Test Coverage

Run the full suite: `bash tests/run_all.sh`

Key test files:
| Test | What it covers |
|------|---------------|
| `foundation_scaffold_test.sh` | Project directory structure |
| `task_to_job_test.sh` | Task → job artifact generation + planner wiring |
| `execute_job_test.sh` | Direct execution path (success + failure) |
| `hook_lifecycle_test.sh` | Hook pending → sent → failed → reconcile |
| `queue_lifecycle_test.sh` | Queue state transitions |
| `queue_cli_test.sh` | Queue CLI commands |
| `contracts_validation_test.sh` | JSON schema validation |
| `launch_plan_test.sh` | Dry-run preview command |
| `review_batch_test.sh` | Review batch generation |
| `review_runtime_integration_test.sh` | Cadence + immediate review trigger |
| `openclaw_test.sh` | OpenClaw JSON bridge commands |
| `metrics_snapshot_test.sh` | Metrics snapshot in state |
| `worker_reliability_test.sh` | Lease heartbeat, retry/backoff, dead_letter worker lifecycle |
| `docs_tracking_test.sh` | docs/ and project docs/ are git-tracked |
