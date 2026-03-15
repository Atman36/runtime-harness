from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SESSION_VERSION = 1
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_POLL_INTERVAL_SECONDS = 0.05
VALID_SESSION_STATUSES = ("active", "reset")


def sessions_root_for_project(project_root: Path) -> Path:
    return Path(project_root) / "state" / "sessions"


class SessionStore:
    """Filesystem-backed session continuity store for agent/task scope."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.sessions_dir = self.root
        self.locks = self.root / "locks"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.locks.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _normalize_task_id(task_id: str) -> str:
        normalized = str(task_id or "").strip()
        if not normalized:
            raise ValueError("Task id is required")
        if "/" in normalized or "\\" in normalized:
            raise ValueError(f"Invalid task id: {normalized}")
        return normalized

    @staticmethod
    def _normalize_agent(agent: str) -> str:
        normalized = str(agent or "").strip()
        if not normalized:
            raise ValueError("Agent id is required")
        if "/" in normalized or "\\" in normalized:
            raise ValueError(f"Invalid agent id: {normalized}")
        return normalized

    @staticmethod
    def _session_id() -> str:
        return f"SESSION-{uuid4().hex[:12].upper()}"

    @staticmethod
    def _scope_key(agent: str, task_id: str) -> str:
        return f"{agent}::{task_id}"

    def _session_path(self, agent: str, task_id: str) -> Path:
        return self.sessions_dir / f"{agent}__{task_id}.json"

    def _lock_path(self, agent: str, task_id: str) -> Path:
        return self.locks / f"{agent}__{task_id}.lock"

    def _acquire_lock(self, agent: str, task_id: str) -> Path:
        lock_path = self._lock_path(agent, task_id)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                lock_path.mkdir(parents=False)
                return lock_path
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for session lock: {agent}/{task_id}")
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
            raise ValueError(f"Session payload must be a JSON object: {path}")
        return payload

    def _base_payload(
        self,
        *,
        agent: str,
        task_id: str,
        project: str | None = None,
        task_path: str | None = None,
    ) -> dict[str, Any]:
        now = self._utc_now()
        payload: dict[str, Any] = {
            "session_version": SESSION_VERSION,
            "session_id": self._session_id(),
            "scope_key": self._scope_key(agent, task_id),
            "scope": {
                "agent": agent,
                "task_id": task_id,
            },
            "project": project or "",
            "task_path": task_path or "",
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
        return payload

    def load_session(self, *, agent: str, task_id: str) -> dict[str, Any] | None:
        normalized_agent = self._normalize_agent(agent)
        normalized_task_id = self._normalize_task_id(task_id)
        path = self._session_path(normalized_agent, normalized_task_id)
        if not path.is_file():
            return None
        payload = self._read_payload(path)
        payload["session_file"] = path.relative_to(self.root).as_posix()
        return payload

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        for path in sorted(self.sessions_dir.glob("*.json"), key=lambda candidate: candidate.stat().st_mtime):
            try:
                payload = self._read_payload(path)
            except (json.JSONDecodeError, OSError, ValueError):
                continue
            payload["session_file"] = path.relative_to(self.root).as_posix()
            sessions.append(payload)
        return sessions

    def get_or_create(
        self,
        *,
        agent: str,
        task_id: str,
        project: str | None = None,
        task_path: str | None = None,
    ) -> dict[str, Any]:
        normalized_agent = self._normalize_agent(agent)
        normalized_task_id = self._normalize_task_id(task_id)
        session_path = self._session_path(normalized_agent, normalized_task_id)
        lock_path = self._acquire_lock(normalized_agent, normalized_task_id)
        try:
            if session_path.is_file():
                payload = self._read_payload(session_path)
            else:
                payload = self._base_payload(
                    agent=normalized_agent,
                    task_id=normalized_task_id,
                    project=project,
                    task_path=task_path,
                )
                self._write_payload(session_path, payload)
            payload["session_file"] = session_path.relative_to(self.root).as_posix()
            return payload
        finally:
            self._release_lock(lock_path)

    def update(
        self,
        *,
        agent: str,
        task_id: str,
        resume: dict[str, Any] | None = None,
        summary: str | None = None,
        note: str | None = None,
        run_id: str | None = None,
        run_path: str | None = None,
        project: str | None = None,
        task_path: str | None = None,
    ) -> dict[str, Any]:
        normalized_agent = self._normalize_agent(agent)
        normalized_task_id = self._normalize_task_id(task_id)
        session_path = self._session_path(normalized_agent, normalized_task_id)
        lock_path = self._acquire_lock(normalized_agent, normalized_task_id)
        try:
            if session_path.is_file():
                payload = self._read_payload(session_path)
            else:
                payload = self._base_payload(
                    agent=normalized_agent,
                    task_id=normalized_task_id,
                    project=project,
                    task_path=task_path,
                )

            if resume is not None and not isinstance(resume, dict):
                raise ValueError("Resume handle must be a JSON object")

            now = self._utc_now()
            payload["session_version"] = SESSION_VERSION
            payload["scope_key"] = self._scope_key(normalized_agent, normalized_task_id)
            payload["scope"] = {"agent": normalized_agent, "task_id": normalized_task_id}
            payload["project"] = project or payload.get("project") or ""
            payload["task_path"] = task_path or payload.get("task_path") or ""
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
            payload["session_file"] = session_path.relative_to(self.root).as_posix()
            return payload
        finally:
            self._release_lock(lock_path)

    def reset(
        self,
        *,
        agent: str,
        task_id: str,
        note: str | None = None,
        project: str | None = None,
        task_path: str | None = None,
    ) -> dict[str, Any]:
        normalized_agent = self._normalize_agent(agent)
        normalized_task_id = self._normalize_task_id(task_id)
        session_path = self._session_path(normalized_agent, normalized_task_id)
        lock_path = self._acquire_lock(normalized_agent, normalized_task_id)
        try:
            if session_path.is_file():
                payload = self._read_payload(session_path)
            else:
                payload = self._base_payload(
                    agent=normalized_agent,
                    task_id=normalized_task_id,
                    project=project,
                    task_path=task_path,
                )

            now = self._utc_now()
            payload["session_version"] = SESSION_VERSION
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
            payload["session_file"] = session_path.relative_to(self.root).as_posix()
            return payload
        finally:
            self._release_lock(lock_path)

    def rotate(
        self,
        *,
        agent: str,
        task_id: str,
        note: str | None = None,
        project: str | None = None,
        task_path: str | None = None,
    ) -> dict[str, Any]:
        normalized_agent = self._normalize_agent(agent)
        normalized_task_id = self._normalize_task_id(task_id)
        session_path = self._session_path(normalized_agent, normalized_task_id)
        lock_path = self._acquire_lock(normalized_agent, normalized_task_id)
        try:
            if session_path.is_file():
                payload = self._read_payload(session_path)
            else:
                payload = self._base_payload(
                    agent=normalized_agent,
                    task_id=normalized_task_id,
                    project=project,
                    task_path=task_path,
                )

            now = self._utc_now()
            previous_id = payload.get("session_id")
            payload["session_id"] = self._session_id()
            payload["session_version"] = SESSION_VERSION
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
            payload["session_file"] = session_path.relative_to(self.root).as_posix()
            return payload
        finally:
            self._release_lock(lock_path)
