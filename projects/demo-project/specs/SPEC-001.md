# SPEC-001 — Bootstrap `demo-project`

## Goal
Verify that `claw` can hold a real project with stable docs, task, spec and state paths.

## Scope
- Keep one starter spec and task
- Use this project as a target for future `task -> job` work

## Constraints
- Runtime run artifacts belong in `runs/`
- Review output belongs in `reviews/`

## Acceptance Criteria
- A launcher can resolve task and spec files under `projects/demo-project/`
- The project remains suitable as a smoke target for future engine integration

## Notes
- Expand this spec once task-to-job orchestration is in place
