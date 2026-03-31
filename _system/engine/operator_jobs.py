from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


OPERATOR_JOB_VERSION = 1
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_POLL_INTERVAL_SECONDS = 0.05
VALID_OPERATOR_JOB_STATUSES = ("queued", "running", "completed", "failed", "cancelled")


def operator_jobs_root_for_project(project_root: Path) -> Path:
    return Path(project_root) / "state" / "operator_jobs"


class OperatorJobStore:
    """Filesystem-backed secondary index for operator-visible job progress."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.jobs_dir = self.root
        self.locks = self.root / "locks"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.locks.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _normalize_job_id(job_id: str) -> str:
        normalized = str(job_id or "").strip()
        if not normalized:
            raise ValueError("Job id is required")
        if "/" in normalized or "\\" in normalized:
            raise ValueError(f"Invalid job id: {normalized}")
        return normalized

    def _job_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _lock_path(self, job_id: str) -> Path:
        return self.locks / f"{job_id}.lock"

    def _acquire_lock(self, job_id: str) -> Path:
        lock_path = self._lock_path(job_id)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                lock_path.mkdir(parents=False)
                return lock_path
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for operator job lock: {job_id}")
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
            raise ValueError(f"Operator job payload must be a JSON object: {path}")
        return payload

    @staticmethod
    def _normalize_status(status: str | None, *, default: str) -> str:
        normalized = str(status or "").strip().lower() or default
        if normalized not in VALID_OPERATOR_JOB_STATUSES:
            raise ValueError(f"Invalid operator job status: {status!r}")
        return normalized

    @staticmethod
    def _normalize_string(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    def _base_payload(self, *, job_id: str, source: str | None = None, run_id: str | None = None) -> dict[str, Any]:
        now = self._utc_now()
        resolved_run_id = self._normalize_optional_string(run_id) or job_id
        return {
            "operator_job_version": OPERATOR_JOB_VERSION,
            "job_id": job_id,
            "run_id": resolved_run_id,
            "run_path": "",
            "task_id": "",
            "task_title": "",
            "source": self._normalize_string(source) or "manual",
            "status": "queued",
            "phase": "queued",
            "queue_state": "pending",
            "result_status": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
            "summary": "",
            "completion_summary": "",
            "log_preview": "",
            "log_path": "",
            "stream_path": "",
            "report_path": "",
            "session": {
                "handle": None,
                "thread_id": None,
                "session_id": None,
            },
            "turn_id": None,
            "note": None,
            "history": [
                {
                    "at": now,
                    "event": "created",
                    "status": "queued",
                    "phase": "queued",
                }
            ],
        }

    @staticmethod
    def summarize(payload: dict[str, Any], *, root: Path | None = None, path: Path | None = None) -> dict[str, Any]:
        summary = {
            "job_id": payload.get("job_id"),
            "run_id": payload.get("run_id"),
            "run_path": payload.get("run_path"),
            "task_id": payload.get("task_id"),
            "task_title": payload.get("task_title"),
            "source": payload.get("source"),
            "status": payload.get("status"),
            "phase": payload.get("phase"),
            "queue_state": payload.get("queue_state"),
            "result_status": payload.get("result_status"),
            "updated_at": payload.get("updated_at"),
            "started_at": payload.get("started_at"),
            "completed_at": payload.get("completed_at"),
            "summary": payload.get("summary"),
            "completion_summary": payload.get("completion_summary"),
            "log_preview": payload.get("log_preview"),
            "log_path": payload.get("log_path"),
            "stream_path": payload.get("stream_path"),
            "report_path": payload.get("report_path"),
            "session": payload.get("session") if isinstance(payload.get("session"), dict) else {},
            "turn_id": payload.get("turn_id"),
            "note": payload.get("note"),
        }
        if root is not None and path is not None:
            try:
                summary["job_file"] = path.relative_to(root).as_posix()
            except ValueError:
                summary["job_file"] = str(path)
        return summary

    def load_job(self, job_id: str) -> dict[str, Any] | None:
        normalized_job_id = self._normalize_job_id(job_id)
        path = self._job_path(normalized_job_id)
        if not path.is_file():
            return None
        payload = self._read_payload(path)
        payload["job_file"] = path.relative_to(self.root).as_posix()
        return payload

    def list_jobs(self, *, status: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        normalized_status = None
        if status is not None:
            normalized_status = self._normalize_status(status, default="queued")

        items: list[tuple[float, dict[str, Any], Path]] = []
        for path in self.jobs_dir.glob("*.json"):
            try:
                payload = self._read_payload(path)
                payload_status = self._normalize_status(str(payload.get("status") or ""), default="queued")
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if normalized_status is not None and payload_status != normalized_status:
                continue
            try:
                stamp = path.stat().st_mtime
            except OSError:
                stamp = 0.0
            items.append((stamp, payload, path))

        items.sort(key=lambda item: item[0], reverse=True)
        summaries = [self.summarize(payload, root=self.root, path=path) for _stamp, payload, path in items]
        if limit is None:
            return summaries
        return summaries[: max(0, int(limit))]

    def update(
        self,
        *,
        job_id: str,
        source: str | None = None,
        status: str | None = None,
        phase: str | None = None,
        run_id: str | None = None,
        run_path: str | None = None,
        task_id: str | None = None,
        task_title: str | None = None,
        queue_state: str | None = None,
        result_status: str | None = None,
        summary: str | None = None,
        completion_summary: str | None = None,
        log_preview: str | None = None,
        log_path: str | None = None,
        stream_path: str | None = None,
        report_path: str | None = None,
        session_handle: str | None = None,
        thread_id: str | None = None,
        session_id: str | None = None,
        turn_id: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        note: str | None = None,
        event: str | None = None,
    ) -> dict[str, Any]:
        normalized_job_id = self._normalize_job_id(job_id)
        job_path = self._job_path(normalized_job_id)
        lock_path = self._acquire_lock(normalized_job_id)
        try:
            if job_path.is_file():
                payload = self._read_payload(job_path)
            else:
                payload = self._base_payload(job_id=normalized_job_id, source=source, run_id=run_id)

            now = self._utc_now()
            resolved_status = self._normalize_status(status or payload.get("status"), default="queued")
            resolved_phase = self._normalize_string(phase) or self._normalize_string(payload.get("phase")) or resolved_status

            payload["operator_job_version"] = OPERATOR_JOB_VERSION
            payload["job_id"] = normalized_job_id
            payload["run_id"] = self._normalize_optional_string(run_id) or self._normalize_string(payload.get("run_id")) or normalized_job_id
            payload["run_path"] = self._normalize_string(run_path) or self._normalize_string(payload.get("run_path"))
            payload["task_id"] = self._normalize_string(task_id) or self._normalize_string(payload.get("task_id"))
            payload["task_title"] = self._normalize_string(task_title) or self._normalize_string(payload.get("task_title"))
            payload["source"] = self._normalize_string(source) or self._normalize_string(payload.get("source")) or "manual"
            payload["status"] = resolved_status
            payload["phase"] = resolved_phase
            payload["queue_state"] = self._normalize_optional_string(queue_state) or payload.get("queue_state")
            payload["result_status"] = self._normalize_optional_string(result_status) or payload.get("result_status")
            payload["updated_at"] = now

            if started_at is not None:
                payload["started_at"] = self._normalize_optional_string(started_at)
            elif resolved_status == "running" and not payload.get("started_at"):
                payload["started_at"] = now

            if completed_at is not None:
                payload["completed_at"] = self._normalize_optional_string(completed_at)
            elif resolved_status in {"completed", "failed", "cancelled"}:
                payload["completed_at"] = self._normalize_optional_string(payload.get("completed_at")) or now

            if summary is not None:
                payload["summary"] = str(summary)
            if completion_summary is not None:
                payload["completion_summary"] = str(completion_summary)
            elif resolved_status == "completed" and payload.get("summary"):
                payload["completion_summary"] = payload.get("summary")
            if log_preview is not None:
                payload["log_preview"] = str(log_preview)
            if log_path is not None:
                payload["log_path"] = self._normalize_string(log_path)
            if stream_path is not None:
                payload["stream_path"] = self._normalize_string(stream_path)
            if report_path is not None:
                payload["report_path"] = self._normalize_string(report_path)

            session = payload.get("session") if isinstance(payload.get("session"), dict) else {}
            if session_handle is not None:
                session["handle"] = self._normalize_optional_string(session_handle)
            if thread_id is not None:
                session["thread_id"] = self._normalize_optional_string(thread_id)
            if session_id is not None:
                session["session_id"] = self._normalize_optional_string(session_id)
            payload["session"] = {
                "handle": session.get("handle"),
                "thread_id": session.get("thread_id"),
                "session_id": session.get("session_id"),
            }
            if turn_id is not None:
                payload["turn_id"] = self._normalize_optional_string(turn_id)
            if note is not None:
                payload["note"] = str(note)

            history = payload.get("history") if isinstance(payload.get("history"), list) else []
            history.append(
                {
                    "at": now,
                    "event": self._normalize_string(event) or "updated",
                    "status": resolved_status,
                    "phase": resolved_phase,
                    "queue_state": payload.get("queue_state"),
                    "result_status": payload.get("result_status"),
                    "note": payload.get("note"),
                }
            )
            payload["history"] = history

            self._write_payload(job_path, payload)
            payload["job_file"] = job_path.relative_to(self.root).as_posix()
            return payload
        finally:
            self._release_lock(lock_path)
