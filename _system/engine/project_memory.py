from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from _system.engine.orchestration_state import (
    default_orchestration_state,
    read_orchestration_state,
    write_orchestration_state,
)
from _system.engine.task_lifecycle import parse_iso_timestamp, utc_now_timestamp

KNOWLEDGE_DIR = "knowledge"
KNOWLEDGE_RUNS_DIR = "runs"
KNOWLEDGE_INDEX_FILE = "MEMORY.md"
PROJECT_MEMORY_FILE = "project_memory.md"
DREAM_LOCK_FILE = ".dream.lock"
DREAM_LOG_FILE = "dream_log.md"
AUTO_DREAM_MIN_HOURS = 24
AUTO_DREAM_MIN_RUNS = 5
AUTO_DREAM_SCAN_THROTTLE_SECONDS = 600
FRONT_MATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
    try:
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def knowledge_root_for_project(project_root: Path) -> Path:
    return project_root / "state" / KNOWLEDGE_DIR


def knowledge_runs_root(project_root: Path) -> Path:
    return knowledge_root_for_project(project_root) / KNOWLEDGE_RUNS_DIR


def knowledge_index_path(project_root: Path) -> Path:
    return knowledge_root_for_project(project_root) / KNOWLEDGE_INDEX_FILE


def project_memory_path(project_root: Path) -> Path:
    return knowledge_root_for_project(project_root) / PROJECT_MEMORY_FILE


def dream_lock_path(project_root: Path) -> Path:
    return project_root / "state" / DREAM_LOCK_FILE


def dream_log_path(project_root: Path) -> Path:
    return project_root / "state" / DREAM_LOG_FILE


def _trim_text(value: str, *, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _read_front_matter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    match = FRONT_MATTER_RE.match(text)
    if match is None:
        return {}, text
    loaded = yaml.safe_load(match.group(1)) or {}
    return dict(loaded) if isinstance(loaded, dict) else {}, text[match.end() :]


def _write_memory_file(path: Path, front_matter: dict[str, Any], body: str) -> None:
    rendered = "---\n" + yaml.safe_dump(front_matter, sort_keys=False, allow_unicode=False).strip() + "\n---\n\n" + body.rstrip() + "\n"
    _write_text(path, rendered)


def _ensure_memory_index(project_root: Path) -> Path:
    path = knowledge_index_path(project_root)
    if not path.exists():
        _write_text(path, "# Project Memory\n\n")
    return path


def _upsert_memory_index(project_root: Path, *, relpath: str, title: str, hook: str) -> None:
    path = _ensure_memory_index(project_root)
    line = f"- [{title}]({relpath}) - {_trim_text(hook, limit=150)}"
    existing = path.read_text(encoding="utf-8").splitlines()
    updated: list[str] = []
    replaced = False
    for item in existing:
        if f"]({relpath})" in item:
            updated.append(line)
            replaced = True
        else:
            updated.append(item)
    if not replaced:
        if updated and updated[-1].strip():
            updated.append("")
        updated.append(line)
    _write_text(path, "\n".join(updated).rstrip() + "\n")


def _collect_review_findings(run_dir: Path) -> list[str]:
    findings_path = run_dir / "review_findings.json"
    if not findings_path.is_file():
        return []
    try:
        payload = _read_json(findings_path)
    except (json.JSONDecodeError, OSError):
        return []
    findings = payload.get("findings") if isinstance(payload, dict) else None
    if not isinstance(findings, list):
        return []
    results: list[str] = []
    for item in findings[:5]:
        if not isinstance(item, dict):
            continue
        description = _trim_text(item.get("description") or item.get("message") or "", limit=240)
        if description:
            results.append(description)
    return results


def _collect_advisory_warnings(result: dict[str, Any]) -> list[str]:
    advisory = result.get("advisory") if isinstance(result.get("advisory"), dict) else {}
    warnings = advisory.get("warnings") if isinstance(advisory.get("warnings"), list) else []
    return [_trim_text(item, limit=240) for item in warnings if str(item).strip()]


def _collect_error_excerpt(run_dir: Path) -> str | None:
    stderr_path = run_dir / "stderr.log"
    if not stderr_path.is_file():
        return None
    lines = [line.strip() for line in stderr_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return None
    return _trim_text(" | ".join(lines[-5:]), limit=400)


def extract_run_knowledge(
    project_root: Path,
    run_dir: Path,
    *,
    job: dict[str, Any],
    meta: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    root = knowledge_root_for_project(project_root)
    runs_root = knowledge_runs_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    runs_root.mkdir(parents=True, exist_ok=True)

    run_id = str(result.get("run_id") or meta.get("run_id") or run_dir.name)
    task_id = str(meta.get("task_id") or job.get("task", {}).get("id") or "").strip() or None
    task_title = str(meta.get("task_title") or job.get("task", {}).get("title") or task_id or run_id).strip()
    result_status = str(result.get("status") or "failed").strip().lower() or "failed"
    agent = str(result.get("agent") or meta.get("preferred_agent") or job.get("preferred_agent") or "").strip() or None
    finished_at = str(result.get("finished_at") or meta.get("finished_at") or utc_now_timestamp()).strip()
    summary = _trim_text(result.get("summary") or "", limit=400)
    review_findings = _collect_review_findings(run_dir)
    advisory_warnings = _collect_advisory_warnings(result)
    error_excerpt = _collect_error_excerpt(run_dir)

    observations: list[str] = []
    if summary:
        observations.append(summary)
    if error_excerpt and error_excerpt not in observations:
        observations.append(f"Error excerpt: {error_excerpt}")
    for item in review_findings:
        observations.append(f"Review finding: {item}")
    for item in advisory_warnings:
        observations.append(f"Advisory warning: {item}")
    if not observations:
        observations.append("Run completed without a durable summary; inspect the attached run artifacts for details.")

    hook = observations[0]
    description = f"{run_id} {result_status}: {task_title}".strip()
    memory_path = runs_root / f"{run_id}.md"
    relpath = memory_path.relative_to(root).as_posix()
    front_matter = {
        "type": "run_memory",
        "run_id": run_id,
        "task_id": task_id,
        "status": result_status,
        "agent": agent,
        "description": description,
        "created_at": finished_at,
        "updated_at": finished_at,
    }
    body_lines = [
        f"# {run_id} - {task_title}",
        "",
        f"- Status: `{result_status}`",
        f"- Agent: `{agent or 'unknown'}`",
        f"- Finished: `{finished_at}`",
        f"- Run path: `{run_dir.relative_to(project_root).as_posix()}`",
        "",
        "## Extracted Signal",
        "",
    ]
    body_lines.extend(f"- {item}" for item in observations)
    body_lines.extend(
        [
            "",
            "## Source Artifacts",
            "",
            f"- `runs/{run_dir.parent.name}/{run_dir.name}/result.json`",
            f"- `runs/{run_dir.parent.name}/{run_dir.name}/report.md`",
            f"- `runs/{run_dir.parent.name}/{run_dir.name}/stderr.log`",
        ]
    )
    _write_memory_file(memory_path, front_matter, "\n".join(body_lines))
    _upsert_memory_index(project_root, relpath=relpath, title=run_id, hook=hook)
    return {
        "status": "written",
        "file": memory_path.relative_to(project_root).as_posix(),
        "files_touched": [
            memory_path.relative_to(project_root).as_posix(),
            knowledge_index_path(project_root).relative_to(project_root).as_posix(),
        ],
        "description": description,
    }


def scan_knowledge_entries(project_root: Path) -> list[dict[str, Any]]:
    root = knowledge_root_for_project(project_root)
    if not root.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.md")):
        if path.name == KNOWLEDGE_INDEX_FILE:
            continue
        try:
            front_matter, _body = _read_front_matter(path)
        except OSError:
            continue
        updated_at = str(front_matter.get("updated_at") or front_matter.get("created_at") or "").strip() or None
        entries.append(
            {
                "path": path.relative_to(project_root).as_posix(),
                "filename": path.relative_to(root).as_posix(),
                "description": str(front_matter.get("description") or path.stem).strip(),
                "updated_at": updated_at,
                "type": str(front_matter.get("type") or "").strip() or None,
            }
        )
    entries.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
    return entries


def format_knowledge_manifest(entries: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for entry in entries:
        tag = f"[{entry['type']}] " if entry.get("type") else ""
        timestamp = entry.get("updated_at") or "unknown"
        lines.append(f"- {tag}{entry['filename']} ({timestamp}): {entry['description']}")
    return "\n".join(lines)


def _completed_runs(project_root: Path) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    runs_root = project_root / "runs"
    if not runs_root.is_dir():
        return runs
    for result_path in sorted(runs_root.rglob("result.json")):
        try:
            payload = _read_json(result_path)
        except (json.JSONDecodeError, OSError):
            continue
        finished_at = parse_iso_timestamp(payload.get("finished_at") or payload.get("created_at"))
        if finished_at is None:
            continue
        runs.append(
            {
                "run_id": str(payload.get("run_id") or result_path.parent.name),
                "status": str(payload.get("status") or "").strip().lower(),
                "summary": _trim_text(payload.get("summary") or "", limit=200),
                "finished_at": finished_at,
                "path": result_path.parent,
            }
        )
    runs.sort(key=lambda item: item["finished_at"], reverse=True)
    return runs


def _task_status_counts(project_root: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    tasks_root = project_root / "tasks"
    if not tasks_root.is_dir():
        return counts
    for task_path in sorted(tasks_root.glob("TASK-*.md")):
        try:
            front_matter, _body = _read_front_matter(task_path)
        except OSError:
            continue
        counts[str(front_matter.get("status") or "todo").strip().lower() or "todo"] += 1
    return counts


def _append_dream_log(project_root: Path, *, summary: str, files_touched: list[str], runs_reviewed: int) -> str:
    path = dream_log_path(project_root)
    timestamp = utc_now_timestamp()
    entry = [
        f"## {timestamp}",
        "",
        f"- Runs reviewed: {runs_reviewed}",
        f"- Files touched: {', '.join(files_touched) if files_touched else 'none'}",
        f"- Summary: {summary}",
        "",
    ]
    if path.exists():
        current = path.read_text(encoding="utf-8")
    else:
        current = "# Dream Log\n\n"
    _write_text(path, current.rstrip() + "\n\n" + "\n".join(entry))
    return path.relative_to(project_root).as_posix()


def _acquire_dream_lock(project_root: Path) -> int | None:
    path = dream_lock_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    os.write(fd, utc_now_timestamp().encode("utf-8"))
    return fd


def _release_dream_lock(project_root: Path, fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    finally:
        try:
            dream_lock_path(project_root).unlink()
        except FileNotFoundError:
            pass


def run_project_dream(project_root: Path, *, force: bool = False, auto: bool = False) -> dict[str, Any]:
    state_path = project_root / "state" / "orchestration_state.json"
    state = read_orchestration_state(state_path) if state_path.exists() else default_orchestration_state()
    dream_state = dict(state.get("dream") or {})
    now = parse_iso_timestamp(utc_now_timestamp())
    assert now is not None

    last_completed_at = parse_iso_timestamp(dream_state.get("last_completed_at"))
    hours_since = ((now - last_completed_at).total_seconds() / 3600.0) if last_completed_at is not None else None
    completed_runs = _completed_runs(project_root)
    if last_completed_at is not None:
        recent_runs = [item for item in completed_runs if item["finished_at"] > last_completed_at]
    else:
        recent_runs = completed_runs

    if auto and not force:
        if hours_since is not None and hours_since < AUTO_DREAM_MIN_HOURS:
            return {
                "status": "skipped",
                "reason": "time_gate",
                "hours_since_last_dream": round(hours_since, 2),
                "runs_since_last_dream": len(recent_runs),
            }
        last_checked = parse_iso_timestamp(dream_state.get("last_checked_at"))
        if len(recent_runs) < AUTO_DREAM_MIN_RUNS:
            if last_checked is not None and (now - last_checked).total_seconds() < AUTO_DREAM_SCAN_THROTTLE_SECONDS:
                return {
                    "status": "skipped",
                    "reason": "scan_throttle",
                    "hours_since_last_dream": round(hours_since or 0.0, 2),
                    "runs_since_last_dream": len(recent_runs),
                }
            dream_state["last_checked_at"] = utc_now_timestamp()
            dream_state["last_result"] = "scan_throttle"
            dream_state["last_run_count"] = len(recent_runs)
            state["dream"] = dream_state
            write_orchestration_state(state_path, state)
            return {
                "status": "skipped",
                "reason": "run_gate",
                "hours_since_last_dream": round(hours_since or 0.0, 2),
                "runs_since_last_dream": len(recent_runs),
            }

    lock_fd = _acquire_dream_lock(project_root)
    if lock_fd is None:
        return {"status": "skipped", "reason": "lock"}

    files_touched: list[str] = []
    try:
        dream_state["last_started_at"] = utc_now_timestamp()
        dream_state["last_checked_at"] = dream_state["last_started_at"]
        state["dream"] = dream_state
        write_orchestration_state(state_path, state)

        entries = scan_knowledge_entries(project_root)
        manifest = format_knowledge_manifest(entries[:20])
        failures = [item for item in recent_runs if item["status"] == "failed"]
        successes = [item for item in recent_runs if item["status"] == "success"]
        failure_patterns = Counter(item["summary"] for item in failures if item["summary"])
        success_patterns = Counter(item["summary"] for item in successes if item["summary"])
        task_counts = _task_status_counts(project_root)
        summary_lines = [
            f"# Project Memory - {project_root.name}",
            "",
            f"- Last dream: `{dream_state.get('last_started_at')}`",
            f"- Runs reviewed: `{len(recent_runs)}`",
            f"- Knowledge files scanned: `{len(entries)}`",
            "",
            "## Task State",
            "",
        ]
        for status, count in sorted(task_counts.items()):
            summary_lines.append(f"- `{status}`: {count}")
        summary_lines.extend(["", "## Recent Success Patterns", ""])
        if success_patterns:
            for text, count in success_patterns.most_common(5):
                summary_lines.append(f"- ({count}x) {text}")
        else:
            summary_lines.append("- No recent successful runs to consolidate.")
        summary_lines.extend(["", "## Recurring Failure Patterns", ""])
        if failure_patterns:
            for text, count in failure_patterns.most_common(5):
                summary_lines.append(f"- ({count}x) {text}")
        else:
            summary_lines.append("- No recurring failures detected in the recent run window.")
        summary_lines.extend(["", "## Memory Manifest", ""])
        summary_lines.append(manifest or "- No knowledge files recorded yet.")

        memory_path = project_memory_path(project_root)
        memory_front_matter = {
            "type": "project_memory",
            "description": f"Consolidated project memory for {project_root.name}",
            "created_at": dream_state["last_started_at"],
            "updated_at": utc_now_timestamp(),
        }
        _write_memory_file(memory_path, memory_front_matter, "\n".join(summary_lines))
        files_touched.append(memory_path.relative_to(project_root).as_posix())
        _upsert_memory_index(
            project_root,
            relpath=memory_path.relative_to(knowledge_root_for_project(project_root)).as_posix(),
            title="Project Memory",
            hook=f"Consolidated {len(recent_runs)} runs and {len(entries)} knowledge files.",
        )
        files_touched.append(knowledge_index_path(project_root).relative_to(project_root).as_posix())

        summary = f"Consolidated {len(recent_runs)} runs into project memory."
        log_path = _append_dream_log(project_root, summary=summary, files_touched=files_touched, runs_reviewed=len(recent_runs))
        files_touched.append(log_path)

        dream_state["last_completed_at"] = utc_now_timestamp()
        dream_state["last_result"] = "completed"
        dream_state["last_run_count"] = len(recent_runs)
        dream_state["last_files_touched"] = files_touched
        state["dream"] = dream_state
        write_orchestration_state(state_path, state)
        return {
            "status": "completed",
            "files_touched": files_touched,
            "runs_reviewed": len(recent_runs),
            "knowledge_files": len(entries),
            "summary": summary,
        }
    except Exception:
        dream_state["last_result"] = "failed"
        state["dream"] = dream_state
        write_orchestration_state(state_path, state)
        raise
    finally:
        _release_dream_lock(project_root, lock_fd)
