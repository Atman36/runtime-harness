# SPEC-005 — Structural guardrails against agent drift

## Context

The claw project is at `/Users/Apple/progect/claw`.

In the last orchestration session, both agents created `projects/claw-dev/` instead of
using the correct `projects/_claw-dev/`, and one agent weakened a test assert
(`project_count >= 2`) to accommodate its accidental scaffold. These are structural
drift patterns that are cheap to detect automatically but expensive to catch in manual review.

This spec adds a standalone guardrails module and CLI command.
Orchestrate integration is **out of scope** — that is a follow-up task once the
guardrail engine is stable and we know what "run diff" means across worktree/shared_project modes.

## Goal

1. Create `_system/engine/guardrails.py` — three drift checks (pure functions, no I/O)
2. Add `cmd_guardrail_check` to `scripts/claw.py` — standalone `claw guardrail-check --diff-path`
3. Add `tests/guardrails_test.sh`

## Out of scope (follow-up)
- Calling guardrail check from `cmd_orchestrate` automatically
- Computing diffs from git history inside the guardrail path
- Blocking commits based on guardrail results

## Files to create / modify

### CREATE: `/Users/Apple/progect/claw/_system/engine/guardrails.py`

```python
"""Structural drift checks for claw orchestration guardrails."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


ASSERT_WEAKENING_PATTERNS = [
    # e.g. >= 3 changed to >= 2, or == 5 changed to >= 4
    re.compile(r'-\s*(assert|assertEqual|assertTrue|assertGreaterEqual).*?(\d+)'),
]

# Patterns that indicate weakened comparison (removing/lowering threshold)
_REMOVED_ASSERT_RE = re.compile(
    r'^-.*(?:assert\w*\s*\(|>=\s*\d+|==\s*\d+|\bge\b)',
    re.MULTILINE
)
_ADDED_WEAKER_RE = re.compile(
    r'^\+.*(?:assert\w*\s*\(|>=\s*\d+|==\s*\d+|\bge\b)',
    re.MULTILINE
)


def check_unauthorized_scaffold(
    diff_text: str,
    allowed_project_slugs: list[str],
) -> list[dict[str, Any]]:
    """Check if diff creates a new projects/<slug>/ not in allowed_project_slugs.

    Returns list of issue dicts.
    """
    issues: list[dict[str, Any]] = []
    # Look for lines like '+++ b/projects/<slug>/...' or new directory markers
    new_project_re = re.compile(r'^\+\+\+\s+b/projects/([^/\s]+)/', re.MULTILINE)
    found_slugs: set[str] = set()
    for match in new_project_re.finditer(diff_text):
        slug = match.group(1)
        if not slug.startswith('_') and slug not in ('_template',):
            found_slugs.add(slug)

    for slug in found_slugs:
        if slug not in allowed_project_slugs:
            issues.append({
                "code": "unauthorized_scaffold",
                "severity": "fail",
                "message": (
                    f"Agent created projects/{slug}/ which is not in allowed project slugs. "
                    f"Allowed: {allowed_project_slugs}"
                ),
            })
    return issues


def check_assert_weakening(diff_text: str) -> list[dict[str, Any]]:
    """Check if diff weakens assertion thresholds.

    Looks for removed assert lines paired with added weaker versions.
    Returns list of warning dicts.
    """
    issues: list[dict[str, Any]] = []

    # Find hunks where assertion lines are removed and replaced with weaker ones
    removed_asserts = _REMOVED_ASSERT_RE.findall(diff_text)
    added_asserts = _ADDED_WEAKER_RE.findall(diff_text)

    if removed_asserts and added_asserts:
        # Extract numbers from removed lines
        removed_nums = [int(n) for n in re.findall(r'>=\s*(\d+)|==\s*(\d+)|\(\s*(\d+)', r) for n in r if n]
        added_nums = [int(n) for n in re.findall(r'>=\s*(\d+)|==\s*(\d+)|\(\s*(\d+)', a) for n in a if n]
        # Simplified: if threshold numbers decreased on average, flag it
        if removed_nums and added_nums:
            removed_max = max(removed_nums)
            added_min = min(added_nums)
            if added_min < removed_max:
                issues.append({
                    "code": "assert_weakening",
                    "severity": "warning",
                    "message": (
                        f"Diff appears to weaken assertion threshold: "
                        f"removed threshold {removed_max}, added threshold {added_min}"
                    ),
                })

    # Also look for direct patterns like: -assert something >= 3 / +assert something >= 2
    hunk_re = re.compile(
        r'^-[^\n]*(?:>=\s*(\d+))[^\n]*\n[^+\n]*\n\+[^\n]*(?:>=\s*(\d+))',
        re.MULTILINE
    )
    for m in hunk_re.finditer(diff_text):
        old_val, new_val = int(m.group(1)), int(m.group(2))
        if new_val < old_val:
            issues.append({
                "code": "assert_weakening",
                "severity": "warning",
                "message": f"Assert threshold weakened from {old_val} to {new_val}",
            })

    return issues


def check_edit_scope_violations(
    diff_text: str,
    edit_scope: list[str],
    project_root_name: str,
) -> list[dict[str, Any]]:
    """Check if diff modifies files outside the declared edit_scope.

    edit_scope is a list of directory names relative to the project root.
    If edit_scope is empty, all edits are allowed.

    Returns list of warning dicts.
    """
    if not edit_scope:
        return []

    issues: list[dict[str, Any]] = []
    # Find modified file paths from diff header lines
    file_re = re.compile(r'^(?:\+\+\+|---)\s+(?:a/|b/)?(.+)', re.MULTILINE)
    modified_paths: set[str] = set()
    for m in file_re.finditer(diff_text):
        path = m.group(1).strip()
        if path != '/dev/null':
            modified_paths.add(path)

    for path in modified_paths:
        parts = Path(path).parts
        if len(parts) < 2:
            continue
        # Check if the first directory component is in edit_scope
        top_dir = parts[0]
        if top_dir not in edit_scope:
            issues.append({
                "code": "edit_scope_violation",
                "severity": "warning",
                "message": (
                    f"File '{path}' is outside declared edit_scope "
                    f"{edit_scope!r} for project '{project_root_name}'"
                ),
            })

    return issues


def run_guardrails(
    diff_text: str,
    allowed_project_slugs: list[str],
    edit_scope: list[str],
    project_root_name: str,
) -> dict[str, Any]:
    """Run all guardrail checks and return a structured result.

    Returns:
        {
            "passed": bool,
            "issue_count": int,
            "fail_count": int,
            "warning_count": int,
            "issues": [{"code": str, "severity": "fail"|"warning", "message": str}]
        }
    """
    issues: list[dict[str, Any]] = []
    issues.extend(check_unauthorized_scaffold(diff_text, allowed_project_slugs))
    issues.extend(check_assert_weakening(diff_text))
    issues.extend(check_edit_scope_violations(diff_text, edit_scope, project_root_name))

    fail_count = sum(1 for i in issues if i.get("severity") == "fail")
    warning_count = sum(1 for i in issues if i.get("severity") == "warning")

    return {
        "passed": fail_count == 0,
        "issue_count": len(issues),
        "fail_count": fail_count,
        "warning_count": warning_count,
        "issues": issues,
    }
```

### MODIFY: `/Users/Apple/progect/claw/scripts/claw.py`

**1. Add import near existing engine imports:**

```python
from _system.engine.guardrails import run_guardrails  # noqa: E402
```

**2. Add `cmd_guardrail_check` function:**

```python
def cmd_guardrail_check(args: argparse.Namespace) -> int:
    """Standalone guardrail check against a diff file."""
    diff_path = Path(args.diff_path)
    if not diff_path.is_file():
        print(json.dumps({"error": f"Diff file not found: {diff_path}"}), file=sys.stderr)
        return 1

    diff_text = diff_path.read_text(encoding="utf-8", errors="replace")

    # Load edit_scope from workflow contract if project is given
    edit_scope: list[str] = []
    project_root_name = args.project or "unknown"
    if args.project:
        try:
            project_root = resolve_project_root(args.project)
            contract = load_workflow_contract(project_root)
            if contract and contract.scope:
                edit_scope = list(contract.scope.edit_scope)
            project_root_name = project_root.name
        except FileNotFoundError:
            pass

    # Allowed project slugs = all existing projects
    allowed_slugs = [p.name for p in (REPO_ROOT / "projects").iterdir() if p.is_dir()]

    result = run_guardrails(
        diff_text=diff_text,
        allowed_project_slugs=allowed_slugs,
        edit_scope=edit_scope,
        project_root_name=project_root_name,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1
```

**3. Register CLI command in `build_parser()`:**

```python
guardrail_check = subcommands.add_parser(
    "guardrail-check",
    help="Check a diff for structural drift: unauthorized scaffold, assert weakening, scope violations"
)
guardrail_check.add_argument("--project", default=None, help="Project slug or path (for edit_scope lookup)")
guardrail_check.add_argument("--diff-path", required=True, help="Path to the git diff file to check")
guardrail_check.set_defaults(func=cmd_guardrail_check)
```

### CREATE: `/Users/Apple/progect/claw/tests/guardrails_test.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

TMPDIR_TESTS="$(mktemp -d)"
cleanup() { rm -rf "$TMPDIR_TESTS"; }
trap cleanup EXIT

echo "=== guardrails test ==="

# Test 1: Clean diff — should pass
cat > "$TMPDIR_TESTS/clean.diff" << 'EOF'
--- a/scripts/claw.py
+++ b/scripts/claw.py
@@ -1,3 +1,4 @@
+import new_module
 import existing
EOF

OUT=$(python3 scripts/claw.py guardrail-check --diff-path "$TMPDIR_TESTS/clean.diff")
echo "Clean diff result: $OUT"
PASSED=$(echo "$OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['passed'])")
[ "$PASSED" = "True" ] || { echo "FAIL: clean diff should pass guardrails"; exit 1; }

# Test 2: Unauthorized scaffold creation
cat > "$TMPDIR_TESTS/bad_scaffold.diff" << 'EOF'
--- /dev/null
+++ b/projects/evil-project/state/project.yaml
@@ -0,0 +1,2 @@
+slug: evil-project
+source_path: /tmp/evil
EOF

OUT=$(python3 scripts/claw.py guardrail-check --diff-path "$TMPDIR_TESTS/bad_scaffold.diff")
echo "Scaffold drift result: $OUT"
PASSED=$(echo "$OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['passed'])")
[ "$PASSED" = "False" ] || { echo "FAIL: unauthorized scaffold should fail guardrails"; exit 1; }
FAIL_COUNT=$(echo "$OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['fail_count'])")
[ "$FAIL_COUNT" -ge 1 ] || { echo "FAIL: should have at least one fail issue"; exit 1; }

# Test 3: Assert weakening
cat > "$TMPDIR_TESTS/assert_weakening.diff" << 'EOF'
--- a/tests/some_test.sh
+++ b/tests/some_test.sh
@@ -10,7 +10,7 @@
-[ "$COUNT" -ge 3 ] || fail
+[ "$COUNT" -ge 2 ] || fail
EOF

OUT=$(python3 scripts/claw.py guardrail-check --diff-path "$TMPDIR_TESTS/assert_weakening.diff")
echo "Assert weakening result: $OUT"
WARN_COUNT=$(echo "$OUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['warning_count'])")
[ "$WARN_COUNT" -ge 1 ] || { echo "FAIL: assert weakening should produce a warning"; exit 1; }

echo "PASS: guardrails test"
```

### Add to `/Users/Apple/progect/claw/tests/run_all.sh`

```bash
bash tests/guardrails_test.sh
```

## Acceptance Criteria

- `_system/engine/guardrails.py` is importable
- `python3 scripts/claw.py guardrail-check --diff-path /path/to/bad.diff` returns exit 1 and JSON with `"passed": false` for unauthorized scaffold diffs
- `python3 scripts/claw.py guardrail-check --diff-path /path/to/clean.diff` returns exit 0 and JSON with `"passed": true`
- Assert weakening produces at least one `"warning"` issue
- `claw orchestrate` calls guardrail check after each completed run and includes result in payload
- `bash tests/guardrails_test.sh` passes
- `bash tests/run_all.sh` still passes

## Constraints

- Guardrail check failure does NOT abort `claw orchestrate` in v1 — only surfaces in payload as warnings/fails
- The assert-weakening check is heuristic — false positives are acceptable, false negatives are not
- Do not shell out to `git diff` inside `guardrails.py` — the diff text is passed in as a string
- Keep `guardrails.py` as a pure function module (no file I/O, no subprocess)
