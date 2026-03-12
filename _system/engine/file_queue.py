from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


QUEUE_VERSION = 2
DEFAULT_MAX_ATTEMPTS = 3
STATE_ORDER = (
    "pending",
    "running",
    "awaiting_approval",
    "done",
    "failed",
    "dead_letter",
)


class QueueEmpty(Exception):
    pass


class DuplicateJobError(ValueError):
    pass


@dataclass
class ClaimedJob:
    job_id: str
    path: Path
    lease_id: str | None = None
    worker_id: str | None = None
    attempt_count: int = 0
    max_attempts: int = DEFAULT_MAX_ATTEMPTS


class FileQueue:
    """Filesystem-backed queue with atomic state transitions and soft leases."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.pending = self.root / "pending"
        self.running = self.root / "running"
        self.done = self.root / "done"
        self.failed = self.root / "failed"
        self.awaiting_approval = self.root / "awaiting_approval"
        self.dead_letter = self.root / "dead_letter"
        for path in (
            self.pending,
            self.running,
            self.done,
            self.failed,
            self.awaiting_approval,
            self.dead_letter,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    @classmethod
    def _expires_at(cls, lease_seconds: int) -> str:
        expires = datetime.now(timezone.utc) + timedelta(seconds=max(1, int(lease_seconds)))
        return expires.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _job_path(self, folder: Path, job_id: str) -> Path:
        return folder / f"{job_id}.json"

    def _state_dir(self, state: str) -> Path:
        mapping = {
            "pending": self.pending,
            "running": self.running,
            "done": self.done,
            "failed": self.failed,
            "awaiting_approval": self.awaiting_approval,
            "dead_letter": self.dead_letter,
        }
        try:
            return mapping[state]
        except KeyError as exc:
            raise ValueError(f"Unsupported queue state: {state}") from exc

    def _find_job_files(self, folder: Path, job_id: str) -> list[Path]:
        files: list[Path] = []
        exact = self._job_path(folder, job_id)
        if exact.exists():
            files.append(exact)
        files.extend(folder.glob(f"{job_id}.*.json"))
        return sorted(files, key=lambda path: path.stat().st_mtime)

    def _find_job_file(self, folder: Path, job_id: str) -> Path | None:
        matches = self._find_job_files(folder, job_id)
        return matches[0] if matches else None

    def _resolve_enqueue_dir(self, state: str) -> Path:
        if state not in {"pending", "awaiting_approval"}:
            raise ValueError(f"Unsupported queue state for enqueue: {state}")
        return self._state_dir(state)

    def _job_exists_anywhere(self, job_id: str) -> bool:
        for state in STATE_ORDER:
            if self._find_job_file(self._state_dir(state), job_id) is not None:
                return True
        return False

    def _job_id_from_path(self, path: Path) -> str:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return path.stem.split(".", 1)[0]

        value = str(payload.get("job_id") or payload.get("run_id") or payload.get("id") or "").strip()
        return value or path.stem.split(".", 1)[0]

    def _move_to_dir_no_overwrite(self, src: Path, target_dir: Path) -> Path:
        target = target_dir / src.name
        if not target.exists():
            os.replace(src, target)
            return target

        while True:
            alt = target_dir / f"{src.stem}.{time.time_ns()}.json"
            if not alt.exists():
                os.replace(src, alt)
                return alt

    def _read_payload(self, path: Path, *, state_hint: str | None = None) -> dict[str, Any]:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            loaded = {"job_id": self._job_id_from_path(path)}
        if not isinstance(loaded, dict):
            loaded = {"job_id": self._job_id_from_path(path)}
        return self._normalize_payload(loaded, state=state_hint)

    def _write_payload(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
        try:
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(tmp_path, path)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def _normalize_payload(self, job_obj: dict[str, Any], *, state: str | None = None) -> dict[str, Any]:
        payload = copy.deepcopy(job_obj)
        job_id = str(payload.get("job_id") or payload.get("run_id") or payload.get("id") or "").strip()
        if not job_id:
            raise ValueError("Job object missing job_id")
        payload["job_id"] = job_id
        payload.setdefault("queue_version", QUEUE_VERSION)

        queue = payload.get("queue") if isinstance(payload.get("queue"), dict) else {}
        history = queue.get("history") if isinstance(queue.get("history"), list) else []

        def as_int(value: Any, default: int) -> int:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                return default

        normalized_queue = {
            "state": state or str(queue.get("state") or "pending"),
            "enqueued_at": queue.get("enqueued_at") or payload.get("created_at") or self._utc_now(),
            "updated_at": queue.get("updated_at") or payload.get("created_at") or self._utc_now(),
            "attempt_count": as_int(queue.get("attempt_count"), 0),
            "max_attempts": max(1, as_int(queue.get("max_attempts"), DEFAULT_MAX_ATTEMPTS) or DEFAULT_MAX_ATTEMPTS),
            "worker_id": queue.get("worker_id"),
            "last_worker_id": queue.get("last_worker_id"),
            "lease_id": queue.get("lease_id"),
            "last_claimed_at": queue.get("last_claimed_at"),
            "lease_heartbeat_at": queue.get("lease_heartbeat_at"),
            "lease_expires_at": queue.get("lease_expires_at"),
            "last_result_status": queue.get("last_result_status"),
            "last_exit_code": queue.get("last_exit_code"),
            "last_error": queue.get("last_error"),
            "completed_at": queue.get("completed_at"),
            "history": [entry for entry in history if isinstance(entry, dict)],
        }
        payload["queue"] = normalized_queue
        return payload

    def _append_history(self, payload: dict[str, Any], *, event: str, from_state: str | None, to_state: str, **extra: Any) -> None:
        entry = {
            "at": self._utc_now(),
            "event": event,
            "from": from_state,
            "to": to_state,
        }
        for key, value in extra.items():
            if value is not None:
                entry[key] = value
        payload["queue"]["history"].append(entry)

    def _transition_path(
        self,
        path: Path,
        state: str,
        *,
        event: str,
        exit_code: int | None = None,
        result_status: str | None = None,
        error: str | None = None,
        clear_lease: bool = True,
    ) -> Path:
        payload = self._read_payload(path)
        from_state = str(payload["queue"].get("state") or "") or None
        queue = payload["queue"]
        queue["state"] = state
        queue["updated_at"] = self._utc_now()
        if queue.get("worker_id") and not queue.get("last_worker_id"):
            queue["last_worker_id"] = queue.get("worker_id")
        if clear_lease:
            if queue.get("worker_id"):
                queue["last_worker_id"] = queue.get("worker_id")
            queue["worker_id"] = None
            queue["lease_id"] = None
            queue["lease_heartbeat_at"] = None
            queue["lease_expires_at"] = None
        if result_status is not None:
            queue["last_result_status"] = result_status
        if exit_code is not None:
            queue["last_exit_code"] = exit_code
        if error is not None:
            queue["last_error"] = error
        if state in {"done", "failed", "dead_letter"}:
            queue["completed_at"] = self._utc_now()
        self._append_history(payload, event=event, from_state=from_state, to_state=state, exit_code=exit_code, error=error)
        self._write_payload(path, payload)
        return self._move_to_dir_no_overwrite(path, self._state_dir(state))

    def enqueue(self, job_obj: dict[str, Any], *, state: str = "pending") -> str:
        payload = self._normalize_payload(job_obj, state=state)
        job_id = payload["job_id"]
        if self._job_exists_anywhere(job_id):
            raise DuplicateJobError(f"Job with job_id='{job_id}' already exists")

        target_dir = self._resolve_enqueue_dir(state)
        payload["queue"]["state"] = state
        payload["queue"]["updated_at"] = self._utc_now()
        self._append_history(payload, event="enqueued", from_state=None, to_state=state)

        tmp_path = target_dir / f".{job_id}.{int(time.time() * 1000)}.tmp"
        final_path = self._job_path(target_dir, job_id)
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, final_path)
        return job_id

    def claim(self, *, worker_id: str | None = None, lease_seconds: int = 600, max_attempts: int | None = None) -> ClaimedJob:
        owner = worker_id or f"worker-{os.getpid()}"
        for path in sorted(self.pending.glob("*.json"), key=lambda candidate: candidate.stat().st_mtime):
            target = self.running / path.name
            try:
                os.replace(path, target)
            except (FileNotFoundError, PermissionError):
                continue

            payload = self._read_payload(target, state_hint="running")
            queue = payload["queue"]
            queue["state"] = "running"
            queue["updated_at"] = self._utc_now()
            queue["attempt_count"] = int(queue.get("attempt_count", 0)) + 1
            if max_attempts is not None:
                queue["max_attempts"] = max(1, int(max_attempts))
            lease_id = uuid4().hex
            queue["worker_id"] = owner
            queue["last_worker_id"] = owner
            queue["lease_id"] = lease_id
            queue["last_claimed_at"] = self._utc_now()
            queue["lease_heartbeat_at"] = self._utc_now()
            queue["lease_expires_at"] = self._expires_at(lease_seconds)
            self._append_history(
                payload,
                event="claimed",
                from_state="pending",
                to_state="running",
                worker_id=owner,
                attempt_count=queue["attempt_count"],
                lease_id=lease_id,
            )
            self._write_payload(target, payload)
            return ClaimedJob(
                job_id=payload["job_id"],
                path=target,
                lease_id=lease_id,
                worker_id=owner,
                attempt_count=queue["attempt_count"],
                max_attempts=queue["max_attempts"],
            )
        raise QueueEmpty()

    def read_claimed(self, claimed: ClaimedJob) -> dict[str, Any]:
        return self._read_payload(claimed.path, state_hint="running")

    def renew_lease(self, claimed: ClaimedJob, lease_seconds: int = 600) -> bool:
        if not claimed.path.exists():
            return False
        payload = self._read_payload(claimed.path, state_hint="running")
        queue = payload["queue"]
        if queue.get("state") != "running":
            return False
        if claimed.lease_id and queue.get("lease_id") != claimed.lease_id:
            return False
        queue["updated_at"] = self._utc_now()
        queue["lease_heartbeat_at"] = self._utc_now()
        queue["lease_expires_at"] = self._expires_at(lease_seconds)
        self._append_history(payload, event="lease_renewed", from_state="running", to_state="running", lease_id=queue.get("lease_id"))
        self._write_payload(claimed.path, payload)
        return True

    def ack(self, claimed: ClaimedJob, *, result_status: str | None = None, exit_code: int | None = None) -> Path:
        return self._transition_path(claimed.path, "done", event="acked", result_status=result_status, exit_code=exit_code)

    def fail(
        self,
        claimed: ClaimedJob,
        *,
        result_status: str | None = None,
        exit_code: int | None = None,
        error: str | None = None,
    ) -> Path:
        return self._transition_path(claimed.path, "failed", event="failed", result_status=result_status, exit_code=exit_code, error=error)

    def dead_letter(
        self,
        claimed: ClaimedJob,
        *,
        result_status: str | None = None,
        exit_code: int | None = None,
        error: str | None = None,
    ) -> Path:
        return self._transition_path(
            claimed.path,
            "dead_letter",
            event="dead_lettered",
            result_status=result_status,
            exit_code=exit_code,
            error=error,
        )

    def await_approval(self, claimed: ClaimedJob) -> Path:
        return self._transition_path(claimed.path, "awaiting_approval", event="awaiting_approval")

    def requeue(self, claimed: ClaimedJob) -> Path:
        return self._transition_path(claimed.path, "pending", event="requeued")

    @staticmethod
    def _is_stale(payload: dict[str, Any], path: Path, stale_after_sec: int, now: float) -> bool:
        lease_expires_at = payload.get("queue", {}).get("lease_expires_at")
        if isinstance(lease_expires_at, str) and lease_expires_at:
            try:
                expires = datetime.fromisoformat(lease_expires_at.replace("Z", "+00:00")).timestamp()
                return now >= expires
            except ValueError:
                pass
        try:
            age = now - path.stat().st_mtime
        except FileNotFoundError:
            return False
        return age >= stale_after_sec

    def reclaim_stale_running_details(self, stale_after_sec: int) -> list[dict[str, Any]]:
        reclaimed: list[dict[str, Any]] = []
        now = time.time()
        for path in sorted(self.running.glob("*.json"), key=lambda candidate: candidate.stat().st_mtime):
            try:
                payload = self._read_payload(path, state_hint="running")
            except FileNotFoundError:
                continue
            if not self._is_stale(payload, path, stale_after_sec, now):
                continue

            queue = payload["queue"]
            attempt_count = int(queue.get("attempt_count") or 0)
            max_attempts = max(1, int(queue.get("max_attempts") or DEFAULT_MAX_ATTEMPTS))
            target_state = "dead_letter" if attempt_count >= max_attempts else "pending"
            error = "lease expired"
            try:
                final_path = self._transition_path(path, target_state, event="reclaimed", error=error)
            except FileNotFoundError:
                continue
            reclaimed.append(
                {
                    "job_id": payload["job_id"],
                    "from_state": "running",
                    "to_state": target_state,
                    "attempt_count": attempt_count,
                    "max_attempts": max_attempts,
                    "path": final_path,
                }
            )
        return reclaimed

    def reclaim_stale_running(self, stale_after_sec: int) -> int:
        return len(self.reclaim_stale_running_details(stale_after_sec))

    def approve(self, job_id: str) -> bool:
        src = self._find_job_file(self.awaiting_approval, job_id)
        if src is None:
            return False
        self._transition_path(src, "pending", event="approved")
        return True

    def retry(self, job_id: str) -> bool:
        src = self._find_job_file(self.failed, job_id)
        if src is None:
            return False
        self._transition_path(src, "pending", event="retried", error=None)
        return True

    def unlock(self, job_id: str) -> bool:
        src = self._find_job_file(self.running, job_id)
        if src is None:
            return False
        self._transition_path(src, "failed", event="unlocked", error="manually unlocked")
        return True

    def queue_state(self, job_id: str) -> str | None:
        for state in STATE_ORDER:
            if self._find_job_file(self._state_dir(state), job_id) is not None:
                return state
        return None

    def find_job(self, job_id: str) -> Path | None:
        for state in STATE_ORDER:
            match = self._find_job_file(self._state_dir(state), job_id)
            if match is not None:
                return match
        return None

    def list_jobs(self, state: str) -> list[dict[str, Any]]:
        folder = self._state_dir(state)
        items: list[dict[str, Any]] = []
        for path in sorted(folder.glob("*.json"), key=lambda candidate: candidate.stat().st_mtime):
            try:
                payload = self._read_payload(path, state_hint=state)
            except FileNotFoundError:
                continue
            payload["queue_file"] = path.relative_to(self.root).as_posix()
            items.append(payload)
        return items

    def snapshot(self) -> dict[str, Any]:
        jobs = {state: self.list_jobs(state) for state in STATE_ORDER}
        counts = {state: len(items) for state, items in jobs.items()}
        return {
            "queue_version": QUEUE_VERSION,
            "counts": counts,
            "jobs": jobs,
        }
