#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-listener-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT

fail() {
  echo "FAIL: $1" >&2
  exit 1
}

pass() {
  echo "ok: $1"
}

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/dispatch_hooks.py" "$workspace/scripts/dispatch_hooks.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/reconcile_hooks.py" "$workspace/scripts/reconcile_hooks.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"

project_root="$workspace/projects/demo-project"
rm -rf "$project_root/runs" "$project_root/reviews" "$project_root/state/queue" "$project_root/state/listener_log.jsonl"
mkdir -p "$project_root/runs" "$project_root/reviews"
mkdir -p "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter}

cat > "$workspace/scripts/listener_capture.py" <<'EOF'
#!/usr/bin/env python3
import json
import sys
from pathlib import Path

destination = Path(sys.argv[1])
payload = {
    "argv": sys.argv[2:],
}
destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
EOF
chmod +x "$workspace/scripts/listener_capture.py"

cat > "$workspace/scripts/listener_fail.py" <<'EOF'
#!/usr/bin/env python3
import sys
sys.stderr.write("listener exploded\n")
raise SystemExit(7)
EOF
chmod +x "$workspace/scripts/listener_fail.py"

cat > "$workspace/_system/registry/listeners.yaml" <<EOF
listeners:
  - id: run-finish-success
    event: run_finished
    condition:
      status: success
    command: python3 scripts/listener_capture.py $tmp_root/matched.json {run_id} {status}
    enabled: true

  - id: run-finish-mismatch
    event: run_finished
    condition:
      status: failed
    command: python3 scripts/listener_capture.py $tmp_root/should-not-exist.json mismatch
    enabled: true

  - id: review-failure
    event: review_created
    command: python3 scripts/listener_fail.py
    enabled: true
EOF

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

cat >/dev/null
echo "Listener dispatch fake agent output"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

task_path="$project_root/tasks/TASK-001.md"
enqueue_out="$tmp_root/enqueue.json"
python3 "$workspace/scripts/claw.py" enqueue "$task_path" > "$enqueue_out"
run_id="$(python3 -c "import json; print(json.load(open('$enqueue_out'))['job_id'])")"
run_dir="$(python3 -c "import json; import pathlib; payload=json.load(open('$enqueue_out')); print((pathlib.Path('$project_root') / payload['run_path']).resolve())")"

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" --once >/dev/null

[ -f "$tmp_root/matched.json" ] || fail "matching listener did not run"
[ ! -e "$tmp_root/should-not-exist.json" ] || fail "non-matching listener should have been skipped"
[ -f "$project_root/state/listener_log.jsonl" ] || fail "listener log missing"

python3 - "$tmp_root/matched.json" "$project_root/state/listener_log.jsonl" "$run_id" <<'PY' || fail "run_finished listener assertions failed"
import json
import sys
from pathlib import Path

captured = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
log_lines = [json.loads(line) for line in Path(sys.argv[2]).read_text(encoding="utf-8").splitlines() if line.strip()]
run_id = sys.argv[3]

assert captured["argv"] == [run_id, "success"], captured
matched = [entry for entry in log_lines if entry["listener_id"] == "run-finish-success"]
assert matched and matched[0]["status"] == "success", log_lines
assert all(entry["listener_id"] != "run-finish-mismatch" for entry in log_lines), log_lines
assert any(entry["event"] == "run_started" for entry in log_lines) is False, log_lines
print("ok")
PY
pass "run_finished dispatch matches enabled listeners and skips mismatches"

rm -f "$project_root/state/listener_log.jsonl"
mkdir -p "$project_root/reviews"
cat > "$run_dir/result.json" <<'EOF'
{
  "run_id": "RUN-0001",
  "status": "failed",
  "agent": "codex"
}
EOF

python3 "$workspace/scripts/claw.py" openclaw review-batch "$project_root" >/dev/null

python3 - "$project_root/state/listener_log.jsonl" <<'PY' || fail "review listener failure should be logged without crashing"
import json
import sys
from pathlib import Path

entries = [json.loads(line) for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines() if line.strip()]
failure = next(entry for entry in entries if entry["listener_id"] == "review-failure")
assert failure["status"] == "failed", failure
assert "listener exploded" in failure["error"], failure
print("ok")
PY
pass "listener failures are logged and do not abort review creation"

echo ""
echo "listener_dispatch_test: all tests passed"
