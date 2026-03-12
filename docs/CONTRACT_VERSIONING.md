# Contract Versioning & Migration — claw

Date: 2026-03-13
Status: authoritative / covers job, result, meta, queue item, hook payload, review decision

---

## Overview

`claw` uses file-backed artifacts as the source of truth. Every artifact type carries a version field so consumers can detect schema evolution without relying on file paths or timestamps.

This document covers:
1. Which artifacts have version fields and what they mean
2. Forward/backward compatibility stance
3. When migration is needed and how to trigger it
4. Who is allowed to modify persisted artifacts and when
5. Validation strategy for catching schema drift early

---

## Version Fields by Artifact

| Artifact | Version field | Location | Current value |
|----------|---------------|----------|---------------|
| `job.json` | `job_version` | Top-level | `1` |
| Queue item | `queue_version` | Top-level | `1` |
| Hook payload | `event_version` | Top-level | `1` |
| Review decision | `decision_version` | Top-level | `1` |
| `result.json` | _(none yet — see below)_ | — | — |
| `meta.json` | _(none yet — see below)_ | — | — |

`result.json` and `meta.json` do not currently carry a version field because they are **output artifacts** that grow in-place during a run and are validated against `result.schema.json` / `meta.schema.json`. Adding `result_version` / `meta_version` is recommended before the first schema-breaking change to either.

---

## Compatibility Stance

### job.json — Immutable after creation

`job.json` is written once by `scripts/build_run.py` and never modified. It is the audit record of the build-time decision. The `job_version: 1` const in the JSON Schema enforces this.

Compatibility rule:
- **Additive changes** (new optional fields): safe, no migration needed. Update `additionalProperties: false` in the schema to allow the new field.
- **Removing or renaming a required field**: breaking — requires a version bump to `job_version: 2`.
- **Changing a field's type or enum**: breaking — requires a version bump.

### queue item — Mutable during worker lifecycle

Queue items start at `queue_version: 1` and are atomically rewritten during state transitions. The schema uses `additionalProperties: true` to allow forward compatibility.

Compatibility rule:
- **Additive changes**: safe. Consumers that do not know a field should ignore it.
- **Removing a field that active workers read**: breaking — coordinate with worker code change.
- **Changing `queue.state` enum values**: breaking — requires migration of all items currently in affected states.

### hook payload — Immutable after creation

Hook payloads are written by `hooklib.py` and not rewritten after delivery. `event_version: 1` is set at creation time.

Compatibility rule:
- **Additive changes**: safe. The `CLAW_HOOK_COMMAND` consumer should tolerate unknown fields.
- **Removing or renaming fields the consumer reads**: breaking — version bump required.

### review decision — Filled in by reviewer agent

Decision stubs are written by `generate_review_batch.py` and filled in by the reviewer. `decision_version: 1` is set in the stub.

Compatibility rule:
- **Additive changes** to the stub format: safe.
- **Changing the stub structure the reviewer agent depends on**: coordinate with reviewer prompt / review policy.

---

## When a Migration Is Needed

A migration is required when any of the following are true:

1. A required field is removed from a schema.
2. A field's type changes in an incompatible way.
3. An enum value is renamed or removed.
4. A new required field (with no default) is added.
5. The interpretation of an existing field changes semantically (e.g. `workspace_mode: "shared_project"` was an alias; if the canonical name changes, existing items need updating).

A migration is **not** required for:
- Adding optional fields with a default or `null` value.
- Adding new allowed enum values (consumers that do not handle the new value will fall through to their default behavior — this is acceptable if a sensible default exists).
- Changing documentation strings in the schema.

---

## Migration Triggers

Migrations are triggered manually by a developer or operator, not automatically by the worker. Automatic migration inside a hot worker loop is unsafe because:
- A worker may hold a lease on an item while migration runs.
- A crashed migration leaves artifacts in a half-migrated state.
- Multiple workers on the same project would race.

### Trigger conditions

| Trigger | When to run a migration |
|---------|------------------------|
| **Deployment** | When deploying a claw version that changes a required field or enum |
| **Schema bump** | When `job_version` / `queue_version` etc. increments |
| **Failed validation** | When `validate_artifacts.py --all` reports errors on existing runs |
| **Tooling upgrade** | When a new `execute_job.py` or `file_queue.py` version reads a field that old artifacts lack |

---

## Who May Migrate Artifacts and When

| Artifact | Who may modify | When |
|----------|---------------|------|
| `job.json` | **Nobody.** It is immutable. | — |
| Queue item | Worker (during state transitions via `file_queue.py`) + migration script (between deploys, when queue is idle) | Only when no worker holds the item's lease |
| Hook payload | Nobody after creation. Delivery state (pending/sent/failed) is tracked via filesystem location, not by rewriting the file. | — |
| Review decision stub | Reviewer agent (fills in findings, approved, waivers) | During review session |
| `result.json` / `meta.json` | `execute_job.py` (during execution) + migration script (between deploys) | Migration only when queue is drained and no worker is running |

**Rule:** Any migration script that touches queue items or run artifacts must:
1. Run with the project queue **idle** (no running workers for that project).
2. Be idempotent — safe to run twice.
3. Validate the result with `scripts/validate_artifacts.py` after completion.

---

## Migration Strategy

### Step 1: Drain the queue

Before migrating, ensure no items are in `running` state:
```bash
python3 scripts/claw.py status <project>
# confirm: 0 running jobs
```

### Step 2: Run the migration script

Migration scripts live in `scripts/migrations/` and are named `migrate_<artifact>_v<from>_to_v<to>.py`.

Example invocation:
```bash
python3 scripts/migrations/migrate_job_v1_to_v2.py --project projects/my-project --dry-run
python3 scripts/migrations/migrate_job_v1_to_v2.py --project projects/my-project
```

A migration script must:
- Accept `--dry-run` to preview changes without writing.
- Accept `--project` or `--all` scope.
- Print a summary of how many items were migrated.
- Exit non-zero if any item could not be migrated.

### Step 3: Validate

```bash
python3 scripts/validate_artifacts.py --project projects/my-project
# or
python3 scripts/validate_artifacts.py --all
```

All artifacts must pass validation before restarting the worker.

### Step 4: Re-run tests

```bash
bash tests/run_all.sh
```

The test suite must pass against the migrated data before production use.

---

## Adding a New Required Field (Cookbook)

When adding a new required field to `job.json` (e.g., `job_version: 2` gains `owner` field):

1. **Bump the version const** in `job.schema.json`: `"const": 2`.
2. **Update `build_run.py`** to write the new field.
3. **Update `execute_job.py`** to read the new field (with a safe default for version-1 items if backward compatibility is needed).
4. **Write a migration script** `migrate_job_v1_to_v2.py` that adds a default value for `owner` in all existing `job.json` files.
5. **Add a test** in `contracts_validation_test.sh` that verifies a v2 job passes schema validation.
6. **Run migration + tests** as described above.

---

## Backward Compatibility During Transition

When `claw` is deployed with a schema bump but migration has not yet run (or is partial), `execute_job.py` and the worker should:

- Read optional fields with `get(field, default)` — never assume a field exists.
- Skip, not crash, if a field is missing and a sensible default exists.
- Reject (not silently corrupt) an item whose version field is unknown.

Example pattern in Python:
```python
workspace_mode = (
    job.get("execution", {}).get("workspace_mode")
    or os.environ.get("CLAW_WORKSPACE_MODE")
    or "project_root"
)
```

This makes the code resilient during a rolling migration and safe against partially-migrated artifacts.

---

## Validation as a First-Class Check

`scripts/validate_artifacts.py` is the authoritative validator. It should be run:

- After every migration
- In CI/CD on the demo-project fixture
- As part of `run_all.sh` smoke suite (via `contracts_validation_test.sh`)
- Manually when investigating a suspicious run

`execute_job.py` also embeds a post-execution validation snapshot in `result.json.validation` and `meta.json.validation`. This catches schema drift that only becomes visible after a real run.

---

## Current Schema Versions (as of 2026-03-13)

| Artifact | Version | Schema file |
|----------|---------|-------------|
| `job.json` | `job_version: 1` | `_system/contracts/job.schema.json` |
| Queue item | `queue_version: 1` | `_system/contracts/queue_item.schema.json` |
| Hook payload | `event_version: 1` | `_system/contracts/hook_payload.schema.json` |
| Review decision | `decision_version: 1` | `_system/contracts/review_decision.schema.json` |
| `result.json` | _(no version field)_ | `_system/contracts/result.schema.json` |
| `meta.json` | _(no version field)_ | `_system/contracts/meta.schema.json` |

**Recommended next step:** add `result_version: 1` and `meta_version: 1` to their respective schemas and to `execute_job.py` before any breaking change to these formats.
