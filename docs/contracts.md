# Contracts

Formal JSON Schema contracts for claw run artifacts, plus tooling for
offline validation and automated review batch generation.

## Schemas

Located in `_system/contracts/`:

| File | Validates | Key required fields |
|------|-----------|---------------------|
| `job.schema.json` | `runs/**/job.json` | `job_version`, `run_id`, `run_path`, `created_at`, `project`, `preferred_agent`, `routing`, `execution`, `task`, `spec`, `artifacts` |
| `result.schema.json` | `runs/**/result.json` | `run_id`, `status` |
| `meta.schema.json` | `runs/**/meta.json` | `run_id`, `status`, `project`, `routing`, `execution` |
| `task_claim.schema.json` | `projects/<slug>/state/claims/*.json` | `claim_version`, `task_id`, `status`, `owner`, `events` |
| `workflow.schema.json` | `projects/<slug>/docs/WORKFLOW.md` front matter | `contract_version`, `project`, `approval_gates`, `retry_policy` |

Schemas follow [JSON Schema draft 2020-12](https://json-schema.org/draft/2020-12/schema).
`jsonschema` (PyPI) is used when available; a built-in fallback covers the
essentials (required fields, types, enum, const) if it is not installed.

### Lifecycle notes

- **`result.json`** starts sparse (`status: pending`, `agent`) and gains
  `started_at`, `exit_code`, `summary`, `validation`, `hook`, etc. as the run progresses.
  The schema therefore makes all execution-phase fields optional.
- **`meta.json`** similarly starts at `status: created` and accumulates
  `executor`, `validation`, `hook`, timestamps after execution.
- **`job.json`** is immutable after creation; `job_version: 1` is enforced
  as a const.
- **`job.json` + `meta.json`** now persist planner-produced `routing` and
  `execution` contracts, so build-time decisions become reproducible artifacts
  instead of implicit runtime heuristics.

## Artifact Validator

`scripts/validate_artifacts.py` validates run artifacts against the schemas.

```bash
# Single artifact file
python3 scripts/validate_artifacts.py projects/my-project/runs/2024-03-12/RUN-0001/job.json

# All artifacts in a run directory
python3 scripts/validate_artifacts.py projects/my-project/runs/2024-03-12/RUN-0001/

# All runs in a project
python3 scripts/validate_artifacts.py --project projects/my-project

# All projects in the repo
python3 scripts/validate_artifacts.py --all

# Project workflow contract
python3 scripts/validate_artifacts.py --workflow projects/my-project

# Suppress passing lines (only show errors)
python3 scripts/validate_artifacts.py --quiet --project projects/my-project
```

Exit codes: `0` = all valid, `1` = validation errors, `2` = usage/missing files.

## Workflow Contract

`projects/<slug>/docs/WORKFLOW.md` is the project-level policy surface.
It is intentionally human-readable Markdown with YAML front matter rather than
a pure JSON artifact, but the front matter is still validated against
`_system/contracts/workflow.schema.json`.

Current control fields include:
- approval gates
- retry budget
- timeout defaults
- edit scope / allowed agents

The validator treats a missing workflow contract as optional. If the file
exists, it must satisfy the schema and field-level validation performed by
`_system/engine/workflow_contract.py`.

## Task Graph Snapshot

`projects/<slug>/state/tasks_snapshot.json` is a derived structural artifact,
not a hand-edited contract.

It is produced by:

```bash
python3 scripts/claw.py task-snapshot projects/my-project
```

Shape:

```json
{
  "snapshot_version": 1,
  "project": "my-project",
  "updated_at": "2026-03-13T12:00:00Z",
  "task_count": 3,
  "tasks": [],
  "checksum": "..."
}
```

The checksum is computed from canonical JSON for the task list. This allows
cheap drift detection and gives the orchestrator a stable structural view of
`depends_on` relationships.

Linting is exposed separately:

```bash
python3 scripts/claw.py task-lint projects/my-project
```

`task-lint` reports unknown dependencies and cycles; `claw orchestrate`
refreshes the snapshot and aborts immediately if a cycle is detected.

## Runtime Validation Integration

`scripts/execute_job.py` now calls artifact validation automatically after
writing `stdout.log`, `stderr.log`, and `report.md`.

Validation snapshot is embedded into:

- `result.json.validation`
- `meta.json.validation`

Shape:

```json
{
  "valid": true,
  "errors": {
    "job.json": [],
    "result.json": [],
    "meta.json": []
  }
}
```


## Persisted Planner Contract

Both `scripts/build_run.py` and `python3 scripts/claw.py launch-plan <task>` now
use the same planner source of truth.

Persisted shape:

```json
{
  "routing": {
    "selected_agent": "claude",
    "selection_source": "routing_rules",
    "routing_rule": "claude-ambiguous-design"
  },
  "execution": {
    "workspace_mode": "git_worktree",
    "workspace_root": "/abs/path/to/project",
    "workspace_materialization_required": true,
    "edit_scope": ["apps", "tests"],
    "parallel_safe": true,
    "concurrency_group": "demo-project:git_worktree:apps,tests"
  }
}
```

This makes two things possible:
- audit the build-time decision without rerunning heuristics manually
- compare `launch-plan` preview with the persisted contract inside a real run

## Review Batch Generator

`scripts/generate_review_batch.py` scans runs and emits review batch
artifacts when policy thresholds are met.

Policy is read from `_system/registry/reviewer_policy.yaml`.

### Triggering rules

| Type | Condition |
|------|-----------|
| **Immediate** | `result.status == "failed"` |
| **Immediate** | `job.task.needs_review == true` |
| **Immediate** | `job.task.risk_flags` contains `risky_area`, `uncertainty`, or `large_diff` |
| **Cadence** | Every `successful_runs_batch` (default: 5) successful runs not yet reviewed |

All immediate-trigger runs are collected into a single batch per invocation.
Cadence batches are only emitted for complete groups; a partial group is
reported as pending.

The **reviewer** is the opposite model from `reviewer_policy.yaml`
`default_mapping` (e.g. `codex` agent → `claude` reviewer, and vice versa).

## Worker Integration

Automatic review generation is no longer only a standalone CLI concern.
`scripts/claw.py worker` now:

- reads completed `result.json`
- keeps cadence state in `projects/<slug>/state/review_cadence.json`
- triggers batch generation automatically on immediate conditions
- resets cadence counter after an emitted cadence batch

Standalone `generate_review_batch.py` remains useful for manual backfill,
dry-run inspection, and batch regeneration workflows.

### Deduplication

On each run the generator reads all existing `REVIEW-*.json` files in the
project's `reviews/` directory and skips any run whose `run_id` already
appears in a batch. Re-running the generator is safe and idempotent.

### Usage

```bash
# Generate batches for one project
python3 scripts/generate_review_batch.py projects/my-project

# Preview without writing files
python3 scripts/generate_review_batch.py --dry-run projects/my-project

# Generate for all projects
python3 scripts/generate_review_batch.py --all
```

### Output format

Batch artifacts are written to `projects/<slug>/reviews/`:

```
REVIEW-<YYYY-MM-DD>-<seq>.json   machine-readable manifest
REVIEW-<YYYY-MM-DD>-<seq>.md     human-readable brief
```

`<seq>` is a zero-padded four-digit counter that increments per calendar
day, so multiple batches in a day get unique sequential IDs.

Example `REVIEW-2024-03-12-0001.json`:

```json
{
  "batch_version": 1,
  "batch_id": "REVIEW-2024-03-12-0001",
  "generated_at": "2024-03-12T10:00:00Z",
  "project": "my-project",
  "reviewer": "claude",
  "trigger_type": "immediate",
  "runs": [
    {
      "run_id": "RUN-0003",
      "run_date": "2024-03-12",
      "run_path": "runs/2024-03-12/RUN-0003",
      "status": "failed",
      "agent": "codex",
      "task_id": "TASK-005",
      "task_title": "Refactor auth middleware",
      "trigger": "failed",
      "needs_review": false,
      "risk_flags": []
    }
  ]
}
```

## Tests

```bash
bash tests/contracts_validation_test.sh
bash tests/review_batch_test.sh
bash tests/review_runtime_integration_test.sh
```

Tests are self-contained (temp directories, no network) and clean up after
themselves. Runtime integration coverage now includes `scripts/claw.py`
worker behavior and review cadence state transitions.
