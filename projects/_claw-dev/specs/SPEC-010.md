# SPEC-010 — Epic/roadmap completion criteria

## Context

The claw project is at `/Users/Apple/progect/claw`.

Currently `claw orchestrate` stops when the queue is empty (`reason_code: queue_empty`).
For large projects with many tasks, this is too coarse — the operator wants to say
"run Epic 12 tasks" and have orchestrate stop exactly when all Epic 12 tasks are done,
even if there are other tasks in the queue.

Task files already have an `epic` frontmatter field (added in Epic 12 tasks).
This spec adds a `--scope epic:N` flag to orchestrate and a `claw epic-status` command.

## Goal

1. Add `cmd_epic_status` — shows completion % by epic tag
2. Add `--scope` flag to `claw orchestrate` (e.g. `--scope epic:12`)
3. Update `state/orchestration_state.json` with `scope_completion` field
4. Add tests

## Files to modify / create

### MODIFY: `scripts/claw.py`

Read the file before editing.

**1. Add `get_epic_tasks(project_root, epic_tag)` helper:**

```python
def get_epic_tasks(project_root: Path, epic_tag: str | None) -> list[dict]:
    """Return task records filtered by epic tag.

    If epic_tag is None or empty, returns all tasks.
    """
    records = collect_task_records(project_root)
    if not epic_tag:
        return records

    filtered = []
    for r in records:
        task_path = Path(r.get("task_path", ""))
        if not task_path.is_file():
            continue
        try:
            text = task_path.read_text(encoding="utf-8")
            fm_match = re.match(r'\A---\n(.*?)\n---\n?', text, re.DOTALL)
            if fm_match:
                import yaml as _yaml
                fm = _yaml.safe_load(fm_match.group(1))
                if isinstance(fm, dict):
                    task_epic = str(fm.get("epic", ""))
                    if task_epic == str(epic_tag):
                        filtered.append(r)
        except Exception:
            continue
    return filtered
```

**2. Add `cmd_epic_status`:**

```python
def cmd_epic_status(args: argparse.Namespace) -> int:
    """Show completion status for a specific epic or all epics."""
    try:
        project_root = resolve_project_root(args.project_root)
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    epic_tag = getattr(args, "epic", None)
    records = collect_task_records(project_root)

    # Group by epic
    epic_groups: dict[str, list[dict]] = {}
    for r in records:
        task_path = Path(r.get("task_path", ""))
        task_epic = "untagged"
        if task_path.is_file():
            try:
                text = task_path.read_text(encoding="utf-8")
                fm_match = re.match(r'\A---\n(.*?)\n---\n?', text, re.DOTALL)
                if fm_match:
                    import yaml as _yaml
                    fm = _yaml.safe_load(fm_match.group(1))
                    if isinstance(fm, dict) and fm.get("epic"):
                        task_epic = str(fm["epic"])
            except Exception:
                pass
        epic_groups.setdefault(task_epic, []).append(r)

    def _epic_summary(tasks: list[dict]) -> dict:
        total = len(tasks)
        done = sum(1 for t in tasks if t.get("status") in ("done", "accepted"))
        blocked = sum(1 for t in tasks if t.get("dependency_blockers"))
        pending = total - done - blocked
        pct = round(done / total * 100) if total > 0 else 0
        return {
            "total": total,
            "done": done,
            "blocked": blocked,
            "pending": pending,
            "completion_pct": pct,
            "complete": done == total,
        }

    if epic_tag:
        tasks = epic_groups.get(str(epic_tag), [])
        payload = {
            "project": project_root.name,
            "epic": epic_tag,
            **_epic_summary(tasks),
            "tasks": [{"task_id": t["task_id"], "status": t["status"]} for t in tasks],
        }
    else:
        epics_out = {}
        for tag, tasks in sorted(epic_groups.items()):
            epics_out[tag] = _epic_summary(tasks)
        payload = {
            "project": project_root.name,
            "epics": epics_out,
        }

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0
```

**3. Add `--scope` flag to orchestrate parser and implement scope-based stopping:**

In `build_parser()`, to the existing `orchestrate` subparser, add:
```python
orchestrate.add_argument("--scope", default=None,
                          help="Stop when scope is complete (e.g. 'epic:12')")
```

In `cmd_orchestrate`, after `max_steps = max(1, int(args.max_steps))`, add:
```python
    scope = getattr(args, "scope", None)
    epic_scope: str | None = None
    if scope and scope.startswith("epic:"):
        epic_scope = scope[len("epic:"):]
```

In the `while steps < max_steps:` loop, at the very top (before checking approvals),
add a scope-completion check:
```python
        # Check epic scope completion
        if epic_scope:
            scope_tasks = get_epic_tasks(project_root, epic_scope)
            scope_done = all(t.get("status") in ("done", "accepted") for t in scope_tasks)
            if scope_tasks and scope_done:
                last_status = "scope_complete"
                break
```

Add `"scope_complete"` to `_STATUS_REASON_CODE` (maps to `None`).

**4. Add `scope_completion` to orchestration_state payload:**

In the `payload` dict at end of `cmd_orchestrate`, add:
```python
"scope": scope,
"scope_completion": (
    {
        "epic": epic_scope,
        **_epic_completion_summary(project_root, epic_scope),
    }
    if epic_scope else None
),
```

Add helper:
```python
def _epic_completion_summary(project_root: Path, epic_tag: str) -> dict:
    tasks = get_epic_tasks(project_root, epic_tag)
    total = len(tasks)
    done = sum(1 for t in tasks if t.get("status") in ("done", "accepted"))
    return {"total": total, "done": done, "complete": done == total and total > 0}
```

**5. Register `epic-status` in `build_parser()`:**

```python
epic_status = subcommands.add_parser(
    "epic-status",
    help="Show completion status for tasks grouped by epic tag"
)
epic_status.add_argument("project_root", help="Project root path or slug")
epic_status.add_argument("--epic", default=None, help="Filter to specific epic tag (e.g. '12')")
epic_status.set_defaults(func=cmd_epic_status)
```

### CREATE: `tests/epic_status_test.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== epic-status test ==="

# Test 1: epic-status on _claw-dev — should show epic:12 tasks
OUT=$(python3 scripts/claw.py epic-status projects/_claw-dev)
echo "All epics: $OUT"
echo "$OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); assert 'epics' in d or 'epic' in d" \
    || { echo "FAIL: epic-status should return valid JSON with epics"; exit 1; }

# Test 2: filter by epic 12
OUT=$(python3 scripts/claw.py epic-status projects/_claw-dev --epic 12)
echo "Epic 12: $OUT"
TOTAL=$(echo "$OUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['total'])")
[ "$TOTAL" -gt 0 ] || { echo "FAIL: epic 12 should have tasks, got $TOTAL"; exit 1; }

# Test 3: orchestrate accepts --scope epic:12 flag
python3 scripts/claw.py orchestrate --help | grep -q "scope" \
    || { echo "FAIL: orchestrate should have --scope flag"; exit 1; }

# Test 4: orchestrate --scope epic:NONEXISTENT stops immediately (no tasks = complete)
OUT=$(python3 scripts/claw.py orchestrate projects/_claw-dev --max-steps 1 --scope epic:NONEXISTENT 2>/dev/null || true)
echo "Nonexistent scope: $OUT"

echo "PASS: epic-status test"
```

### Add to `tests/run_all.sh`

```bash
bash tests/epic_status_test.sh
```

## Acceptance Criteria

- `claw epic-status projects/_claw-dev --epic 12` returns JSON with `total`, `done`, `blocked`, `pending`, `completion_pct`
- `claw epic-status projects/_claw-dev` (no --epic) returns JSON with `epics` dict grouped by epic tag
- `claw orchestrate projects/_claw-dev --scope epic:12` stops with `status: scope_complete` once all epic:12 tasks are done/accepted
- `claw orchestrate` payload includes `scope_completion` field when `--scope` is given
- `bash tests/epic_status_test.sh` passes
- `bash tests/run_all.sh` still passes

## Constraints

- `epic` tag is read from task frontmatter YAML — it is already in TASK-004..010 as `epic: 12`
- Missing `epic` field → task goes into `untagged` group, not an error
- `--scope epic:N` with no matching tasks → treat as complete (empty scope = done)
- Do not modify `collect_task_records()` — use it as-is, then filter by epic tag separately
- `re` and `yaml` are already available in `scripts/claw.py` — no new imports needed
