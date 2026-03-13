"""Structural drift checks for claw orchestration guardrails."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


Issue = dict[str, Any]

_DIFF_PATH_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$", re.MULTILINE)
_PROJECT_PATH_RE = re.compile(r"^projects/([^/]+)/")
_INLINE_COMPARE_RE = re.compile(r"(==|>=|<=|>|<)\s*(-?\d+)")
_ASSERT_CALL_RE = re.compile(
    r"\b(assertEqual|assertGreaterEqual|assertGreater|assertLessEqual|assertLess)\s*\([^,\n]+,\s*(-?\d+)"
)


def _diff_paths(diff_text: str) -> list[str]:
    paths: list[str] = []
    for match in _DIFF_PATH_RE.finditer(diff_text):
        paths.append(match.group(2).strip())
    return paths


def _normalize_allowed_prefixes(edit_scope: list[str]) -> tuple[str, ...]:
    prefixes: list[str] = []
    for entry in edit_scope:
        value = str(entry).strip().strip("/")
        if value:
            prefixes.append(value)
    return tuple(prefixes)


def _extract_assert_signature(line: str) -> tuple[str, int] | None:
    compare_match = _INLINE_COMPARE_RE.search(line)
    if compare_match is not None:
        return compare_match.group(1), int(compare_match.group(2))

    call_match = _ASSERT_CALL_RE.search(line)
    if call_match is None:
        return None

    mapping = {
        "assertEqual": "==",
        "assertGreaterEqual": ">=",
        "assertGreater": ">",
        "assertLessEqual": "<=",
        "assertLess": "<",
    }
    return mapping[call_match.group(1)], int(call_match.group(2))


def _is_weaker_assertion(old: tuple[str, int], new: tuple[str, int]) -> bool:
    old_comp, old_value = old
    new_comp, new_value = new

    if old_comp in ("==", ">=", ">") and new_comp in ("==", ">=", ">"):
        if new_value < old_value:
            return True
        if old_comp == "==" and new_comp != "==" and new_value <= old_value:
            return True

    if old_comp in ("==", "<=", "<") and new_comp in ("==", "<=", "<"):
        if new_value > old_value:
            return True
        if old_comp == "==" and new_comp != "==" and new_value >= old_value:
            return True

    return False


def check_unauthorized_scaffold(
    diff_text: str,
    allowed_project_slugs: list[str],
) -> list[Issue]:
    """Return issues for new or modified project scaffolds outside the allowlist."""
    allowed = set(allowed_project_slugs)
    issues: list[Issue] = []
    seen: set[str] = set()

    for path in _diff_paths(diff_text):
        match = _PROJECT_PATH_RE.match(path)
        if match is None:
            continue
        slug = match.group(1)
        if slug in allowed or slug in seen:
            continue
        seen.add(slug)
        issues.append(
            {
                "code": "unauthorized_scaffold",
                "severity": "fail",
                "path": path,
                "message": f"Diff touches unauthorized project scaffold 'projects/{slug}/'.",
            }
        )

    return issues


def check_assert_weakening(diff_text: str) -> list[Issue]:
    """Return issues for weakened assertions detected inside diff hunks."""
    issues: list[Issue] = []
    current_path = "unknown"
    removed: list[tuple[str, tuple[str, int]]] = []
    added: list[tuple[str, tuple[str, int]]] = []

    def flush_hunk() -> None:
        pair_count = min(len(removed), len(added))
        for index in range(pair_count):
            old_line, old_signature = removed[index]
            new_line, new_signature = added[index]
            if not _is_weaker_assertion(old_signature, new_signature):
                continue
            issues.append(
                {
                    "code": "assert_weakening",
                    "severity": "fail",
                    "path": current_path,
                    "message": (
                        f"Assertion appears weaker in diff: '{old_line.strip()}' -> '{new_line.strip()}'."
                    ),
                }
            )

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            flush_hunk()
            removed = []
            added = []
            match = _DIFF_PATH_RE.match(line)
            current_path = match.group(2).strip() if match is not None else "unknown"
            continue
        if line.startswith("@@"):
            flush_hunk()
            removed = []
            added = []
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            continue
        if not line or line[0] not in "-+":
            continue

        signature = _extract_assert_signature(line[1:])
        if signature is None:
            continue
        if line.startswith("-"):
            removed.append((line[1:], signature))
        else:
            added.append((line[1:], signature))

    flush_hunk()
    return issues


def check_edit_scope_violations(
    diff_text: str,
    edit_scope: list[str],
    project_root_name: str,
) -> list[Issue]:
    """Return issues for files modified outside the declared edit scope."""
    allowed_prefixes = _normalize_allowed_prefixes(edit_scope)
    if not allowed_prefixes:
        return []

    issues: list[Issue] = []
    for path in sorted(set(_diff_paths(diff_text))):
        if path == "/dev/null":
            continue
        normalized = Path(path).as_posix().lstrip("./")
        if normalized.startswith("projects/"):
            project_prefix = f"projects/{project_root_name}/"
            if normalized.startswith(project_prefix):
                normalized = normalized[len(project_prefix):]
        if any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in allowed_prefixes):
            continue
        issues.append(
            {
                "code": "edit_scope_violation",
                "severity": "fail",
                "path": path,
                "message": (
                    f"Diff path '{path}' is outside edit_scope {list(allowed_prefixes)!r} "
                    f"for project '{project_root_name}'."
                ),
            }
        )
    return issues


def run_guardrails(
    diff_text: str,
    allowed_project_slugs: list[str],
    edit_scope: list[str],
    project_root_name: str,
) -> dict[str, Any]:
    """Run all standalone structural guardrail checks against one diff payload."""
    issues: list[Issue] = []
    issues.extend(check_unauthorized_scaffold(diff_text, allowed_project_slugs))
    issues.extend(check_assert_weakening(diff_text))
    issues.extend(check_edit_scope_violations(diff_text, edit_scope, project_root_name))

    fail_count = sum(1 for issue in issues if issue.get("severity") == "fail")
    warning_count = sum(1 for issue in issues if issue.get("severity") == "warning")

    return {
        "passed": fail_count == 0,
        "project_root_name": project_root_name,
        "edit_scope": list(edit_scope),
        "allowed_project_slugs": list(allowed_project_slugs),
        "issue_count": len(issues),
        "fail_count": fail_count,
        "warning_count": warning_count,
        "issues": issues,
    }
