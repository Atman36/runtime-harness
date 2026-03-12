#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

assert_not_ignored() {
  local path="$1"
  if git check-ignore -q "$path"; then
    echo "Expected path to be trackable, but it is ignored: $path" >&2
    git check-ignore -v "$path" >&2 || true
    exit 1
  fi
  if [ ! -e "$path" ]; then
    echo "Expected path to exist: $path" >&2
    exit 1
  fi
}

assert_not_ignored "docs/PLAN.md"
assert_not_ignored "docs/STATUS.md"
assert_not_ignored "docs/BACKLOG.md"
assert_not_ignored "projects/_template/docs/README.md"
assert_not_ignored "projects/demo-project/docs/README.md"

echo "docs tracking test: ok"
