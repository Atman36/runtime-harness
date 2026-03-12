# CODEx Test Report

## What changed

- Added `README.md` with a short description of the demo runner and usage instructions.
- Added `templates/report.template.md` as the starter markdown report for each run.
- Added `scripts/run_demo_task.sh` to create `.demo-runs/<timestamp>/`, copy the spec to `spec.md`, copy the report template to `report.md`, and generate `meta.json`.
- Marked `scripts/run_demo_task.sh` as executable.

## How to run it

```bash
bash scripts/run_demo_task.sh specs/SPEC-TEST-001.md
```

The command creates a new timestamped directory under `.demo-runs/`.

## Caveats

- `report.md` is copied from the template as-is; placeholders are not interpolated.
- The script expects the provided spec path to exist relative to the current working directory.
