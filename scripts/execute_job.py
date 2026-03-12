#!/usr/bin/env python3

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import yaml

from hooklib import build_hook_payload, dispatch_hook_file, read_json, trim_text, utc_now, write_hook_payload, write_json


def resolve_run_dir(argument: str) -> Path:
    path = Path(argument).expanduser().resolve()
    if path.is_dir():
        job_path = path / "job.json"
        if not job_path.is_file():
            raise FileNotFoundError(f"job.json not found in run directory: {path}")
        return path

    if path.is_file() and path.name == "job.json":
        return path.parent

    raise FileNotFoundError(f"Expected a run directory or job.json path: {argument}")


def project_root_from_run_dir(run_dir: Path) -> Path:
    resolved = run_dir.resolve()
    for ancestor in resolved.parents:
        if ancestor.name == "runs":
            return ancestor.parent
    raise ValueError(f"Could not resolve project root from run directory: {run_dir}")


def parse_agents_registry(path: Path) -> dict:
    if not path.is_file():
        return {}

    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return {}

    agents = loaded.get("agents") or {}
    if not isinstance(agents, dict):
        return {}

    return {agent_name: dict(config) for agent_name, config in agents.items() if isinstance(config, dict)}


def default_agent_config(agent: str) -> dict:
    defaults = {
        "codex": {
            "command": "codex",
            "args": "exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -C {project_root}",
            "prompt_mode": "arg",
            "cwd": "project_root",
            "default_timeout_seconds": "3600",
        },
        "claude": {
            "command": "claude",
            "args": "-p --permission-mode bypassPermissions --output-format text",
            "prompt_mode": "arg",
            "cwd": "project_root",
            "default_timeout_seconds": "3600",
        },
    }
    return dict(defaults.get(agent, {}))


def parse_timeout_seconds(raw_value: str | int | None, fallback: int) -> int:
    if raw_value in (None, ""):
        return fallback
    try:
        return max(1, int(raw_value))
    except (TypeError, ValueError):
        return fallback


def render_agent_args(template: str, project_root: Path, run_dir: Path) -> list[str]:
    if not template:
        return []
    rendered = template.format(project_root=project_root, run_dir=run_dir)
    return shlex.split(rendered)


def resolve_command_cwd(mode: str, project_root: Path, run_dir: Path) -> Path:
    if mode == "project_root":
        return project_root
    if mode == "run_dir":
        return run_dir
    raise ValueError(f"Unsupported agent cwd mode: {mode}")


def build_command(agent: str, prompt: str, project_root: Path, run_dir: Path, registry: dict) -> tuple[list[str], str, int, str | None, Path]:
    override = os.environ.get(f"CLAW_AGENT_COMMAND_{agent.upper()}") or os.environ.get("CLAW_AGENT_COMMAND")
    agent_config = default_agent_config(agent)
    agent_config.update(registry.get(agent, {}))
    default_timeout = parse_timeout_seconds(agent_config.get("default_timeout_seconds"), 3600)

    if override:
        return ["/bin/bash", "-lc", override], override, default_timeout, prompt, project_root

    executable = agent_config.get("command", "").strip()
    if not executable:
        raise ValueError(f"Unsupported preferred_agent: {agent}")

    prompt_mode = agent_config.get("prompt_mode", "arg").strip().lower() or "arg"
    if prompt_mode not in {"arg", "stdin"}:
        raise ValueError(f"Unsupported prompt mode for {agent}: {prompt_mode}")

    cwd_mode = agent_config.get("cwd", "project_root").strip().lower() or "project_root"
    working_directory = resolve_command_cwd(cwd_mode, project_root, run_dir)

    command = [executable, *render_agent_args(agent_config.get("args", ""), project_root, run_dir)]
    display_parts = list(command)
    prompt_input = None

    if prompt_mode == "arg":
        command.append(prompt)
        display_parts.append("<prompt>")
    else:
        prompt_input = prompt
        display_parts.append("<stdin>")

    return command, " ".join(display_parts), default_timeout, prompt_input, working_directory


def render_report(job: dict, status: str, started_at: str, finished_at: str, exit_code: int, command_display: str, summary: str, artifacts: dict, working_directory: Path) -> str:
    risk_line = "- None noted during execution."
    if status != "success":
        risk_line = "- Run failed or ended non-zero; inspect stderr.log before trusting artifacts."

    summary_block = summary or "Agent produced no summary output. Inspect stdout.log and stderr.log."

    lines = [
        "# Run Report",
        "",
        f"- Project: {job.get('project', '')}",
        f"- Task: {job.get('task', {}).get('id', '')}",
        f"- Spec: {job.get('spec', {}).get('source_path', '')}",
        f"- Created at: {job.get('created_at', '')}",
        f"- Started at: {started_at}",
        f"- Finished at: {finished_at}",
        f"- Agent: {job.get('preferred_agent', '')}",
        f"- Status: {status}",
        f"- Exit code: {exit_code}",
        "",
        "## Summary",
        summary_block,
        "",
        "## Verification",
        f"- Command: {command_display}",
        f"- Working directory: {working_directory}",
        f"- Stdout log: {artifacts.get('stdout_path', 'stdout.log')}",
        f"- Stderr log: {artifacts.get('stderr_path', 'stderr.log')}",
        "",
        "## Risks",
        risk_line,
        "",
    ]
    return "\n".join(lines)


def run_post_artifact_validation(run_dir: Path) -> dict:
    try:
        from validate_artifacts import validate_run_dir

        errors = validate_run_dir(run_dir)
        return {
            "valid": not any(artifact_errors for artifact_errors in errors.values()),
            "errors": errors,
        }
    except Exception as exc:  # pragma: no cover - validation must not hide run result
        return {
            "valid": False,
            "errors": {
                "_exception": [str(exc)],
            },
        }


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/execute_job.py <run-dir|job.json>", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parent.parent
    agents_registry_path = repo_root / "_system" / "registry" / "agents.yaml"

    try:
        run_dir = resolve_run_dir(sys.argv[1])
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    job_path = run_dir / "job.json"
    meta_path = run_dir / "meta.json"
    result_path = run_dir / "result.json"

    try:
        project_root = project_root_from_run_dir(run_dir)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    job = read_json(job_path)
    meta = read_json(meta_path)
    result = read_json(result_path)

    artifacts = job.get("artifacts", {})
    prompt_path = run_dir / artifacts.get("prompt_path", "prompt.txt")
    stdout_path = run_dir / artifacts.get("stdout_path", "stdout.log")
    stderr_path = run_dir / artifacts.get("stderr_path", "stderr.log")
    report_path = run_dir / artifacts.get("report_path", "report.md")

    if not prompt_path.is_file():
        print(f"Prompt file not found: {prompt_path}", file=sys.stderr)
        return 1

    prompt = prompt_path.read_text(encoding="utf-8")
    preferred_agent = job.get("preferred_agent") or job.get("task", {}).get("preferred_agent") or "codex"
    registry = parse_agents_registry(agents_registry_path)

    try:
        command, command_display, default_timeout, command_input, working_directory = build_command(
            preferred_agent,
            prompt,
            project_root,
            run_dir,
            registry,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    timeout_seconds = default_timeout
    timeout_override = os.environ.get("CLAW_AGENT_TIMEOUT_SECONDS")
    if timeout_override:
        try:
            timeout_seconds = int(timeout_override)
        except ValueError:
            timeout_seconds = default_timeout

    created_at = meta.get("created_at") or result.get("created_at") or job.get("created_at") or utc_now()
    started_at = utc_now()

    meta.update(
        {
            "status": "running",
            "started_at": started_at,
            "executor": {
                "agent": preferred_agent,
                "command": command_display,
                "timeout_seconds": timeout_seconds,
            },
        }
    )
    write_json(meta_path, meta)

    running_result = {
        "run_id": job.get("run_id"),
        "status": "running",
        "created_at": created_at,
        "started_at": started_at,
        "agent": preferred_agent,
    }
    write_json(result_path, running_result)

    start_monotonic = time.monotonic()
    stdout_text = ""
    stderr_text = ""
    exit_code = 1
    status = "failed"

    try:
        completed = subprocess.run(
            command,
            input=command_input,
            capture_output=True,
            text=True,
            cwd=working_directory,
            timeout=timeout_seconds,
            check=False,
        )
        stdout_text = completed.stdout or ""
        stderr_text = completed.stderr or ""
        exit_code = completed.returncode
        status = "success" if exit_code == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        stdout_text = exc.stdout or ""
        stderr_text = (exc.stderr or "") + f"\nTimed out after {timeout_seconds} seconds\n"
        exit_code = 124
        status = "failed"
    except FileNotFoundError as exc:
        stderr_text = f"Command not found: {exc.filename}\n"
        exit_code = 127
        status = "failed"
    except Exception as exc:  # pragma: no cover - safety net
        stderr_text = f"Execution error: {exc}\n"
        exit_code = 1
        status = "failed"

    finished_at = utc_now()
    duration_seconds = round(time.monotonic() - start_monotonic, 3)

    stdout_path.write_text(stdout_text, encoding="utf-8")
    stderr_path.write_text(stderr_text, encoding="utf-8")

    summary_source = stdout_text if stdout_text.strip() else stderr_text
    summary = trim_text(summary_source)

    final_result = {
        "run_id": job.get("run_id"),
        "status": status,
        "created_at": created_at,
        "started_at": started_at,
        "finished_at": finished_at,
        "agent": preferred_agent,
        "exit_code": exit_code,
        "duration_seconds": duration_seconds,
        "command": command_display,
        "summary": summary,
    }
    meta.update(
        {
            "status": "completed" if status == "success" else "failed",
            "finished_at": finished_at,
            "last_exit_code": exit_code,
        }
    )
    write_json(meta_path, meta)

    report_text = render_report(
        job=job,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        exit_code=exit_code,
        command_display=command_display,
        summary=summary,
        artifacts=artifacts,
        working_directory=working_directory,
    )
    report_path.write_text(report_text, encoding="utf-8")

    validation_result = run_post_artifact_validation(run_dir)
    final_result["validation"] = validation_result
    meta["validation"] = validation_result

    final_result["hook"] = {}
    meta["hook"] = {}

    try:
        hook_payload = build_hook_payload(run_dir, project_root, job, meta, final_result)
        hook_path = write_hook_payload(project_root, hook_payload, "pending")
        hook_delivery = dispatch_hook_file(hook_path)
        hook_rel_path = hook_delivery["path"].relative_to(project_root).as_posix()
        hook_snapshot = {
            "hook_id": hook_delivery["hook_id"],
            "delivery_status": hook_delivery["status"],
            "path": hook_rel_path,
        }
        final_result["hook"] = hook_snapshot
        meta["hook"] = hook_snapshot
    except Exception as exc:  # pragma: no cover - delivery must not hide run result
        final_result["hook"] = {
            "delivery_status": "error",
            "error": str(exc),
        }
        meta["hook"] = {
            "delivery_status": "error",
            "error": str(exc),
        }

    write_json(result_path, final_result)
    write_json(meta_path, meta)

    print(f"Executed job: {run_dir} [{status}]")
    return 0 if status == "success" else exit_code


if __name__ == "__main__":
    raise SystemExit(main())
