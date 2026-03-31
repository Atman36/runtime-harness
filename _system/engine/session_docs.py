from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from _system.engine.file_exchange import FileExchangeError, fetch_path, put_file
from _system.engine.handoff_notes import validate_session_handoff_document


SESSION_DOCS_VERSION = 1
LOCK_TIMEOUT_SECONDS = 5.0
LOCK_POLL_INTERVAL_SECONDS = 0.05


def session_docs_root_for_project(project_root: Path) -> Path:
    return Path(project_root) / "state" / "session_docs"


class SessionDocsStore:
    """Filesystem-backed shared session files for one task scope."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.locks = self.root / "locks"
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
    def _normalize_text(value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    def _task_dir(self, task_id: str) -> Path:
        return self.root / self._normalize_task_id(task_id)

    def _docs_dir(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "files"

    def _manifest_path(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "manifest.json"

    def _lock_path(self, task_id: str) -> Path:
        return self.locks / f"{self._normalize_task_id(task_id)}.lock"

    def _acquire_lock(self, task_id: str) -> Path:
        lock_path = self._lock_path(task_id)
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                lock_path.mkdir(parents=False)
                return lock_path
            except FileExistsError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for session-doc lock: {task_id}")
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
            raise ValueError(f"Session-doc payload must be a JSON object: {path}")
        return payload

    def _base_manifest(self, *, task_id: str, project: str | None = None) -> dict[str, Any]:
        now = self._utc_now()
        return {
            "session_docs_version": SESSION_DOCS_VERSION,
            "task_id": task_id,
            "project": project or "",
            "created_at": now,
            "updated_at": now,
            "documents": [],
        }

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _serialize_manifest(self, manifest: dict[str, Any], manifest_path: Path) -> dict[str, Any]:
        payload = dict(manifest)
        payload["documents"] = sorted(
            [
                item
                for item in payload.get("documents", [])
                if isinstance(item, dict) and str(item.get("path") or "").strip()
            ],
            key=lambda item: str(item.get("path") or ""),
        )
        payload["manifest_file"] = manifest_path.relative_to(self.root.parent.parent).as_posix()
        payload["files_root"] = self._docs_dir(str(payload.get("task_id") or "")).relative_to(self.root.parent.parent).as_posix()
        payload["document_count"] = len(payload["documents"])
        return payload

    def load_manifest(self, *, task_id: str) -> dict[str, Any] | None:
        normalized_task_id = self._normalize_task_id(task_id)
        manifest_path = self._manifest_path(normalized_task_id)
        if not manifest_path.is_file():
            return None
        manifest = self._read_payload(manifest_path)
        return self._serialize_manifest(manifest, manifest_path)

    def list_documents(self, *, task_id: str) -> dict[str, Any] | None:
        return self.load_manifest(task_id=task_id)

    def put_document(
        self,
        *,
        task_id: str,
        relative_path: str,
        source_file: Path,
        project: str | None = None,
        author: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        normalized_task_id = self._normalize_task_id(task_id)
        lock_path = self._acquire_lock(normalized_task_id)
        try:
            manifest_path = self._manifest_path(normalized_task_id)
            if manifest_path.is_file():
                manifest = self._read_payload(manifest_path)
            else:
                manifest = self._base_manifest(task_id=normalized_task_id, project=project)

            docs_dir = self._docs_dir(normalized_task_id)
            validation = validate_session_handoff_document(relative_path, source_file)
            result = put_file(docs_dir, relative_path, source_file, deny_globs=())
            target_path = Path(str(result["target_path"]))
            now = self._utc_now()
            record = {
                "path": str(result["relative_path"]),
                "bytes": int(result["bytes_written"]),
                "sha256": self._sha256(target_path),
                "updated_at": now,
                "author": self._normalize_text(author),
                "note": self._normalize_text(note),
            }
            if isinstance(validation, dict):
                record.update(validation)

            manifest["session_docs_version"] = SESSION_DOCS_VERSION
            manifest["task_id"] = normalized_task_id
            manifest["project"] = project or str(manifest.get("project") or "")
            manifest["updated_at"] = now

            documents = manifest.get("documents") if isinstance(manifest.get("documents"), list) else []
            filtered = [
                item
                for item in documents
                if isinstance(item, dict) and str(item.get("path") or "").strip() != record["path"]
            ]
            filtered.append(record)
            manifest["documents"] = filtered

            self._write_payload(manifest_path, manifest)
            payload = self._serialize_manifest(manifest, manifest_path)
            payload["document"] = record
            payload.update(result)
            return payload
        finally:
            self._release_lock(lock_path)

    def fetch_document(self, *, task_id: str, relative_path: str, output_file: Path) -> dict[str, Any]:
        normalized_task_id = self._normalize_task_id(task_id)
        manifest = self.load_manifest(task_id=normalized_task_id)
        if manifest is None:
            raise FileNotFoundError(f"Session-doc manifest not found for task {normalized_task_id}")

        docs_dir = self._docs_dir(normalized_task_id)
        result = fetch_path(docs_dir, relative_path, output_file, deny_globs=())
        document = next(
            (item for item in manifest.get("documents", []) if str(item.get("path") or "").strip() == str(result["relative_path"])),
            None,
        )
        payload = dict(manifest)
        payload["document"] = document
        payload.update(result)
        return payload
