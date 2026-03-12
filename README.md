# claw

Local project shell and orchestration workspace for spec-driven work with Codex and Claude.

## Current slice

This repository now implements the `foundation` stage and the first `task/spec -> job` adapter slice from [docs/PLAN.md](docs/PLAN.md):

- `_system/registry/` stores agent routing and review policy
- `_system/templates/` stores shared task, spec, prompt and report templates
- `projects/_template/` is the reusable project scaffold
- `projects/demo-project/` is the first concrete workspace inside `claw`
- `scripts/create_project.sh` creates a new project from the scaffold
- `scripts/run_task.sh` converts a task plus referenced spec into a deterministic run directory
- `scripts/run_demo_task.sh` keeps the original demo run flow for smoke checks

## Repository layout

```text
claw/
├── _system/
│   ├── registry/
│   ├── scripts/
│   └── templates/
├── projects/
│   ├── _template/
│   └── demo-project/
├── scripts/
├── tests/
└── docs/
```

## Commands

Create a new project scaffold inside the current repository:

```bash
bash scripts/create_project.sh my-project
```

Create a project scaffold in another destination root:

```bash
bash scripts/create_project.sh my-project /tmp/claw-workspace
```

Run the legacy demo flow:

```bash
bash scripts/run_demo_task.sh specs/SPEC-TEST-001.md
```

Create a run directory from a project task:

```bash
bash scripts/run_task.sh projects/demo-project/tasks/TASK-001.md
```

Run the shell smoke tests:

```bash
bash tests/run_all.sh
```

## Notes

- `run_task.sh` creates `projects/<slug>/runs/YYYY-MM-DD/RUN-XXXX/` with `task.md`, `spec.md`, `prompt.txt`, `meta.json`, `job.json`, `result.json`, `report.md`, `stdout.log`, and `stderr.log`.
- `run_demo_task.sh` remains as a legacy smoke runner and still prefers `_system/templates/report.template.md` with fallback to `templates/report.template.md`.
