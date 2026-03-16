#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-operator-session-test.XXXXXX")"
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
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"

project_root="$workspace/projects/demo-project"

mkdir -p "$workspace/projects/branch-project/state"
cat > "$workspace/projects/branch-project/state/project.yaml" <<'EOF'
slug: branch-project
status: active
execution:
  workspace_mode: isolated_checkout
EOF

operator_session() {
  python3 "$workspace/scripts/claw.py" openclaw "$@"
}

scope_id="transport/thread-42"

update_out="$tmp_root/operator-session-update.json"
operator_session session-update "$project_root" \
  --scope "$scope_id" \
  --engine codex \
  --project demo-project \
  --branch feature/operator \
  --resume-handle "codex-session-123" \
  --summary "Continue from previous operator turn." > "$update_out"

status_out="$tmp_root/operator-session-status.json"
operator_session session-status "$project_root" \
  --scope "$scope_id" \
  --engine codex > "$status_out"

bind_out="$tmp_root/operator-bind.json"
operator_session bind-context \
  --message "Continue the current thread" \
  --default-project demo-project \
  --default-agent codex \
  --default-branch feature/operator \
  --session-scope "$scope_id" > "$bind_out"

reply_out="$tmp_root/operator-bind-reply.json"
operator_session bind-context \
  --message "Continue the replied thread" \
  --reply-message $'Prior reply\nctx: project=branch-project agent=codex branch=reply/thread' \
  --default-project demo-project \
  --default-agent codex \
  --default-branch feature/operator \
  --session-scope "$scope_id" > "$reply_out"

reset_out="$tmp_root/operator-session-reset.json"
operator_session session-reset "$project_root" \
  --scope "$scope_id" \
  --engine codex \
  --note "explicit reset" > "$reset_out"

thread_out="$tmp_root/operator-session-new-thread.json"
operator_session session-new-thread "$project_root" \
  --scope "$scope_id" \
  --engine codex \
  --note "fresh thread" > "$thread_out"

session_file="$(python3 - "$status_out" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
print(payload["session_file"])
PY
)"
assert_file "$workspace/$session_file"

python3 - "$update_out" "$status_out" "$bind_out" "$reply_out" "$reset_out" "$thread_out" <<'PY'
import json
import sys
from pathlib import Path

update = json.loads(Path(sys.argv[1]).read_text())
status = json.loads(Path(sys.argv[2]).read_text())
bind_payload = json.loads(Path(sys.argv[3]).read_text())
reply_payload = json.loads(Path(sys.argv[4]).read_text())
reset = json.loads(Path(sys.argv[5]).read_text())
new_thread = json.loads(Path(sys.argv[6]).read_text())

assert update["resume"]["handle"] == "codex-session-123", update
assert update["binding"]["project"] == "demo-project", update["binding"]
assert update["binding"]["branch"] == "feature/operator", update["binding"]

assert status["resume_line"] == "codex resume codex-session-123", status
assert status["resume"]["kind"] == "text", status["resume"]
assert status["binding"]["ctx_footer"] == "ctx: project=demo-project agent=codex branch=feature/operator", status["binding"]

assert bind_payload["continuation"]["mode"] == "resume", bind_payload["continuation"]
assert bind_payload["continuation"]["source"] == "stored_session", bind_payload["continuation"]
assert bind_payload["continuation"]["resume_line"] == "codex resume codex-session-123", bind_payload["continuation"]
assert bind_payload["operator_session"]["session_id"] == update["session_id"], bind_payload["operator_session"]

assert reply_payload["resolved"]["project"] == "branch-project", reply_payload["resolved"]
assert reply_payload["resolved"]["branch"] == "reply/thread", reply_payload["resolved"]
assert reply_payload["continuation"]["mode"] == "fresh", reply_payload["continuation"]
assert reply_payload["continuation"]["source"] == "context_changed", reply_payload["continuation"]
assert reply_payload["continuation"]["resume_line"] is None, reply_payload["continuation"]

assert reset["status"] == "reset", reset
assert reset["resume"] is None, reset
assert reset["binding"]["project"] == "demo-project", reset["binding"]
assert reset["binding"]["branch"] == "feature/operator", reset["binding"]

assert new_thread["session_id"] != reset["session_id"], new_thread
assert new_thread["resume"] is None, new_thread
assert new_thread["rotation_count"] >= 1, new_thread
assert new_thread["binding"]["project"] == "demo-project", new_thread["binding"]
assert new_thread["binding"]["branch"] == "feature/operator", new_thread["binding"]
PY

python3 "$workspace/scripts/validate_artifacts.py" "$workspace/$session_file" --quiet >/dev/null

echo "operator session test: ok"
