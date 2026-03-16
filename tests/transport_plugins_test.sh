#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-transport-plugins-test.XXXXXX")"
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

claw() {
  python3 "$workspace/scripts/claw.py" "$@"
}

project_root="$workspace/projects/demo-project"
mkdir -p "$project_root/docs"

# Test 1: explicit backend discovery surfaces the built-in registry contract.
list_out="$tmp_root/transports.json"
claw openclaw transports "$project_root" > "$list_out"

python3 - "$list_out" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["status"] == "ok", payload
assert payload["project"] == "demo-project", payload
assert len(payload["backends"]) == 1, payload
backend = payload["backends"][0]
assert backend["backend_id"] == "file_exchange", backend
assert backend["provider"] == "file_exchange", backend
assert backend["enabled"] is True, backend
assert backend["source"] == "default.file_exchange", backend
PY

# Test 2: doctor is green on default config.
doctor_ok="$tmp_root/doctor-ok.json"
claw openclaw doctor "$project_root" > "$doctor_ok"

python3 - "$doctor_ok" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["status"] == "ok", payload
assert payload["errors"] == 0, payload
assert payload["diagnostics"] == [], payload
PY

# Test 3: malformed legacy file_exchange config is rejected by doctor and runtime.
cat > "$project_root/state/project.yaml" <<'EOF'
slug: demo-project
operator_transport:
  file_exchange:
    deny_globs: nope
EOF

doctor_bad="$tmp_root/doctor-bad.json"
if claw openclaw doctor "$project_root" > "$doctor_bad"; then
  echo "Expected malformed transport config doctor to fail" >&2
  exit 1
fi

python3 - "$doctor_bad" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["status"] == "error", payload
assert payload["errors"] == 1, payload
codes = {item["code"] for item in payload["diagnostics"]}
assert "TRANSPORT_CONFIG_INVALID" in codes, payload
PY

input_file="$tmp_root/input.txt"
printf 'payload\n' > "$input_file"
put_stdout="$tmp_root/file-put.stdout"
put_stderr="$tmp_root/file-put.stderr"
if claw openclaw file-put "$project_root" docs/upload.txt --source-file "$input_file" >"$put_stdout" 2>"$put_stderr"; then
  echo "Expected file-put to reject malformed transport config" >&2
  exit 1
fi

python3 - "$put_stderr" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["code"] == "TRANSPORT_CONFIG_INVALID", payload
assert "deny_globs" in payload["error"], payload
PY

# Test 4: duplicate provider entries fail deterministically.
cat > "$project_root/state/project.yaml" <<'EOF'
slug: demo-project
operator_transport:
  backends:
    - id: exchange-one
      provider: file_exchange
      config: {}
    - id: exchange-two
      provider: file_exchange
      config: {}
EOF

doctor_dup="$tmp_root/doctor-duplicate.json"
if claw openclaw doctor "$project_root" > "$doctor_dup"; then
  echo "Expected duplicate transport provider doctor to fail" >&2
  exit 1
fi

python3 - "$doctor_dup" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["status"] == "error", payload
codes = {item["code"] for item in payload["diagnostics"]}
assert "TRANSPORT_PROVIDER_DUPLICATE" in codes, payload
PY

# Test 5: required binary and backend setup checks are surfaced via doctor.
python3 - "$workspace/_system/registry/operator_transports.yaml" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
path.write_text(
    path.read_text(encoding="utf-8")
    + """
  missing_binary:
    module: _system.engine.transport_plugins.missing_binary
    factory: load_transport_backend
    description: Test transport requiring an external CLI
    required_binaries:
      - claw-missing-binary
""",
    encoding="utf-8",
)
PY

cat > "$workspace/_system/engine/transport_plugins/missing_binary.py" <<'EOF'
from __future__ import annotations

from _system.engine.operator_transport import TransportDiagnostic


class MissingBinaryBackend:
    def validate_config(self, config, *, backend_id):
        if not isinstance(config, dict):
            raise ValueError(f"Transport backend '{backend_id}' config must be an object")
        return dict(config)

    def setup_checks(self, *, project_root, backend_id, config):
        return [
            TransportDiagnostic(
                severity="error",
                code="TRANSPORT_UNSUPPORTED_COMBINATION",
                message=f"Transport backend '{backend_id}' does not support this test combination",
                backend_id=backend_id,
                provider="missing_binary",
                hint="Use a supported provider in project state",
            )
        ]


def load_transport_backend():
    return MissingBinaryBackend()
EOF

cat > "$project_root/state/project.yaml" <<'EOF'
slug: demo-project
operator_transport:
  backends:
    - id: missing-binary
      provider: missing_binary
      config: {}
EOF

doctor_missing="$tmp_root/doctor-missing.json"
if claw openclaw doctor "$project_root" > "$doctor_missing"; then
  echo "Expected missing binary transport doctor to fail" >&2
  exit 1
fi

python3 - "$doctor_missing" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["status"] == "error", payload
codes = {item["code"] for item in payload["diagnostics"]}
assert "TRANSPORT_BINARY_MISSING" in codes, payload
assert "TRANSPORT_UNSUPPORTED_COMBINATION" in codes, payload
PY

# Test 6: built-in file_exchange setup checks catch unsupported workspace mode.
cat > "$project_root/state/project.yaml" <<'EOF'
slug: demo-project
execution:
  workspace_mode: unsupported_mode
EOF

doctor_workspace="$tmp_root/doctor-workspace.json"
if claw openclaw doctor "$project_root" > "$doctor_workspace"; then
  echo "Expected unsupported workspace mode doctor to fail" >&2
  exit 1
fi

python3 - "$doctor_workspace" <<'PY'
import json
import pathlib
import sys

payload = json.loads(pathlib.Path(sys.argv[1]).read_text())
assert payload["status"] == "error", payload
codes = {item["code"] for item in payload["diagnostics"]}
assert "TRANSPORT_UNSUPPORTED_COMBINATION" in codes, payload
assert any("workspace_mode" in item["message"] for item in payload["diagnostics"]), payload
PY

echo "transport plugins test: ok"
