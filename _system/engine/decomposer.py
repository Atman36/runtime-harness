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
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
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

    def has_cycle(node: str) -> bool:
        if color.get(node) == "black":
            return False
        if color.get(node) == "grey":
            return True
        color[node] = "grey"
        for dep in graph.get(node, []):
            if dep in graph and has_cycle(dep):
                return True
        color[node] = "black"
        return False

    for task_id in graph:
        if task_id not in color:
            if has_cycle(task_id):
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
    dry_run: bool = True,
) -> dict[str, Any]:
    """Decompose input_text into TASK + SPEC files for project_root.

    Returns summary dict with created tasks list.
    dry_run=True (default): returns validated plan without writing files.
    dry_run=False: writes TASK-NN.md + SPEC-NN.md + state/sprint_index.json.
    """
    import datetime

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
        "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
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
