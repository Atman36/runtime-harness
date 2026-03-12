#!/usr/bin/env python3

import sys
from pathlib import Path

from hooklib import dispatch_hook_file, iter_hook_files, resolve_project_roots


def main() -> int:
    if len(sys.argv) > 2:
        print("Usage: python3 scripts/dispatch_hooks.py [project-root]", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parent.parent
    target = sys.argv[1] if len(sys.argv) == 2 else None

    try:
        project_roots = resolve_project_roots(repo_root, target)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    failures = 0

    for project_root in project_roots:
        for hook_path in iter_hook_files(project_root, "pending"):
            outcome = dispatch_hook_file(hook_path)
            print(f"{project_root.name}: {outcome['hook_id']} -> {outcome['status']}")
            if outcome["outcome"] == "failed":
                failures += 1

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
