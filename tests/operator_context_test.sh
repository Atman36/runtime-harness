#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-operator-context-test.XXXXXX")"
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

mkdir -p "$workspace/projects/branch-project/state"
cat > "$workspace/projects/branch-project/state/project.yaml" <<'EOF'
slug: branch-project
status: active
execution:
  workspace_mode: isolated_checkout
EOF

bind_context() {
  python3 "$workspace/scripts/claw.py" openclaw bind-context "$@"
}

direct_out="$tmp_root/direct.json"
bind_context \
  --message $'/agent codex /project _claw-dev @feature/directive\nContinue with runtime contract.' \
  --default-project demo-project \
  --default-agent claude \
  --default-branch main > "$direct_out"

python3 - "$direct_out" "$workspace" <<'PY'
import json
import pathlib
import sys

payload = json.loads(open(sys.argv[1]).read())
workspace = pathlib.Path(sys.argv[2]).resolve()
resolved = payload["resolved"]
sources = payload["sources"]
assert resolved["project"] == "_claw-dev", resolved
assert resolved["agent"] == "codex", resolved
assert resolved["branch"] == "feature/directive", resolved
assert resolved["workspace_mode"] == "git_worktree", resolved
assert resolved["workspace_materialization_required"] is True, resolved
assert sources["project"] == "directive", sources
assert sources["agent"] == "directive", sources
assert sources["branch"] == "directive", sources
assert sources["workspace_mode"] == "branch_target", sources
assert resolved["project_root"] == str(workspace / "projects" / "_claw-dev"), resolved
assert payload["ctx_footer"] == "ctx: project=_claw-dev agent=codex branch=feature/directive", payload
PY

reply_out="$tmp_root/reply.json"
bind_context \
  --message $'/agent codex /project _claw-dev @feature/directive\nFollow-up' \
  --reply-message $'Previous reply\nctx: project=demo-project agent=claude branch=reply/thread' \
  --default-project branch-project \
  --default-agent codex > "$reply_out"

python3 - "$reply_out" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
resolved = payload["resolved"]
sources = payload["sources"]
assert payload["reply_context"] == {
    "project": "demo-project",
    "agent": "claude",
    "branch": "reply/thread",
}, payload["reply_context"]
assert resolved["project"] == "demo-project", resolved
assert resolved["agent"] == "claude", resolved
assert resolved["branch"] == "reply/thread", resolved
assert sources["project"] == "reply_context", sources
assert sources["agent"] == "reply_context", sources
assert sources["branch"] == "reply_context", sources
assert payload["ctx_footer"] == "ctx: project=demo-project agent=claude branch=reply/thread", payload
PY

footer_default_out="$tmp_root/footer-default.json"
bind_context \
  --message $'Continue the same scope\nctx: project=branch-project agent=codex branch=release/v2' > "$footer_default_out"

python3 - "$footer_default_out" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
resolved = payload["resolved"]
sources = payload["sources"]
assert payload["defaults"] == {
    "project": "branch-project",
    "agent": "codex",
    "branch": "release/v2",
}, payload["defaults"]
assert resolved["project"] == "branch-project", resolved
assert resolved["agent"] == "codex", resolved
assert resolved["branch"] == "release/v2", resolved
assert resolved["workspace_mode"] == "isolated_checkout", resolved
assert sources["project"] == "default", sources
assert sources["agent"] == "default", sources
assert sources["branch"] == "default", sources
assert sources["workspace_mode"] == "project_default", sources
assert payload["message_body"] == "Continue the same scope", payload["message_body"]
PY

unknown_out="$tmp_root/unknown.stdout"
unknown_err="$tmp_root/unknown.stderr"
if bind_context --message "/project missing-project inspect status" >"$unknown_out" 2>"$unknown_err"; then
  echo "Expected unknown project binding to fail" >&2
  exit 1
fi

python3 - "$unknown_err" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
assert payload["code"] == "CONTEXT_INVALID", payload
assert "missing-project" in payload["error"], payload
PY

conflict_out="$tmp_root/conflict.stdout"
conflict_err="$tmp_root/conflict.stderr"
if bind_context --message "/agent codex /agent claude investigate" >"$conflict_out" 2>"$conflict_err"; then
  echo "Expected conflicting directives to fail" >&2
  exit 1
fi

python3 - "$conflict_err" <<'PY'
import json
import sys

payload = json.loads(open(sys.argv[1]).read())
assert payload["code"] == "CONTEXT_INVALID", payload
assert "Conflicting /agent directives" in payload["error"], payload
PY

echo "operator context test: ok"
