# SPEC-004 — claw import-project CLI command

## Context

The claw project is at `/Users/Apple/progect/claw`.

Currently, adding a new project to claw requires manually copying `projects/_template/`,
editing `state/project.yaml`, and hand-writing `docs/WORKFLOW.md`. This is slow and
error-prone for onboarding external repos. This spec adds a single CLI command that
does it all in one call.

## Goal

1. Add `cmd_import_project(args)` to `scripts/claw.py`
2. Register `import-project` CLI command in `build_parser()`
3. Add `tests/import_project_test.sh`

## Files to create / modify

### MODIFY: `/Users/Apple/progect/claw/scripts/claw.py`

Add function `cmd_import_project` (place it near `cmd_create_project` at line 1353).

**Algorithm:**

```python
def cmd_import_project(args: argparse.Namespace) -> int:
    slug = args.slug
    source_path = Path(args.path).expanduser().resolve()

    # Validate slug: lowercase alphanumeric + hyphens
    import re
    if not re.match(r'^[a-z0-9][a-z0-9-]*$', slug):
        print(json.dumps({"error": "Invalid slug. Use lowercase letters, digits, hyphens only."}), file=sys.stderr)
        return 1

    # Target project directory
    project_root = REPO_ROOT / "projects" / slug
    if project_root.exists():
        print(json.dumps({"error": f"Project '{slug}' already exists at {project_root}"}), file=sys.stderr)
        return 1

    # Discover edit_scope from top-level directories of source repo
    # (exclude hidden dirs and common non-source dirs)
    EXCLUDED_DIRS = {'.git', '.github', 'node_modules', '__pycache__', '.venv', 'venv', '.tox', 'dist', 'build'}
    if source_path.is_dir():
        edit_scope = sorted(
            d.name for d in source_path.iterdir()
            if d.is_dir() and d.name not in EXCLUDED_DIRS and not d.name.startswith('.')
        )
    else:
        edit_scope = []

    # Copy template scaffold
    template_root = REPO_ROOT / "projects" / "_template"
    import shutil
    shutil.copytree(str(template_root), str(project_root))

    # Write state/project.yaml
    import yaml
    project_yaml = {
        "slug": slug,
        "source_path": str(source_path),
        "created_at": utc_now(),
    }
    state_dir = project_root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "project.yaml").write_text(
        yaml.dump(project_yaml, default_flow_style=False, allow_unicode=True),
        encoding="utf-8"
    )

    # Write docs/WORKFLOW.md with discovered edit_scope
    workflow_path = project_root / "docs" / "WORKFLOW.md"
    workflow_template = workflow_path.read_text(encoding="utf-8")
    # Replace placeholder slug
    workflow_content = workflow_template.replace("{{PROJECT_SLUG}}", slug)
    # Inject edit_scope list
    if edit_scope:
        scope_yaml_lines = "\n".join(f"    - {d}" for d in edit_scope)
        workflow_content = workflow_content.replace(
            "  edit_scope: []",
            "  edit_scope:\n" + scope_yaml_lines
        )
    workflow_path.write_text(workflow_content, encoding="utf-8")

    payload = {
        "status": "created",
        "slug": slug,
        "project_root": str(project_root),
        "source_path": str(source_path),
        "edit_scope": edit_scope,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0
```

**Register in `build_parser()` near `create-project`:**

```python
import_project = subcommands.add_parser(
    "import-project",
    help="Bootstrap a new project from an existing external repository"
)
import_project.add_argument("--slug", required=True, help="Project slug (lowercase, hyphens)")
import_project.add_argument("--path", required=True, help="Path to the external repository")
import_project.set_defaults(func=cmd_import_project)
```

### CREATE: `/Users/Apple/progect/claw/tests/import_project_test.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

SLUG="test-import-$$"
FAKE_REPO="$(mktemp -d)"
# Create some top-level dirs in fake repo
mkdir -p "$FAKE_REPO/src" "$FAKE_REPO/docs" "$FAKE_REPO/tests" "$FAKE_REPO/.git"

cleanup() {
    rm -rf "$FAKE_REPO"
    rm -rf "$REPO_ROOT/projects/$SLUG" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== import-project test ==="

OUT=$(python3 scripts/claw.py import-project --slug "$SLUG" --path "$FAKE_REPO")
echo "Output: $OUT"

# Check project directory created
[ -d "projects/$SLUG" ] || { echo "FAIL: project dir not created"; exit 1; }

# Check state/project.yaml
[ -f "projects/$SLUG/state/project.yaml" ] || { echo "FAIL: project.yaml not created"; exit 1; }
grep -q "slug: $SLUG" "projects/$SLUG/state/project.yaml" || { echo "FAIL: slug not in project.yaml"; exit 1; }

# Check WORKFLOW.md has edit_scope with discovered dirs
[ -f "projects/$SLUG/docs/WORKFLOW.md" ] || { echo "FAIL: WORKFLOW.md not created"; exit 1; }
grep -q "docs\|src\|tests" "projects/$SLUG/docs/WORKFLOW.md" || { echo "FAIL: edit_scope not populated"; exit 1; }
# Placeholder should be replaced
grep -qv "{{PROJECT_SLUG}}" "projects/$SLUG/docs/WORKFLOW.md" || { echo "FAIL: placeholder not replaced"; exit 1; }

# Duplicate slug should fail
if python3 scripts/claw.py import-project --slug "$SLUG" --path "$FAKE_REPO" 2>/dev/null; then
    echo "FAIL: duplicate slug should have been rejected"
    exit 1
fi

echo "PASS: import-project test"
```

### Add to `/Users/Apple/progect/claw/tests/run_all.sh`

Add the new test to the test suite (find the pattern where other tests are invoked and add):
```bash
bash tests/import_project_test.sh
```

## Acceptance Criteria

- `python3 scripts/claw.py import-project --slug my-app --path /tmp/some-repo` creates `projects/my-app/`
- `projects/my-app/state/project.yaml` contains `slug: my-app` and `source_path`
- `projects/my-app/docs/WORKFLOW.md` has `edit_scope` populated from top-level dirs of the source repo (excluding `.git`, `node_modules`, etc.)
- `{{PROJECT_SLUG}}` placeholder is replaced with the actual slug
- Re-importing the same slug returns exit code 1 with JSON error
- `bash tests/import_project_test.sh` passes
- `bash tests/run_all.sh` still passes

## Constraints

- Do NOT modify the external repo at `--path`
- `.git` and other hidden dirs must be excluded from `edit_scope`
- `state/project.yaml` overwrites any existing template placeholder file (template may have one)
- Use `shutil.copytree` for copying — do not shell out to `cp -r`
- `yaml` import: PyYAML is already available in the project (used in `workflow_contract.py`)
