#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-reviewer-policy-runtime-test.XXXXXX")"
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

assert_contains() {
  local path="$1"
  local expected="$2"
  if ! grep -Fq -- "$expected" "$path"; then
    echo "Expected '$expected' in $path" >&2
    exit 1
  fi
}

assert_batch_count() {
  local reviews_dir="$1"
  local expected="$2"
  local actual
  actual="$(find "$reviews_dir" -maxdepth 1 -name 'REVIEW-*.json' | wc -l | tr -d ' ')"
  if [ "$actual" -ne "$expected" ]; then
    echo "Expected $expected review batch(es), got $actual in $reviews_dir" >&2
    exit 1
  fi
}

mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
cp "$repo_root/scripts/run_task.sh" "$workspace/scripts/run_task.sh"
cp "$repo_root/scripts/build_run.py" "$workspace/scripts/build_run.py"
cp "$repo_root/scripts/execute_job.sh" "$workspace/scripts/execute_job.sh"
cp "$repo_root/scripts/execute_job.py" "$workspace/scripts/execute_job.py"
cp "$repo_root/scripts/validate_artifacts.py" "$workspace/scripts/validate_artifacts.py"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"
cp "$repo_root/scripts/hooklib.py" "$workspace/scripts/hooklib.py"
cp "$repo_root/scripts/claw.py" "$workspace/scripts/claw.py"

cat > "$workspace/_system/registry/reviewer_policy.yaml" <<'EOF'
reviewer_policy:
  cadence:
    successful_runs_batch: 2
  immediate_triggers:
    - failed
  default_mapping:
    codex: claude
    claude: codex
EOF

project_root="$workspace/projects/demo-project"
rm -rf "$project_root/runs" "$project_root/reviews" "$project_root/state/queue"
mkdir -p "$project_root/runs" "$project_root/reviews" "$project_root/state/queue"/{pending,running,done,failed,awaiting_approval}

cat > "$workspace/scripts/fake_success_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

echo "POLICY SUCCESS"
EOF
chmod +x "$workspace/scripts/fake_success_agent.sh"

cat > "$workspace/scripts/fake_fail_agent.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

echo "POLICY FAILURE" >&2
exit 7
EOF
chmod +x "$workspace/scripts/fake_fail_agent.sh"

cat > "$workspace/scripts/fake_reviewer.py" <<'EOF'
#!/usr/bin/env python3
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.stdin.read()
for path in sorted((Path.cwd() / "reviews" / "decisions").glob("*.json")):
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("decision") != "pending":
        continue
    payload["decided_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload["decision"] = "approved"
    payload["findings"] = []
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
EOF
chmod +x "$workspace/scripts/fake_reviewer.py"

task_path="$project_root/tasks/TASK-001.md"
today="$(date +"%Y-%m-%d")"
reviews_dir="$project_root/reviews"
cadence_state="$project_root/state/review_cadence.json"

python3 "$workspace/scripts/claw.py" enqueue "$task_path" >/dev/null
risk_run="$(find "$project_root/runs/$today" -maxdepth 1 -type d -name 'RUN-*' | sort | tail -1)"

python3 - "$risk_run/job.json" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.loads(path.read_text(encoding="utf-8"))
data["task"]["risk_flags"] = ["risky_area"]
path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
PY

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
CLAW_AGENT_COMMAND_CLAUDE="python3 $workspace/scripts/fake_reviewer.py" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" >/dev/null

assert_batch_count "$reviews_dir" 0
assert_file "$cadence_state"
assert_contains "$cadence_state" '"successful_since_last_batch": 1'

python3 "$workspace/scripts/claw.py" enqueue "$task_path" >/dev/null

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_success_agent.sh" \
CLAW_AGENT_COMMAND_CLAUDE="python3 $workspace/scripts/fake_reviewer.py" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" >/dev/null

assert_batch_count "$reviews_dir" 1
assert_contains "$cadence_state" '"successful_since_last_batch": 0'

python3 - "$reviews_dir" <<'PY'
import json
import pathlib
import sys

reviews = sorted(pathlib.Path(sys.argv[1]).glob("REVIEW-*.json"))
assert len(reviews) == 1, reviews
payload = json.loads(reviews[0].read_text(encoding="utf-8"))
assert payload["trigger_type"] == "cadence", payload
assert len(payload["runs"]) == 2, payload
run_ids = sorted(run["run_id"] for run in payload["runs"])
assert run_ids == ["RUN-0001", "RUN-0002"], run_ids
PY

python3 "$workspace/scripts/claw.py" enqueue "$task_path" >/dev/null

CLAW_AGENT_COMMAND_CODEX="bash $workspace/scripts/fake_fail_agent.sh" \
CLAW_AGENT_COMMAND_CLAUDE="python3 $workspace/scripts/fake_reviewer.py" \
  python3 "$workspace/scripts/claw.py" worker "$project_root" >/dev/null

assert_batch_count "$reviews_dir" 2

python3 - "$reviews_dir" <<'PY'
import json
import pathlib
import sys

reviews = sorted(pathlib.Path(sys.argv[1]).glob("REVIEW-*.json"))
assert len(reviews) == 2, reviews
payloads = [json.loads(path.read_text(encoding="utf-8")) for path in reviews]
immediate = next(batch for batch in payloads if batch["trigger_type"] == "immediate")
assert immediate["runs"][0]["trigger"] == "failed", immediate
PY

echo "reviewer policy runtime test: ok"
