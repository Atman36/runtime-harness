#!/usr/bin/env python3

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


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


def parse_agents_registry(path: Path) -> dict:
    agents = {}
    current_agent = None

    if not path.is_file():
        return agents

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("  ") and not raw_line.startswith("    ") and raw_line.strip().endswith(":"):
            current_agent = raw_line.strip()[:-1]
            agents[current_agent] = {}
            continue

        if current_agent and raw_line.startswith("    "):
            stripped = raw_line.strip()
            if ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            agents[current_agent][key.strip()] = value.strip().strip('"')

    return agents


def build_command(agent: str, prompt: str, project_root: Path, registry: dict) -> tuple[list[str], str, int]:
    override = os.environ.get(f"CLAW_AGENT_COMMAND_{agent.upper()}") or os.environ.get("CLAW_AGENT_COMMAND")

    default_timeout = 3600
    if agent in registry:
        try:
            default_timeout = int(registry[agent].get("default_timeout_seconds", default_timeout))
        except ValueError:
            default_timeout = 3600

    if override:
        return ["/bin/bash", "-lc", override], override, default_timeout

    if agent == "codex":
        command = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox",
            "-C",
            str(project_root),
            prompt,
        ]
        display = "codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -C <project_root> <prompt>"
        return command, display, default_timeout

    if agent == "claude":
        command = [
            "claude",
            "-p",
            "--permission-mode",
            "bypassPermissions",
            "--output-format",
            "text",
            prompt,
        ]
        display = "claude -p --permission-mode bypassPermissions --output-format text <prompt>"
        return command, display, default_timeout

    raise ValueError(f"Unsupported preferred_agent: {agent}")


def trim_summary(text: str, limit: int = 1200) -> str:
    compact = text.strip()
    if not compact:
        return ""
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def render_report(job: dict, status: str, started_at: str, finished_at: str, exit_code: int, command_display: str, summary: str, artifacts: dict, project_root: Path) -> str:
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
        f"- Working directory: {project_root}",
        f"- Stdout log: {artifacts.get('stdout_path', 'stdout.log')}",
        f"- Stderr log: {artifacts.get('stderr_path', 'stderr.log')}",
        "",
        "## Risks",
        risk_line,
        "",
    ]
    return "\n".join(lines)


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
        command, command_display, default_timeout = build_command(preferred_agent, prompt, run_dir.parents[2], registry)
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

    project_root = run_dir.parents[2]
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
            input=prompt if command[:2] == ["/bin/bash", "-lc"] else None,
            capture_output=True,
            text=True,
            cwd=project_root,
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
    summary = trim_summary(summary_source)

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
    write_json(result_path, final_result)

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
        project_root=project_root,
    )
    report_path.write_text(report_text, encoding="utf-8")

    print(f"Executed job: {run_dir} [{status}]")
    return 0 if status == "success" else exit_code


if __name__ == "__main__":
    raise SystemExit(main())
