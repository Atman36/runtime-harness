#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

bash "$repo_root/tests/foundation_scaffold_test.sh"
