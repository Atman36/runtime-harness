#!/usr/bin/env python3

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hooklib import (
    build_delivery_snapshot,
    build_hook_payload,
    build_hook_snapshot,
    dispatch_hook_file,
    read_json,
    trim_text,
    utc_now,
    write_hook_payload,
    write_json,
)
from _system.engine.trusted_command import command_display, parse_trusted_argv


@dataclass(frozen=True)
class WorkspaceContext:
    mode: str
    source_project_root: Path
    project_root: Path
    run_dir: Path
    workspace_root: Path
    git_root: Path | None = None
    preserved: bool = True

    def snapshot(self, cwd: Path) -> dict[str, object]:
        return {
            "mode": self.mode,
            "source_project_root": str(self.source_project_root),
            "project_root": str(self.project_root),
            "run_dir": str(self.run_dir),
            "workspace_root": str(self.workspace_root),
            "git_root": str(self.git_root) if self.git_root is not None else None,
            "cwd": str(cwd),
            "preserved": self.preserved,
        }


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
            "workspace_mode": "project_root",
            "default_timeout_seconds": "3600",
        },
        "claude": {
            "command": "claude",
            "args": "-p --permission-mode bypassPermissions --output-format text",
            "prompt_mode": "arg",
            "cwd": "project_root",
            "workspace_mode": "project_root",
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


def render_agent_args(
    template: str,
    project_root: Path,
    run_dir: Path,
    *,
    source_project_root: Path,
    workspace_root: Path,
) -> list[str]:
    if not template:
        return []
    rendered = template.format(
        project_root=project_root,
        source_project_root=source_project_root,
        run_dir=run_dir,
        workspace_root=workspace_root,
    )
    return shlex.split(rendered)


def resolve_command_cwd(mode: str, project_root: Path, run_dir: Path, *, workspace_root: Path) -> Path:
    if mode == "project_root":
        return project_root
    if mode == "run_dir":
        return run_dir
    if mode == "workspace_root":
        return workspace_root
    raise ValueError(f"Unsupported agent cwd mode: {mode}")


def find_git_root(project_root: Path) -> Path:
    completed = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise ValueError(f"git worktree mode requires a git repository: {stderr or project_root}")
    git_root = (completed.stdout or "").strip()
    if not git_root:
        raise ValueError(f"Unable to resolve git root for {project_root}")
    return Path(git_root).resolve()


def workspace_base_path(git_root: Path) -> Path:
    override = os.environ.get("CLAW_WORKSPACE_BASE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return git_root.parent / f".{git_root.name}-worktrees"


def acquire_lock(lock_path: Path, *, timeout_seconds: float = 30.0) -> int:
    deadline = time.monotonic() + max(1.0, timeout_seconds)
    while True:
        try:
            return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for workspace lock: {lock_path}")
            time.sleep(0.05)


def release_lock(lock_path: Path, lock_fd: int) -> None:
    try:
        os.close(lock_fd)
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _sanitize_segment(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value).strip("-") or "workspace"


def worktree_name_for_run(source_project_root: Path, run_dir: Path, git_root: Path) -> str:
    try:
        relative_project = source_project_root.relative_to(git_root)
        project_token = "-".join(relative_project.parts)
    except ValueError:
        project_token = source_project_root.name
    date_token = run_dir.parent.name if run_dir.parent.name else "run"
    return _sanitize_segment(f"{project_token}-{date_token}-{run_dir.name}")


def ensure_git_worktree(source_project_root: Path, run_dir: Path) -> WorkspaceContext:
    git_root = find_git_root(source_project_root)
    base_dir = workspace_base_path(git_root)
    base_dir.mkdir(parents=True, exist_ok=True)

    worktree_root = base_dir / worktree_name_for_run(source_project_root, run_dir, git_root)
    lock_path = base_dir / f".{worktree_root.name}.lock"
    lock_fd = acquire_lock(lock_path)
    try:
        if worktree_root.exists() and not (worktree_root / ".git").exists():
            shutil.rmtree(worktree_root)
        if not (worktree_root / ".git").exists():
            completed = subprocess.run(
                ["git", "-C", str(git_root), "worktree", "add", "--detach", str(worktree_root), "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0 and not (worktree_root / ".git").exists():
                stderr = (completed.stderr or completed.stdout or "").strip()
                raise ValueError(f"Failed to create git worktree at {worktree_root}: {stderr}")
    finally:
        release_lock(lock_path, lock_fd)

    try:
        relative_project = source_project_root.relative_to(git_root)
    except ValueError as exc:
        raise ValueError(f"Project root {source_project_root} is not inside git root {git_root}") from exc

    effective_project_root = (worktree_root / relative_project).resolve()
    if not effective_project_root.exists():
        raise ValueError(f"Effective project root missing inside worktree: {effective_project_root}")

    return WorkspaceContext(
        mode="git_worktree",
        source_project_root=source_project_root,
        project_root=effective_project_root,
        run_dir=run_dir,
        workspace_root=worktree_root,
        git_root=git_root,
        preserved=True,
    )


def ensure_isolated_checkout(source_project_root: Path, run_dir: Path) -> WorkspaceContext:
    git_root = find_git_root(source_project_root)
    base_dir = workspace_base_path(git_root)
    base_dir.mkdir(parents=True, exist_ok=True)

    checkout_name = worktree_name_for_run(source_project_root, run_dir, git_root)
    checkout_root = base_dir / f"checkout-{checkout_name}"
    lock_path = base_dir / f".{checkout_root.name}.lock"
    lock_fd = acquire_lock(lock_path)
    try:
        if checkout_root.exists() and not (checkout_root / ".git").exists():
            shutil.rmtree(checkout_root)
        if not checkout_root.exists():
            completed = subprocess.run(
                ["git", "clone", "--shared", "--no-checkout", str(git_root), str(checkout_root)],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0 and not (checkout_root / ".git").exists():
                stderr = (completed.stderr or completed.stdout or "").strip()
                raise ValueError(f"Failed to create isolated checkout at {checkout_root}: {stderr}")

            completed = subprocess.run(
                ["git", "-C", str(checkout_root), "checkout", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                stderr = (completed.stderr or completed.stdout or "").strip()
                raise ValueError(f"Failed to checkout HEAD in isolated checkout at {checkout_root}: {stderr}")
    finally:
        release_lock(lock_path, lock_fd)

    try:
        relative_project = source_project_root.relative_to(git_root)
    except ValueError as exc:
        raise ValueError(f"Project root {source_project_root} is not inside git root {git_root}") from exc

    effective_project_root = (checkout_root / relative_project).resolve()
    if not effective_project_root.exists():
        raise ValueError(f"Effective project root missing inside isolated checkout: {effective_project_root}")

    return WorkspaceContext(
        mode="isolated_checkout",
        source_project_root=source_project_root,
        project_root=effective_project_root,
        run_dir=run_dir,
        workspace_root=checkout_root,
        git_root=git_root,
        preserved=True,
    )


def resolve_workspace(
    agent: str,
    source_project_root: Path,
    run_dir: Path,
    registry: dict,
    job_execution: dict | None = None,
) -> WorkspaceContext:
    agent_config = default_agent_config(agent)
    agent_config.update(registry.get(agent, {}))

    execution = job_execution or {}
    requested_mode = (
        (str(execution.get("workspace_mode") or "").strip() or None)
        or os.environ.get("CLAW_WORKSPACE_MODE")
        or agent_config.get("workspace_mode")
        or "project_root"
    )
    mode = str(requested_mode).strip().lower() or "project_root"

    if mode in ("project_root", "shared_project"):
        return WorkspaceContext(
            mode="project_root",
            source_project_root=source_project_root,
            project_root=source_project_root,
            run_dir=run_dir,
            workspace_root=source_project_root,
            git_root=None,
            preserved=True,
        )

    if mode == "run_dir":
        return WorkspaceContext(
            mode="run_dir",
            source_project_root=source_project_root,
            project_root=source_project_root,
            run_dir=run_dir,
            workspace_root=run_dir,
            git_root=None,
            preserved=True,
        )

    if mode == "git_worktree":
        return ensure_git_worktree(source_project_root, run_dir)

    if mode == "isolated_checkout":
        return ensure_isolated_checkout(source_project_root, run_dir)

    raise ValueError(f"Unsupported workspace mode: {mode}")


def build_command(agent: str, prompt: str, workspace: WorkspaceContext, registry: dict) -> tuple[list[str], str, int, str | None, Path]:
    override_env = f"CLAW_AGENT_COMMAND_{agent.upper()}"
    override_raw = os.environ.get(override_env) or os.environ.get("CLAW_AGENT_COMMAND")
    agent_config = default_agent_config(agent)
    agent_config.update(registry.get(agent, {}))
    default_timeout = parse_timeout_seconds(agent_config.get("default_timeout_seconds"), 3600)

    cwd_mode = str(agent_config.get("cwd", "project_root")).strip().lower() or "project_root"
    working_directory = resolve_command_cwd(
        cwd_mode,
        workspace.project_root,
        workspace.run_dir,
        workspace_root=workspace.workspace_root,
    )

    if override_raw:
        env_name = override_env if os.environ.get(override_env) else "CLAW_AGENT_COMMAND"
        override = parse_trusted_argv(override_raw, env_name=env_name)
        if override is None:
            raise ValueError(f"{env_name} must be a trusted argv command")
        return override, command_display(override), default_timeout, prompt, working_directory

    executable = str(agent_config.get("command", "")).strip()
    if not executable:
        raise ValueError(f"Unsupported preferred_agent: {agent}")

    prompt_mode = str(agent_config.get("prompt_mode", "arg")).strip().lower() or "arg"
    if prompt_mode not in {"arg", "stdin"}:
        raise ValueError(f"Unsupported prompt mode for {agent}: {prompt_mode}")

    command = [
        executable,
        *render_agent_args(
            str(agent_config.get("args", "")),
            workspace.project_root,
            workspace.run_dir,
            source_project_root=workspace.source_project_root,
            workspace_root=workspace.workspace_root,
        ),
    ]
    display_parts = list(command)
    prompt_input = None

    if prompt_mode == "arg":
        command.append(prompt)
        display_parts.append("<prompt>")
    else:
        prompt_input = prompt
        display_parts.append("<stdin>")

    return command, command_display(display_parts), default_timeout, prompt_input, working_directory


def render_report(
    job: dict,
    status: str,
    started_at: str,
    finished_at: str,
    exit_code: int,
    command_display: str,
    summary: str,
    artifacts: dict,
    working_directory: Path,
    workspace: WorkspaceContext,
) -> str:
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
        "## Workspace",
        f"- Mode: {workspace.mode}",
        f"- Source project root: {workspace.source_project_root}",
        f"- Effective project root: {workspace.project_root}",
        f"- Workspace root: {workspace.workspace_root}",
        f"- Run directory: {workspace.run_dir}",
        f"- Git root: {workspace.git_root or 'n/a'}",
        f"- Preserved: {'yes' if workspace.preserved else 'no'}",
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
        source_project_root = project_root_from_run_dir(run_dir)
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
    job_execution = job.get("execution") if isinstance(job.get("execution"), dict) else {}
    preferred_agent = (
        job_execution.get("agent")
        or job.get("preferred_agent")
        or job.get("task", {}).get("preferred_agent")
        or "codex"
    )
    registry = parse_agents_registry(agents_registry_path)

    try:
        workspace = resolve_workspace(preferred_agent, source_project_root, run_dir, registry, job_execution)
        command, command_display, default_timeout, command_input, working_directory = build_command(
            preferred_agent,
            prompt,
            workspace,
            registry,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    timeout_seconds = parse_timeout_seconds(os.environ.get("CLAW_AGENT_TIMEOUT_SECONDS"), default_timeout)

    created_at = meta.get("created_at") or result.get("created_at") or job.get("created_at") or utc_now()
    started_at = utc_now()
    workspace_snapshot = workspace.snapshot(working_directory)

    meta.update(
        {
            "status": "running",
            "started_at": started_at,
            "workspace": workspace_snapshot,
            "executor": {
                "agent": preferred_agent,
                "command": command_display,
                "cwd": str(working_directory),
                "workspace_mode": workspace.mode,
                "timeout_seconds": timeout_seconds,
            },
        }
    )
    write_json(meta_path, meta)

    running_result = dict(result)
    running_result.update(
        {
            "run_id": job.get("run_id"),
            "status": "running",
            "created_at": created_at,
            "started_at": started_at,
            "agent": preferred_agent,
            "workspace": workspace_snapshot,
        }
    )
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

    final_result = dict(running_result)
    final_result.update(
        {
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
            "workspace": workspace_snapshot,
        }
    )
    meta.update(
        {
            "status": "completed" if status == "success" else "failed",
            "finished_at": finished_at,
            "last_exit_code": exit_code,
            "workspace": workspace_snapshot,
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
        workspace=workspace,
    )
    report_path.write_text(report_text, encoding="utf-8")

    validation_result = run_post_artifact_validation(run_dir)
    final_result["validation"] = validation_result
    meta["validation"] = validation_result

    final_result["hook"] = {}
    meta["hook"] = {}
    final_result["delivery"] = {}
    meta["delivery"] = {}

    try:
        hook_payload = build_hook_payload(run_dir, source_project_root, job, meta, final_result)
        hook_path = write_hook_payload(source_project_root, hook_payload, "pending")
        hook_delivery = dispatch_hook_file(hook_path)
        delivered_hook_path = hook_delivery["path"]
        delivered_hook_payload = read_json(delivered_hook_path)
        hook_snapshot = build_hook_snapshot(source_project_root, delivered_hook_path, delivered_hook_payload)
        delivery_snapshot = build_delivery_snapshot(
            source_project_root,
            run_id=final_result["run_id"],
            run_date=meta.get("run_date"),
            result=final_result,
            meta=meta,
            hook_path=delivered_hook_path,
            hook_payload=delivered_hook_payload,
        )
        final_result["hook"] = hook_snapshot
        meta["hook"] = hook_snapshot
        final_result["delivery"] = delivery_snapshot
        meta["delivery"] = delivery_snapshot
    except Exception as exc:  # pragma: no cover - delivery must not hide run result
        final_result["hook"] = {
            "delivery_status": "error",
            "error": str(exc),
        }
        meta["hook"] = {
            "delivery_status": "error",
            "error": str(exc),
        }
        final_result["delivery"] = {
            "required": True,
            "status": "missing",
            "hook_written": False,
            "hook_status": "error",
            "last_error": str(exc),
        }
        meta["delivery"] = dict(final_result["delivery"])

    write_json(result_path, final_result)
    write_json(meta_path, meta)

    print(f"Executed job: {run_dir} [{status}]")
    return 0 if status == "success" else exit_code


if __name__ == "__main__":
    raise SystemExit(main())
