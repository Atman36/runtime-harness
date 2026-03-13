# SPEC-002 — Task graph snapshot and lint

## Context

The claw project is at `/Users/Apple/progect/claw`.

`scripts/claw.py` already has `collect_task_records(project_root)` (line 534)
which returns a list of task dicts including `task_id`, `status`, `dependencies`,
`ready`, etc. There is also `build_metrics_snapshot()` / `refresh_metrics_snapshot()`
that writes `state/metrics_snapshot.json`. This spec follows the same pattern
for a **task graph snapshot**: a structural snapshot of task nodes and their
dependency graph, separate from the operational metrics.

The snapshot enables:
- Faster dashboard/status queries
- Pre-orchestration graph integrity checks
- Early detection of malformed or cyclic dependencies

## Goal

1. Add `build_task_snapshot(project_root)` to `scripts/claw.py` — returns a dict
   with all task records plus a checksum.
2. Add `refresh_task_snapshot(project_root)` — calls build + writes
   `state/tasks_snapshot.json` atomically.
3. Add `detect_task_cycles(records)` — returns list of cycle paths (list of lists).
4. Add `lint_task_graph(project_root)` — runs full lint: unknown deps, cycles,
   parse errors. Returns list of issue dicts.
5. Add CLI commands `task-snapshot` and `task-lint` to `scripts/claw.py`.
6. Call `refresh_task_snapshot()` at the start of `cmd_orchestrate()`.

## Implementation

### In `scripts/claw.py`

Read the existing code first with the Read tool, then add these functions.

**`build_task_snapshot(project_root: Path) -> dict`**

```python
import hashlib

def build_task_snapshot(project_root: Path) -> dict:
    records = collect_task_records(project_root)
    # Build serializable task list (task_path is a Path object, convert to str)
    tasks = []
    for r in records:
        tasks.append({
            "task_id": r["task_id"],
            "title": r["title"],
            "status": r["status"],
            "priority": r["priority"],
            "dependencies": r["dependencies"],
            "dependency_blockers": r["dependency_blockers"],
            "preferred_agent": r["preferred_agent"],
            "needs_review": r["needs_review"],
            "ready": r["ready"],
            "active": r["active"],
            "task_path": r["task_path_rel"],
        })
    # Canonical JSON for checksum (sorted keys, no whitespace)
    canonical = json.dumps(tasks, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    checksum = hashlib.sha256(canonical.encode()).hexdigest()
    return {
        "snapshot_version": 1,
        "project": project_root.name,
        "updated_at": utc_now(),
        "task_count": len(tasks),
        "tasks": tasks,
        "checksum": checksum,
    }
```

**`refresh_task_snapshot(project_root: Path) -> dict`**

```python
def refresh_task_snapshot(project_root: Path) -> dict:
    snapshot = build_task_snapshot(project_root)
    write_json_atomic(project_root / "state" / "tasks_snapshot.json", snapshot)
    return snapshot
```

**`detect_task_cycles(records: list[dict]) -> list[list[str]]`**

Use iterative DFS with grey/black coloring:

```python
def detect_task_cycles(records: list[dict]) -> list[list[str]]:
    """Return list of cycle paths. Each cycle is a list of task_ids forming the loop."""
    graph: dict[str, list[str]] = {r["task_id"]: r["dependencies"] for r in records}
    all_ids = set(graph)
    color: dict[str, str] = {}  # white (absent) / grey / black
    cycles: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        if color.get(node) == "black":
            return
        if color.get(node) == "grey":
            # Found a cycle — extract the loop
            loop_start = path.index(node)
            cycles.append(path[loop_start:] + [node])
            return
        color[node] = "grey"
        for dep in graph.get(node, []):
            if dep in all_ids:
                dfs(dep, path + [node])
        color[node] = "black"

    for task_id in all_ids:
        if task_id not in color:
            dfs(task_id, [])

    return cycles
```

**`lint_task_graph(project_root: Path) -> list[dict]`**

```python
def lint_task_graph(project_root: Path) -> list[dict]:
    """Lint the task graph. Return list of issue dicts with keys: code, task_id, message."""
    records = collect_task_records(project_root)
    all_ids = {r["task_id"] for r in records}
    issues: list[dict] = []

    # Check for unknown dependency references
    for r in records:
        for dep in r["dependencies"]:
            if dep not in all_ids:
                issues.append({
                    "code": "unknown_dependency",
                    "task_id": r["task_id"],
                    "message": f"Task {r['task_id']} depends on unknown task {dep!r}",
                })

    # Check for cycles
    cycles = detect_task_cycles(records)
    for cycle in cycles:
        cycle_str = " -> ".join(cycle)
        issues.append({
            "code": "task_graph_cycle",
            "task_id": cycle[0],
            "message": f"Dependency cycle detected: {cycle_str}",
        })

    return issues
```

**`cmd_task_snapshot(args: argparse.Namespace) -> int`**

```python
def cmd_task_snapshot(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    snapshot = refresh_task_snapshot(project_root)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    return 0
```

**`cmd_task_lint(args: argparse.Namespace) -> int`**

```python
def cmd_task_lint(args: argparse.Namespace) -> int:
    project_root = resolve_project_root(args.project_root)
    issues = lint_task_graph(project_root)
    payload = {
        "project": project_root.name,
        "issue_count": len(issues),
        "issues": issues,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if issues else 0
```

### Register CLI commands in `build_parser()` (around line 2063)

Add before the final `return parser` statement:

```python
task_snapshot = subcommands.add_parser("task-snapshot", help="Generate and write task graph snapshot")
task_snapshot.add_argument("project_root")
task_snapshot.set_defaults(func=cmd_task_snapshot)

task_lint = subcommands.add_parser("task-lint", help="Lint the task dependency graph")
task_lint.add_argument("project_root")
task_lint.set_defaults(func=cmd_task_lint)
```

### Hook `refresh_task_snapshot` into `cmd_orchestrate`

At the very start of `cmd_orchestrate()` (after `budget_state = load_orchestration_state(...)`),
add:

```python
# Refresh task snapshot and abort if graph has lint errors
refresh_task_snapshot(project_root)
graph_issues = lint_task_graph(project_root)
if graph_issues:
    cycle_issues = [i for i in graph_issues if i["code"] == "task_graph_cycle"]
    if cycle_issues:
        _openclaw_error(graph_issues[0]["message"], "task_graph_cycle")
        return 1
```

Also add `"task_graph_issues": graph_issues` to the `payload` dict in `cmd_orchestrate`
(after the existing `ready_tasks` key).

### Add `hashlib` import

The `hashlib` module must be imported at the top of `scripts/claw.py`.
Check if it's already imported (search for `import hashlib`). If not, add it
to the stdlib imports block near the top of the file.

## Acceptance Criteria

- `python3 scripts/claw.py task-snapshot projects/claw-dev` writes
  `projects/claw-dev/state/tasks_snapshot.json` and prints the snapshot JSON
- `python3 scripts/claw.py task-lint projects/claw-dev` prints a JSON payload
  with `issue_count` and `issues` list
- Cycle detection works: if TASK-A depends on TASK-B and TASK-B depends on TASK-A,
  `task-lint` reports a cycle
- `claw orchestrate` refreshes the snapshot on each run
- All existing tests still pass: `bash /Users/Apple/progect/claw/tests/run_all.sh`

## Constraints

- `tasks_snapshot.json` is a derived artifact — document this in the file itself
  via the `snapshot_version` field and `updated_at` timestamp
- Do not modify `collect_task_records()` — call it as-is
- The snapshot must NOT be written to `runs/` — it belongs in `state/`
- `detect_task_cycles` must handle disconnected graphs (nodes with no edges)
