# SPEC-002 — Fix RUN-XXXX race condition in run_task.sh

## Goal
Eliminate the race condition in sequential run directory naming inside
`scripts/run_task.sh`. Two concurrent invocations currently read the same
"last number" and both create `RUN-0002`, causing one run to clobber the other.

## Scope
- Edit only `scripts/run_task.sh` (absolute path:
  `/Users/Apple/progect/claw/scripts/run_task.sh`)
- No changes to any other file
- Keep the `RUN-XXXX` naming convention (zero-padded 4 digits)
- Keep the `runs/YYYY-MM-DD/RUN-XXXX/` path structure

## Problem details
Lines 210–224 in `run_task.sh` currently do:
1. `find` existing `RUN-*` dirs to get the highest number
2. Compute `next = last + 1`
3. `mkdir -p "$run_dir"`

Between step 2 and step 3, another process can claim the same number.

## Solution
Use `mkdir` atomicity (POSIX guarantees exclusive creation):
1. Scan for the highest existing number (same `find` logic, gives a starting hint)
2. Loop: try `mkdir "$run_dir"` (without `-p`) — if it succeeds, we own it; if it
   fails (directory already exists), increment and retry
3. After the atomic `mkdir`, run the existing setup code as-is

Replace the block from
```
mkdir -p "$run_dir"
```
with an atomic retry loop that handles concurrency.

## Acceptance Criteria
- `scripts/run_task.sh` uses `mkdir` (no `-p`) in an exclusive-creation loop
- Two concurrent `run_task.sh` invocations no longer collide on the same `RUN-XXXX`
- Existing tests in `tests/` still pass (or are not broken by the change)
- The resulting `run_dir` variable points to the exclusively created directory

## Notes
- The `run_day_root` mkdir (line `mkdir -p "$run_day_root"`) can stay with `-p`
- Do NOT use `flock` or external tools — plain `mkdir` atomicity is sufficient
- Do NOT rewrite the script beyond the `run_id` / `run_dir` block
