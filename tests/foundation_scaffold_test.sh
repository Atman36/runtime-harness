#!/usr/bin/env bash

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
tmp_root="$(mktemp -d "${TMPDIR:-/tmp}/claw-foundation-test.XXXXXX")"
workspace="$tmp_root/workspace"

cleanup() {
  rm -rf "$tmp_root"
}

trap cleanup EXIT

assert_file() {
  local path="$1"
  if [ ! -f "$path" ]; then
    echo "Expected file to exist: $path" >&2
    exit 1
  fi
}

assert_dir() {
  local path="$1"
  if [ ! -d "$path" ]; then
    echo "Expected directory to exist: $path" >&2
    exit 1
  fi
}

assert_contains() {
  local path="$1"
  local expected="$2"
  if ! grep -Fq "$expected" "$path"; then
    echo "Expected '$expected' in $path" >&2
    exit 1
  fi
}

mkdir -p "$workspace"

cp -R "$repo_root/_system" "$workspace/_system"
cp -R "$repo_root/projects" "$workspace/projects"
mkdir -p "$workspace/scripts"
cp "$repo_root/scripts/create_project.sh" "$workspace/scripts/create_project.sh"

bash "$workspace/scripts/create_project.sh" alpha-project "$workspace"

project_root="$workspace/projects/alpha-project"

assert_dir "$project_root/docs"
assert_dir "$project_root/specs"
assert_dir "$project_root/tasks"
assert_dir "$project_root/runs"
assert_dir "$project_root/reviews"
assert_dir "$project_root/state"
assert_dir "$project_root/state/hooks"
assert_dir "$project_root/state/hooks/pending"
assert_dir "$project_root/state/hooks/sent"
assert_dir "$project_root/state/hooks/failed"
assert_dir "$project_root/.codex/agents"
assert_dir "$project_root/.claude/agents"

assert_file "$workspace/_system/registry/agents.yaml"
assert_file "$workspace/_system/registry/routing_rules.yaml"
assert_file "$workspace/_system/registry/reviewer_policy.yaml"
assert_file "$workspace/_system/templates/task.template.md"
assert_file "$workspace/_system/templates/spec.template.md"
assert_file "$workspace/_system/templates/prompt.template.md"
assert_file "$workspace/_system/templates/report.template.md"

assert_file "$project_root/docs/README.md"
assert_file "$project_root/specs/SPEC-001.md"
assert_file "$project_root/tasks/TASK-001.md"
assert_file "$project_root/state/project.yaml"
assert_file "$project_root/.codex/config.toml"
assert_file "$project_root/.codex/agents/project-explorer.toml"
assert_file "$project_root/.codex/agents/project-reviewer.toml"
assert_file "$project_root/.codex/agents/project-worker.toml"
assert_file "$project_root/.claude/agents/project-explorer.md"
assert_file "$project_root/.claude/agents/project-reviewer.md"
assert_file "$project_root/.claude/agents/project-implementer.md"

assert_contains "$project_root/docs/README.md" "alpha-project"
assert_contains "$project_root/tasks/TASK-001.md" "spec: ../specs/SPEC-001.md"
assert_contains "$project_root/state/project.yaml" "slug: alpha-project"
assert_contains "$project_root/.codex/agents/project-explorer.toml" "alpha-project"
assert_contains "$project_root/.claude/agents/project-explorer.md" "alpha-project"

echo "foundation scaffold test: ok"
