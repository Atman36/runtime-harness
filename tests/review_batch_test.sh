#!/usr/bin/env bash
# Tests for generate_review_batch.py: immediate triggers, cadence batching,
# deduplication, dry-run mode, and reviewer assignment.

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-review-batch-test.XXXXXX")"
generator="$repo_root/scripts/generate_review_batch.py"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}
trap cleanup EXIT

# ── helpers ───────────────────────────────────────────────────────────────────

project_root=""  # set per test

reset_project() {
  project_root="$tmp_root/test-project"
  rm -rf "$project_root"
  mkdir -p "$project_root/runs" "$project_root/reviews" "$project_root/state"
  cat > "$project_root/state/project.yaml" <<'EOF'
slug: test-project
EOF
}

make_run() {
  local run_date="$1"
  local run_id="$2"
  local result_status="$3"   # success | failed
  local agent="$4"
  local needs_review="${5:-false}"
  local risk_flags="${6:-[]}"

  local meta_status
  meta_status="$( [ "$result_status" = "success" ] && echo "completed" || echo "failed" )"

  local run_dir="$project_root/runs/$run_date/$run_id"
  mkdir -p "$run_dir"

  cat > "$run_dir/meta.json" <<EOF
{
  "run_id": "$run_id",
  "run_date": "$run_date",
  "status": "$meta_status",
  "project": "test-project",
  "task_id": "TASK-001",
  "task_title": "Test task",
  "preferred_agent": "$agent"
}
EOF

  cat > "$run_dir/result.json" <<EOF
{
  "run_id": "$run_id",
  "status": "$result_status",
  "agent": "$agent"
}
EOF

  cat > "$run_dir/job.json" <<EOF
{
  "job_version": 1,
  "run_id": "$run_id",
  "run_path": "runs/$run_date/$run_id",
  "created_at": "2024-01-01T00:00:00Z",
  "project": "test-project",
  "preferred_agent": "$agent",
  "task": {
    "id": "TASK-001",
    "title": "Test task",
    "needs_review": $needs_review,
    "risk_flags": $risk_flags
  },
  "spec": {"source_path": "specs/SPEC-001.md", "copied_path": "spec.md"},
  "artifacts": {
    "prompt_path": "prompt.txt",
    "meta_path": "meta.json",
    "report_path": "report.md",
    "result_path": "result.json",
    "stdout_path": "stdout.log",
    "stderr_path": "stderr.log"
  }
}
EOF
}

count_batches() {
  find "$project_root/reviews" -maxdepth 1 -name "REVIEW-*.json" 2>/dev/null | wc -l | tr -d ' '
}

first_batch_file() {
  find "$project_root/reviews" -maxdepth 1 -name "REVIEW-*.json" 2>/dev/null | sort | head -1
}

assert_batch_field() {
  local batch_file="$1"
  local field="$2"
  local expected="$3"
  python3 - "$batch_file" "$field" "$expected" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
field, expected = sys.argv[2], sys.argv[3]
actual = str(data.get(field, ""))
assert actual == expected, f"{field}: expected {expected!r}, got {actual!r}"
PY
}

assert_run_in_batch() {
  local batch_file="$1"
  local run_id="$2"
  python3 - "$batch_file" "$run_id" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
run_id = sys.argv[2]
ids = [r["run_id"] for r in data.get("runs", [])]
assert run_id in ids, f"{run_id} not found in batch runs: {ids}"
PY
}

# ── Test 1: failed run triggers immediate batch ───────────────────────────────

reset_project
make_run "2024-01-01" "RUN-0001" "failed" "codex"

python3 "$generator" "$project_root" >/dev/null

n="$(count_batches)"
[ "$n" -eq 1 ] || { echo "FAIL test1: expected 1 batch for failed run, got $n" >&2; exit 1; }

batch="$(first_batch_file)"
assert_batch_field "$batch" "trigger_type" "immediate"
assert_batch_field "$batch" "reviewer"     "claude"
assert_run_in_batch "$batch" "RUN-0001"

python3 - "$batch" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
trigger = data["runs"][0]["trigger"]
assert trigger == "failed", f"Expected failed trigger, got {trigger!r}"
PY

echo "  ok: test1 — failed run triggers immediate batch"

# ── Test 2: deduplication — re-running does not add more batches ──────────────

python3 "$generator" "$project_root" >/dev/null

n="$(count_batches)"
[ "$n" -eq 1 ] || { echo "FAIL test2: expected 1 batch after re-run, got $n" >&2; exit 1; }

echo "  ok: test2 — deduplication prevents double-batching"

# ── Test 3: needs_review=true triggers immediate batch ────────────────────────

reset_project
make_run "2024-01-01" "RUN-0001" "success" "codex" "true"

python3 "$generator" "$project_root" >/dev/null

n="$(count_batches)"
[ "$n" -eq 1 ] || { echo "FAIL test3: expected 1 batch, got $n" >&2; exit 1; }

batch="$(first_batch_file)"
python3 - "$batch" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
assert data["trigger_type"] == "immediate"
assert data["runs"][0]["trigger"] == "needs_review", \
    f"Expected needs_review trigger, got {data['runs'][0]['trigger']!r}"
PY

echo "  ok: test3 — needs_review flag triggers immediate batch"

# ── Test 4: risk_flags trigger ────────────────────────────────────────────────

reset_project
make_run "2024-01-01" "RUN-0001" "success" "codex" "false" '["risky_area","large_diff"]'

python3 "$generator" "$project_root" >/dev/null

n="$(count_batches)"
[ "$n" -eq 1 ] || { echo "FAIL test4: expected 1 batch, got $n" >&2; exit 1; }

batch="$(first_batch_file)"
python3 - "$batch" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
assert data["trigger_type"] == "immediate"
trigger = data["runs"][0]["trigger"]
assert trigger.startswith("risk_flags:"), f"Expected risk_flags: trigger, got {trigger!r}"
PY

echo "  ok: test4 — risk_flags trigger"

# ── Test 5: cadence batch emitted for exactly 5 successful runs ───────────────

reset_project
for i in 1 2 3 4 5; do
  make_run "2024-01-01" "$(printf 'RUN-%04d' "$i")" "success" "claude"
done

python3 "$generator" "$project_root" >/dev/null

n="$(count_batches)"
[ "$n" -eq 1 ] || { echo "FAIL test5: expected 1 cadence batch, got $n" >&2; exit 1; }

batch="$(first_batch_file)"
python3 - "$batch" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
assert data["trigger_type"] == "cadence", f"Expected cadence, got {data['trigger_type']!r}"
assert len(data["runs"]) == 5, f"Expected 5 runs, got {len(data['runs'])}"
assert data["reviewer"] == "codex", f"Expected codex reviewer for claude runs, got {data['reviewer']!r}"
PY

echo "  ok: test5 — cadence batch for 5 successful runs"

# ── Test 6: fewer than 5 successful runs → no cadence batch ──────────────────

reset_project
for i in 1 2 3 4; do
  make_run "2024-01-01" "$(printf 'RUN-%04d' "$i")" "success" "codex"
done

python3 "$generator" "$project_root" >/dev/null

n="$(count_batches)"
[ "$n" -eq 0 ] || { echo "FAIL test6: expected 0 batches for 4 successful runs, got $n" >&2; exit 1; }

echo "  ok: test6 — 4 successful runs do not trigger cadence batch"

# ── Test 7: dry-run produces no files ─────────────────────────────────────────

reset_project
make_run "2024-01-01" "RUN-0001" "failed" "codex"

python3 "$generator" --dry-run "$project_root" >/dev/null

n="$(count_batches)"
[ "$n" -eq 0 ] || { echo "FAIL test7: dry-run should not create files, got $n" >&2; exit 1; }

echo "  ok: test7 — dry-run creates no files"

# ── Test 8: opposite model reviewer from policy mapping ───────────────────────

reset_project
make_run "2024-01-01" "RUN-0001" "failed" "claude"

python3 "$generator" "$project_root" >/dev/null

batch="$(first_batch_file)"
python3 - "$batch" <<'PY'
import json, sys
data = json.loads(open(sys.argv[1]).read())
assert data["reviewer"] == "codex", \
    f"Expected codex reviewer for claude agent, got {data['reviewer']!r}"
PY

echo "  ok: test8 — reviewer is opposite model from policy mapping"

# ── Test 9: md file written alongside json ────────────────────────────────────

reset_project
make_run "2024-01-01" "RUN-0001" "failed" "codex"
python3 "$generator" "$project_root" >/dev/null

batch_json="$(first_batch_file)"
batch_md="${batch_json%.json}.md"

[ -f "$batch_md" ] || { echo "FAIL test9: expected .md file alongside .json, not found: $batch_md" >&2; exit 1; }
grep -q "# Review Batch" "$batch_md" || { echo "FAIL test9: .md missing header" >&2; exit 1; }

echo "  ok: test9 — .md file written alongside .json"

# ── Test 10: mix immediate + cadence in one run ───────────────────────────────

reset_project
# 5 successful (cadence) + 1 failed (immediate)
for i in 1 2 3 4 5; do
  make_run "2024-01-01" "$(printf 'RUN-%04d' "$i")" "success" "codex"
done
make_run "2024-01-01" "RUN-0006" "failed" "codex"

python3 "$generator" "$project_root" >/dev/null

n="$(count_batches)"
[ "$n" -eq 2 ] || { echo "FAIL test10: expected 2 batches (1 immediate + 1 cadence), got $n" >&2; exit 1; }

python3 - "$project_root/reviews" <<'PY'
import json, pathlib, sys
reviews = sorted(pathlib.Path(sys.argv[1]).glob("REVIEW-*.json"))
types = {json.loads(p.read_text())["trigger_type"] for p in reviews}
assert types == {"immediate", "cadence"}, f"Expected both trigger types, got {types}"
PY

echo "  ok: test10 — mix of immediate and cadence batches"

# ── Test 11: copied entrypoint runs without hooklib.py in workspace ──────────

rm -rf "$workspace"
mkdir -p "$workspace/scripts"
cp -R "$repo_root/_system" "$workspace/_system"
cp "$repo_root/scripts/generate_review_batch.py" "$workspace/scripts/generate_review_batch.py"

project_root="$workspace/test-project"
mkdir -p "$project_root/runs" "$project_root/reviews" "$project_root/state"
cat > "$project_root/state/project.yaml" <<'EOF'
slug: test-project
EOF

run_dir="$project_root/runs/2024-01-01/RUN-0001"
mkdir -p "$run_dir"
cat > "$run_dir/meta.json" <<'EOF'
{
  "run_id": "RUN-0001",
  "run_date": "2024-01-01",
  "status": "failed",
  "project": "test-project",
  "task_id": "TASK-001",
  "task_title": "Copied entrypoint task",
  "preferred_agent": "codex"
}
EOF

cat > "$run_dir/result.json" <<'EOF'
{
  "run_id": "RUN-0001",
  "status": "failed",
  "agent": "codex"
}
EOF

cat > "$run_dir/job.json" <<'EOF'
{
  "job_version": 1,
  "run_id": "RUN-0001",
  "run_path": "runs/2024-01-01/RUN-0001",
  "created_at": "2024-01-01T00:00:00Z",
  "project": "test-project",
  "preferred_agent": "codex",
  "task": {
    "id": "TASK-001",
    "title": "Copied entrypoint task",
    "needs_review": false,
    "risk_flags": []
  },
  "spec": {"source_path": "specs/SPEC-001.md", "copied_path": "spec.md"},
  "artifacts": {
    "prompt_path": "prompt.txt",
    "meta_path": "meta.json",
    "report_path": "report.md",
    "result_path": "result.json",
    "stdout_path": "stdout.log",
    "stderr_path": "stderr.log"
  }
}
EOF

python3 "$workspace/scripts/generate_review_batch.py" "$project_root" >/dev/null

n="$(find "$project_root/reviews" -maxdepth 1 -name "REVIEW-*.json" | wc -l | tr -d ' ')"
[ "$n" -eq 1 ] || { echo "FAIL test11: copied generate_review_batch.py should work without hooklib.py, got $n batches" >&2; exit 1; }

echo "  ok: test11 — copied generate_review_batch.py does not require hooklib.py"

echo "review batch test: ok"
