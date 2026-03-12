#!/usr/bin/env python3

import sys
from pathlib import Path

from hooklib import dispatch_hook_file, is_stale_pending_hook, iter_hook_files, read_json, resolve_project_roots, stale_seconds_from_env, write_json_atomic


def is_dead_letter(hook_path: Path) -> bool:
    """Return True if hook payload is marked dead_letter or has exhausted max_delivery_attempts."""
    payload = read_json(hook_path)
    if payload.get("dead_letter") is True:
        return True
    delivery_attempts = payload.get("delivery_attempts", 0)
    max_delivery_attempts = payload.get("max_delivery_attempts", 3)
    if isinstance(delivery_attempts, int) and int(delivery_attempts) >= int(max_delivery_attempts):
        return True
    return False


def main() -> int:
    if len(sys.argv) > 2:
        print("Usage: python3 scripts/reconcile_hooks.py [project-root]", file=sys.stderr)
        return 1

    repo_root = Path(__file__).resolve().parent.parent
    target = sys.argv[1] if len(sys.argv) == 2 else None
    stale_after_seconds = stale_seconds_from_env()

    try:
        project_roots = resolve_project_roots(repo_root, target)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    failures = 0

    for project_root in project_roots:
        failed_hooks = iter_hook_files(project_root, "failed")
        stale_pending_hooks = [
            hook_path
            for hook_path in iter_hook_files(project_root, "pending")
            if is_stale_pending_hook(hook_path, stale_after_seconds)
        ]

        for hook_path in failed_hooks + stale_pending_hooks:
            if is_dead_letter(hook_path):
                payload = read_json(hook_path)
                hook_id = payload.get("hook_id") or hook_path.stem
                print(f"{project_root.name}: {hook_id} -> dead_letter (skipped)")
                failures += 1
                continue
            outcome = dispatch_hook_file(hook_path)
            print(f"{project_root.name}: {outcome['hook_id']} -> {outcome['status']}")
            if outcome["outcome"] == "failed":
                failures += 1

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
