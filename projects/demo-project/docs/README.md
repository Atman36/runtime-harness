# demo-project

Demo project used to exercise the `claw` foundation layer and future orchestration runs.

## Current flow

Create a deterministic run directory from the starter task:

```bash
bash scripts/run_task.sh projects/demo-project/tasks/TASK-001.md
```

Create and execute the run immediately:

```bash
bash scripts/run_task.sh --execute projects/demo-project/tasks/TASK-001.md
```

This writes runtime artifacts under `projects/demo-project/runs/YYYY-MM-DD/RUN-XXXX/` and updates `meta.json`, `result.json`, `report.md`, `stdout.log`, and `stderr.log` after execution.
