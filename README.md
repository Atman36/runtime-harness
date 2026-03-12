# claw

A local-first project orchestration shell for managing tasks, specs, and agent runs through Codex and Claude.

---

## What it does

`claw` gives you a structured way to:

- Maintain a **project workspace** with tasks, specs, and docs
- **Submit tasks to a file-backed queue** and run them with Codex or Claude
- **Dry-run preview** routing + workspace decisions before launch (`claw launch-plan`)
- **Receive completion callbacks** back into your chat session (OpenClaw bridge)
- **Generate review batches** automatically on cadence or after risky runs

Everything is stored as files on disk. There is no daemon, no database, and no hidden in-memory state.

---

## Architecture at a glance

```
Project shell          Engine                  OpenClaw bridge
─────────────          ──────                  ───────────────
tasks/ specs/ docs/    file queue              claw openclaw status
runs/ reviews/         worker loop             claw openclaw enqueue
_system/registry/      task planner            claw openclaw callback
_system/contracts/     agent_exec.py           claw openclaw wake
                       hooks/reconcile
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full system narrative.

---

## Quick start

### 1. Create a project

```bash
python3 scripts/claw.py create-project my-project
```

This scaffolds `projects/my-project/` with the standard directory layout.

### 2. Add a task and spec

Copy from `projects/_template/tasks/` and `projects/_template/specs/`. Edit the YAML front matter (`id`, `title`, `preferred_agent`, `spec`, etc.).

### 3. Preview execution decision

```bash
python3 scripts/claw.py launch-plan projects/my-project/tasks/TASK-001.md
```

Shows which agent will run, which routing rule matched, workspace mode, and the command preview — without writing any files.

### 4. Run a task

```bash
# Build + execute directly
python3 scripts/claw.py run --execute projects/my-project/tasks/TASK-001.md

# Or queue it for the worker
python3 scripts/claw.py run --enqueue projects/my-project/tasks/TASK-001.md
python3 scripts/claw.py worker projects/my-project --once
```

### 5. Check status

```bash
python3 scripts/claw.py status projects/my-project
```

### 6. Review results

```bash
# Generate a review batch
python3 scripts/claw.py review-batch projects/my-project

# Validate run artifacts against schema
python3 scripts/validate_artifacts.py --project projects/my-project
```

---

## Key concepts

### Run artifacts

Every task execution creates an immutable run directory:

```
projects/my-project/runs/2026-03-13/RUN-0001/
├── job.json        # full execution contract (routing, workspace, task, spec)
├── meta.json       # execution status + validation snapshot
├── result.json     # machine-readable outcome
├── prompt.txt      # rendered agent prompt
├── task.md         # snapshot of task at run time
├── spec.md         # snapshot of spec at run time
├── stdout.log
├── stderr.log
└── report.md
```

`job.json` is immutable after creation. `result.json` and `meta.json` are updated in-place as the run progresses.

### Routing

The task planner (`_system/engine/task_planner.py`) decides which agent to use and in which workspace:

1. `preferred_agent` in task front matter (if not `auto`)
2. Rules in `_system/registry/routing_rules.yaml` matched against task properties
3. Default fallback (currently `default-codex`)

The routing decision is persisted in `job.json` and is visible in `launch-plan` before any run starts.

### Queue

The file queue (`_system/engine/file_queue.py`) manages job state atomically:

```
pending → running → done
                 ↘ failed → (retry + backoff) → pending
                                            ↘ dead_letter

awaiting_approval → (approve) → pending
```

While a job runs, `claw.py worker` renews its lease heartbeat. Retry timing is persisted in the queue item itself (`next_retry_at`, `retry_backoff_seconds`), so the operational state stays visible on disk.

Queue items live in `projects/<slug>/state/queue/<state>/`.

### Hooks

After execution, a completion hook is written to `state/hooks/pending/` and dispatched immediately if `CLAW_HOOK_COMMAND` is set. Undelivered hooks are retried by `claw reconcile` or `claw openclaw wake`.

### Review system

Review batches are generated automatically:
- After every 5 successful runs (cadence)
- Immediately on `failed`, `needs_review`, or risky `risk_flags`

Reviewer is always the opposite model (codex run → claude reviews, vice versa).

---

## Agent selection

| Use Claude when | Use Codex when |
|-----------------|---------------|
| Design / UX / flow | Clear implementation spec |
| Ambiguous or exploratory spec | Bug fixes / refactoring |
| Architecture decisions | Shell/Python glue code |
| Reviewing Codex output | Local code changes with clear DoD |

Set `preferred_agent: auto` in the task front matter to let the routing rules decide.

---

## OpenClaw integration

If you use Claude in a chat session (OpenClaw), the engine can:
- Accept task submissions from chat
- Send completion summaries back via `claw openclaw callback`
- Run periodic reconcile via `claw openclaw wake` (cron or event-driven)

All `openclaw` commands return clean JSON on stdout for programmatic consumption.

---

## Testing

```bash
# Full test suite
bash tests/run_all.sh

# Specific suites
bash tests/contracts_validation_test.sh
bash tests/queue_lifecycle_test.sh
bash tests/worker_reliability_test.sh
bash tests/openclaw_test.sh
```

---

## Documentation

| Doc | Purpose |
|-----|---------|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System layers, entities, lifecycles, agent backends |
| [`docs/CONTRACT_VERSIONING.md`](docs/CONTRACT_VERSIONING.md) | Schema versioning + migration strategy |
| [`docs/contracts.md`](docs/contracts.md) | Artifact schemas, validation tooling, planner contract |
| [`docs/EXECUTION_FLOW.md`](docs/EXECUTION_FLOW.md) | End-to-end run and queue flow with command reference |
| [`docs/PLAN.md`](docs/PLAN.md) | Implementation plan and architectural decisions |
| [`docs/STATUS.md`](docs/STATUS.md) | Current phase and live audit log |

---

## Project layout

```
claw/
├── _system/
│   ├── registry/          # agents.yaml, routing_rules.yaml, reviewer_policy.yaml
│   ├── templates/         # task, spec, project templates
│   ├── contracts/         # JSON Schema for all artifact types
│   └── engine/            # file_queue.py, task_planner.py, agent_exec.py, runtime.py
├── projects/
│   ├── _template/         # canonical project scaffold
│   └── <slug>/            # your projects
│       ├── docs/ specs/ tasks/ runs/ reviews/
│       └── state/         # queue/, hooks/, review_cadence.json, metrics_snapshot.json
├── scripts/               # claw.py, build_run.py, execute_job.py, validate_artifacts.py, …
└── tests/                 # run_all.sh + per-feature test scripts
```
