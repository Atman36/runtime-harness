# SPEC-TEST-001 — Minimal orchestration slice

## Goal
Create a tiny, testable slice of the future project-orchestration system inside this repository.

## Deliverables
1. `README.md` at repo root with a short explanation of what was added.
2. `templates/report.template.md` — a markdown template for agent completion reports.
3. `scripts/run_demo_task.sh` — a shell script that:
   - accepts one argument: path to a spec file
   - creates `.demo-runs/<timestamp>/`
   - copies the input spec into that run folder as `spec.md`
   - creates `report.md` from the template if the template exists
   - writes a small `meta.json` with keys: `spec_path`, `created_at`, `status`
4. Make the script executable.

## Constraints
- Keep it simple and local.
- Use only shell utilities already common on macOS.
- Do not add external dependencies.
- Do not touch files outside this repository.

## Acceptance criteria
- `bash scripts/run_demo_task.sh specs/SPEC-TEST-001.md` works.
- After running, a new `.demo-runs/<timestamp>/` directory exists.
- That directory contains `spec.md`, `report.md`, and `meta.json`.
- `README.md` explains how to run the demo.

## Output expectations
When finished, also create `CODEx_TEST_REPORT.md` in repo root with:
- what you changed
- how to run it
- any caveats
