from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

import yaml


DEFAULT_DENY_GLOBS = (
    ".git",
    ".git/**",
    "**/.git",
    "**/.git/**",
    ".env",
    "**/.env",
    ".env.*",
    "**/.env.*",
    "state/operator_sessions",
    "state/operator_sessions/**",
    "state/approvals",
    "state/approvals/**",
    "**/*.pem",
    "**/*.key",
    "**/*.p12",
)


class FileExchangeError(ValueError):
    def __init__(self, message: str, code: str = "FILE_EXCHANGE_INVALID") -> None:
        super().__init__(message)
        self.code = code


def _read_yaml_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return {}
    return dict(loaded)


def load_file_exchange_policy(project_root: Path, repo_root: Path | None = None) -> dict[str, Any]:
    from _system.engine.operator_transport import load_transport_backend  # noqa: PLC0415

    resolved_project_root = Path(project_root).resolve()
    resolved_repo_root = Path(repo_root).resolve() if repo_root is not None else resolved_project_root.parents[1]
    loaded = load_transport_backend(resolved_repo_root, resolved_project_root, provider_id="file_exchange")
    raw_globs = loaded.config.get("deny_globs") if isinstance(loaded.config, dict) else []
    deny_globs = [str(item).strip() for item in raw_globs if str(item).strip()] if isinstance(raw_globs, list) else []
    merged = list(DEFAULT_DENY_GLOBS)
    for pattern in deny_globs:
        if pattern not in merged:
            merged.append(pattern)
    return {
        "deny_globs": tuple(merged),
    }


def _normalize_relative_path(relative_path: str | Path) -> PurePosixPath:
    raw_value = str(relative_path or "").strip().replace("\\", "/")
    if not raw_value:
        raise FileExchangeError("Relative path is required")
    if raw_value.startswith("/"):
        raise FileExchangeError("Absolute paths are not allowed for file exchange")

    normalized = PurePosixPath(raw_value)
    parts: list[str] = []
    for part in normalized.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            raise FileExchangeError("Relative path cannot escape the target root")
        parts.append(part)
    if not parts:
        raise FileExchangeError("Relative path must point inside the target root")
    return PurePosixPath(*parts)


def _is_within_root(root: Path, candidate: Path) -> bool:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()
    return resolved_candidate == resolved_root or resolved_root in resolved_candidate.parents


def _matches_glob(relative_path: PurePosixPath, pattern: str) -> bool:
    normalized_pattern = str(pattern or "").strip().replace("\\", "/")
    if not normalized_pattern:
        return False
    relative_text = relative_path.as_posix()
    if relative_text == normalized_pattern:
        return True
    if relative_path.match(normalized_pattern):
        return True
    if normalized_pattern.endswith("/**"):
        prefix = normalized_pattern[:-3].rstrip("/")
        return relative_text == prefix or relative_text.startswith(f"{prefix}/")
    return False


def _ensure_not_denied(relative_path: PurePosixPath, deny_globs: Sequence[str]) -> None:
    for pattern in deny_globs:
        if _matches_glob(relative_path, pattern):
            raise FileExchangeError(
                f"Relative path '{relative_path.as_posix()}' is blocked by deny-glob '{pattern}'",
                code="FILE_EXCHANGE_DENIED",
            )


def _resolve_target_path(target_root: Path, relative_path: PurePosixPath) -> Path:
    resolved_root = target_root.resolve()
    candidate = (resolved_root / Path(*relative_path.parts)).resolve()
    if not _is_within_root(resolved_root, candidate):
        raise FileExchangeError("Resolved path escapes the target root")
    return candidate


def _write_bytes_atomic(target_path: Path, payload: bytes) -> int:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target_path.name}.tmp-", dir=str(target_path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target_path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
    return len(payload)


def put_file(target_root: Path, relative_path: str | Path, source_file: Path, *, deny_globs: Sequence[str]) -> dict[str, Any]:
    normalized_relative = _normalize_relative_path(relative_path)
    _ensure_not_denied(normalized_relative, deny_globs)

    source_path = Path(source_file).expanduser().resolve()
    if not source_path.is_file():
        raise FileExchangeError(f"source-file not found: {source_file}")

    target_path = _resolve_target_path(target_root, normalized_relative)
    if target_path.exists() and target_path.is_dir():
        raise FileExchangeError(f"Target path is a directory: {normalized_relative.as_posix()}")

    payload = source_path.read_bytes()
    bytes_written = _write_bytes_atomic(target_path, payload)
    return {
        "operation": "file_put",
        "relative_path": normalized_relative.as_posix(),
        "target_path": str(target_path),
        "bytes_written": bytes_written,
        "atomic": True,
    }


def _zip_directory(source_dir: Path, output_file: Path, *, deny_globs: Sequence[str], target_root: Path) -> dict[str, Any]:
    source_root = source_dir.resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    fd, temp_name = tempfile.mkstemp(prefix=f".{output_file.name}.tmp-", dir=str(output_file.parent))
    os.close(fd)
    temp_path = Path(temp_name)
    added_entries = 0
    skipped_entries = 0
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for current_root, dir_names, file_names in os.walk(source_root, followlinks=False):
                current_path = Path(current_root)
                relative_root = current_path.relative_to(source_root)

                filtered_dirs: list[str] = []
                for dir_name in list(dir_names):
                    candidate_dir = current_path / dir_name
                    relative_candidate = (relative_root / dir_name).as_posix()
                    relative_path = PurePosixPath(relative_candidate)
                    if candidate_dir.is_symlink():
                        skipped_entries += 1
                        continue
                    try:
                        _ensure_not_denied(relative_path, deny_globs)
                    except FileExchangeError:
                        skipped_entries += 1
                        continue
                    filtered_dirs.append(dir_name)
                dir_names[:] = filtered_dirs

                for file_name in file_names:
                    candidate_file = current_path / file_name
                    relative_candidate = (relative_root / file_name).as_posix()
                    relative_path = PurePosixPath(relative_candidate)
                    if candidate_file.is_symlink():
                        skipped_entries += 1
                        continue
                    if not _is_within_root(source_root, candidate_file) or not _is_within_root(target_root, candidate_file):
                        skipped_entries += 1
                        continue
                    try:
                        _ensure_not_denied(relative_path, deny_globs)
                    except FileExchangeError:
                        skipped_entries += 1
                        continue
                    archive.write(candidate_file, arcname=relative_path.as_posix())
                    added_entries += 1
        os.replace(temp_path, output_file)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass

    return {
        "operation": "file_fetch",
        "relative_path": source_dir.relative_to(target_root.resolve()).as_posix(),
        "source_path": str(source_root),
        "output_file": str(output_file),
        "archive": "zip",
        "bytes_written": output_file.stat().st_size if output_file.exists() else 0,
        "entries_written": added_entries,
        "entries_skipped": skipped_entries,
    }


def fetch_path(target_root: Path, relative_path: str | Path, output_file: Path, *, deny_globs: Sequence[str]) -> dict[str, Any]:
    normalized_relative = _normalize_relative_path(relative_path)
    _ensure_not_denied(normalized_relative, deny_globs)

    source_path = _resolve_target_path(target_root, normalized_relative)
    if not source_path.exists():
        raise FileExchangeError(f"Requested path not found: {normalized_relative.as_posix()}")
    if source_path.is_symlink():
        raise FileExchangeError(f"Symlink paths are not allowed: {normalized_relative.as_posix()}")

    destination = Path(output_file).expanduser().resolve()
    if source_path.is_dir():
        return _zip_directory(source_path, destination, deny_globs=deny_globs, target_root=target_root.resolve())

    payload = source_path.read_bytes()
    bytes_written = _write_bytes_atomic(destination, payload)
    return {
        "operation": "file_fetch",
        "relative_path": normalized_relative.as_posix(),
        "source_path": str(source_path),
        "output_file": str(destination),
        "archive": None,
        "bytes_written": bytes_written,
    }
