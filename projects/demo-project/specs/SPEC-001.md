# SPEC-001 — Bootstrap `demo-project`

## Goal
Verify that `claw` can turn a project task plus spec into deterministic run artifacts.

## Scope
- Keep one starter spec and task
- Generate a stable run directory for the demo task
- Produce machine-readable job metadata plus human-readable prompt/report files

## Constraints
- Runtime run artifacts belong in `runs/`
- Review output belongs in `reviews/`

## Acceptance Criteria
- A launcher can resolve task and spec files under `projects/demo-project/`
- The launcher creates `runs/YYYY-MM-DD/RUN-XXXX/`
- Each run contains `task.md`, `spec.md`, `prompt.txt`, `meta.json`, `job.json`, `result.json`, and `report.md`

## Notes
- Expand this spec once engine execution is wired on top of the adapter
