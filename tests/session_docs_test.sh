#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-session-docs-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}

trap cleanup EXIT

assert_file() {
  local path="$1"
  if [ ! -f "$path" ]; then
    echo "Expected file to exist: $path" >&2
    exit 1
  fi
}

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/dispatch_hooks.py" "$workspace/scripts/dispatch_hooks.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/reconcile_hooks.py" "$workspace/scripts/reconcile_hooks.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"

project_root="$workspace/projects/demo-project"
docs_root="$project_root/state/session_docs"
rm -rf "$docs_root"

summary_source="$tmp_root/summary.md"
decision_source="$tmp_root/decision.md"
cat > "$summary_source" <<'EOF'
# Compact Handoff Summary

## Primary Request and Intent

- Continue TASK-001 and keep the runtime change minimal.

## Key Technical Concepts

- Session docs are the scratchpad for cross-agent handoff.

## Files and Code Sections

- `scripts/claw.py`
- `_system/engine/session_docs.py`

## Errors and Fixes

- No blocker yet; validation added before publishing the summary.

## Problem Solving

- Reused the existing session docs contract instead of inventing a second channel.

## All User Messages

- "Continue TASK-001 and keep the summary durable."

## Pending Tasks

- Re-run tests after the handoff note is stored.

## Current Work

- Wiring compact handoff validation into `session-file-put`.

## Optional Next Step

- Fetch the summary from `state/session_docs/` and resume directly.
EOF
printf '# Decisions\nCodex implementation notes.\n' > "$decision_source"
invalid_summary="$tmp_root/invalid-summary.md"
printf '# Compact Handoff Summary\n\n## Primary Request and Intent\n\n- Missing the rest.\n' > "$invalid_summary"

summary_out="$tmp_root/summary-put.json"
python3 "$workspace/scripts/claw.py" session-file-put "$project_root" \
  --task-id TASK-001 \
  handoff/summary.md \
  --source-file "$summary_source" \
  --author claude \
  --note "compact handoff summary" > "$summary_out"

decision_out="$tmp_root/decision-put.json"
python3 "$workspace/scripts/claw.py" session-file-put "$project_root" \
  --task-id TASK-001 \
  notes/implementation.md \
  --source-file "$decision_source" \
  --author codex \
  --note "runtime notes" > "$decision_out"

list_out="$tmp_root/session-files.json"
python3 "$workspace/scripts/claw.py" session-files "$project_root" \
  --task-id TASK-001 > "$list_out"

if python3 "$workspace/scripts/claw.py" session-file-put "$project_root" \
  --task-id TASK-001 \
  handoff/summary.md \
  --source-file "$invalid_summary" >/dev/null 2>&1; then
  echo "Expected invalid compact handoff summary to fail validation" >&2
  exit 1
fi

status_out="$tmp_root/session-status.json"
python3 "$workspace/scripts/claw.py" session-update "$project_root" \
  --agent codex \
  --task-id TASK-001 \
  --summary "bridge to shared files" >/dev/null
python3 "$workspace/scripts/claw.py" session-status "$project_root" \
  --agent codex \
  --task-id TASK-001 > "$status_out"

fetch_out="$tmp_root/fetch.json"
fetched_file="$tmp_root/fetched-summary.md"
python3 "$workspace/scripts/claw.py" session-file-fetch "$project_root" \
  --task-id TASK-001 \
  handoff/summary.md \
  --output-file "$fetched_file" > "$fetch_out"

manifest_file="$docs_root/TASK-001/manifest.json"
assert_file "$manifest_file"
assert_file "$docs_root/TASK-001/files/handoff/summary.md"
assert_file "$docs_root/TASK-001/files/notes/implementation.md"
assert_file "$fetched_file"

python3 - "$summary_out" "$decision_out" "$list_out" "$status_out" "$fetch_out" "$manifest_file" "$fetched_file" <<'PY'
import json
import pathlib
import sys

summary_put = json.loads(pathlib.Path(sys.argv[1]).read_text())
decision_put = json.loads(pathlib.Path(sys.argv[2]).read_text())
listing = json.loads(pathlib.Path(sys.argv[3]).read_text())
status = json.loads(pathlib.Path(sys.argv[4]).read_text())
fetch = json.loads(pathlib.Path(sys.argv[5]).read_text())
manifest = json.loads(pathlib.Path(sys.argv[6]).read_text())
fetched = pathlib.Path(sys.argv[7]).read_text()

assert summary_put["document"]["author"] == "claude", summary_put
assert summary_put["document"]["path"] == "handoff/summary.md", summary_put
assert summary_put["document"]["format"] == "compact_handoff_v1", summary_put
assert summary_put["document"]["section_count"] == 9, summary_put
assert decision_put["document"]["author"] == "codex", decision_put
assert decision_put["document"]["path"] == "notes/implementation.md", decision_put

assert listing["task_id"] == "TASK-001", listing
assert listing["document_count"] == 2, listing
paths = [item["path"] for item in listing["documents"]]
assert paths == ["handoff/summary.md", "notes/implementation.md"], paths

assert status["shared_files"]["document_count"] == 2, status
assert status["shared_files"]["manifest_file"].endswith("state/session_docs/TASK-001/manifest.json"), status

assert fetch["document"]["author"] == "claude", fetch
assert fetch["relative_path"] == "handoff/summary.md", fetch
assert "## Pending Tasks" in fetched, fetched

assert manifest["session_docs_version"] == 1, manifest
assert len(manifest["documents"]) == 2, manifest
summary_doc = manifest["documents"][0]
assert summary_doc["format"] == "compact_handoff_v1", summary_doc
PY

python3 "$workspace/scripts/validate_artifacts.py" "$manifest_file" --quiet >/dev/null

echo "session docs test: ok"
