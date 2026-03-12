#!/usr/bin/env bash

set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: bash scripts/run_demo_task.sh <spec-path>" >&2
  exit 1
fi

spec_path="$1"

if [ ! -f "$spec_path" ]; then
  echo "Spec file not found: $spec_path" >&2
  exit 1
fi

run_id="$(date +"%Y%m%d-%H%M%S")"
created_at="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
run_dir=".demo-runs/$run_id"
template_path="templates/report.template.md"

mkdir -p "$run_dir"
cp "$spec_path" "$run_dir/spec.md"

if [ -f "$template_path" ]; then
  cp "$template_path" "$run_dir/report.md"
fi

json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

cat > "$run_dir/meta.json" <<EOF
{
  "spec_path": "$(json_escape "$spec_path")",
  "created_at": "$(json_escape "$created_at")",
  "status": "created"
}
EOF

printf 'Created demo run: %s\n' "$run_dir"
