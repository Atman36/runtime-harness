#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-file-exchange-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"

git -C "$workspace" init >/dev/null
git -C "$workspace" config user.name "Claw Tests"
git -C "$workspace" config user.email "claw-tests@example.com"
git -C "$workspace" add . >/dev/null
git -C "$workspace" commit -m "test fixture" >/dev/null

project_root="$workspace/projects/_claw-dev"
mkdir -p "$project_root/docs" "$project_root/exports/nested"
printf 'TOP-SECRET\n' > "$project_root/.env"
printf 'artifact\n' > "$project_root/exports/nested/result.txt"

input_file="$tmp_root/input.txt"
printf 'uploaded from operator\n' > "$input_file"

file_put() {
  python3 "$workspace/scripts/claw.py" openclaw file-put "$@"
}

file_fetch() {
  python3 "$workspace/scripts/claw.py" openclaw file-fetch "$@"
}

bind_context() {
  python3 "$workspace/scripts/claw.py" openclaw bind-context "$@"
}

put_out="$tmp_root/put.json"
file_put "$project_root" docs/operator-upload.txt --source-file "$input_file" > "$put_out"

python3 - "$put_out" "$project_root" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
project_root = pathlib.Path(sys.argv[2]).resolve()
target_path = project_root / "docs" / "operator-upload.txt"
assert payload["status"] == "ok", payload
assert payload["operation"] == "file_put", payload
assert payload["workspace_mode"] == "project_root", payload
assert payload["relative_path"] == "docs/operator-upload.txt", payload
assert pathlib.Path(payload["target_path"]).resolve() == target_path, payload
assert target_path.read_text() == "uploaded from operator\n", target_path.read_text()
assert not list(target_path.parent.glob("operator-upload.txt.tmp-*")), "temporary files left behind"
PY

deny_stdout="$tmp_root/deny.stdout"
deny_stderr="$tmp_root/deny.stderr"
if file_fetch "$project_root" .env --output-file "$tmp_root/deny.txt" >"$deny_stdout" 2>"$deny_stderr"; then
  echo "Expected deny-glob fetch to fail" >&2
  exit 1
fi

python3 - "$deny_stderr" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["code"] == "FILE_EXCHANGE_DENIED", payload
assert ".env" in payload["error"], payload
PY

escape_stdout="$tmp_root/escape.stdout"
escape_stderr="$tmp_root/escape.stderr"
if file_put "$project_root" ../escape.txt --source-file "$input_file" >"$escape_stdout" 2>"$escape_stderr"; then
  echo "Expected path traversal upload to fail" >&2
  exit 1
fi

python3 - "$escape_stderr" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["code"] == "FILE_EXCHANGE_INVALID", payload
assert "escape" in payload["error"].lower() or "relative" in payload["error"].lower(), payload
PY

zip_out="$tmp_root/fetch-dir.json"
zip_file="$tmp_root/exports.zip"
file_fetch "$project_root" exports --output-file "$zip_file" > "$zip_out"

python3 - "$zip_out" "$zip_file" <<'PY'
import json
import pathlib
import sys
import zipfile

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
zip_path = pathlib.Path(sys.argv[2]).resolve()
assert payload["status"] == "ok", payload
assert payload["operation"] == "file_fetch", payload
assert payload["archive"] == "zip", payload
assert pathlib.Path(payload["output_file"]).resolve() == zip_path, payload
with zipfile.ZipFile(zip_path) as archive:
    names = archive.namelist()
    assert "nested/result.txt" in names, names
    assert archive.read("nested/result.txt") == b"artifact\n"
PY

task_path="$project_root/tasks/TASK-900.md"
spec_path="$project_root/specs/SPEC-900.md"
cat > "$task_path" <<'EOF'
---
id: TASK-900
title: "Temporary worktree file exchange task"
status: ready
spec: ../specs/SPEC-900.md
preferred_agent: codex
review_policy: standard
project: _claw-dev
workspace_mode: git_worktree
needs_review: false
risk_flags: []
---

# Task
Temporary task for file exchange test.
EOF

cat > "$spec_path" <<'EOF'
# SPEC-900

Temporary spec for file exchange test.
EOF

run_log="$tmp_root/build-run.log"
python3 "$workspace/scripts/build_run.py" "$task_path" > "$run_log"
run_dir="$(python3 - "$run_log" <<'PY'
import pathlib
import sys

for line in pathlib.Path(sys.argv[1]).read_text().splitlines():
    if line.startswith("Created task run: "):
        print(line.split(": ", 1)[1].strip())
        break
else:
    raise SystemExit("run dir not found")
PY
)"

context_file="$tmp_root/context.json"
bind_context \
  --message $'/project _claw-dev @feature/file-exchange\nUpload fixture' \
  > "$context_file"

worktree_out="$tmp_root/worktree-put.json"
file_put "$project_root" docs/worktree-only.txt \
  --source-file "$input_file" \
  --context-json-file "$context_file" \
  --run "$run_dir" > "$worktree_out"

python3 - "$worktree_out" "$project_root" "$run_dir" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
project_root = pathlib.Path(sys.argv[2]).resolve()
run_dir = pathlib.Path(sys.argv[3]).resolve()
target_path = pathlib.Path(payload["target_path"]).resolve()

assert payload["status"] == "ok", payload
assert payload["workspace_mode"] == "git_worktree", payload
assert target_path.read_text() == "uploaded from operator\n"
assert project_root / "docs" / "worktree-only.txt" != target_path
assert not (project_root / "docs" / "worktree-only.txt").exists()
assert "RUN-900" not in str(target_path)
assert run_dir.name in payload["target_root"], payload
PY

missing_run_stdout="$tmp_root/missing-run.stdout"
missing_run_stderr="$tmp_root/missing-run.stderr"
if file_fetch "$project_root" docs/operator-upload.txt \
  --context-json-file "$context_file" \
  --output-file "$tmp_root/should-not-exist.txt" >"$missing_run_stdout" 2>"$missing_run_stderr"; then
  echo "Expected worktree-bound fetch without --run to fail" >&2
  exit 1
fi

python3 - "$missing_run_stderr" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["code"] == "FILE_EXCHANGE_INVALID", payload
assert "run" in payload["error"].lower(), payload
PY

echo "file exchange test: ok"
