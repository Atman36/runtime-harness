# SPEC-003 — Design: auto-integrate validation + review batch into runtime lifecycle

## Goal
Produce a concrete architecture design for embedding schema validation
and review batch generation into the main worker/execute_job lifecycle so
they run automatically — without requiring the user to manually call
`validate_artifacts.py` or `generate_review_batch.py`.

## Context
Current state (read these files for full context):
- `/Users/Apple/progect/claw/scripts/execute_job.py`  — runs one job, writes result/report/hook
- `/Users/Apple/progect/claw/scripts/claw.py`           — worker loop (`cmd_worker`)
- `/Users/Apple/progect/claw/scripts/validate_artifacts.py`    — standalone CLI validator
- `/Users/Apple/progect/claw/scripts/generate_review_batch.py` — standalone batch generator
- `/Users/Apple/progect/claw/_system/contracts/`                — JSON schemas
- `/Users/Apple/progect/claw/_system/registry/reviewer_policy.yaml`

Known gap (from PLAN.md "Что проявилось как слабое место"):
1. Validation lives only as a standalone CLI, not called by the worker or execute_job
2. Review batch is generated manually, not after each successful run cadence
3. Worker has no automatic cadence tracking (every 5 successful runs = trigger review batch)

## Scope
Produce a DESIGN DOCUMENT only — no code changes.

Design must cover:
1. **Where to call validation**: inside `execute_job.py` after artifacts are written,
   OR in `claw worker` after each job completes. Pick the better option and justify.
2. **Review batch cadence**: how to track "N successful runs since last batch" inside
   the project state directory. Propose the minimal state file/counter needed.
3. **Risk-based triggers**: immediate batch generation when `result.status == failed`
   or task has `risk_flags` or `needs_review == true`. Describe the trigger logic.
4. **API shape**: what function signatures / module boundaries look like so these
   integrations stay composable without turning execute_job.py into a monolith.
5. **Tradeoffs**: what breaks or gets harder if you inline everything vs keep helpers
   separate. Consider hook delivery and failure modes.

## Constraints
- Do not propose external databases or services
- Keep filesystem as source of truth
- Minimal new state: prefer appending to existing `result.json` / `meta.json` over
  new state files where possible
- Design must be implementable in ≤ 2 Python files changed / 1 new file added

## Output
Write the design document to:
`/Users/Apple/progect/claw/projects/demo-project/reviews/REVIEW-runtime-integration-design.md`

Structure the document as:
1. Summary (2-3 sentences)
2. Proposed integration points (numbered list with rationale)
3. State tracking design
4. Risk trigger logic
5. Proposed module API (pseudo-code function signatures only)
6. Tradeoffs table
7. Recommended next steps (max 5 bullet points)

## Acceptance Criteria
- File `reviews/REVIEW-runtime-integration-design.md` exists in the project
- Covers all 5 design questions from Scope
- Stays within ≤ 600 lines
- Does not contain actual implementation code — only design artifacts
