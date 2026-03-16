from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from _system.engine.operator_transport import TransportConfigError, TransportDiagnostic


SUPPORTED_WORKSPACE_MODES = {"project_root", "shared_project", "git_worktree", "isolated_checkout"}


def _read_yaml_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return {}
    return dict(loaded)


class FileExchangeTransportBackend:
    def validate_config(self, config: dict[str, Any], *, backend_id: str) -> dict[str, Any]:
        if not isinstance(config, dict):
            raise TransportConfigError(
                f"Transport backend '{backend_id}' config must be an object",
                backend_id=backend_id,
                provider="file_exchange",
            )

        unknown_keys = sorted(set(config.keys()) - {"deny_globs"})
        if unknown_keys:
            raise TransportConfigError(
                f"Transport backend '{backend_id}' config contains unsupported keys: {', '.join(unknown_keys)}",
                backend_id=backend_id,
                provider="file_exchange",
                hint="Supported keys: deny_globs",
            )

        raw_globs = config.get("deny_globs") or []
        if not isinstance(raw_globs, list):
            raise TransportConfigError(
                f"Transport backend '{backend_id}' config.deny_globs must be a list",
                backend_id=backend_id,
                provider="file_exchange",
            )

        deny_globs: list[str] = []
        for index, item in enumerate(raw_globs):
            pattern = str(item or "").strip()
            if not pattern:
                raise TransportConfigError(
                    f"Transport backend '{backend_id}' config.deny_globs[{index}] must be a non-empty string",
                    backend_id=backend_id,
                    provider="file_exchange",
                )
            if pattern not in deny_globs:
                deny_globs.append(pattern)

        return {"deny_globs": deny_globs}

    def setup_checks(
        self,
        *,
        project_root: Path,
        backend_id: str,
        config: dict[str, Any],
    ) -> list[TransportDiagnostic]:
        del config
        project_state = _read_yaml_object(project_root / "state" / "project.yaml")
        execution = project_state.get("execution") if isinstance(project_state.get("execution"), dict) else {}
        workspace_mode = str(execution.get("workspace_mode") or "").strip() or "project_root"
        if workspace_mode not in SUPPORTED_WORKSPACE_MODES:
            return [
                TransportDiagnostic(
                    severity="error",
                    code="TRANSPORT_UNSUPPORTED_COMBINATION",
                    message=(
                        f"Transport backend '{backend_id}' does not support execution.workspace_mode "
                        f"'{workspace_mode}'"
                    ),
                    backend_id=backend_id,
                    provider="file_exchange",
                    hint=(
                        "Use one of: project_root/shared_project, git_worktree, "
                        "isolated_checkout"
                    ),
                )
            ]
        return []


def load_transport_backend() -> FileExchangeTransportBackend:
    return FileExchangeTransportBackend()
