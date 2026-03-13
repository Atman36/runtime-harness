# SPEC-008 — Project command registry in WORKFLOW.md

## Context

The claw project is at `/Users/Apple/progect/claw`.

Currently `claw orchestrate` and worker hard-code the test command as
`bash tests/run_all.sh`. Different projects use different test runners
(e.g. `pytest`, `npm test`, `make check`). This spec adds a `commands`
section to WORKFLOW.md that lets each project declare its own test/lint/build/smoke
commands, and makes the orchestrator/worker use them.

## Goal

1. Add `commands` field to `WorkflowContract` dataclass in `workflow_contract.py`
2. Add `commands` block to `projects/_template/docs/WORKFLOW.md`
3. Add `cmd_run_checks` — `claw run-checks --project slug [--type test|lint|build|smoke]`
4. Update `cmd_orchestrate` to use `commands.test` instead of hardcoded fallback

## Files to modify / create

### MODIFY: `_system/engine/workflow_contract.py`

Read the file first.

**1. Add `Commands` dataclass:**

```python
@dataclass(frozen=True)
class Commands:
    test: str = "bash tests/run_all.sh"
    lint: str = ""
    build: str = ""
    smoke: str = ""
```

**2. Add `commands` field to `WorkflowContract`:**

```python
@dataclass(frozen=True)
class WorkflowContract:
    ...
    commands: Commands = field(default_factory=Commands)
    ...
```

**3. Update `_build_contract(data)` to parse `commands`:**

In the function that builds a WorkflowContract from a dict (search for where `WorkflowScope` is built),
add after the scope block:

```python
    # commands
    cmds_data = data.get("commands") or {}
    commands = Commands(
        test=str(cmds_data.get("test", "bash tests/run_all.sh")),
        lint=str(cmds_data.get("lint", "")),
        build=str(cmds_data.get("build", "")),
        smoke=str(cmds_data.get("smoke", "")),
    )
```

Pass `commands=commands` to the `WorkflowContract(...)` constructor.

**4. Export `Commands` from `__init__.py`:**

Add `from _system.engine.workflow_contract import Commands` and `"Commands"` to `__all__`.

### MODIFY: `projects/_template/docs/WORKFLOW.md`

Add a `commands` block to the YAML front matter after `notes`:

```yaml
commands:
  test: "bash tests/run_all.sh"
  lint: ""
  build: ""
  smoke: ""
```

Also add it to `projects/demo-project/docs/WORKFLOW.md` with the same defaults.

### MODIFY: `scripts/claw.py`

Read the file before editing.

**1. Add `cmd_run_checks`:**

```python
def cmd_run_checks(args: argparse.Namespace) -> int:
    """Run the registered command(s) for a project from its WORKFLOW.md commands registry."""
    try:
        project_root = resolve_project_root(args.project_root)
    except FileNotFoundError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

    contract = load_workflow_contract(project_root)
    if contract is None:
        # Graceful fallback
        print(json.dumps({
            "status": "no_contract",
            "message": "No WORKFLOW.md found — using default test command",
            "command": "bash tests/run_all.sh",
        }), ensure_ascii=False)
        contract_commands = None
    else:
        contract_commands = contract.commands

    check_type = getattr(args, "type", "test") or "test"

    # Map type to command
    if contract_commands:
        cmd_str = {
            "test": contract_commands.test,
            "lint": contract_commands.lint,
            "build": contract_commands.build,
            "smoke": contract_commands.smoke,
        }.get(check_type, "")
    else:
        cmd_str = "bash tests/run_all.sh" if check_type == "test" else ""

    if not cmd_str:
        print(json.dumps({
            "status": "skipped",
            "type": check_type,
            "message": f"No '{check_type}' command registered in WORKFLOW.md commands",
        }), ensure_ascii=False)
        return 0

    import subprocess as _sp
    result = _sp.run(
        cmd_str,
        shell=True,
        cwd=str(project_root.parent.parent),  # run from repo root
        capture_output=False,  # let output flow through
    )
    payload = {
        "status": "success" if result.returncode == 0 else "failed",
        "type": check_type,
        "command": cmd_str,
        "returncode": result.returncode,
    }
    print(json.dumps(payload, ensure_ascii=False))
    return result.returncode
```

**2. Update `cmd_orchestrate` to use `commands.test`:**

Find the place in `cmd_orchestrate` where worker is called or where test command would run.
Currently `run_worker_once` internally calls `bash tests/run_all.sh` — this is in
`execute_job.py`. For now, just expose the command in the orchestrate payload:

In the `payload` dict at the end of `cmd_orchestrate`, add:
```python
"test_command": (workflow_contract.commands.test if workflow_contract else "bash tests/run_all.sh"),
```

This makes the command visible. The actual execution uses it through `cmd_run_checks`.

**3. Register in `build_parser()`:**

```python
run_checks = subcommands.add_parser(
    "run-checks",
    help="Run registered project commands (test/lint/build/smoke) from WORKFLOW.md"
)
run_checks.add_argument("project_root", help="Project root path or slug")
run_checks.add_argument("--type", default="test", choices=["test", "lint", "build", "smoke"],
                         help="Which command to run (default: test)")
run_checks.set_defaults(func=cmd_run_checks)
```

### CREATE: `tests/command_registry_test.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

echo "=== command registry test ==="

# Test 1: run-checks on demo-project (has test command = bash tests/run_all.sh)
OUT=$(python3 scripts/claw.py run-checks projects/demo-project --type test)
echo "run-checks result: $OUT"
STATUS=$(echo "$OUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[ "$STATUS" = "success" ] || { echo "FAIL: run-checks test should succeed, got: $STATUS"; exit 1; }

# Test 2: run-checks for type with no command registered → skipped (not error)
OUT=$(python3 scripts/claw.py run-checks projects/demo-project --type smoke)
echo "smoke result: $OUT"
STATUS=$(echo "$OUT" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
[ "$STATUS" = "skipped" ] || { echo "FAIL: unregistered type should be skipped, got: $STATUS"; exit 1; }

# Test 3: workflow-validate shows commands
OUT=$(python3 scripts/claw.py workflow-validate projects/demo-project)
echo "validate: $OUT"
# Should not error
echo "$OUT" | python3 -c "import sys,json; json.load(sys.stdin)" || { echo "FAIL: workflow-validate not valid JSON"; exit 1; }

echo "PASS: command registry test"
```

### Add to `tests/run_all.sh`

```bash
bash tests/command_registry_test.sh
```

## Acceptance Criteria

- `WorkflowContract` has a `commands` attribute with `test`, `lint`, `build`, `smoke` fields
- `claw run-checks projects/demo-project` runs `bash tests/run_all.sh` and exits 0
- `claw run-checks projects/demo-project --type smoke` exits 0 with `{"status": "skipped"}` when smoke is empty
- `claw orchestrate` payload includes `test_command` field
- Missing `commands` block in WORKFLOW.md → graceful fallback to `bash tests/run_all.sh`
- `bash tests/command_registry_test.sh` passes
- `bash tests/run_all.sh` still passes

## Constraints

- Do NOT change `execute_job.py` in this task — `test_command` integration into the worker loop is out of scope
- `shell=True` is a **controlled compromise** in `cmd_run_checks`: the command comes from the WORKFLOW.md contract (project-controlled config, not user runtime input). This is consistent with how `bash tests/run_all.sh` is already hardcoded. A follow-up task should migrate to argv contract when the trusted_command layer supports multi-word commands.
- Both `_template` and `demo-project` WORKFLOW.md files must be updated
