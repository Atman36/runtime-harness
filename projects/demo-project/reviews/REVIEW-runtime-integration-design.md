# Architecture Design: Auto-Integrate Validation and Review Batch into Runtime

## 1. Summary

Currently, `validate_artifacts.py` and `generate_review_batch.py` are standalone CLI tools
never invoked by the worker or `execute_job.py`. This design embeds schema validation into
`execute_job.py` (where all artifact paths are already resolved) and wires review-batch
generation into `cmd_worker` in `claw.py` (the only component with access to project-wide
run history and cadence state). Together these changes require modifying exactly 2 files
and adding no new scripts.

---

## 2. Proposed Integration Points

### 2.1 Validation — inside `execute_job.py`, post-artifact write

**Where**: After `stdout_path`, `stderr_path`, and `report_path` are written (line ~329),
and before the final `write_json(result_path, final_result)` call.

**Rationale**:
- `execute_job.py` already holds resolved paths to `run_dir`, `result_path`, `meta_path`.
- Validation results can be embedded directly into `final_result["validation"]` and
  `meta["validation"]`, keeping the filesystem as the single source of truth.
- No additional IPC or subprocess required — `validate_run_dir()` is an importable function
  in `validate_artifacts.py` with a clean signature.
- Keeping validation inside `execute_job.py` means every run—whether launched manually or
  by the worker—is validated consistently.
- A validation failure does NOT change `result.status`; it adds a `"validation"` sub-key so
  downstream consumers can distinguish agent failure from artifact malformation.

**Rejected alternative — call from `cmd_worker`**:
The worker invokes `execute_job.py` as a subprocess and only sees its return code. Embedding
validation there would require parsing extra output from the subprocess or a second
subprocess call, introducing an IPC seam. It also means manually launched runs skip
validation entirely.

### 2.2 Review batch — inside `cmd_worker` in `claw.py`, post-job completion

**Where**: In `cmd_worker` (claw.py lines ~80–100), after `queue.ack(claimed)` or
`queue.fail(claimed)`, call a new helper `maybe_trigger_review(project_root, run_dir, policy)`.

**Rationale**:
- Cadence counting requires project-wide state that persists across multiple job executions.
  The worker loop is the only place where that state can be naturally accumulated without a
  database.
- `generate_batches()` in `generate_review_batch.py` is importable. The worker can import
  it directly rather than spawning a subprocess.
- Immediate triggers (failed, needs_review, risk_flags) are evaluated per-run; cadence
  triggers require an accumulator. Splitting the decision across two callsites
  (validate in execute_job, review in worker) keeps each callsite's responsibility narrow.

---

## 3. State Tracking Design

### 3.1 Validation state — embedded in `result.json`

Append a `"validation"` key to the existing `final_result` dict in `execute_job.py`:

```
result.json (after integration):
{
  "run_id":   "...",
  "status":   "success" | "failed",
  ...existing fields...,
  "validation": {
    "valid":   true | false,
    "errors":  { "job.json": [], "result.json": [...], "meta.json": [] }
  }
}
```

This is a pure append; no existing field is removed or renamed. The JSON schema for
`result.json` should be extended to allow the optional `"validation"` property.

### 3.2 Cadence state — `state/review_cadence.json` (one new file per project)

The spec prefers appending to existing files, but `result.json` and `meta.json` are
per-run, while cadence is a project-level counter. A single lightweight file is the
minimal new state:

```
{project_root}/state/review_cadence.json:
{
  "successful_since_last_batch": 3,
  "last_batch_generated_at": "2026-03-10T14:00:00Z"
}
```

**Rules:**
- Increment `successful_since_last_batch` after every `result.status == "success"` run.
- Reset to `0` whenever a cadence batch is emitted.
- Do NOT decrement on failure — failures emit immediate batches independently.
- File is created on first use with `{"successful_since_last_batch": 0}`.
- The worker reads, increments, and writes this file atomically (write-to-temp + rename),
  following the same pattern already used for queue claim files.

---

## 4. Risk Trigger Logic

Evaluation order (short-circuit):

```
1. If result.status == "failed"              → immediate batch, trigger = "failed"
2. If job.task.needs_review == true          → immediate batch, trigger = "needs_review"
3. If job.task.risk_flags ∩ IMMEDIATE_FLAGS  → immediate batch, trigger = "risk_flags:<flags>"
4. If result.status == "success"
      AND no immediate trigger fired:
   - increment cadence counter
   - if counter >= cadence_batch_size:
       emit cadence batch, reset counter
```

`IMMEDIATE_FLAGS = {"risky_area", "uncertainty", "large_diff"}` — already defined in
`generate_review_batch.py`. No new constants needed.

**Interaction with existing batch logic**: `generate_batches()` already de-duplicates by
scanning `reviews/REVIEW-*.json` for already-reviewed run IDs. The incremental cadence
counter in `state/review_cadence.json` tracks only the window since the last emitted batch,
avoiding double-counting.

**Hook delivery and review batches**: Review batch generation happens *after* hook
delivery in `execute_job.py`. The worker calls review batch generation only after
`execute_job.py` exits, so hook delivery failures in execute_job do not block review
decisions.

---

## 5. Proposed Module API

Function signatures only — no implementation.

### 5.1 `validate_artifacts.py` (no new public API needed)

```python
# Already exists — expose as importable:
def validate_run_dir(run_dir: Path) -> dict[str, list[str]]:
    """Return {artifact_name: [error_strings]} for each schema-mapped artifact."""

def validate_file(artifact_path: Path) -> list[str]:
    """Validate a single artifact file. Return list of error strings (empty = valid)."""
```

### 5.2 `generate_review_batch.py` (no new public API needed)

```python
# Already exists — expose as importable:
def generate_batches(
    project_root: Path,
    policy: dict,
    dry_run: bool = False,
) -> list[dict]:
    """Scan project runs, emit immediate and cadence batches per policy."""

def classify_run(run: dict) -> str | None:
    """Return trigger reason string or None. Pure function, no I/O."""

def load_policy(path: Path) -> dict:
    """Load and validate reviewer_policy.yaml."""
```

### 5.3 New helpers in `execute_job.py`

```python
def run_post_artifact_validation(run_dir: Path) -> dict:
    """
    Import and call validate_run_dir(run_dir).
    Return {"valid": bool, "errors": dict[str, list[str]]}.
    Must not raise — catch all exceptions and return {"valid": False, "errors": {"_exception": [str(exc)]}}.
    """

# Usage in main(), after report_path.write_text(...):
validation_result = run_post_artifact_validation(run_dir)
final_result["validation"] = validation_result
```

### 5.4 New helpers in `claw.py` (`cmd_worker`)

```python
def load_cadence_state(project_root: Path) -> dict:
    """
    Read {project_root}/state/review_cadence.json.
    Return {"successful_since_last_batch": int, "last_batch_generated_at": str | None}.
    Return default state if file absent or malformed.
    """

def save_cadence_state(project_root: Path, state: dict) -> None:
    """
    Write {project_root}/state/review_cadence.json atomically (tmp + rename).
    """

def maybe_trigger_review(
    project_root: Path,
    run_dir: Path,
    result_status: str,
    policy: dict,
) -> list[dict]:
    """
    Evaluate immediate triggers and cadence counter for one completed run.
    Call generate_batches() if a trigger fires.
    Update cadence state file.
    Return list of batch dicts emitted (may be empty).
    Must not raise — log errors to stderr, return [].
    """

# Usage in cmd_worker(), after queue.ack/fail:
policy = load_policy(POLICY_PATH)
maybe_trigger_review(project_root, run_dir, result_status="done"|"failed", policy=policy)
```

---

## 6. Tradeoffs

| Dimension                        | Inline everything in execute_job.py    | Split: validate in execute_job / review in worker (proposed) | Keep both as post-subprocess calls in worker |
|----------------------------------|----------------------------------------|--------------------------------------------------------------|----------------------------------------------|
| **Monolith risk**                | High — execute_job grows unbounded     | Low — each file has one new concern                          | Low                                          |
| **Validation consistency**       | Guaranteed for every run path          | Guaranteed (validation stays in execute_job)                 | Only guaranteed if worker is used            |
| **Cadence state access**         | Impossible — no loop state             | Natural in worker loop                                       | Natural in worker loop                       |
| **Result embedding**             | Easy                                   | Easy (validation in execute_job can write to result.json)    | Hard — requires parsing subprocess output    |
| **Hook delivery ordering**       | Must manage carefully                  | Clear: hook fires in execute_job, review fires after exit    | Clear                                        |
| **Failure isolation**            | Validation error could mask run result | Validation errors silently recorded, run result unchanged    | Subprocess failure is fully isolated         |
| **Import coupling**              | validate_artifacts imported by execute_job | validate_artifacts ← execute_job; generate_review_batch ← claw | None — but loses embedding capability   |
| **Files changed**                | 1 (execute_job.py only)                | 2 (execute_job.py + claw.py)                                 | 1 (claw.py only)                             |
| **New files added**              | 0                                      | 1 (state/review_cadence.json per project, not a script)      | 1 same                                       |
| **Manual-run coverage**          | Full                                   | Validation: full. Review batch: worker only                  | Worker only for both                         |

**Recommendation**: The proposed split (column 2) is optimal. Validation belongs as close to
artifact creation as possible. Review batch generation requires loop-level state only the
worker has.

---

## 7. Recommended Next Steps

- **Step 1**: Add `run_post_artifact_validation()` to `execute_job.py`; write result to
  `final_result["validation"]`. Extend `result.schema.json` with an optional `"validation"`
  property.

- **Step 2**: Add `load_cadence_state()` / `save_cadence_state()` / `maybe_trigger_review()`
  to `claw.py`; call `maybe_trigger_review()` at the end of each worker loop iteration.

- **Step 3**: Let `claw.py` lazily create `state/review_cadence.json` on first write with
  `{"successful_since_last_batch": 0}`. No scaffold change is required because
  `projects/_template` already includes `state/`.

- **Step 4**: Optionally add a `--skip-review` flag to `cmd_worker` so automated test runs can
  suppress review generation without disabling validation.

- **Step 5**: Write integration tests that simulate a 5-run cadence and a failed-run
  immediate trigger, asserting the correct `reviews/` artifacts are produced. Keep tests
  file-system only — no mocks.

---

## Assumptions

1. `validate_artifacts.py` and `generate_review_batch.py` are on `sys.path` or importable
   from the scripts directory at runtime (current repo layout supports this).
2. The cadence counter represents "successful runs since the last *emitted* batch", not
   since the last *reviewed* run — matching the existing `generate_batches()` de-dup logic.
3. A failed run resets nothing — the cadence counter is untouched when an immediate batch
   fires; failed runs are counted separately.
4. Validation does not block run completion — a validation failure is recorded in
   `result.json["validation"]` but does not change `result.status`.
5. `reviewer_policy.yaml` is loaded once per worker loop pass (or once per `cmd_worker`
   invocation) rather than per-run, as it does not change during a worker session.
