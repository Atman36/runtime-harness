# runtime harness

> Filesystem-first orchestration layer for running agent tasks with Codex and Claude.

---

Every task run produces immutable artifacts on disk. There is no daemon, no database, no hidden in-memory state. The filesystem *is* the system.

## Features

- **Task + spec workflow** — structured YAML front matter, project scaffolding, canonical templates
- **File-backed queue** — atomic state transitions (`pending → running → done / failed → dead_letter`)
- **Worker loop** — lease heartbeat, exponential backoff, retry exhaustion → `dead_letter`
- **Agent routing** — rules-based dispatch to Codex or Claude; `launch-plan` for dry-run preview
- **Hooks & callbacks** — idempotent delivery, retry on failure, `reconcile` for missed events
- **Review cadence** — automatic review batch generation on cadence or on risky/failed runs
- **OpenClaw bridge** — submit tasks and receive completion summaries from a chat session
- **Schema validation** — JSON Schema contracts for all artifact types; `validate_artifacts.py`

## Quick start

```bash
# 1. Create a project
python3 scripts/claw.py create-project my-project

# 2. Preview the execution decision (dry run)
python3 scripts/claw.py launch-plan projects/my-project/tasks/TASK-001.md

# 3. Run a task directly
python3 scripts/claw.py run --execute projects/my-project/tasks/TASK-001.md

# 4. Or queue it and run the worker
python3 scripts/claw.py run --enqueue projects/my-project/tasks/TASK-001.md
python3 scripts/claw.py worker projects/my-project --once

# 5. Check status
python3 scripts/claw.py status projects/my-project

# 6. Generate a review batch
python3 scripts/claw.py review-batch projects/my-project

# 7. Validate run artifacts
python3 scripts/validate_artifacts.py --project projects/my-project
```

## Architecture

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

## Run artifacts

Every execution creates an immutable run directory:

```
projects/my-project/runs/YYYY-MM-DD/RUN-0001/
├── job.json        # full execution contract (immutable after creation)
├── meta.json       # execution status + validation snapshot
├── result.json     # machine-readable outcome
├── prompt.txt      # rendered agent prompt
├── task.md         # task snapshot at run time
├── spec.md         # spec snapshot at run time
├── stdout.log
├── stderr.log
└── report.md
```

## Queue lifecycle

```
pending → running → done
                 ↘ failed → (retry w/ backoff) → pending
                                               → dead_letter

awaiting_approval → (approve) → pending
```

Queue items live in `projects/<slug>/state/queue/<state>/`.

## Agent routing

| Use Claude when | Use Codex when |
|---|---|
| Design / UX / flow | Clear implementation spec |
| Ambiguous or exploratory spec | Bug fixes / refactoring |
| Architecture decisions | Shell/Python glue code |
| Reviewing Codex output | Local code changes with clear DoD |

Set `preferred_agent: auto` in task front matter to let routing rules decide.

## OpenClaw

When running Claude in a chat session, the engine can:

- Accept task submissions from chat (`claw openclaw enqueue`)
- Send completion summaries back (`claw openclaw callback`)
- Reconcile missed hooks via cron or event trigger (`claw openclaw wake`)

All `openclaw` commands emit clean JSON on stdout.

## Testing

```bash
# Full test suite
bash tests/run_all.sh

# Individual suites
bash tests/contracts_validation_test.sh
bash tests/queue_lifecycle_test.sh
bash tests/openclaw_test.sh
bash tests/worker_reliability_test.sh
```

## Project layout

```
runtime-harness/
├── _system/
│   ├── registry/          # agents.yaml, routing_rules.yaml, reviewer_policy.yaml
│   ├── templates/         # task, spec, project templates
│   ├── contracts/         # JSON Schema for all artifact types
│   └── engine/            # file_queue.py, task_planner.py, agent_exec.py, runtime.py
├── projects/
│   ├── _template/         # canonical project scaffold
│   └── <slug>/
│       ├── docs/ specs/ tasks/ runs/ reviews/
│       └── state/         # queue/, hooks/, review_cadence.json, metrics_snapshot.json
├── scripts/               # claw.py, build_run.py, execute_job.py, validate_artifacts.py
└── tests/                 # run_all.sh + per-feature test scripts
```

## Requirements

- Python 3.9+
- Bash
- `codex` and/or `claude` CLI available in `PATH`

## Documentation

| Doc | Purpose |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System layers, entities, run lifecycle, agent backends |
| [`docs/CONTRACT_VERSIONING.md`](docs/CONTRACT_VERSIONING.md) | Schema versioning and migration strategy |
| [`docs/PARALLEL_EXECUTION.md`](docs/PARALLEL_EXECUTION.md) | Worktree isolation, merge discipline, concurrency groups, continuous loop requirements |
| [`docs/contracts.md`](docs/contracts.md) | Artifact schemas and validation tooling |
| [`docs/EXECUTION_FLOW.md`](docs/EXECUTION_FLOW.md) | End-to-end run and queue flow with command reference |

## License

MIT
