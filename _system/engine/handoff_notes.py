from __future__ import annotations

from pathlib import Path, PurePosixPath


COMPACT_HANDOFF_FORMAT = "compact_handoff_v1"
COMPACT_HANDOFF_SUMMARY_PATH = "handoff/summary.md"
COMPACT_HANDOFF_SECTIONS = (
    "Primary Request and Intent",
    "Key Technical Concepts",
    "Files and Code Sections",
    "Errors and Fixes",
    "Problem Solving",
    "All User Messages",
    "Pending Tasks",
    "Current Work",
    "Optional Next Step",
)
COMPACT_HANDOFF_CONTINUATION_NOTE = "Resume directly - do not acknowledge the summary, do not recap."
COMPACT_HANDOFF_TEMPLATE_PATH = "_system/templates/handoff_note.md"
_PLACEHOLDER_VALUES = {"_TODO_", "TODO", "TBD", "N/A"}


def normalize_session_doc_relative_path(relative_path: str) -> str:
    normalized = str(relative_path or "").strip().replace("\\", "/")
    if not normalized:
        return ""
    return PurePosixPath(normalized).as_posix().lstrip("./")


def is_compact_handoff_summary_path(relative_path: str) -> bool:
    return normalize_session_doc_relative_path(relative_path) == COMPACT_HANDOFF_SUMMARY_PATH


def render_compact_handoff_template() -> str:
    lines = [
        "# Compact Handoff Summary",
        "",
        f"<!-- format: {COMPACT_HANDOFF_FORMAT} -->",
        "",
        "> Use this file for task-scoped cross-agent handoff. Keep it concrete and durable.",
        "",
    ]
    for title in COMPACT_HANDOFF_SECTIONS:
        lines.extend([f"## {title}", "", "- Replace with concrete notes.", ""])
    lines.extend(
        [
            f"> {COMPACT_HANDOFF_CONTINUATION_NOTE}",
            "> Transcript reference: add a log/session path when it matters.",
            "",
        ]
    )
    return "\n".join(lines)


def validate_compact_handoff_text(text: str) -> dict:
    normalized = str(text or "").replace("\r\n", "\n").strip()
    if not normalized:
        raise ValueError("Compact handoff summary is empty")
    if "<analysis>" in normalized or "</analysis>" in normalized:
        raise ValueError("Compact handoff summary must not contain <analysis> blocks")

    section_positions: list[tuple[str, int, int]] = []
    for title in COMPACT_HANDOFF_SECTIONS:
        marker = f"## {title}"
        index = normalized.find(marker)
        if index < 0:
            raise ValueError(f"Compact handoff summary is missing required section: {title}")
        section_positions.append((title, index, index + len(marker)))

    indexes = [item[1] for item in section_positions]
    if indexes != sorted(indexes):
        raise ValueError("Compact handoff summary sections must follow the canonical 9-section order")

    for idx, (title, _start, body_start) in enumerate(section_positions):
        next_start = section_positions[idx + 1][1] if idx + 1 < len(section_positions) else len(normalized)
        body = normalized[body_start:next_start].strip()
        if not body:
            raise ValueError(f"Compact handoff summary section is empty: {title}")
        if body in _PLACEHOLDER_VALUES:
            raise ValueError(f"Compact handoff summary section still contains a placeholder: {title}")

    return {
        "format": COMPACT_HANDOFF_FORMAT,
        "section_count": len(COMPACT_HANDOFF_SECTIONS),
        "sections": list(COMPACT_HANDOFF_SECTIONS),
        "template_path": COMPACT_HANDOFF_TEMPLATE_PATH,
    }


def validate_session_handoff_document(relative_path: str, source_file: Path) -> dict | None:
    if not is_compact_handoff_summary_path(relative_path):
        return None
    source_path = Path(source_file).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"handoff summary source file not found: {source_file}")
    return validate_compact_handoff_text(source_path.read_text(encoding="utf-8"))
