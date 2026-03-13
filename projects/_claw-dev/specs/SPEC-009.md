# SPEC-009 — claw decompose-epic via LLM

## Context

The claw project is at `/Users/Apple/progect/claw`.

Currently, creating tasks and specs for a large epic requires manually writing
each TASK-NN.md and SPEC-NN.md. The `claw-epic-sprint` Claude skill automates
this in chat, but there is no CLI command for it. This spec adds `claw decompose-epic`
which calls the Claude API to decompose an input roadmap/epic text into
properly-structured TASK + SPEC file pairs.

## Goal

Implement in two strict phases — the agent MUST complete phase 1 before writing any files:

**Phase 1 — Plan generation (dry-run):**
1. Create `_system/engine/decomposer.py` with LLM call + JSON extraction + validation
2. Add `cmd_decompose_epic --dry-run` that prints a validated task plan as JSON (no file writes)

**Phase 2 — Materialization (only after plan is validated):**
3. Add `cmd_decompose_epic` (without `--dry-run`) that writes TASK-NN.md + SPEC-NN.md
4. Write `state/sprint_index.json`
5. Add test

**Default behavior: `--dry-run` is the default.** Writing files requires explicit `--write` flag.

## Out of scope
- Live agent feedback loop on generated specs
- Approval gate before materialization (tracked as a follow-up)

## Files to create / modify

### CREATE: `_system/engine/decomposer.py`

```python
"""LLM-driven epic decomposition for claw."""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


DECOMPOSE_SYSTEM_PROMPT = """You are a software planning assistant for the claw orchestration system.
You decompose epics and roadmaps into concrete, actionable task + spec pairs.

Rules:
- Each task should represent 2-3 hours of focused work
- Tasks must have clear, testable acceptance criteria
- Dependencies must be valid (reference actual task IDs)
- No circular dependencies
- File scope must be explicit: list every file to be created or modified
- preferred_agent: use 'codex' for clear implementation, 'claude' for design/architecture
"""

DECOMPOSE_USER_TEMPLATE = """Decompose the following epic/roadmap into task + spec pairs for the claw project.

Project: {project_slug}
Existing tasks (do not recreate these): {existing_task_ids}

Input:
{input_text}

Output format — return a JSON array of task objects:
[
  {{
    "id": "TASK-NN",
    "title": "Short task title",
    "preferred_agent": "codex|claude",
    "priority": "high|medium|low",
    "dependencies": ["TASK-MM"],
    "tags": ["tag1", "tag2"],
    "goal": "One sentence goal",
    "scope": ["file/path1.py", "file/path2.sh"],
    "acceptance_criteria": ["criterion 1", "criterion 2"],
    "notes": "Implementation notes"
  }}
]

Return ONLY valid JSON. No markdown code blocks, no explanation."""


def _call_llm(prompt: str, system: str) -> str:
    """Call claude CLI to get decomposition. Returns raw output string."""
    result = subprocess.run(
        ["claude", "-p", prompt, "--system-prompt", system, "--no-markdown"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI failed: {result.stderr[:500]}")
    return result.stdout.strip()


def _extract_json(text: str) -> Any:
    """Extract JSON from LLM output (may have prose wrapper)."""
    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Try to find JSON array in the text
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError(f"No valid JSON array found in LLM output: {text[:200]}")


def _validate_tasks(tasks: list[dict], existing_ids: set[str]) -> list[str]:
    """Validate decomposed tasks. Returns list of error strings."""
    errors: list[str] = []
    seen_ids: set[str] = set()
    all_ids = existing_ids | {t.get("id", "") for t in tasks}

    for task in tasks:
        tid = task.get("id", "")
        if not tid or not re.match(r'^TASK-\d+$', tid):
            errors.append(f"Invalid task id: {tid!r}")
        if tid in seen_ids:
            errors.append(f"Duplicate task id: {tid}")
        seen_ids.add(tid)
        for dep in task.get("dependencies", []):
            if dep not in all_ids and dep not in existing_ids:
                errors.append(f"Task {tid} references unknown dependency {dep!r}")

    # Check for cycles (simple DFS)
    graph = {t["id"]: t.get("dependencies", []) for t in tasks if t.get("id")}
    color: dict[str, str] = {}
    def has_cycle(node: str, path: list[str]) -> bool:
        if color.get(node) == "black":
            return False
        if color.get(node) == "grey":
            return True
        color[node] = "grey"
        for dep in graph.get(node, []):
            if dep in graph and has_cycle(dep, path + [node]):
                return True
        color[node] = "black"
        return False

    for task_id in graph:
        if task_id not in color:
            if has_cycle(task_id, []):
                errors.append(f"Dependency cycle detected involving {task_id}")

    return errors


def _next_task_number(project_root: Path) -> int:
    """Find the next available task number."""
    tasks_dir = project_root / "tasks"
    existing = list(tasks_dir.glob("TASK-*.md")) if tasks_dir.is_dir() else []
    nums = []
    for f in existing:
        m = re.match(r'TASK-(\d+)\.md', f.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums, default=0) + 1


def _write_task_file(project_root: Path, task: dict, task_num: int) -> Path:
    """Write TASK-NN.md file."""
    tid = f"TASK-{task_num:03d}"
    task_path = project_root / "tasks" / f"{tid}.md"
    spec_ref = f"../specs/SPEC-{task_num:03d}.md"

    deps = task.get("dependencies", [])
    tags = task.get("tags", [])

    content = f"""---
id: {tid}
title: "{task.get('title', tid)}"
status: todo
spec: {spec_ref}
preferred_agent: {task.get('preferred_agent', 'codex')}
review_policy: standard
priority: {task.get('priority', 'medium')}
project: {project_root.name}
needs_review: false
risk_flags: []
tags: {json.dumps(tags)}
dependencies: {json.dumps(deps)}
epic: decomposed
---

# Task

## Goal
{task.get('goal', '')}

## Notes
{task.get('notes', '')}
"""
    task_path.write_text(content, encoding="utf-8")
    return task_path


def _write_spec_file(project_root: Path, task: dict, task_num: int) -> Path:
    """Write SPEC-NN.md file."""
    tid = f"TASK-{task_num:03d}"
    spec_id = f"SPEC-{task_num:03d}"
    spec_path = project_root / "specs" / f"{spec_id}.md"

    criteria = "\n".join(f"- {c}" for c in task.get("acceptance_criteria", []))
    scope = "\n".join(f"- `{f}`" for f in task.get("scope", []))

    content = f"""# {spec_id} — {task.get('title', tid)}

## Context

The claw project is at `/Users/Apple/progect/claw`.
This spec was generated by `claw decompose-epic`.

## Goal

{task.get('goal', '')}

## Files to create / modify

{scope if scope else "_(see implementation notes)_"}

## Acceptance Criteria

{criteria if criteria else "_(see task goal)_"}

## Notes

{task.get('notes', '')}

## Constraints

- Scope limited to the files listed above
- All existing tests must still pass after this change
"""
    spec_path.write_text(content, encoding="utf-8")
    return spec_path


def decompose_epic(
    project_root: Path,
    input_text: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Decompose input_text into TASK + SPEC files for project_root.

    Returns summary dict with created tasks list.
    """
    project_slug = project_root.name

    # Find existing task IDs
    tasks_dir = project_root / "tasks"
    existing_ids: set[str] = set()
    if tasks_dir.is_dir():
        for f in tasks_dir.glob("TASK-*.md"):
            m = re.match(r'(TASK-\d+)\.md', f.name)
            if m:
                existing_ids.add(m.group(1))

    prompt = DECOMPOSE_USER_TEMPLATE.format(
        project_slug=project_slug,
        existing_task_ids=sorted(existing_ids) or "none",
        input_text=input_text,
    )

    raw_output = _call_llm(prompt, DECOMPOSE_SYSTEM_PROMPT)
    tasks = _extract_json(raw_output)

    if not isinstance(tasks, list):
        raise ValueError(f"LLM returned non-list: {type(tasks)}")

    validation_errors = _validate_tasks(tasks, existing_ids)
    if validation_errors:
        return {
            "status": "validation_failed",
            "errors": validation_errors,
            "raw_tasks": tasks,
        }

    if dry_run:
        return {
            "status": "dry_run",
            "task_count": len(tasks),
            "tasks": tasks,
        }

    # Write files
    start_num = _next_task_number(project_root)
    created: list[dict[str, str]] = []
    for i, task in enumerate(tasks):
        task_num = start_num + i
        task_path = _write_task_file(project_root, task, task_num)
        spec_path = _write_spec_file(project_root, task, task_num)
        created.append({
            "task_id": f"TASK-{task_num:03d}",
            "task_file": str(task_path.relative_to(project_root)),
            "spec_file": str(spec_path.relative_to(project_root)),
        })

    # Write sprint_index.json
    sprint_index = {
        "generated_at": __import__('datetime').datetime.utcnow().isoformat() + "Z",
        "project": project_slug,
        "task_count": len(created),
        "tasks": created,
    }
    state_dir = project_root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "sprint_index.json").write_text(
        json.dumps(sprint_index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return {
        "status": "created",
        "task_count": len(created),
        "tasks": created,
        "sprint_index": str(project_root / "state" / "sprint_index.json"),
    }
```

### MODIFY: `scripts/claw.py`

**1. Add import:**

```python
from _system.engine.decomposer import decompose_epic as _decompose_epic  # noqa: E402
```

**2. Add `cmd_decompose_epic`:**

```python
def cmd_decompose_epic(args: argparse.Namespace) -> int:
    """Decompose an epic/roadmap file into TASK + SPEC pairs via LLM."""
    try:
        project_root = resolve_project_root(args.project)
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    input_path = Path(args.input)
    if not input_path.is_file():
        print(json.dumps({"error": f"Input file not found: {input_path}"}), file=sys.stderr)
        return 1

    input_text = input_path.read_text(encoding="utf-8")
    dry_run = getattr(args, "dry_run", False)

    try:
        result = _decompose_epic(project_root, input_text, dry_run=dry_run)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in ("created", "dry_run") else 1
```

**3. Register in `build_parser()`:**

```python
decompose_epic = subcommands.add_parser(
    "decompose-epic",
    help="Decompose an epic/roadmap into TASK + SPEC file pairs via LLM"
)
decompose_epic.add_argument("--project", required=True, help="Project slug or path")
decompose_epic.add_argument("--input", required=True, help="Path to roadmap/epic markdown file")
decompose_epic.add_argument("--dry-run", action="store_true", default=True,
                             help="Preview tasks without writing files (default: True)")
decompose_epic.add_argument("--write", action="store_true", default=False,
                             help="Actually write TASK + SPEC files (requires --write)")
decompose_epic.set_defaults(func=cmd_decompose_epic)
```

Update `cmd_decompose_epic` to check `args.write`:
```python
    # Default is dry-run; only write files if --write is explicitly passed
    dry_run = not getattr(args, "write", False)
```

### CREATE: `tests/decompose_epic_test.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== decompose-epic test (dry-run only — no LLM call) ==="

# We can't easily test the LLM call in CI, so we test the CLI structure
# and that --dry-run flag is accepted

# Test 1: missing input file → error
OUT=$(python3 scripts/claw.py decompose-epic --project projects/demo-project --input /nonexistent/roadmap.md 2>&1 || true)
echo "Missing input result: $OUT"
echo "$OUT" | grep -q "error\|not found" || { echo "FAIL: should error on missing file"; exit 1; }

# Test 2: dry-run flag accepted (will fail if claude CLI not available, which is ok)
TMPFILE="$(mktemp /tmp/epic-test-XXXX.md)"
echo "# Test epic\n\n## Task 1\nDo something simple" > "$TMPFILE"
cleanup() { rm -f "$TMPFILE"; }
trap cleanup EXIT

# Just check the command exists and accepts flags
python3 scripts/claw.py decompose-epic --help > /dev/null
echo "Command exists: OK"

echo "PASS: decompose-epic test"
```

### Add to `tests/run_all.sh`

```bash
bash tests/decompose_epic_test.sh
```

## Acceptance Criteria

- `python3 scripts/claw.py decompose-epic --help` works
- `python3 scripts/claw.py decompose-epic --project projects/demo-project --input missing.md` exits non-zero with JSON error
- `_system/engine/decomposer.py` is importable
- `_validate_tasks()` rejects: duplicate IDs, unknown dependency refs, cycles
- When LLM produces valid tasks, TASK-NN.md + SPEC-NN.md files are created in the project
- `state/sprint_index.json` is written listing created files
- `bash tests/decompose_epic_test.sh` passes
- `bash tests/run_all.sh` still passes

## Constraints

- LLM call uses `claude -p` CLI (not Anthropic SDK) to remain dependency-free
- If `claude` CLI is unavailable, the function raises `RuntimeError` cleanly (not a crash)
- `decomposer.py` must be pure: no reading from `scripts/claw.py` internals
- `--dry-run` MUST NOT write any files
- Generated task IDs auto-increment from the highest existing TASK-NN number
