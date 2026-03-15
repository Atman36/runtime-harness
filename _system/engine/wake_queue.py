from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


WAKE_VERSION = 1
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_POLL_INTERVAL_SECONDS = 0.05
VALID_WAKE_REASONS = ("timer", "assignment", "mention", "manual", "approval")


def wake_root_for_project(project_root: Path) -> Path:
    return Path(project_root) / "state" / "wakes"


class WakeQueue:
    """Filesystem-backed wake queue with deterministic coalescing per agent/task scope."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.pending = self.root / "pending"
        self.locks = self.root / "locks"
        for path in (self.pending, self.locks):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _normalize_scope_value(value: str, *, field: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"Wake scope missing {field}")
        return normalized

    @classmethod
    def _build_scope(cls, *, agent: str, task_id: str, run_id: str | None = None) -> dict[str, str]:
        scope = {
            "agent": cls._normalize_scope_value(agent, field="agent"),
            "task_id": cls._normalize_scope_value(task_id, field="task_id"),
        }
        if run_id:
            scope["run_id"] = str(run_id).strip()
        return scope

    @staticmethod
    def scope_key(*, agent: str, task_id: str) -> str:
        return f"{agent.strip()}::{task_id.strip()}"

    @classmethod
    def wake_id_for_scope(cls, *, agent: str, task_id: str) -> str:
        digest = hashlib.sha1(cls.scope_key(agent=agent, task_id=task_id).encode("utf-8")).hexdigest()[:12].upper()
        return f"WAKE-{digest}"

    def _pending_path(self, wake_id: str) -> Path:
        return self.pending / f"{wake_id}.json"

    def _lock_path(self, wake_id: str) -> Path:
        return self.locks / f"{wake_id}.lock"

    def _acquire_lock(self, wake_id: str) -> Path:
        lock_path = self._lock_path(wake_id)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                lock_path.mkdir(parents=False)
                return lock_path
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for wake lock: {wake_id}")
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
            raise ValueError(f"Wake payload must be a JSON object: {path}")
        return payload

    @staticmethod
    def _empty_reason_counts() -> dict[str, int]:
        return {reason: 0 for reason in VALID_WAKE_REASONS}

    @classmethod
    def _base_payload(cls, *, wake_id: str, agent: str, task_id: str, run_id: str | None = None) -> dict[str, Any]:
        now = cls._utc_now()
        return {
            "wake_version": WAKE_VERSION,
            "wake_id": wake_id,
            "scope_key": cls.scope_key(agent=agent, task_id=task_id),
            "scope": cls._build_scope(agent=agent, task_id=task_id, run_id=run_id),
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "last_reason": None,
            "coalesced_count": 0,
            "reason_counts": cls._empty_reason_counts(),
            "events": [],
        }

    @staticmethod
    def summarize(payload: dict[str, Any], *, queue_root: Path | None = None, wake_path: Path | None = None) -> dict[str, Any]:
        summary = {
            "wake_id": payload.get("wake_id"),
            "scope": payload.get("scope") if isinstance(payload.get("scope"), dict) else {},
            "scope_key": payload.get("scope_key"),
            "status": payload.get("status"),
            "updated_at": payload.get("updated_at"),
            "last_reason": payload.get("last_reason"),
            "coalesced_count": int(payload.get("coalesced_count") or 0),
            "reason_counts": payload.get("reason_counts") if isinstance(payload.get("reason_counts"), dict) else {},
        }
        if queue_root is not None and wake_path is not None:
            try:
                summary["wake_file"] = wake_path.relative_to(queue_root).as_posix()
            except ValueError:
                summary["wake_file"] = str(wake_path)
        return summary

    def enqueue(
        self,
        *,
        agent: str,
        task_id: str,
        reason: str,
        run_id: str | None = None,
        source: str | None = None,
        note: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_reason = str(reason or "").strip().lower()
        if normalized_reason not in VALID_WAKE_REASONS:
            raise ValueError(f"Unsupported wake reason: {reason!r}")

        normalized_agent = self._normalize_scope_value(agent, field="agent")
        normalized_task_id = self._normalize_scope_value(task_id, field="task_id")
        normalized_run_id = str(run_id or "").strip() or None
        wake_id = self.wake_id_for_scope(agent=normalized_agent, task_id=normalized_task_id)
        wake_path = self._pending_path(wake_id)
        lock_path = self._acquire_lock(wake_id)
        try:
            if wake_path.is_file():
                payload = self._read_payload(wake_path)
                status = "coalesced"
            else:
                payload = self._base_payload(
                    wake_id=wake_id,
                    agent=normalized_agent,
                    task_id=normalized_task_id,
                    run_id=normalized_run_id,
                )
                status = "queued"

            payload["status"] = "pending"
            payload["updated_at"] = self._utc_now()
            payload["last_reason"] = normalized_reason

            scope = payload.get("scope") if isinstance(payload.get("scope"), dict) else {}
            scope["agent"] = normalized_agent
            scope["task_id"] = normalized_task_id
            if normalized_run_id:
                scope["run_id"] = normalized_run_id
            payload["scope"] = scope

            reason_counts = payload.get("reason_counts") if isinstance(payload.get("reason_counts"), dict) else {}
            merged_reason_counts = self._empty_reason_counts()
            for key, value in reason_counts.items():
                if key in merged_reason_counts:
                    try:
                        merged_reason_counts[key] = max(0, int(value))
                    except (TypeError, ValueError):
                        merged_reason_counts[key] = 0
            merged_reason_counts[normalized_reason] += 1
            payload["reason_counts"] = merged_reason_counts

            try:
                coalesced_count = max(0, int(payload.get("coalesced_count") or 0))
            except (TypeError, ValueError):
                coalesced_count = 0
            payload["coalesced_count"] = coalesced_count + 1

            event = {
                "at": payload["updated_at"],
                "reason": normalized_reason,
            }
            if normalized_run_id:
                event["run_id"] = normalized_run_id
            if source:
                event["source"] = str(source)
            if note:
                event["note"] = str(note)
            if context:
                event["context"] = context

            events = payload.get("events") if isinstance(payload.get("events"), list) else []
            events.append(event)
            payload["events"] = events

            self._write_payload(wake_path, payload)
            return {
                "status": status,
                "wake_id": wake_id,
                "wake_path": wake_path.relative_to(self.root).as_posix(),
                "scope": payload["scope"],
                "reason": normalized_reason,
                "coalesced_count": payload["coalesced_count"],
                "reason_counts": payload["reason_counts"],
            }
        finally:
            self._release_lock(lock_path)

    def list_pending(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.pending.glob("*.json"), key=lambda candidate: candidate.stat().st_mtime):
            try:
                payload = self._read_payload(path)
            except (json.JSONDecodeError, OSError, ValueError):
                continue
            payload["wake_file"] = path.relative_to(self.root).as_posix()
            items.append(payload)
        return items

    def snapshot(self, *, limit: int = 20) -> dict[str, Any]:
        pending = self.list_pending()
        reason_counts = self._empty_reason_counts()
        coalesced_events = 0
        for payload in pending:
            counts = payload.get("reason_counts") if isinstance(payload.get("reason_counts"), dict) else {}
            for reason in VALID_WAKE_REASONS:
                try:
                    reason_counts[reason] += max(0, int(counts.get(reason, 0)))
                except (TypeError, ValueError):
                    continue
            try:
                coalesced_events += max(0, int(payload.get("coalesced_count") or 0))
            except (TypeError, ValueError):
                continue

        return {
            "wake_version": WAKE_VERSION,
            "counts": {
                "pending": len(pending),
                "coalesced_events": coalesced_events,
                "reason_counts": reason_counts,
            },
            "pending": [
                self.summarize(payload, queue_root=self.root, wake_path=self.root / payload["wake_file"])
                for payload in pending[: max(1, int(limit))]
            ],
        }
