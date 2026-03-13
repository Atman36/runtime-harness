---
id: TASK-004
title: "claw import-project CLI command"
status: done
spec: ../specs/SPEC-004.md
preferred_agent: codex
review_policy: standard
priority: high
project: _claw-dev
needs_review: false
risk_flags: []
tags: [cli, project-management, epic-12]
dependencies: []
epic: 12
---

# Task

## Goal
Add `claw import-project --slug <slug> --path <repo-path>` command that bootstraps
a new claw project from an existing external repository in one CLI call.

## Notes
- Uses `projects/_template/` scaffold as the base
- Generates WORKFLOW.md with `edit_scope` derived from top-level dirs of the repo
- Creates `state/project.yaml` with `slug` and `source_path`
- Does NOT modify the external repo itself
- New test in `tests/import_project_test.sh`
