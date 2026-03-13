#!/usr/bin/env bash
# trigger_contract_test.sh — Typed TriggerEnvelope contract tests
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCHEMA="$REPO_ROOT/_system/contracts/trigger_envelope.schema.json"

PASS=0
FAIL=0

pass() { echo "  ok   $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL $1"; FAIL=$((FAIL + 1)); }

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/claw-trigger-contract-test.XXXXXX")"
WORKSPACE="$TMP_ROOT/workspace"
cleanup() { rm -rf "$TMP_ROOT"; }
trap cleanup EXIT

mkdir -p "$WORKSPACE/scripts"
cp -R "$REPO_ROOT/_system" "$WORKSPACE/_system"
cp -R "$REPO_ROOT/projects" "$WORKSPACE/projects"
cp "$REPO_ROOT/scripts/build_run.py" "$WORKSPACE/scripts/build_run.py"
cp "$REPO_ROOT/scripts/claw.py" "$WORKSPACE/scripts/claw.py"
cp "$REPO_ROOT/scripts/execute_job.py" "$WORKSPACE/scripts/execute_job.py"
cp "$REPO_ROOT/scripts/generate_review_batch.py" "$WORKSPACE/scripts/generate_review_batch.py"
cp "$REPO_ROOT/scripts/hooklib.py" "$WORKSPACE/scripts/hooklib.py"
cp "$REPO_ROOT/scripts/reconcile_hooks.py" "$WORKSPACE/scripts/reconcile_hooks.py"
cp "$REPO_ROOT/scripts/run_task.sh" "$WORKSPACE/scripts/run_task.sh"
cp "$REPO_ROOT/scripts/validate_artifacts.py" "$WORKSPACE/scripts/validate_artifacts.py"

DEMO_PROJECT="$WORKSPACE/projects/demo-project"
DEMO_TASK="$WORKSPACE/projects/demo-project/tasks/TASK-001.md"
VALIDATE="$WORKSPACE/scripts/validate_artifacts.py"
CLAW="$WORKSPACE/scripts/claw.py"

rm -rf "$DEMO_PROJECT/runs" "$DEMO_PROJECT/reviews" "$DEMO_PROJECT/state/queue" "$DEMO_PROJECT/state/hooks" "$DEMO_PROJECT/state/approvals"
mkdir -p \
  "$DEMO_PROJECT/runs" \
  "$DEMO_PROJECT/reviews/decisions" \
  "$DEMO_PROJECT/state/queue"/{pending,running,done,failed,awaiting_approval,dead_letter} \
  "$DEMO_PROJECT/state/hooks"/{pending,failed,sent}

# 1. Schema exists and is valid JSON
if [[ -f "$SCHEMA" ]] && python3 -c "import json; json.load(open('$SCHEMA'))" 2>/dev/null; then
  pass "schema file exists and is valid JSON"
else
  fail "schema file missing or invalid: $SCHEMA"
fi

TMP_DIR="$TMP_ROOT/trigger-dir"
mkdir -p "$TMP_DIR"
validate_trigger() {
  local label="$1"
  local json_content="$2"
  local expect_ok="$3"
  local trigger_path="$TMP_DIR/trigger.json"
  echo "$json_content" > "$trigger_path"
  local out rc
  out=$(python3 "$VALIDATE" "$trigger_path" 2>&1) && rc=0 || rc=$?
  if [[ "$expect_ok" == "ok" ]]; then
    if [[ $rc -eq 0 ]]; then pass "$label"; else fail "$label (expected pass, got: $out)"; fi
  else
    if [[ $rc -ne 0 ]]; then pass "$label"; else fail "$label (expected fail, but validation passed)"; fi
  fi
}

# 2-6. Direct schema validation
validate_trigger "valid manual envelope" '{"trigger_type": "manual"}' ok
validate_trigger "valid schedule envelope" '{"trigger_type": "schedule", "payload": {"scheduled_at": "2026-03-13T10:00:00Z", "cron_expression": "0 10 * * *"}}' ok
validate_trigger "valid webhook envelope" '{"trigger_type": "webhook", "triggered_by": "github-app", "idempotency_key": "gh-push-abc123", "payload": {"source": "github", "event_name": "push"}}' ok
validate_trigger "invalid trigger_type rejected" '{"trigger_type": "unknown"}' fail
validate_trigger "missing trigger_type rejected" '{"triggered_by": "someone"}' fail

# 7. enqueue with trigger-json
TRIGGER_JSON='{"trigger_type":"schedule","idempotency_key":"test-sched-001","payload":{"scheduled_at":"2026-03-13T10:00:00Z"}}'
enqueue_out=$(cd "$WORKSPACE" && python3 "$CLAW" openclaw enqueue "$DEMO_PROJECT" "$DEMO_TASK" --trigger-json "$TRIGGER_JSON" 2>&1) && eq_rc=0 || eq_rc=$?
if [[ $eq_rc -ne 0 ]]; then
  fail "openclaw enqueue --trigger-json failed (rc=$eq_rc): $enqueue_out"
else
  trigger_type=$(echo "$enqueue_out" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('trigger',{}).get('trigger_type',''))" 2>/dev/null || true)
  run_path=$(echo "$enqueue_out" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('run_path',''))" 2>/dev/null || true)
  if [[ "$trigger_type" == "schedule" ]]; then pass "openclaw enqueue --trigger-json returns trigger.trigger_type"; else fail "openclaw enqueue --trigger-json: expected trigger_type=schedule, got '$trigger_type'"; fi
  if [[ -n "$run_path" ]]; then
    trigger_file="$DEMO_PROJECT/$run_path/trigger.json"
    if [[ -f "$trigger_file" ]]; then
      pass "trigger.json written to run dir"
      val_out=$(python3 "$VALIDATE" "$trigger_file" 2>&1) && val_rc=0 || val_rc=$?
      if [[ $val_rc -eq 0 ]]; then pass "trigger.json passes schema validation"; else fail "trigger.json fails schema validation: $val_out"; fi
    else
      fail "trigger.json not found at expected path: $trigger_file"
    fi
  else
    fail "openclaw enqueue --trigger-json: no run_path in output"
  fi
fi

# 8. enqueue without trigger-json
plain_out=$(cd "$WORKSPACE" && python3 "$CLAW" openclaw enqueue "$DEMO_PROJECT" "$DEMO_TASK" 2>&1) && plain_rc=0 || plain_rc=$?
if [[ $plain_rc -eq 0 ]]; then
  has_trigger=$(echo "$plain_out" | python3 -c "import json,sys; d=json.load(sys.stdin); print('yes' if 'trigger' in d else 'no')" 2>/dev/null || echo "parse_err")
  if [[ "$has_trigger" == "no" ]]; then pass "enqueue without --trigger-json succeeds and omits trigger key"; else fail "enqueue without --trigger-json unexpectedly includes trigger key"; fi
else
  fail "enqueue without --trigger-json failed (rc=$plain_rc): $plain_out"
fi

echo ""
echo "trigger_contract_test: $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]]
