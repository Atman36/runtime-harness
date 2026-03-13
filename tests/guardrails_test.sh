#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-guardrails-test.XXXXXX")"

cleanup() {
  rm -rf "$tmp_root"
}

trap cleanup EXIT

assert_contains() {
  local path="$1"
  local expected="$2"
  if ! grep -Fq -- "$expected" "$path"; then
    echo "Expected '$expected' in $path" >&2
    exit 1
  fi
}

run_failure_case() {
  local name="$1"
  local diff_path="$2"
  local stdout_path="$3"
  local stderr_path="$4"

  if python3 "$repo_root/scripts/claw.py" guardrail-check --project "_claw-dev" --diff-path "$diff_path" >"$stdout_path" 2>"$stderr_path"; then
    echo "Expected guardrail-check to fail for case: $name" >&2
    exit 1
  fi
}

cat > "$tmp_root/unauthorized.diff" <<'EOF'
diff --git a/projects/claw-dev/state/project.yaml b/projects/claw-dev/state/project.yaml
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/projects/claw-dev/state/project.yaml
@@ -0,0 +1 @@
+slug: claw-dev
EOF

cat > "$tmp_root/assert_weakening.diff" <<'EOF'
diff --git a/tests/example_test.py b/tests/example_test.py
index 1111111..2222222 100644
--- a/tests/example_test.py
+++ b/tests/example_test.py
@@ -10,1 +10,1 @@
-assert project_count >= 2
+assert project_count >= 1
EOF

cat > "$tmp_root/scope_violation.diff" <<'EOF'
diff --git a/_system/engine/runtime.py b/_system/engine/runtime.py
index 1111111..2222222 100644
--- a/_system/engine/runtime.py
+++ b/_system/engine/runtime.py
@@ -1,1 +1,1 @@
-from pathlib import Path
+from pathlib import PurePath as Path
EOF

run_failure_case "unauthorized scaffold" "$tmp_root/unauthorized.diff" "$tmp_root/unauthorized.out" "$tmp_root/unauthorized.err"
assert_contains "$tmp_root/unauthorized.out" '"code": "unauthorized_scaffold"'
assert_contains "$tmp_root/unauthorized.out" '"passed": false'

run_failure_case "assert weakening" "$tmp_root/assert_weakening.diff" "$tmp_root/assert_weakening.out" "$tmp_root/assert_weakening.err"
assert_contains "$tmp_root/assert_weakening.out" '"code": "assert_weakening"'
assert_contains "$tmp_root/assert_weakening.out" '"fail_count": 1'

run_failure_case "edit scope violation" "$tmp_root/scope_violation.diff" "$tmp_root/scope_violation.out" "$tmp_root/scope_violation.err"
assert_contains "$tmp_root/scope_violation.out" '"code": "edit_scope_violation"'
assert_contains "$tmp_root/scope_violation.out" '"project_root_name": "_claw-dev"'

echo "guardrails test: ok"
