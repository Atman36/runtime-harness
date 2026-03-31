#!/usr/bin/env python3
"""Validate claw run artifacts against JSON Schema contracts.

Usage:
  # Validate a single artifact file:
  python3 scripts/validate_artifacts.py runs/2024-03-12/RUN-0001/job.json

  # Validate all artifacts in a run directory:
  python3 scripts/validate_artifacts.py runs/2024-03-12/RUN-0001/

  # Validate all runs under a project root:
  python3 scripts/validate_artifacts.py --project projects/my-project

  # Validate all projects in the repo:
  python3 scripts/validate_artifacts.py --all

Exit codes:
  0  All validated artifacts are valid
  1  One or more validation errors found
  2  Usage error or missing required files
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import jsonschema

    _HAS_JSONSCHEMA = True
except ImportError:
    _HAS_JSONSCHEMA = False

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
CONTRACTS_DIR = REPO_ROOT / "_system" / "contracts"

ARTIFACT_SCHEMAS = {
    "job.json": "job.schema.json",
    "result.json": "result.schema.json",
    "meta.json": "meta.schema.json",
}

OPTIONAL_ARTIFACT_SCHEMAS = {
    "trigger.json": "trigger_envelope.schema.json",
}

QUEUE_SCHEMA = "queue_item.schema.json"
QUEUE_STATE_DIRS = {"pending", "running", "awaiting_approval", "done", "failed", "dead_letter"}
WAKE_SCHEMA = "wake_item.schema.json"
CLAIM_SCHEMA = "task_claim.schema.json"
SESSION_SCHEMA = "session_state.schema.json"
OPERATOR_SESSION_SCHEMA = "operator_session_state.schema.json"
OPERATOR_JOB_SCHEMA = "operator_job_state.schema.json"
SESSION_DOCS_SCHEMA = "session_docs_manifest.schema.json"
REVIEW_DECISION_SCHEMA = "review_decision.schema.json"


def load_schema(schema_filename: str) -> dict:
    path = CONTRACTS_DIR / schema_filename
    if not path.is_file():
        raise FileNotFoundError(f"Schema not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_with_jsonschema(data: dict, schema: dict) -> list[str]:
    validator_cls = jsonschema.validators.validator_for(schema)
    validator = validator_cls(schema)
    errors = []
    for err in validator.iter_errors(data):
        loc = ".".join(str(p) for p in err.absolute_path) or "<root>"
        errors.append(f"{loc}: {err.message}")
    return errors


def validate_fallback(data: dict, schema: dict) -> list[str]:
    """Minimal fallback validator: required fields, type, enum, const."""
    errors: list[str] = []
    _check_node(data, schema, "", errors)
    return errors


def _check_node(data, schema: dict, path: str, errors: list) -> None:
    label = path or "<root>"
    schema_type = schema.get("type")

    if schema_type:
        types = schema_type if isinstance(schema_type, list) else [schema_type]
        type_ok = any(_matches_type(data, t) for t in types)
        if not type_ok:
            errors.append(f"{label}: expected type {schema_type}, got {type(data).__name__}")
            return

    if "const" in schema and data != schema["const"]:
        errors.append(f"{label}: expected {schema['const']!r}, got {data!r}")

    if "enum" in schema and data not in schema["enum"]:
        errors.append(f"{label}: {data!r} not in {schema['enum']}")

    if isinstance(data, dict):
        for req in schema.get("required", []):
            if req not in data:
                errors.append(f"{label}: missing required field '{req}'")
        for key, sub_schema in schema.get("properties", {}).items():
            if key in data:
                _check_node(data[key], sub_schema, f"{path}.{key}" if path else key, errors)

    elif isinstance(data, list):
        item_schema = schema.get("items", {})
        for i, item in enumerate(data):
            _check_node(item, item_schema, f"{label}[{i}]", errors)


def _matches_type(data, t: str) -> bool:
    if t == "null":
        return data is None
    if t == "boolean":
        return isinstance(data, bool)
    if t == "integer":
        return isinstance(data, int) and not isinstance(data, bool)
    if t == "number":
        return isinstance(data, (int, float)) and not isinstance(data, bool)
    if t == "string":
        return isinstance(data, str)
    if t == "array":
        return isinstance(data, list)
    if t == "object":
        return isinstance(data, dict)
    return False


def validate_file(artifact_path: Path) -> list[str]:
    schema_filename = ARTIFACT_SCHEMAS.get(artifact_path.name) or OPTIONAL_ARTIFACT_SCHEMAS.get(artifact_path.name)
    if (
        not schema_filename
        and artifact_path.suffix == '.json'
        and artifact_path.parent.name in QUEUE_STATE_DIRS
        and artifact_path.parent.parent.name == "queue"
    ):
        schema_filename = QUEUE_SCHEMA
    if (
        not schema_filename
        and artifact_path.suffix == '.json'
        and artifact_path.parent.name == "pending"
        and artifact_path.parent.parent.name == "wakes"
    ):
        schema_filename = WAKE_SCHEMA
    if (
        not schema_filename
        and artifact_path.suffix == ".json"
        and artifact_path.parent.name == "claims"
        and artifact_path.parent.parent.name == "state"
    ):
        schema_filename = CLAIM_SCHEMA
    if not schema_filename and artifact_path.suffix == ".json":
        if artifact_path.parent.name == "sessions" and artifact_path.parent.parent.name == "state":
            schema_filename = SESSION_SCHEMA
        elif (
            artifact_path.parent.parent.name == "sessions"
            and artifact_path.parent.parent.parent.name == "state"
        ):
            schema_filename = SESSION_SCHEMA
        elif artifact_path.parent.name == "operator_sessions" and artifact_path.parent.parent.name == "state":
            schema_filename = OPERATOR_SESSION_SCHEMA
        elif (
            artifact_path.parent.parent.name == "operator_sessions"
            and artifact_path.parent.parent.parent.name == "state"
        ):
            schema_filename = OPERATOR_SESSION_SCHEMA
        elif artifact_path.parent.name == "operator_jobs" and artifact_path.parent.parent.name == "state":
            schema_filename = OPERATOR_JOB_SCHEMA
        elif (
            artifact_path.parent.parent.name == "operator_jobs"
            and artifact_path.parent.parent.parent.name == "state"
        ):
            schema_filename = OPERATOR_JOB_SCHEMA
        elif artifact_path.name == "manifest.json" and artifact_path.parent.parent.name == "session_docs":
            schema_filename = SESSION_DOCS_SCHEMA
        elif artifact_path.parent.name == "decisions" and artifact_path.parent.parent.name == "reviews":
            schema_filename = REVIEW_DECISION_SCHEMA
    if not schema_filename:
        known = ', '.join(list(ARTIFACT_SCHEMAS) + list(OPTIONAL_ARTIFACT_SCHEMAS))
        return [f"No schema registered for '{artifact_path.name}' (expected one of: {known} or a queue item JSON)"]

    try:
        schema = load_schema(schema_filename)
    except FileNotFoundError as exc:
        return [str(exc)]

    try:
        data = json.loads(artifact_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"Invalid JSON: {exc}"]

    if _HAS_JSONSCHEMA:
        return validate_with_jsonschema(data, schema)
    return validate_fallback(data, schema)


def validate_run_dir(run_dir: Path) -> dict[str, list[str]]:
    """Return {artifact_name: [errors]} for each known artifact in the run dir."""
    results = {}
    for artifact_name in ARTIFACT_SCHEMAS:
        artifact_path = run_dir / artifact_name
        if artifact_path.is_file():
            results[artifact_name] = validate_file(artifact_path)
        else:
            results[artifact_name] = [f"File not found: {artifact_path}"]
    for artifact_name in OPTIONAL_ARTIFACT_SCHEMAS:
        artifact_path = run_dir / artifact_name
        if artifact_path.is_file():
            results[artifact_name] = validate_file(artifact_path)
    return results


def iter_run_dirs(project_root: Path):
    runs_root = project_root / "runs"
    if not runs_root.is_dir():
        return
    for date_dir in sorted(runs_root.iterdir()):
        if not date_dir.is_dir():
            continue
        for run_dir in sorted(date_dir.iterdir()):
            if run_dir.is_dir() and run_dir.name.startswith("RUN-"):
                yield run_dir


def print_result(label: str, errors: list[str], verbose: bool) -> int:
    if errors:
        print(f"  FAIL {label}")
        for e in errors:
            print(f"       {e}")
        return 1
    if verbose:
        print(f"  ok   {label}")
    return 0


def validate_project(project_root: Path, quiet: bool) -> int:
    total_errors = 0
    for run_dir in iter_run_dirs(project_root):
        results = validate_run_dir(run_dir)
        run_rel = run_dir.relative_to(project_root)
        has_errors = any(errs for errs in results.values())
        if has_errors or not quiet:
            print(f"  Run: {run_rel}")
        for artifact_name, errors in results.items():
            total_errors += print_result(artifact_name, errors, not quiet)
    return total_errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate claw run artifacts against JSON Schema contracts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--project", metavar="PROJECT_ROOT", help="Validate all runs in a project directory")
    group.add_argument("--all", action="store_true", help="Validate all runs in all projects under this repo")
    group.add_argument("--workflow", metavar="PROJECT_ROOT", help="Validate the workflow contract for a project")
    parser.add_argument("path", nargs="?", help="Run directory or artifact file to validate")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress passing artifact lines")
    args = parser.parse_args()

    if not _HAS_JSONSCHEMA:
        print("Warning: jsonschema not installed — using built-in fallback validator.", file=sys.stderr)
        print("         Install with: pip install jsonschema", file=sys.stderr)

    total_errors = 0

    if args.workflow:
        from _system.engine.workflow_contract import load_workflow_contract, validate_workflow_contract
        project_root = Path(args.workflow).expanduser().resolve()
        contract = load_workflow_contract(project_root)
        if contract is None:
            if not args.quiet:
                print(f"No workflow contract found at {project_root / 'docs' / 'WORKFLOW.md'}")
            return 0
        errors = validate_workflow_contract(contract)
        if errors:
            print(f"Workflow contract errors ({len(errors)}):")
            for e in errors:
                print(f"  - {e}")
            return 1
        if not args.quiet:
            print("Workflow contract is valid.")
        return 0

    elif args.all:
        projects_root = REPO_ROOT / "projects"
        if not projects_root.is_dir():
            print(f"Projects directory not found: {projects_root}", file=sys.stderr)
            return 2
        projects = sorted(p for p in projects_root.iterdir() if p.is_dir() and not p.name.startswith("_"))
        if not projects:
            if not args.quiet:
                print("No projects found.")
            return 0
        for project_root in projects:
            print(f"Project: {project_root.name}")
            total_errors += validate_project(project_root, args.quiet)

    elif args.project:
        project_root = Path(args.project).expanduser().resolve()
        if not project_root.is_dir():
            print(f"Project directory not found: {project_root}", file=sys.stderr)
            return 2
        print(f"Project: {project_root.name}")
        total_errors += validate_project(project_root, args.quiet)

    elif args.path:
        target = Path(args.path).expanduser().resolve()
        if not target.exists():
            print(f"Path not found: {target}", file=sys.stderr)
            return 2
        if target.is_file():
            total_errors += print_result(str(target), validate_file(target), not args.quiet)
        elif target.is_dir():
            # Run dir: contains artifact files directly; project dir: contains runs/
            if any((target / a).is_file() for a in ARTIFACT_SCHEMAS):
                results = validate_run_dir(target)
                for artifact_name, errors in results.items():
                    total_errors += print_result(artifact_name, errors, not args.quiet)
            else:
                print(f"Project: {target.name}")
                total_errors += validate_project(target, args.quiet)
    else:
        parser.print_help()
        return 2

    if total_errors:
        print(f"\n{total_errors} validation error(s) found.", file=sys.stderr)
        return 1

    if not args.quiet:
        print("All artifacts valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
