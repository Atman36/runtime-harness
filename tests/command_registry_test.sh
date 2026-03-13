#!/usr/bin/env bash

set -euo pipefail

if [ "${CLAW_SKIP_COMMAND_REGISTRY_TEST:-0}" = "1" ]; then
  echo "command registry test: skipped in nested run-checks execution"
  exit 0
fi

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== command registry test ==="

# Test 1: run-checks on demo-project uses registered/default test command and succeeds.
OUT=$(CLAW_SKIP_COMMAND_REGISTRY_TEST=1 python3 scripts/claw.py run-checks projects/demo-project --type test)
echo "run-checks result: $OUT"
STATUS=$(echo "$OUT" | tail -n 1 | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[ "$STATUS" = "success" ] || { echo "FAIL: run-checks test should succeed, got: $STATUS"; exit 1; }

# Test 2: empty command type returns skipped, not error.
OUT=$(python3 scripts/claw.py run-checks projects/demo-project --type smoke)
echo "smoke result: $OUT"
STATUS=$(echo "$OUT" | tail -n 1 | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[ "$STATUS" = "skipped" ] || { echo "FAIL: unregistered type should be skipped, got: $STATUS"; exit 1; }

# Test 3: workflow-validate exposes commands in the payload.
OUT=$(python3 scripts/claw.py workflow-validate projects/demo-project)
echo "validate: $OUT"
COMMAND=$(echo "$OUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['commands']['test'])")
[ "$COMMAND" = "bash tests/run_all.sh" ] || { echo "FAIL: workflow-validate should surface default test command, got: $COMMAND"; exit 1; }

# Test 4: missing commands block falls back to bash tests/run_all.sh.
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-command-registry-test.XXXXXX")"
trap 'rm -rf "$tmp_root"' EXIT
mkdir -p "$tmp_root/projects/fallback-project/state" "$tmp_root/projects/fallback-project/docs" "$tmp_root/tests"
cat > "$tmp_root/projects/fallback-project/state/project.yaml" <<'EOF'
slug: fallback-project
EOF
cat > "$tmp_root/projects/fallback-project/docs/WORKFLOW.md" <<'EOF'
---
contract_version: 1
project: "fallback-project"
---
EOF
cat > "$tmp_root/tests/run_all.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "fallback checks ok"
EOF
chmod +x "$tmp_root/tests/run_all.sh"

OUT=$(python3 scripts/claw.py run-checks "$tmp_root/projects/fallback-project" --type test)
echo "fallback result: $OUT"
COMMAND=$(echo "$OUT" | tail -n 1 | python3 -c "import sys,json; print(json.load(sys.stdin)['command'])")
STATUS=$(echo "$OUT" | tail -n 1 | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[ "$COMMAND" = "bash tests/run_all.sh" ] || { echo "FAIL: fallback command should be bash tests/run_all.sh, got: $COMMAND"; exit 1; }
[ "$STATUS" = "success" ] || { echo "FAIL: fallback run-checks should succeed, got: $STATUS"; exit 1; }

echo "PASS: command registry test"
