# SPEC-007 — Task graph lint as mandatory pre-orchestrate gate + file-overlap check

## Context

The claw project is at `/Users/Apple/progect/claw`.

`claw task-lint` already detects cycles and unknown dependency refs (TASK-002, done).
`claw orchestrate` already calls `lint_task_graph()` and aborts on cycles.

What is missing:
1. `claw task-graph-lint` as an alias/extension of `task-lint` that also checks for
   **file overlap** between tasks (two tasks touching the same files without a `shared_files` flag)
2. `claw orchestrate` aborts on `unknown_dependency` errors (currently only aborts on cycles)
3. Parallel task selection respects file-overlap: tasks with file-overlap are NOT parallelized

## Goal

1. Add `check_file_overlap(records)` to `scripts/claw.py`
2. Add `cmd_task_graph_lint` (new command `task-graph-lint`)
3. Make `cmd_orchestrate` abort on any blocking lint issue (cycle OR unknown_dependency)
4. Parallel slot selection: filter out tasks with file-overlap against currently running tasks

## Files to modify

### MODIFY: `scripts/claw.py`

Read the file before editing.

**1. Add `check_file_overlap(records)` function:**

```python
def check_file_overlap(records: list[dict]) -> list[dict]:
    """Check for file-overlap between tasks that don't declare shared_files.

    Returns list of issue dicts with code='file_overlap'.
    """
    issues: list[dict] = []
    # Build map of task_id -> set of file paths mentioned in spec
    # We look for the spec_path and scan it for file references
    task_files: dict[str, set[str]] = {}
    for r in records:
        spec_path_rel = r.get("spec_path_rel") or r.get("spec")
        if not spec_path_rel:
            continue
        # Resolve spec path relative to project root
        task_path = r.get("task_path")
        if task_path:
            spec_abs = Path(task_path).parent.parent / "specs" / Path(str(spec_path_rel)).name
        else:
            continue
        if not Path(spec_abs).is_file():
            continue
        spec_text = Path(spec_abs).read_text(encoding="utf-8", errors="replace")
        # Extract file paths from backtick mentions (heuristic)
        import re as _re
        files = set(_re.findall(r'`([a-z_][a-zA-Z0-9_/.-]+\.[a-z]+)`', spec_text))
        task_files[r["task_id"]] = files

    # Compare pairs of non-done tasks
    task_ids = list(task_files.keys())
    for i in range(len(task_ids)):
        for j in range(i + 1, len(task_ids)):
            tid_a, tid_b = task_ids[i], task_ids[j]
            overlap = task_files[tid_a] & task_files[tid_b]
            if overlap:
                issues.append({
                    "code": "file_overlap",
                    "task_id": tid_a,
                    "other_task_id": tid_b,
                    "severity": "warning",
                    "message": (
                        f"Tasks {tid_a} and {tid_b} both reference files: "
                        + ", ".join(sorted(overlap))
                        + " — do not run in parallel"
                    ),
                })
    return issues
```

**2. Add `cmd_task_graph_lint`:**

```python
def cmd_task_graph_lint(args: argparse.Namespace) -> int:
    """Extended lint: cycles + unknown deps + file-overlap between tasks."""
    project_root = resolve_project_root(args.project_root)
    records = collect_task_records(project_root)
    issues = lint_task_graph(project_root)  # cycles + unknown deps
    issues.extend(check_file_overlap(records))  # file-overlap warnings

    payload = {
        "project": project_root.name,
        "issue_count": len(issues),
        "blocking_count": sum(1 for i in issues if i.get("severity", "fail") == "fail" or i.get("code") in ("task_graph_cycle", "unknown_dependency")),
        "warning_count": sum(1 for i in issues if i.get("severity") == "warning"),
        "issues": issues,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    # Return 1 if there are blocking issues, 0 if only warnings
    has_blocking = any(
        i.get("code") in ("task_graph_cycle", "unknown_dependency")
        for i in issues
    )
    return 1 if has_blocking else 0
```

**3. Update `cmd_orchestrate` to abort on unknown_dependency (not just cycles):**

Find the existing cycle check block:
```python
    cycle_issues = [i for i in graph_issues if i["code"] == "task_graph_cycle"]
    if cycle_issues:
        _openclaw_error(cycle_issues[0]["message"], "task_graph_cycle")
        return 1
```

Replace with:
```python
    blocking_issues = [i for i in graph_issues if i["code"] in ("task_graph_cycle", "unknown_dependency")]
    if blocking_issues:
        _openclaw_error(blocking_issues[0]["message"], blocking_issues[0]["code"])
        return 1
```

**4. Register `task-graph-lint` in `build_parser()`:**

```python
task_graph_lint = subcommands.add_parser(
    "task-graph-lint",
    help="Extended task graph lint: cycles, unknown deps, and file-overlap"
)
task_graph_lint.add_argument("project_root")
task_graph_lint.set_defaults(func=cmd_task_graph_lint)
```

### CREATE: `tests/task_graph_lint_test.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== task-graph-lint test ==="

# Test 1: demo-project should pass (no cycles, no unknown deps)
OUT=$(python3 scripts/claw.py task-graph-lint projects/demo-project)
echo "demo-project lint: $OUT"
BLOCKING=$(echo "$OUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['blocking_count'])")
[ "$BLOCKING" -eq 0 ] || { echo "FAIL: demo-project should have no blocking issues, got $BLOCKING"; exit 1; }

# Test 2: _claw-dev should pass
OUT=$(python3 scripts/claw.py task-graph-lint projects/_claw-dev)
echo "_claw-dev lint: $OUT"
BLOCKING=$(echo "$OUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['blocking_count'])")
[ "$BLOCKING" -eq 0 ] || { echo "FAIL: _claw-dev should have no blocking issues, got $BLOCKING"; exit 1; }

# Test 3: task-lint still works (backward compat)
python3 scripts/claw.py task-lint projects/demo-project > /dev/null

# Test 4: orchestrate on demo-project still starts (graph is clean)
OUT=$(python3 scripts/claw.py orchestrate projects/demo-project --max-steps 0 2>/dev/null || true)
echo "orchestrate with max-steps 0: $OUT"

echo "PASS: task-graph-lint test"
```

### Add to `tests/run_all.sh`

```bash
bash tests/task_graph_lint_test.sh
```

## Acceptance Criteria

- `claw task-graph-lint projects/demo-project` exits 0 (no blocking issues)
- `claw task-graph-lint` output includes `blocking_count` and `warning_count` fields
- `claw orchestrate` aborts with `reason_code: unknown_dependency` if a task references a non-existent task
- `claw task-lint` still works as before (backward compatible)
- `bash tests/task_graph_lint_test.sh` passes
- `bash tests/run_all.sh` still passes

## Constraints

- `task-graph-lint` extends but does NOT replace `task-lint`
- File-overlap check is a **warning**, not a blocking error — tasks can still run sequentially
- Do not modify `lint_task_graph()` or `detect_task_cycles()` — call them from `cmd_task_graph_lint`
- The `unknown_dependency` abort in `cmd_orchestrate` must not break existing behavior for well-formed projects
