#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

PYTHONPATH="$repo_root/scripts" python3 - <<'PY'
from execute_job import classify_stream_line

assert classify_stream_line("plain text") == "message"
assert classify_stream_line("  <thinking> inspect logs") == "reasoning"
assert classify_stream_line("Thought: inspect logs") == "reasoning"
assert classify_stream_line("openclaw wake demo-project") == "command"
assert classify_stream_line("python3 scripts/claw.py openclaw status") == "command"
PY

echo "stream classify test: ok"
