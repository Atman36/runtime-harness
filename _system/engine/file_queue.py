from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class QueueEmpty(Exception):
    pass


class DuplicateJobError(ValueError):
    pass


@dataclass
class ClaimedJob:
    job_id: str
    path: Path


class FileQueue:
    """Small filesystem-backed queue with atomic state transitions."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.pending = self.root / "pending"
        self.running = self.root / "running"
        self.done = self.root / "done"
        self.failed = self.root / "failed"
        self.awaiting_approval = self.root / "awaiting_approval"
        for path in (self.pending, self.running, self.done, self.failed, self.awaiting_approval):
            path.mkdir(parents=True, exist_ok=True)

    def _job_path(self, folder: Path, job_id: str) -> Path:
        return folder / f"{job_id}.json"

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
        if state == "pending":
            return self.pending
        if state == "awaiting_approval":
            return self.awaiting_approval
        raise ValueError(f"Unsupported queue state for enqueue: {state}")

    def _job_exists_anywhere(self, job_id: str) -> bool:
        for folder in (self.pending, self.running, self.done, self.failed, self.awaiting_approval):
            if self._find_job_file(folder, job_id) is not None:
                return True
        return False

    def _job_id_from_path(self, path: Path) -> str:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return path.stem.split(".", 1)[0]

        value = str(payload.get("job_id") or payload.get("id") or "").strip()
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

    def enqueue(self, job_obj: dict[str, Any], *, state: str = "pending") -> str:
        job_id = str(job_obj.get("job_id") or job_obj.get("id") or "").strip()
        if not job_id:
            raise ValueError("Job object missing job_id")
        if self._job_exists_anywhere(job_id):
            raise DuplicateJobError(f"Job with job_id='{job_id}' already exists")

        target_dir = self._resolve_enqueue_dir(state)
        tmp_path = target_dir / f".{job_id}.{int(time.time() * 1000)}.tmp"
        final_path = self._job_path(target_dir, job_id)
        tmp_path.write_text(json.dumps(job_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp_path, final_path)
        return job_id

    def claim(self) -> ClaimedJob:
        for path in sorted(self.pending.glob("*.json"), key=lambda candidate: candidate.stat().st_mtime):
            target = self.running / path.name
            try:
                os.replace(path, target)
            except (FileNotFoundError, PermissionError):
                continue
            return ClaimedJob(job_id=self._job_id_from_path(target), path=target)
        raise QueueEmpty()

    def read_claimed(self, claimed: ClaimedJob) -> dict[str, Any]:
        return json.loads(claimed.path.read_text(encoding="utf-8"))

    def ack(self, claimed: ClaimedJob) -> Path:
        return self._move_to_dir_no_overwrite(claimed.path, self.done)

    def fail(self, claimed: ClaimedJob) -> Path:
        return self._move_to_dir_no_overwrite(claimed.path, self.failed)

    def await_approval(self, claimed: ClaimedJob) -> Path:
        return self._move_to_dir_no_overwrite(claimed.path, self.awaiting_approval)

    def requeue(self, claimed: ClaimedJob) -> Path:
        return self._move_to_dir_no_overwrite(claimed.path, self.pending)

    def reclaim_stale_running(self, stale_after_sec: int) -> int:
        reclaimed = 0
        now = time.time()
        for path in sorted(self.running.glob("*.json"), key=lambda candidate: candidate.stat().st_mtime):
            try:
                age = now - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age < stale_after_sec:
                continue
            try:
                self._move_to_dir_no_overwrite(path, self.pending)
            except FileNotFoundError:
                continue
            reclaimed += 1
        return reclaimed

    def approve(self, job_id: str) -> bool:
        src = self._find_job_file(self.awaiting_approval, job_id)
        if src is None:
            return False
        self._move_to_dir_no_overwrite(src, self.pending)
        return True

    def unlock(self, job_id: str) -> bool:
        src = self._find_job_file(self.running, job_id)
        if src is None:
            return False
        self._move_to_dir_no_overwrite(src, self.failed)
        return True

    def queue_state(self, job_id: str) -> str | None:
        for state, folder in (
            ("pending", self.pending),
            ("running", self.running),
            ("done", self.done),
            ("failed", self.failed),
            ("awaiting_approval", self.awaiting_approval),
        ):
            if self._find_job_file(folder, job_id) is not None:
                return state
        return None

    def find_job(self, job_id: str) -> Path | None:
        for folder in (self.pending, self.running, self.done, self.failed, self.awaiting_approval):
            match = self._find_job_file(folder, job_id)
            if match is not None:
                return match
        return None
