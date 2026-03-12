#!/usr/bin/env python3
"""Generate review batch artifacts for a claw project.

Scans runs under projects/<slug>/runs/ and applies reviewer_policy.yaml rules:

  Immediate triggers (single run warrants review):
    - result status is "failed"
    - job.task.needs_review is true
    - job.task.risk_flags contains any of: risky_area, uncertainty, large_diff

  Cadence trigger (batch of N successful runs):
    - Every successful_runs_batch (default: 5) successful runs not yet reviewed

The reviewer is the opposite model from reviewer_policy.yaml default_mapping.
Already-reviewed runs (found in any existing batch in reviews/) are skipped.

Batch artifacts are written to projects/<slug>/reviews/:
  REVIEW-<YYYY-MM-DD>-<seq>.json  — machine-readable manifest
  REVIEW-<YYYY-MM-DD>-<seq>.md   — human-readable review brief

Usage:
  python3 scripts/generate_review_batch.py <project-root>
  python3 scripts/generate_review_batch.py --dry-run <project-root>
  python3 scripts/generate_review_batch.py --all
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
POLICY_PATH = REPO_ROOT / "_system" / "registry" / "reviewer_policy.yaml"

IMMEDIATE_STATUS_TRIGGERS = {"failed"}
IMMEDIATE_FLAG_TRIGGERS = {"risky_area", "uncertainty", "large_diff"}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_policy(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"reviewer_policy.yaml not found: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    policy = loaded.get("reviewer_policy", {})
    if not isinstance(policy, dict):
        raise ValueError("reviewer_policy.yaml malformed: expected mapping under 'reviewer_policy'")
    return policy


def read_json_safe(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def resolve_reviewer(preferred_agent: str, policy: dict) -> str:
    mapping = policy.get("default_mapping", {})
    return mapping.get(preferred_agent, "claude")


def load_run(run_dir: Path, project_root: Path) -> dict | None:
    meta = read_json_safe(run_dir / "meta.json")
    result = read_json_safe(run_dir / "result.json")
    job = read_json_safe(run_dir / "job.json")

    run_id = meta.get("run_id") or job.get("run_id") or run_dir.name
    run_date = meta.get("run_date") or run_dir.parent.name

    if not run_id:
        return None

    status = result.get("status") or meta.get("status") or "unknown"
    agent = result.get("agent") or meta.get("preferred_agent") or job.get("preferred_agent") or ""

    task = job.get("task", {})
    needs_review = task.get("needs_review", False)
    risk_flags = task.get("risk_flags", [])
    if not isinstance(risk_flags, list):
        risk_flags = []

    return {
        "run_id": run_id,
        "run_date": run_date,
        "run_path": run_dir.relative_to(project_root).as_posix(),
        "status": status,
        "agent": agent,
        "task_id": task.get("id", meta.get("task_id", "")),
        "task_title": task.get("title", meta.get("task_title", "")),
        "needs_review": bool(needs_review),
        "risk_flags": risk_flags,
    }


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


def load_reviewed_run_ids(reviews_dir: Path) -> set[str]:
    """Collect all run_ids already present in any existing batch file."""
    reviewed: set[str] = set()
    if not reviews_dir.is_dir():
        return reviewed
    for batch_file in reviews_dir.glob("REVIEW-*.json"):
        try:
            data = json.loads(batch_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for run_entry in data.get("runs", []):
            rid = run_entry.get("run_id")
            if rid:
                reviewed.add(rid)
    return reviewed


def classify_run(run: dict) -> str | None:
    """Return a trigger reason string, or None if the run does not need immediate review."""
    if run["status"] in IMMEDIATE_STATUS_TRIGGERS:
        return "failed"
    if run["needs_review"]:
        return "needs_review"
    flags = set(run.get("risk_flags", []))
    matching = flags & IMMEDIATE_FLAG_TRIGGERS
    if matching:
        return f"risk_flags:{','.join(sorted(matching))}"
    return None


def next_batch_seq(reviews_dir: Path, today: str) -> str:
    prefix = f"REVIEW-{today}-"
    existing = [p.stem for p in reviews_dir.glob(f"{prefix}*.json")] if reviews_dir.is_dir() else []
    max_seq = 0
    for name in existing:
        try:
            seq = int(name.rsplit("-", 1)[-1])
            max_seq = max(max_seq, seq)
        except (ValueError, IndexError):
            pass
    return f"REVIEW-{today}-{max_seq + 1:04d}"


def write_batch(reviews_dir: Path, batch: dict) -> tuple[Path, Path]:
    reviews_dir.mkdir(parents=True, exist_ok=True)
    batch_id = batch["batch_id"]

    json_path = reviews_dir / f"{batch_id}.json"
    md_path = reviews_dir / f"{batch_id}.md"

    json_path.write_text(json.dumps(batch, indent=2) + "\n", encoding="utf-8")

    lines = [
        f"# Review Batch: {batch_id}",
        "",
        f"- Project: {batch['project']}",
        f"- Reviewer: {batch['reviewer']}",
        f"- Trigger type: {batch['trigger_type']}",
        f"- Generated at: {batch['generated_at']}",
        f"- Run count: {len(batch['runs'])}",
        "",
        "## Runs",
        "",
    ]
    for run in batch["runs"]:
        lines.append(f"### {run['run_id']} ({run['run_date']})")
        lines.append(f"- Path: `{run['run_path']}`")
        lines.append(f"- Status: {run['status']}")
        lines.append(f"- Agent: {run['agent']}")
        lines.append(f"- Task: {run['task_id']} — {run['task_title']}")
        lines.append(f"- Trigger: {run['trigger']}")
        if run.get("risk_flags"):
            lines.append(f"- Risk flags: {', '.join(run['risk_flags'])}")
        lines.append("")

    lines.extend([
        "## Review Instructions",
        "",
        f"Reviewer agent `{batch['reviewer']}` should inspect each run's `result.json`, `report.md`,",
        "and `stdout.log` / `stderr.log`. Flag any concerns in a response artifact.",
        "",
    ])

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def write_decision_stubs(project_root: Path, batch: dict) -> None:
    """Create pending decision stub files for each run in a batch."""
    decisions_dir = project_root / "reviews" / "decisions"
    decisions_dir.mkdir(parents=True, exist_ok=True)
    batch_id = batch["batch_id"]
    reviewer = batch["reviewer"]

    for run in batch["runs"]:
        run_id = run["run_id"]
        trigger = run.get("trigger", "")
        stub = {
            "review_id": str(uuid4()),
            "run_id": run_id,
            "reviewer_agent": reviewer,
            "decided_at": None,
            "decision": "pending",
            "findings": [],
            "batch_id": batch_id,
            "trigger": trigger,
        }
        stub_path = decisions_dir / f"{batch_id}--{run_id}.json"
        stub_path.write_text(json.dumps(stub, indent=2) + "\n", encoding="utf-8")


def generate_batches(project_root: Path, policy: dict, dry_run: bool = False) -> list[dict]:
    """Generate review batches for one project. Returns list of batch dicts emitted."""
    reviews_dir = project_root / "reviews"
    reviewed_ids = load_reviewed_run_ids(reviews_dir)
    cadence_batch_size = int(policy.get("cadence", {}).get("successful_runs_batch", 5))
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    immediate_runs: list[dict] = []
    cadence_queue: list[dict] = []

    for run_dir in iter_run_dirs(project_root):
        run = load_run(run_dir, project_root)
        if run is None or run["run_id"] in reviewed_ids:
            continue
        trigger = classify_run(run)
        if trigger:
            run["trigger"] = trigger
            immediate_runs.append(run)
        elif run["status"] == "success":
            run["trigger"] = "cadence"
            cadence_queue.append(run)

    batches: list[dict] = []

    # One batch for all immediate triggers
    if immediate_runs:
        agents = [r["agent"] for r in immediate_runs if r["agent"]]
        reviewer = resolve_reviewer(agents[0] if agents else "codex", policy)
        batch_id = next_batch_seq(reviews_dir, today) if not dry_run else f"REVIEW-{today}-DRY"
        batch = {
            "batch_version": 1,
            "batch_id": batch_id,
            "generated_at": utc_now(),
            "project": project_root.name,
            "reviewer": reviewer,
            "trigger_type": "immediate",
            "runs": immediate_runs,
        }
        batches.append(batch)
        if not dry_run:
            json_path, md_path = write_batch(reviews_dir, batch)
            print(f"  Written: {json_path.relative_to(project_root)}")
            print(f"  Written: {md_path.relative_to(project_root)}")
            write_decision_stubs(project_root, batch)
        else:
            print(f"  [dry-run] Would write immediate batch: {batch_id} ({len(immediate_runs)} run(s))")

    # Cadence batches — only emit complete groups
    for i in range(0, len(cadence_queue), cadence_batch_size):
        chunk = cadence_queue[i : i + cadence_batch_size]
        if len(chunk) < cadence_batch_size:
            remaining = cadence_batch_size - len(chunk)
            print(f"  Pending cadence: {len(chunk)} successful run(s) (need {remaining} more to trigger)")
            continue
        agents = [r["agent"] for r in chunk if r["agent"]]
        reviewer = resolve_reviewer(agents[0] if agents else "codex", policy)
        batch_id = next_batch_seq(reviews_dir, today) if not dry_run else f"REVIEW-{today}-CADENCE-DRY-{i:04d}"
        batch = {
            "batch_version": 1,
            "batch_id": batch_id,
            "generated_at": utc_now(),
            "project": project_root.name,
            "reviewer": reviewer,
            "trigger_type": "cadence",
            "runs": chunk,
        }
        batches.append(batch)
        if not dry_run:
            json_path, md_path = write_batch(reviews_dir, batch)
            print(f"  Written: {json_path.relative_to(project_root)}")
            print(f"  Written: {md_path.relative_to(project_root)}")
            write_decision_stubs(project_root, batch)
        else:
            print(f"  [dry-run] Would write cadence batch: {batch_id} ({len(chunk)} run(s))")

    if not immediate_runs and not cadence_queue:
        if not dry_run:
            print("  No new runs to review.")

    return batches


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate review batch artifacts for claw projects.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("project", nargs="?", metavar="PROJECT_ROOT", help="Project directory to process")
    parser.add_argument("--all", action="store_true", help="Process all projects in the repo")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be written without creating files")
    args = parser.parse_args()

    try:
        policy = load_policy(POLICY_PATH)
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error loading policy: {exc}", file=sys.stderr)
        return 2

    if args.all:
        projects_root = REPO_ROOT / "projects"
        if not projects_root.is_dir():
            print(f"Projects directory not found: {projects_root}", file=sys.stderr)
            return 2
        projects = sorted(p for p in projects_root.iterdir() if p.is_dir() and not p.name.startswith("_"))
        if not projects:
            print("No projects found.")
            return 0
        for project_root in projects:
            print(f"Project: {project_root.name}")
            generate_batches(project_root, policy, dry_run=args.dry_run)
        return 0

    if args.project:
        project_root = Path(args.project).expanduser().resolve()
        if not project_root.is_dir():
            print(f"Project directory not found: {project_root}", file=sys.stderr)
            return 2
        print(f"Project: {project_root.name}")
        generate_batches(project_root, policy, dry_run=args.dry_run)
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
