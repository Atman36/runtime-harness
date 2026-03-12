# claw

Local project shell and orchestration workspace for spec-driven work with Codex and Claude.

## Current slice

This repository now implements the `foundation` stage from [docs/PLAN.md](docs/PLAN.md):

- `_system/registry/` stores agent routing and review policy
- `_system/templates/` stores shared task, spec, prompt and report templates
- `projects/_template/` is the reusable project scaffold
- `projects/demo-project/` is the first concrete workspace inside `claw`
- `scripts/create_project.sh` creates a new project from the scaffold
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

Run the shell smoke tests:

```bash
bash tests/run_all.sh
```

## Notes

- `run_demo_task.sh` now prefers `_system/templates/report.template.md` and falls back to `templates/report.template.md`.
- Report placeholder interpolation is still not implemented; this matches the known gap documented in [docs/PRD.md](docs/PRD.md).
