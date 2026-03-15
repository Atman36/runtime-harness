from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


CLAIM_VERSION = 1
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_POLL_INTERVAL_SECONDS = 0.05
VALID_CLAIM_STATUSES = ("claimed", "released", "blocked")


def claims_root_for_project(project_root: Path) -> Path:
    return Path(project_root) / "state" / "claims"


class TaskClaimStore:
    """Filesystem-backed task claim store with atomic claim/release semantics."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.claims_dir = self.root
        self.locks = self.root / "locks"
        self.claims_dir.mkdir(parents=True, exist_ok=True)
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
        return normalized

    def _claim_path(self, task_id: str) -> Path:
        return self.claims_dir / f"{task_id}.json"

    def _lock_path(self, task_id: str) -> Path:
        return self.locks / f"{task_id}.lock"

    def _acquire_lock(self, task_id: str) -> Path:
        lock_path = self._lock_path(task_id)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                lock_path.mkdir(parents=False)
                return lock_path
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for claim lock: {task_id}")
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
            raise ValueError(f"Claim payload must be a JSON object: {path}")
        return payload

    def _base_payload(self, *, task_id: str, project: str | None = None, task_path: str | None = None) -> dict[str, Any]:
        now = self._utc_now()
        payload: dict[str, Any] = {
            "claim_version": CLAIM_VERSION,
            "task_id": task_id,
            "project": project or "",
            "task_path": task_path or "",
            "status": "released",
            "owner": None,
            "created_at": now,
            "updated_at": now,
            "claimed_at": None,
            "released_at": None,
            "reason": None,
            "note": None,
            "events": [],
        }
        return payload

    def load_claim(self, task_id: str) -> dict[str, Any] | None:
        normalized_id = self._normalize_task_id(task_id)
        path = self._claim_path(normalized_id)
        if not path.is_file():
            return None
        payload = self._read_payload(path)
        payload["claim_file"] = path.relative_to(self.root).as_posix()
        return payload

    def list_claims(self) -> list[dict[str, Any]]:
        claims: list[dict[str, Any]] = []
        for path in sorted(self.claims_dir.glob("*.json"), key=lambda candidate: candidate.stat().st_mtime):
            try:
                payload = self._read_payload(path)
            except (json.JSONDecodeError, OSError, ValueError):
                continue
            payload["claim_file"] = path.relative_to(self.root).as_posix()
            claims.append(payload)
        return claims

    def claim(
        self,
        *,
        task_id: str,
        agent: str,
        reason: str | None = None,
        note: str | None = None,
        task_path: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        normalized_task_id = self._normalize_task_id(task_id)
        normalized_agent = self._normalize_agent(agent)
        claim_path = self._claim_path(normalized_task_id)
        lock_path = self._acquire_lock(normalized_task_id)
        try:
            if claim_path.is_file():
                payload = self._read_payload(claim_path)
            else:
                payload = self._base_payload(task_id=normalized_task_id, project=project, task_path=task_path)

            status = str(payload.get("status") or "released").strip()
            owner = payload.get("owner")
            if status == "claimed" and owner and str(owner).strip() != normalized_agent:
                return {
                    "status": "conflict",
                    "task_id": normalized_task_id,
                    "agent": normalized_agent,
                    "current_owner": owner,
                    "claim": payload,
                    "claim_file": claim_path.relative_to(self.root).as_posix(),
                }
            if status == "claimed" and owner and str(owner).strip() == normalized_agent:
                return {
                    "status": "already_claimed",
                    "task_id": normalized_task_id,
                    "agent": normalized_agent,
                    "claim": payload,
                    "claim_file": claim_path.relative_to(self.root).as_posix(),
                }

            now = self._utc_now()
            previous_owner = payload.get("owner")
            payload["claim_version"] = CLAIM_VERSION
            payload["task_id"] = normalized_task_id
            payload["project"] = project or payload.get("project") or ""
            payload["task_path"] = task_path or payload.get("task_path") or ""
            payload["status"] = "claimed"
            payload["owner"] = normalized_agent
            payload["updated_at"] = now
            payload["claimed_at"] = now
            payload["released_at"] = None
            payload["reason"] = reason
            payload["note"] = note

            events = payload.get("events") if isinstance(payload.get("events"), list) else []
            event: dict[str, Any] = {
                "at": now,
                "event": "claimed",
                "status": "claimed",
                "owner": normalized_agent,
            }
            if previous_owner and str(previous_owner).strip() != normalized_agent:
                event["previous_owner"] = previous_owner
            if reason:
                event["reason"] = str(reason)
            if note:
                event["note"] = str(note)
            events.append(event)
            payload["events"] = events

            self._write_payload(claim_path, payload)
            return {
                "status": "claimed",
                "task_id": normalized_task_id,
                "agent": normalized_agent,
                "claim": payload,
                "claim_file": claim_path.relative_to(self.root).as_posix(),
            }
        finally:
            self._release_lock(lock_path)

    def release(
        self,
        *,
        task_id: str,
        agent: str,
        status: str = "released",
        reason: str | None = None,
        note: str | None = None,
        task_path: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        normalized_task_id = self._normalize_task_id(task_id)
        normalized_agent = self._normalize_agent(agent)
        normalized_status = str(status or "released").strip()
        if normalized_status not in VALID_CLAIM_STATUSES or normalized_status == "claimed":
            raise ValueError(f"Unsupported release status: {status}")

        claim_path = self._claim_path(normalized_task_id)
        if not claim_path.is_file():
            return {
                "status": "not_found",
                "task_id": normalized_task_id,
                "agent": normalized_agent,
            }

        lock_path = self._acquire_lock(normalized_task_id)
        try:
            payload = self._read_payload(claim_path)
            owner = payload.get("owner")
            if owner and str(owner).strip() != normalized_agent:
                return {
                    "status": "conflict",
                    "task_id": normalized_task_id,
                    "agent": normalized_agent,
                    "current_owner": owner,
                    "claim": payload,
                    "claim_file": claim_path.relative_to(self.root).as_posix(),
                }

            now = self._utc_now()
            payload["claim_version"] = CLAIM_VERSION
            payload["task_id"] = normalized_task_id
            payload["project"] = project or payload.get("project") or ""
            payload["task_path"] = task_path or payload.get("task_path") or ""
            payload["status"] = normalized_status
            payload["owner"] = normalized_agent
            payload["updated_at"] = now
            payload["released_at"] = now
            payload["reason"] = reason
            payload["note"] = note

            events = payload.get("events") if isinstance(payload.get("events"), list) else []
            event: dict[str, Any] = {
                "at": now,
                "event": normalized_status,
                "status": normalized_status,
                "owner": normalized_agent,
            }
            if reason:
                event["reason"] = str(reason)
            if note:
                event["note"] = str(note)
            events.append(event)
            payload["events"] = events

            self._write_payload(claim_path, payload)
            return {
                "status": normalized_status,
                "task_id": normalized_task_id,
                "agent": normalized_agent,
                "claim": payload,
                "claim_file": claim_path.relative_to(self.root).as_posix(),
            }
        finally:
            self._release_lock(lock_path)
