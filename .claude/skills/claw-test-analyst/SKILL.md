---
name: claw-test-analyst
description: Run the claw test suite and analyze failures with actionable fixes. Use when the user wants to run tests, check if something is broken, verify a change didn't break anything, prepare for a commit, or says phrases like "запусти тесты", "прогони тесты", "run tests", "что сломалось", "check tests", "are tests passing", "перед коммитом", "тесты падают". Also trigger automatically when the user has just made code changes and is about to commit.
---

# claw-test-analyst

The claw test suite is mandatory before every commit. Running and interpreting it correctly is part of the workflow.

## Step 1: Run the tests

```bash
bash tests/run_all.sh
```

This runs all 16 test files in sequence. Capture full output.

If the user wants to run a specific test:
```bash
bash tests/<test_file>.sh
```

## Step 2: Parse the output

The test runner outputs results in this format:
```
=== Running: tests/foundation_scaffold_test.sh ===
PASS
=== Running: tests/task_to_job_test.sh ===
FAIL: Expected file runs/... to exist
```

Extract:
- Total tests run
- Total passed / failed
- Which test files failed
- The specific assertion that failed within each file

## Step 3: For each failure, diagnose

### Read the failing test file

Read `tests/<failing_test>.sh` to understand what it's testing. Look for:
- What files/artifacts it expects to exist
- What values it checks
- What commands it runs

### Map failure to source code

Cross-reference the failing assertion with the relevant source files:

| Test file | Primary source files |
|-----------|---------------------|
| `foundation_scaffold_test.sh` | `scripts/create_project.sh`, `projects/_template/` |
| `task_to_job_test.sh` | `scripts/build_run.py`, `_system/engine/task_planner.py` |
| `execute_job_test.sh` | `scripts/execute_job.py`, `_system/engine/agent_exec.py` |
| `hook_lifecycle_test.sh` | `_system/engine/runtime.py`, `scripts/claw.py` (dispatch/reconcile) |
| `queue_lifecycle_test.sh` | `_system/engine/file_queue.py` |
| `queue_cli_test.sh` | `scripts/claw.py` (cmd_enqueue, cmd_worker) |
| `contracts_validation_test.sh` | `_system/contracts/*.json`, `scripts/validate_artifacts.py` |
| `launch_plan_test.sh` | `scripts/claw.py` (cmd_launch_plan) |
| `review_batch_test.sh` | `scripts/generate_review_batch.py` |
| `review_runtime_integration_test.sh` | `scripts/execute_job.py`, worker loop |
| `openclaw_test.sh` | `scripts/claw.py` (cmd_openclaw) |
| `metrics_snapshot_test.sh` | `_system/engine/runtime.py` or metrics module |
| `worker_reliability_test.sh` | `_system/engine/file_queue.py` (lease, retry, backoff) |
| `docs_tracking_test.sh` | `docs/`, git tracking |

### Common failure patterns

**"Expected file X to exist"** → A command didn't produce the expected artifact. Check the builder/executor for the file path or naming convention.

**"Expected status: done, got: failed"** → Execution error. Run the relevant test manually to see the agent's stderr.

**"JSON validation failed"** → An artifact doesn't match its contract in `_system/contracts/`. Check the schema field names and required properties.

**"Hook not delivered"** → Timing issue in test. Check if the hook path construction matches `state/hooks/pending/` layout.

**"Worker didn't process item"** → Queue item wasn't claimed. Check lease logic in `file_queue.py`.

## Step 4: Report findings

```
## Test results: <timestamp>

Passed: N / Total
Failed: M

### Failures

#### tests/worker_reliability_test.sh
Assertion: "lease renewal updates lease_expires_at"
File: _system/engine/file_queue.py:142
Likely cause: <1 sentence>
Fix: <specific change or investigation needed>

#### tests/hook_lifecycle_test.sh
...
```

## Step 5: After fixing

If you made a fix, run the specific failing test first to verify:
```bash
bash tests/<fixed_test>.sh
```

Then run the full suite before declaring success:
```bash
bash tests/run_all.sh
```

Do not commit until `run_all.sh` passes completely.

---

## Test files reference

```
tests/
├── run_all.sh                          ← master runner
├── foundation_scaffold_test.sh         ← directory structure
├── task_to_job_test.sh                 ← task→job pipeline
├── execute_job_test.sh                 ← direct execution
├── hook_lifecycle_test.sh              ← hook pending→sent→failed
├── queue_lifecycle_test.sh             ← queue state machine
├── queue_cli_test.sh                   ← CLI commands
├── contracts_validation_test.sh        ← JSON schema validation
├── launch_plan_test.sh                 ← dry-run preview
├── review_batch_test.sh                ← review generation
├── review_runtime_integration_test.sh  ← cadence + immediate triggers
├── openclaw_test.sh                    ← JSON bridge
├── metrics_snapshot_test.sh            ← metrics state
├── worker_reliability_test.sh          ← lease, retry, dead_letter
└── docs_tracking_test.sh               ← docs git tracking
```
