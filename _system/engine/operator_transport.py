from __future__ import annotations

import importlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml


TRANSPORT_REGISTRY_PATH = Path("_system/registry/operator_transports.yaml")
DEFAULT_FILE_EXCHANGE_PROVIDER = "file_exchange"
DEFAULT_FILE_EXCHANGE_BACKEND_ID = "file_exchange"
BACKEND_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
RESERVED_BACKEND_IDS = {"all", "default", "none"}


class TransportConfigError(ValueError):
    def __init__(
        self,
        message: str,
        code: str = "TRANSPORT_CONFIG_INVALID",
        *,
        backend_id: str | None = None,
        provider: str | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.backend_id = backend_id
        self.provider = provider
        self.hint = hint

    def to_diagnostic(self) -> "TransportDiagnostic":
        return TransportDiagnostic(
            severity="error",
            code=self.code,
            message=str(self),
            backend_id=self.backend_id,
            provider=self.provider,
            hint=self.hint,
        )


@dataclass(frozen=True)
class TransportDiagnostic:
    severity: str
    code: str
    message: str
    backend_id: str | None = None
    provider: str | None = None
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
        }
        if self.backend_id:
            payload["backend_id"] = self.backend_id
        if self.provider:
            payload["provider"] = self.provider
        if self.hint:
            payload["hint"] = self.hint
        return payload


@dataclass(frozen=True)
class TransportProviderDefinition:
    provider_id: str
    module: str
    factory: str
    description: str
    required_binaries: tuple[str, ...]


@dataclass(frozen=True)
class TransportBackendDefinition:
    backend_id: str
    provider: str
    enabled: bool
    config: dict[str, Any]
    source: str


@dataclass(frozen=True)
class LoadedTransportBackend:
    definition: TransportBackendDefinition
    provider: TransportProviderDefinition
    backend: "OperatorTransportBackend"
    config: dict[str, Any]


class OperatorTransportBackend(Protocol):
    def validate_config(self, config: dict[str, Any], *, backend_id: str) -> dict[str, Any]:
        ...

    def setup_checks(
        self,
        *,
        project_root: Path,
        backend_id: str,
        config: dict[str, Any],
    ) -> list[TransportDiagnostic]:
        ...


def _read_yaml_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        return {}
    return dict(loaded)


def _registry_path(repo_root: Path) -> Path:
    return repo_root / TRANSPORT_REGISTRY_PATH


def _validate_backend_id(value: str, *, field_name: str, code: str) -> str:
    identifier = str(value or "").strip()
    if not identifier:
        raise TransportConfigError(f"{field_name} is required", code=code)
    if identifier in RESERVED_BACKEND_IDS:
        raise TransportConfigError(
            f"{field_name} '{identifier}' is reserved",
            code=code,
        )
    if not BACKEND_ID_RE.fullmatch(identifier):
        raise TransportConfigError(
            f"{field_name} '{identifier}' must match {BACKEND_ID_RE.pattern}",
            code=code,
        )
    return identifier


def load_transport_providers(repo_root: Path) -> dict[str, TransportProviderDefinition]:
    registry_path = _registry_path(Path(repo_root).resolve())
    if not registry_path.is_file():
        raise TransportConfigError(
            f"Transport registry not found: {registry_path}",
            code="TRANSPORT_REGISTRY_INVALID",
            hint="Restore _system/registry/operator_transports.yaml",
        )

    payload = _read_yaml_object(registry_path)
    raw_providers = payload.get("providers")
    if not isinstance(raw_providers, dict) or not raw_providers:
        raise TransportConfigError(
            f"Transport registry '{registry_path}' must define a non-empty providers map",
            code="TRANSPORT_REGISTRY_INVALID",
        )

    providers: dict[str, TransportProviderDefinition] = {}
    for raw_provider_id, raw_definition in raw_providers.items():
        provider_id = _validate_backend_id(raw_provider_id, field_name="Transport provider id", code="TRANSPORT_REGISTRY_INVALID")
        if not isinstance(raw_definition, dict):
            raise TransportConfigError(
                f"Transport provider '{provider_id}' must be an object",
                code="TRANSPORT_REGISTRY_INVALID",
                provider=provider_id,
            )

        module = str(raw_definition.get("module") or "").strip()
        factory = str(raw_definition.get("factory") or "").strip()
        description = str(raw_definition.get("description") or "").strip()
        raw_required_binaries = raw_definition.get("required_binaries") or []
        if not module or not factory:
            raise TransportConfigError(
                f"Transport provider '{provider_id}' must define module and factory",
                code="TRANSPORT_REGISTRY_INVALID",
                provider=provider_id,
            )
        if not isinstance(raw_required_binaries, list):
            raise TransportConfigError(
                f"Transport provider '{provider_id}' required_binaries must be a list",
                code="TRANSPORT_REGISTRY_INVALID",
                provider=provider_id,
            )

        required_binaries: list[str] = []
        for item in raw_required_binaries:
            binary = str(item or "").strip()
            if not binary:
                raise TransportConfigError(
                    f"Transport provider '{provider_id}' required_binaries entries must be non-empty strings",
                    code="TRANSPORT_REGISTRY_INVALID",
                    provider=provider_id,
                )
            required_binaries.append(binary)

        providers[provider_id] = TransportProviderDefinition(
            provider_id=provider_id,
            module=module,
            factory=factory,
            description=description,
            required_binaries=tuple(required_binaries),
        )
    return providers


def discover_transport_backends(repo_root: Path, project_root: Path) -> list[TransportBackendDefinition]:
    providers = load_transport_providers(repo_root)
    backends = _load_project_transport_backends(project_root)
    _validate_transport_backends(backends, providers)
    return backends


def _load_project_transport_backends(project_root: Path) -> list[TransportBackendDefinition]:
    project_state = _read_yaml_object(Path(project_root).resolve() / "state" / "project.yaml")
    operator_transport = project_state.get("operator_transport")
    if operator_transport is None:
        return [_default_file_exchange_backend()]
    if not isinstance(operator_transport, dict):
        raise TransportConfigError("operator_transport must be an object")

    raw_backends = operator_transport.get("backends")
    legacy_file_exchange = operator_transport.get("file_exchange")

    if raw_backends is None:
        if legacy_file_exchange is None:
            return [_default_file_exchange_backend()]
        if not isinstance(legacy_file_exchange, dict):
            raise TransportConfigError(
                "operator_transport.file_exchange must be an object",
                backend_id=DEFAULT_FILE_EXCHANGE_BACKEND_ID,
                provider=DEFAULT_FILE_EXCHANGE_PROVIDER,
            )
        return [
            TransportBackendDefinition(
                backend_id=DEFAULT_FILE_EXCHANGE_BACKEND_ID,
                provider=DEFAULT_FILE_EXCHANGE_PROVIDER,
                enabled=True,
                config=dict(legacy_file_exchange),
                source="operator_transport.file_exchange",
            )
        ]

    if legacy_file_exchange is not None:
        raise TransportConfigError(
            "operator_transport.file_exchange cannot be combined with operator_transport.backends",
            code="TRANSPORT_CONFIG_INVALID",
            backend_id=DEFAULT_FILE_EXCHANGE_BACKEND_ID,
            provider=DEFAULT_FILE_EXCHANGE_PROVIDER,
            hint="Move file_exchange config into operator_transport.backends[].config",
        )

    if not isinstance(raw_backends, list):
        raise TransportConfigError("operator_transport.backends must be a list")

    backends: list[TransportBackendDefinition] = []
    for index, raw_backend in enumerate(raw_backends):
        if not isinstance(raw_backend, dict):
            raise TransportConfigError(
                f"operator_transport.backends[{index}] must be an object",
                code="TRANSPORT_CONFIG_INVALID",
            )

        backend_id = _validate_backend_id(
            raw_backend.get("id"),
            field_name=f"Transport backend id at operator_transport.backends[{index}]",
            code="TRANSPORT_ID_INVALID",
        )
        provider_id = _validate_backend_id(
            raw_backend.get("provider"),
            field_name=f"Transport provider id at operator_transport.backends[{index}]",
            code="TRANSPORT_PROVIDER_UNKNOWN",
        )
        raw_config = raw_backend.get("config") or {}
        if not isinstance(raw_config, dict):
            raise TransportConfigError(
                f"Transport backend '{backend_id}' config must be an object",
                code="TRANSPORT_CONFIG_INVALID",
                backend_id=backend_id,
                provider=provider_id,
            )
        enabled = raw_backend.get("enabled", True)
        if not isinstance(enabled, bool):
            raise TransportConfigError(
                f"Transport backend '{backend_id}' enabled must be a boolean",
                code="TRANSPORT_CONFIG_INVALID",
                backend_id=backend_id,
                provider=provider_id,
            )
        backends.append(
            TransportBackendDefinition(
                backend_id=backend_id,
                provider=provider_id,
                enabled=enabled,
                config=dict(raw_config),
                source=f"operator_transport.backends[{index}]",
            )
        )
    return backends


def _default_file_exchange_backend() -> TransportBackendDefinition:
    return TransportBackendDefinition(
        backend_id=DEFAULT_FILE_EXCHANGE_BACKEND_ID,
        provider=DEFAULT_FILE_EXCHANGE_PROVIDER,
        enabled=True,
        config={},
        source="default.file_exchange",
    )


def _validate_transport_backends(
    backends: list[TransportBackendDefinition],
    providers: dict[str, TransportProviderDefinition],
) -> None:
    seen_backend_ids: set[str] = set()
    seen_providers: set[str] = set()
    for backend in backends:
        if backend.backend_id in seen_backend_ids:
            raise TransportConfigError(
                f"Duplicate transport backend id '{backend.backend_id}'",
                code="TRANSPORT_ID_INVALID",
                backend_id=backend.backend_id,
                provider=backend.provider,
            )
        seen_backend_ids.add(backend.backend_id)

        if backend.provider not in providers:
            raise TransportConfigError(
                f"Transport backend '{backend.backend_id}' references unknown provider '{backend.provider}'",
                code="TRANSPORT_PROVIDER_UNKNOWN",
                backend_id=backend.backend_id,
                provider=backend.provider,
                hint=f"Declare provider '{backend.provider}' in {_registry_path(Path.cwd()).name}",
            )

        if backend.provider in seen_providers:
            raise TransportConfigError(
                f"Duplicate transport provider '{backend.provider}' configured via backend '{backend.backend_id}'",
                code="TRANSPORT_PROVIDER_DUPLICATE",
                backend_id=backend.backend_id,
                provider=backend.provider,
                hint="Keep one configured backend per provider to avoid ambiguous transport routing",
            )
        seen_providers.add(backend.provider)


def describe_transport_backends(repo_root: Path, project_root: Path) -> list[dict[str, Any]]:
    providers = load_transport_providers(repo_root)
    backends = discover_transport_backends(repo_root, project_root)
    return [
        {
            "backend_id": backend.backend_id,
            "provider": backend.provider,
            "enabled": backend.enabled,
            "source": backend.source,
            "description": providers[backend.provider].description,
        }
        for backend in backends
    ]


def run_transport_doctor(repo_root: Path, project_root: Path) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    project_root = Path(project_root).resolve()
    diagnostics: list[TransportDiagnostic] = []
    backends_payload: list[dict[str, Any]] = []

    try:
        providers = load_transport_providers(repo_root)
        backends = _load_project_transport_backends(project_root)
        _validate_transport_backends(backends, providers)
    except TransportConfigError as exc:
        diagnostics.append(exc.to_diagnostic())
        return _doctor_payload(project_root, backends_payload, diagnostics)

    for backend in backends:
        provider = providers[backend.provider]
        backends_payload.append(
            {
                "backend_id": backend.backend_id,
                "provider": backend.provider,
                "enabled": backend.enabled,
                "source": backend.source,
                "description": provider.description,
            }
        )
        if not backend.enabled:
            continue

        for binary in provider.required_binaries:
            if shutil.which(binary) is None:
                diagnostics.append(
                    TransportDiagnostic(
                        severity="error",
                        code="TRANSPORT_BINARY_MISSING",
                        message=f"Transport backend '{backend.backend_id}' requires missing binary '{binary}'",
                        backend_id=backend.backend_id,
                        provider=backend.provider,
                        hint=f"Install '{binary}' or disable transport backend '{backend.backend_id}'",
                    )
                )

        try:
            loaded = _instantiate_transport_backend(provider, backend)
        except TransportConfigError as exc:
            diagnostics.append(exc.to_diagnostic())
            continue

        diagnostics.extend(loaded.backend.setup_checks(project_root=project_root, backend_id=backend.backend_id, config=loaded.config))

    return _doctor_payload(project_root, backends_payload, diagnostics)


def _doctor_payload(project_root: Path, backends: list[dict[str, Any]], diagnostics: list[TransportDiagnostic]) -> dict[str, Any]:
    error_count = sum(1 for item in diagnostics if item.severity == "error")
    warning_count = sum(1 for item in diagnostics if item.severity == "warning")
    return {
        "status": "ok" if error_count == 0 else "error",
        "project": project_root.name,
        "backends": backends,
        "errors": error_count,
        "warnings": warning_count,
        "diagnostics": [item.to_dict() for item in diagnostics],
    }


def load_transport_backend(
    repo_root: Path,
    project_root: Path,
    *,
    backend_id: str | None = None,
    provider_id: str | None = None,
) -> LoadedTransportBackend:
    repo_root = Path(repo_root).resolve()
    project_root = Path(project_root).resolve()
    providers = load_transport_providers(repo_root)
    backends = discover_transport_backends(repo_root, project_root)
    definition = _select_backend(backends, backend_id=backend_id, provider_id=provider_id)
    if not definition.enabled:
        raise TransportConfigError(
            f"Transport backend '{definition.backend_id}' is disabled",
            code="TRANSPORT_BACKEND_DISABLED",
            backend_id=definition.backend_id,
            provider=definition.provider,
        )

    provider = providers[definition.provider]
    for binary in provider.required_binaries:
        if shutil.which(binary) is None:
            raise TransportConfigError(
                f"Transport backend '{definition.backend_id}' requires missing binary '{binary}'",
                code="TRANSPORT_BINARY_MISSING",
                backend_id=definition.backend_id,
                provider=definition.provider,
                hint=f"Install '{binary}' or disable transport backend '{definition.backend_id}'",
            )

    loaded = _instantiate_transport_backend(provider, definition)
    setup_errors = [item for item in loaded.backend.setup_checks(project_root=project_root, backend_id=definition.backend_id, config=loaded.config) if item.severity == "error"]
    if setup_errors:
        first = setup_errors[0]
        raise TransportConfigError(
            first.message,
            code=first.code,
            backend_id=first.backend_id,
            provider=first.provider,
            hint=first.hint,
        )
    return loaded


def _select_backend(
    backends: list[TransportBackendDefinition],
    *,
    backend_id: str | None,
    provider_id: str | None,
) -> TransportBackendDefinition:
    matches = backends
    if backend_id:
        matches = [item for item in matches if item.backend_id == backend_id]
    if provider_id:
        matches = [item for item in matches if item.provider == provider_id]
    if not matches:
        target = backend_id or provider_id or "<unspecified>"
        raise TransportConfigError(
            f"Transport backend '{target}' is not configured",
            code="TRANSPORT_BACKEND_NOT_FOUND",
            backend_id=backend_id,
            provider=provider_id,
        )
    if len(matches) > 1:
        raise TransportConfigError(
            f"Transport backend selection for '{provider_id or backend_id}' is ambiguous",
            code="TRANSPORT_PROVIDER_DUPLICATE",
            backend_id=backend_id,
            provider=provider_id,
        )
    return matches[0]


def _instantiate_transport_backend(
    provider: TransportProviderDefinition,
    definition: TransportBackendDefinition,
) -> LoadedTransportBackend:
    try:
        module = importlib.import_module(provider.module)
    except Exception as exc:  # pragma: no cover - surfaced through doctor/runtime tests
        raise TransportConfigError(
            f"Failed to import transport provider '{provider.provider_id}' from {provider.module}: {exc}",
            code="TRANSPORT_PROVIDER_LOAD_FAILED",
            backend_id=definition.backend_id,
            provider=provider.provider_id,
        ) from exc

    factory = getattr(module, provider.factory, None)
    if factory is None or not callable(factory):
        raise TransportConfigError(
            f"Transport provider '{provider.provider_id}' is missing callable factory '{provider.factory}'",
            code="TRANSPORT_PROVIDER_LOAD_FAILED",
            backend_id=definition.backend_id,
            provider=provider.provider_id,
        )

    try:
        backend = factory()
    except Exception as exc:  # pragma: no cover - surfaced through doctor/runtime tests
        raise TransportConfigError(
            f"Transport provider '{provider.provider_id}' factory '{provider.factory}' failed: {exc}",
            code="TRANSPORT_PROVIDER_LOAD_FAILED",
            backend_id=definition.backend_id,
            provider=provider.provider_id,
        ) from exc

    try:
        normalized_config = backend.validate_config(dict(definition.config), backend_id=definition.backend_id)
    except TransportConfigError:
        raise
    except Exception as exc:
        raise TransportConfigError(
            str(exc),
            code="TRANSPORT_CONFIG_INVALID",
            backend_id=definition.backend_id,
            provider=provider.provider_id,
        ) from exc

    return LoadedTransportBackend(
        definition=definition,
        provider=provider,
        backend=backend,
        config=normalized_config,
    )
