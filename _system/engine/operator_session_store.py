from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml

from _system.engine.operator_context import render_ctx_footer


SESSION_VERSION = 1
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_POLL_INTERVAL_SECONDS = 0.05
_SCOPE_KIND_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")

_RESUME_LINE_TEMPLATES = {
    "codex": "codex resume {handle}",
    "claude": "claude --resume {handle}",
}


def operator_sessions_root_for_repo(repo_root: Path) -> Path:
    return Path(repo_root) / "state" / "operator_sessions"


class OperatorSessionStore:
    """Filesystem-backed operator session continuity store for transport scopes."""

    def __init__(self, root: Path, *, repo_root: Path | None = None):
        self.root = Path(root)
        self.repo_root = Path(repo_root).expanduser().resolve() if repo_root is not None else None
        self.sessions_dir = self.root
        self.locks = self.root / "locks"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.locks.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _normalize_scope_id(scope_id: str) -> str:
        normalized = str(scope_id or "").strip()
        if not normalized:
            raise ValueError("Scope id is required")
        if any(char in normalized for char in ("\n", "\r", "\0")):
            raise ValueError("Scope id contains unsupported control characters")
        return normalized

    @staticmethod
    def _normalize_scope_kind(scope_kind: str | None) -> str:
        normalized = str(scope_kind or "thread").strip().lower()
        if not _SCOPE_KIND_PATTERN.fullmatch(normalized):
            raise ValueError(f"Invalid scope kind: {scope_kind}")
        return normalized

    @staticmethod
    def _normalize_engine(engine: str) -> str:
        normalized = str(engine or "").strip()
        if not normalized:
            raise ValueError("Engine is required")
        if "/" in normalized or "\\" in normalized:
            raise ValueError(f"Invalid engine id: {normalized}")
        return normalized

    @staticmethod
    def _normalize_token(value: Any) -> str | None:
        token = str(value or "").strip()
        return token or None

    @staticmethod
    def _session_id() -> str:
        return f"OPSESSION-{uuid4().hex[:12].upper()}"

    @classmethod
    def _scope_key(cls, scope_kind: str, scope_id: str, engine: str) -> str:
        return f"{scope_kind}:{scope_id}::{engine}"

    @classmethod
    def _filename(cls, scope_kind: str, scope_id: str, engine: str) -> str:
        scope_hash = hashlib.sha1(scope_id.encode("utf-8")).hexdigest()[:12]
        readable_scope = re.sub(r"[^A-Za-z0-9._-]+", "_", scope_id).strip("._-") or "scope"
        readable_scope = readable_scope[:48]
        return f"{scope_kind}__{readable_scope}__{engine}__{scope_hash}.json"

    def _session_path(self, scope_kind: str, scope_id: str, engine: str) -> Path:
        return self.sessions_dir / self._filename(scope_kind, scope_id, engine)

    def _lock_path(self, scope_kind: str, scope_id: str, engine: str) -> Path:
        lock_name = self._filename(scope_kind, scope_id, engine).removesuffix(".json")
        return self.locks / f"{lock_name}.lock"

    def _acquire_lock(self, scope_kind: str, scope_id: str, engine: str) -> Path:
        lock_path = self._lock_path(scope_kind, scope_id, engine)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                lock_path.mkdir(parents=False)
                return lock_path
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for operator session lock: {scope_kind}/{scope_id}/{engine}")
                time.sleep(LOCK_POLL_INTERVAL_SECONDS)

    @staticmethod
    def _release_lock(lock_path: Path) -> None:
        try:
            lock_path.rmdir()
        except FileNotFoundError:
            return

    @staticmethod
    def _write_payload(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        try:
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    @staticmethod
    def _read_payload(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Operator session payload must be a JSON object: {path}")
        return payload

    def _agent_resume_template(self, engine: str) -> str | None:
        if self.repo_root is None:
            return _RESUME_LINE_TEMPLATES.get(engine)
        registry_path = self.repo_root / "_system" / "registry" / "agents.yaml"
        if registry_path.is_file():
            payload = yaml.safe_load(registry_path.read_text(encoding="utf-8")) or {}
            agents = payload.get("agents") if isinstance(payload, dict) else {}
            config = agents.get(engine) if isinstance(agents, dict) else None
            if isinstance(config, dict):
                template = self._normalize_token(config.get("resume_line_template"))
                if template:
                    return template
        return _RESUME_LINE_TEMPLATES.get(engine)

    def derive_resume_line(self, *, engine: str, resume: dict[str, Any] | None) -> str | None:
        normalized_engine = self._normalize_engine(engine)
        if not isinstance(resume, dict):
            return None
        handle = self._normalize_token(resume.get("handle"))
        if not handle:
            return None
        template = self._agent_resume_template(normalized_engine)
        if not template:
            return None
        return template.format(handle=handle, engine=normalized_engine)

    def _binding_payload(
        self,
        *,
        engine: str,
        project: str | None = None,
        project_root: str | None = None,
        branch: str | None = None,
        workspace_mode: str | None = None,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        prior = existing if isinstance(existing, dict) else {}
        bound_project = self._normalize_token(project) or self._normalize_token(prior.get("project"))
        bound_project_root = self._normalize_token(project_root) or self._normalize_token(prior.get("project_root"))
        bound_branch = self._normalize_token(branch) or self._normalize_token(prior.get("branch"))
        bound_workspace_mode = self._normalize_token(workspace_mode) or self._normalize_token(prior.get("workspace_mode"))
        payload = {
            "project": bound_project,
            "project_root": bound_project_root,
            "agent": engine,
            "branch": bound_branch,
            "workspace_mode": bound_workspace_mode,
        }
        payload["ctx_footer"] = render_ctx_footer(payload)
        return payload

    def _base_payload(
        self,
        *,
        scope_kind: str,
        scope_id: str,
        engine: str,
        project: str | None = None,
        project_root: str | None = None,
        branch: str | None = None,
        workspace_mode: str | None = None,
    ) -> dict[str, Any]:
        now = self._utc_now()
        return {
            "session_version": SESSION_VERSION,
            "session_id": self._session_id(),
            "scope_key": self._scope_key(scope_kind, scope_id, engine),
            "scope": {
                "kind": scope_kind,
                "id": scope_id,
            },
            "engine": engine,
            "binding": self._binding_payload(
                engine=engine,
                project=project,
                project_root=project_root,
                branch=branch,
                workspace_mode=workspace_mode,
            ),
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "resume": None,
            "handoff": {
                "summary": "",
                "updated_at": now,
                "run_id": None,
                "run_path": None,
                "note": None,
            },
            "last_run_id": None,
            "last_run_path": None,
            "reset_count": 0,
            "rotation_count": 0,
            "events": [
                {
                    "at": now,
                    "event": "created",
                }
            ],
        }

    def load_session(self, *, scope_id: str, engine: str, scope_kind: str = "thread") -> dict[str, Any] | None:
        normalized_scope_kind = self._normalize_scope_kind(scope_kind)
        normalized_scope_id = self._normalize_scope_id(scope_id)
        normalized_engine = self._normalize_engine(engine)
        path = self._session_path(normalized_scope_kind, normalized_scope_id, normalized_engine)
        if not path.is_file():
            return None
        payload = self._read_payload(path)
        relative_root = self.repo_root if self.repo_root is not None else self.root
        payload["session_file"] = path.relative_to(relative_root).as_posix()
        return payload

    def update(
        self,
        *,
        scope_id: str,
        engine: str,
        scope_kind: str = "thread",
        resume: dict[str, Any] | None = None,
        summary: str | None = None,
        note: str | None = None,
        run_id: str | None = None,
        run_path: str | None = None,
        project: str | None = None,
        project_root: str | None = None,
        branch: str | None = None,
        workspace_mode: str | None = None,
    ) -> dict[str, Any]:
        normalized_scope_kind = self._normalize_scope_kind(scope_kind)
        normalized_scope_id = self._normalize_scope_id(scope_id)
        normalized_engine = self._normalize_engine(engine)
        session_path = self._session_path(normalized_scope_kind, normalized_scope_id, normalized_engine)
        lock_path = self._acquire_lock(normalized_scope_kind, normalized_scope_id, normalized_engine)
        try:
            if session_path.is_file():
                payload = self._read_payload(session_path)
            else:
                payload = self._base_payload(
                    scope_kind=normalized_scope_kind,
                    scope_id=normalized_scope_id,
                    engine=normalized_engine,
                    project=project,
                    project_root=project_root,
                    branch=branch,
                    workspace_mode=workspace_mode,
                )

            if resume is not None and not isinstance(resume, dict):
                raise ValueError("Resume handle must be a JSON object")

            now = self._utc_now()
            payload["session_version"] = SESSION_VERSION
            payload["scope_key"] = self._scope_key(normalized_scope_kind, normalized_scope_id, normalized_engine)
            payload["scope"] = {"kind": normalized_scope_kind, "id": normalized_scope_id}
            payload["engine"] = normalized_engine
            payload["binding"] = self._binding_payload(
                engine=normalized_engine,
                project=project,
                project_root=project_root,
                branch=branch,
                workspace_mode=workspace_mode,
                existing=payload.get("binding"),
            )
            payload["status"] = "active"
            payload["updated_at"] = now

            if resume is not None:
                payload["resume"] = resume

            handoff = payload.get("handoff") if isinstance(payload.get("handoff"), dict) else {}
            handoff["updated_at"] = now
            handoff["note"] = note or handoff.get("note")
            if summary is not None:
                handoff["summary"] = str(summary)
            if run_id is not None:
                handoff["run_id"] = run_id
            if run_path is not None:
                handoff["run_path"] = run_path
            if "summary" not in handoff:
                handoff["summary"] = ""
            payload["handoff"] = handoff

            if run_id is not None:
                payload["last_run_id"] = run_id
            if run_path is not None:
                payload["last_run_path"] = run_path

            events = payload.get("events") if isinstance(payload.get("events"), list) else []
            event: dict[str, Any] = {"at": now, "event": "updated"}
            if run_id:
                event["run_id"] = run_id
            if note:
                event["note"] = str(note)
            events.append(event)
            payload["events"] = events

            self._write_payload(session_path, payload)
            relative_root = self.repo_root if self.repo_root is not None else self.root
            payload["session_file"] = session_path.relative_to(relative_root).as_posix()
            return payload
        finally:
            self._release_lock(lock_path)

    def reset(
        self,
        *,
        scope_id: str,
        engine: str,
        scope_kind: str = "thread",
        note: str | None = None,
    ) -> dict[str, Any]:
        normalized_scope_kind = self._normalize_scope_kind(scope_kind)
        normalized_scope_id = self._normalize_scope_id(scope_id)
        normalized_engine = self._normalize_engine(engine)
        session_path = self._session_path(normalized_scope_kind, normalized_scope_id, normalized_engine)
        lock_path = self._acquire_lock(normalized_scope_kind, normalized_scope_id, normalized_engine)
        try:
            if session_path.is_file():
                payload = self._read_payload(session_path)
            else:
                payload = self._base_payload(
                    scope_kind=normalized_scope_kind,
                    scope_id=normalized_scope_id,
                    engine=normalized_engine,
                )

            now = self._utc_now()
            payload["session_version"] = SESSION_VERSION
            payload["scope_key"] = self._scope_key(normalized_scope_kind, normalized_scope_id, normalized_engine)
            payload["scope"] = {"kind": normalized_scope_kind, "id": normalized_scope_id}
            payload["engine"] = normalized_engine
            payload["status"] = "reset"
            payload["updated_at"] = now
            payload["resume"] = None
            payload["handoff"] = {
                "summary": "",
                "updated_at": now,
                "run_id": None,
                "run_path": None,
                "note": note,
            }
            payload["reset_count"] = max(0, int(payload.get("reset_count", 0))) + 1

            events = payload.get("events") if isinstance(payload.get("events"), list) else []
            event: dict[str, Any] = {"at": now, "event": "reset"}
            if note:
                event["note"] = str(note)
            events.append(event)
            payload["events"] = events

            self._write_payload(session_path, payload)
            relative_root = self.repo_root if self.repo_root is not None else self.root
            payload["session_file"] = session_path.relative_to(relative_root).as_posix()
            return payload
        finally:
            self._release_lock(lock_path)

    def rotate(
        self,
        *,
        scope_id: str,
        engine: str,
        scope_kind: str = "thread",
        note: str | None = None,
    ) -> dict[str, Any]:
        normalized_scope_kind = self._normalize_scope_kind(scope_kind)
        normalized_scope_id = self._normalize_scope_id(scope_id)
        normalized_engine = self._normalize_engine(engine)
        session_path = self._session_path(normalized_scope_kind, normalized_scope_id, normalized_engine)
        lock_path = self._acquire_lock(normalized_scope_kind, normalized_scope_id, normalized_engine)
        try:
            if session_path.is_file():
                payload = self._read_payload(session_path)
            else:
                payload = self._base_payload(
                    scope_kind=normalized_scope_kind,
                    scope_id=normalized_scope_id,
                    engine=normalized_engine,
                )

            now = self._utc_now()
            previous_id = payload.get("session_id")
            payload["session_id"] = self._session_id()
            payload["session_version"] = SESSION_VERSION
            payload["scope_key"] = self._scope_key(normalized_scope_kind, normalized_scope_id, normalized_engine)
            payload["scope"] = {"kind": normalized_scope_kind, "id": normalized_scope_id}
            payload["engine"] = normalized_engine
            payload["status"] = "reset"
            payload["updated_at"] = now
            payload["resume"] = None
            payload["handoff"] = {
                "summary": "",
                "updated_at": now,
                "run_id": None,
                "run_path": None,
                "note": note,
            }
            payload["rotation_count"] = max(0, int(payload.get("rotation_count", 0))) + 1

            events = payload.get("events") if isinstance(payload.get("events"), list) else []
            event: dict[str, Any] = {"at": now, "event": "rotated"}
            if previous_id:
                event["previous_session_id"] = previous_id
            if note:
                event["note"] = str(note)
            events.append(event)
            payload["events"] = events

            self._write_payload(session_path, payload)
            relative_root = self.repo_root if self.repo_root is not None else self.root
            payload["session_file"] = session_path.relative_to(relative_root).as_posix()
            return payload
        finally:
            self._release_lock(lock_path)
