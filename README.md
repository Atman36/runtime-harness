# Minimal orchestration slice

This repository contains a tiny local demo for bootstrapping a spec run.

## Added pieces

- `templates/report.template.md` - markdown template copied into each demo run
- `scripts/run_demo_task.sh` - creates a timestamped run folder in `.demo-runs/`

## Run the demo

```bash
bash scripts/run_demo_task.sh specs/SPEC-TEST-001.md
```

After running, inspect the latest folder in `.demo-runs/` for `spec.md`, `report.md`, and `meta.json`.
