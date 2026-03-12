#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"

bash "$repo_root/tests/foundation_scaffold_test.sh"
bash "$repo_root/tests/task_to_job_test.sh"
bash "$repo_root/tests/execute_job_test.sh"
bash "$repo_root/tests/hook_lifecycle_test.sh"
bash "$repo_root/tests/queue_cli_test.sh"
bash "$repo_root/tests/queue_lifecycle_test.sh"
bash "$repo_root/tests/contracts_validation_test.sh"
bash "$repo_root/tests/review_batch_test.sh"
