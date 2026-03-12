from __future__ import annotations

import json
import shlex
from pathlib import Path


SHELL_META_TOKENS = {
    "|",
    "||",
    "&",
    "&&",
    ";",
    ";;",
    "<",
    "<<",
    "<<<",
    ">",
    ">>",
    ">|",
    "1>",
    "1>>",
    "2>",
    "2>>",
}
SHELL_INTERPRETERS = {"bash", "sh", "zsh", "dash", "fish"}
SHELL_EVAL_FLAGS = {"-c", "-lc", "-ic", "-ec", "-xc", "-xec"}


def command_display(command: list[str]) -> str:
    return shlex.join(command)


def _reject_shell_evaluation(argv: list[str], *, env_name: str) -> None:
    if not argv:
        raise ValueError(f"{env_name} must define a trusted argv command")

    executable = Path(argv[0]).name
    if executable in SHELL_INTERPRETERS and len(argv) >= 2 and argv[1] in SHELL_EVAL_FLAGS:
        raise ValueError(f"{env_name} must be a trusted argv command, not shell eval ({argv[0]} {argv[1]})")

    for token in argv:
        if token in SHELL_META_TOKENS:
            raise ValueError(f"{env_name} must be a trusted argv command without shell operators")
        if "$(" in token or "`" in token:
            raise ValueError(f"{env_name} must be a trusted argv command without shell substitution")


def parse_trusted_argv(raw_value: str | None, *, env_name: str) -> list[str] | None:
    value = (raw_value or "").strip()
    if not value:
        return None

    argv: list[str]
    if value.startswith("["):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{env_name} must be valid JSON array argv or plain argv string: {exc}") from exc
        if not isinstance(loaded, list) or not loaded or any(not isinstance(item, str) or not item.strip() for item in loaded):
            raise ValueError(f"{env_name} JSON override must be a non-empty array of strings")
        argv = [item for item in loaded if item.strip()]
    else:
        try:
            argv = shlex.split(value)
        except ValueError as exc:
            raise ValueError(f"{env_name} must be a valid argv string: {exc}") from exc

    _reject_shell_evaluation(argv, env_name=env_name)
    return argv
