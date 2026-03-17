#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

SLUG="test-import-$$"
FAKE_REPO="$(mktemp -d)"

mkdir -p "$FAKE_REPO/src" "$FAKE_REPO/docs" "$FAKE_REPO/tests" "$FAKE_REPO/.git"

cleanup() {
    rm -rf "$FAKE_REPO"
    rm -rf "$REPO_ROOT/projects/$SLUG" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== import-project test ==="

OUT=$(python3 scripts/claw.py import-project --slug "$SLUG" --path "$FAKE_REPO")
echo "Output: $OUT"

[ -d "projects/$SLUG" ] || { echo "FAIL: project dir not created"; exit 1; }

[ -f "projects/$SLUG/state/project.yaml" ] || { echo "FAIL: project.yaml not created"; exit 1; }
grep -q "slug: $SLUG" "projects/$SLUG/state/project.yaml" || { echo "FAIL: slug not in project.yaml"; exit 1; }

[ -f "projects/$SLUG/docs/WORKFLOW.md" ] || { echo "FAIL: WORKFLOW.md not created"; exit 1; }
grep -q "docs\|src\|tests" "projects/$SLUG/docs/WORKFLOW.md" || { echo "FAIL: edit_scope not populated"; exit 1; }
grep -qv "{{PROJECT_SLUG}}" "projects/$SLUG/docs/WORKFLOW.md" || { echo "FAIL: placeholder not replaced"; exit 1; }
[ -f "projects/$SLUG/.codex/config.toml" ] || { echo "FAIL: Codex config not created"; exit 1; }
[ -f "projects/$SLUG/.codex/agents/project-explorer.toml" ] || { echo "FAIL: Codex subagent not created"; exit 1; }
[ -f "projects/$SLUG/.claude/agents/project-explorer.md" ] || { echo "FAIL: Claude subagent not created"; exit 1; }
grep -q "$SLUG" "projects/$SLUG/.codex/agents/project-explorer.toml" || { echo "FAIL: Codex subagent placeholder not replaced"; exit 1; }
grep -q "$SLUG" "projects/$SLUG/.claude/agents/project-explorer.md" || { echo "FAIL: Claude subagent placeholder not replaced"; exit 1; }

if python3 scripts/claw.py import-project --slug "$SLUG" --path "$FAKE_REPO" 2>/dev/null; then
    echo "FAIL: duplicate slug should have been rejected"
    exit 1
fi

echo "PASS: import-project test"
