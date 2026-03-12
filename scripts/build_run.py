#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _system.engine.task_planner import TaskRunPlan, plan_task_run


FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


@dataclass(frozen=True)
class RunContext:
    repo_root: Path
    script_dir: Path
    task_path: Path
    task_dir: Path
    project_root: Path
    project_slug: str
    spec_path: Path
    prompt_template: Path
    report_template: Path


class RunBuildError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="build_run.py")
    parser.add_argument("--execute", action="store_true", help="Execute the run immediately after creation")
    parser.add_argument("task_path")
    return parser.parse_args()


def ensure_file(path: Path, message: str) -> None:
    if not path.is_file():
        raise RunBuildError(message)


def read_front_matter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    match = FRONT_MATTER_RE.match(text)
    if match is None:
        return {}
    loaded = yaml.safe_load(match.group(1)) or {}
    if not isinstance(loaded, dict):
        return {}
    return dict(loaded)


def read_project_state(path: Path) -> dict[str, Any]:
    ensure_file(path, f"Project state file not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return {}
    return dict(loaded)


def find_project_root(task_path: Path) -> Path:
    for ancestor in [task_path.parent, *task_path.parents]:
        if (ancestor / "state" / "project.yaml").is_file():
            return ancestor
    raise RunBuildError(f"Project state file not found: {task_path.parent / 'state' / 'project.yaml'}")


def resolve_path(base_dir: Path, target_path: str) -> Path:
    candidate = Path(target_path).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir / candidate).resolve()
    return candidate


def render_template(path: Path, replacements: dict[str, str]) -> str:
    text = path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(f"{{{{{key}}}}}", value)
    return text


def validate_task_front_matter(task_path: Path, project_slug: str, front_matter: dict[str, Any]) -> tuple[str, str, list[str]]:
    task_id = str(front_matter.get("id") or "").strip()
    spec_reference = str(front_matter.get("spec") or "").strip()
    if not task_id or not spec_reference:
        raise RunBuildError(f"Task front matter must include id and spec: {task_path}")

    project_from_task = str(front_matter.get("project") or "").strip()
    if project_from_task and project_from_task != project_slug:
        raise RunBuildError(f"Task project '{project_from_task}' does not match project directory '{project_slug}'")

    needs_review = front_matter.get("needs_review", False)
    if not isinstance(needs_review, bool):
        raise RunBuildError(f"Task front matter needs_review must be true or false: {task_path}")

    risk_flags = front_matter.get("risk_flags", [])
    if not isinstance(risk_flags, list):
        raise RunBuildError(f"Task front matter risk_flags must be a JSON array: {task_path}")

    normalized_flags = [str(item) for item in risk_flags]
    return task_id, spec_reference, normalized_flags


def next_run_directory(project_root: Path, created_date: str) -> tuple[str, Path]:
    run_day_root = project_root / "runs" / created_date
    run_day_root.mkdir(parents=True, exist_ok=True)

    max_run_number = 0
    for candidate in run_day_root.iterdir():
        if not candidate.is_dir() or not candidate.name.startswith("RUN-"):
            continue
        suffix = candidate.name.removeprefix("RUN-")
        if suffix.isdigit():
            max_run_number = max(max_run_number, int(suffix))

    next_run_number = max_run_number + 1
    while True:
        run_id = f"RUN-{next_run_number:04d}"
        run_dir = run_day_root / run_id
        try:
            run_dir.mkdir()
            return run_id, run_dir
        except FileExistsError:
            next_run_number += 1


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def routing_payload(plan: TaskRunPlan) -> dict[str, Any]:
    return {
        "selected_agent": plan.routing.selected_agent,
        "selection_source": plan.routing.selection_source,
        "routing_rule": plan.routing.routing_rule,
    }


def execution_payload(plan: TaskRunPlan) -> dict[str, Any]:
    return {
        "workspace_mode": plan.execution.workspace_mode,
        "workspace_root": plan.execution.workspace_root,
        "workspace_materialization_required": plan.execution.workspace_materialization_required,
        "edit_scope": plan.execution.edit_scope,
        "parallel_safe": plan.execution.parallel_safe,
        "concurrency_group": plan.execution.concurrency_group,
    }


def create_run(context: RunContext) -> Path:
    front_matter = read_front_matter(context.task_path)
    task_id, spec_reference, risk_flags = validate_task_front_matter(context.task_path, context.project_slug, front_matter)

    project_state_path = context.project_root / "state" / "project.yaml"
    project_state = read_project_state(project_state_path)
    project_slug_from_state = str(project_state.get("slug") or "").strip()
    if not project_slug_from_state:
        raise RunBuildError(f"Project state file must include slug: {project_state_path}")
    if project_slug_from_state != context.project_slug:
        raise RunBuildError(
            f"Project slug '{project_slug_from_state}' in state/project.yaml does not match directory '{context.project_slug}'"
        )

    plan = plan_task_run(context.repo_root, context.task_path)
    spec_path = plan.spec_path
    ensure_file(spec_path, f"Spec file not found: {spec_path}")

    run_date = datetime.now().strftime("%Y-%m-%d")
    created_at = utc_now()
    run_id, run_dir = next_run_directory(context.project_root, run_date)

    shutil.copy2(context.task_path, run_dir / "task.md")
    shutil.copy2(spec_path, run_dir / "spec.md")

    replacements = {
        "PROJECT_SLUG": context.project_slug,
        "TASK_ID": task_id,
        "SPEC_PATH": spec_reference,
        "CREATED_AT": created_at,
    }
    (run_dir / "prompt.txt").write_text(render_template(context.prompt_template, replacements), encoding="utf-8")
    (run_dir / "report.md").write_text(render_template(context.report_template, replacements), encoding="utf-8")
    (run_dir / "stdout.log").write_text("", encoding="utf-8")
    (run_dir / "stderr.log").write_text("", encoding="utf-8")

    task_source_rel = context.task_path.relative_to(context.project_root).as_posix()
    spec_source_rel = spec_path.relative_to(context.project_root).as_posix()
    routing = routing_payload(plan)
    execution = execution_payload(plan)
    preferred_agent = plan.routing.selected_agent
    review_policy = plan.review_policy
    priority = plan.priority
    task_status = str(front_matter.get("status") or "").strip()
    task_title = plan.task_title
    concurrency_key = front_matter.get("concurrency_key")
    if concurrency_key is not None:
        concurrency_key = str(concurrency_key).strip() or None

    meta = {
        "meta_version": 1,
        "run_id": run_id,
        "run_date": run_date,
        "created_at": created_at,
        "status": "created",
        "project": context.project_slug,
        "task_id": task_id,
        "task_title": task_title,
        "task_path": task_source_rel,
        "spec_path": spec_source_rel,
        "preferred_agent": preferred_agent,
        "review_policy": review_policy,
        "priority": priority,
        "routing": routing,
        "execution": execution,
    }
    if concurrency_key:
        meta["concurrency_key"] = concurrency_key

    job = {
        "job_version": 1,
        "run_id": run_id,
        "run_path": f"runs/{run_date}/{run_id}",
        "created_at": created_at,
        "project": context.project_slug,
        "preferred_agent": preferred_agent,
        "review_policy": review_policy,
        "routing": routing,
        "execution": execution,
        "task": {
            "id": task_id,
            "title": task_title,
            "status": task_status,
            "priority": priority,
            "source_path": task_source_rel,
            "copied_path": "task.md",
            "needs_review": bool(front_matter.get("needs_review", False)),
            "risk_flags": risk_flags,
        },
        "spec": {
            "source_path": spec_source_rel,
            "copied_path": "spec.md",
        },
        "artifacts": {
            "prompt_path": "prompt.txt",
            "meta_path": "meta.json",
            "report_path": "report.md",
            "result_path": "result.json",
            "stdout_path": "stdout.log",
            "stderr_path": "stderr.log",
        },
    }
    if concurrency_key:
        job["task"]["concurrency_key"] = concurrency_key

    result = {
        "result_version": 1,
        "run_id": run_id,
        "status": "pending",
        "created_at": created_at,
        "agent": preferred_agent,
    }

    write_json(run_dir / "meta.json", meta)
    write_json(run_dir / "job.json", job)
    write_json(run_dir / "result.json", result)
    return run_dir


def build_context(task_path_arg: str) -> RunContext:
    task_path = Path(task_path_arg).expanduser().resolve()
    ensure_file(task_path, f"Task file not found: {task_path_arg}")

    script_dir = Path(__file__).resolve().parent
    repo_root = script_dir.parent
    task_dir = task_path.parent
    project_root = find_project_root(task_path)
    project_slug = project_root.name

    prompt_template = repo_root / "_system" / "templates" / "prompt.template.md"
    report_template = repo_root / "_system" / "templates" / "report.template.md"
    ensure_file(prompt_template, f"Prompt template not found: {prompt_template}")
    ensure_file(report_template, f"Report template not found: {report_template}")

    spec_reference = str((read_front_matter(task_path) or {}).get("spec") or "").strip()
    spec_path = resolve_path(task_dir, spec_reference) if spec_reference else task_dir / "MISSING-SPEC"

    return RunContext(
        repo_root=repo_root,
        script_dir=script_dir,
        task_path=task_path,
        task_dir=task_dir,
        project_root=project_root,
        project_slug=project_slug,
        spec_path=spec_path,
        prompt_template=prompt_template,
        report_template=report_template,
    )


def execute_run(script_dir: Path, run_dir: Path) -> int:
    completed = subprocess.run([sys.executable, str(script_dir / "execute_job.py"), str(run_dir)], check=False)
    return int(completed.returncode)


def main() -> int:
    args = parse_args()
    try:
        context = build_context(args.task_path)
        run_dir = create_run(context)
    except (FileNotFoundError, RunBuildError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Created task run: {run_dir}")
    if not args.execute:
        return 0
    return execute_run(context.script_dir, run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
