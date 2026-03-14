# SPEC-021 — Budget and governance guardrails

## Context

The claw project is at `/Users/Apple/progect/claw`.

PaperClip pairs agent autonomy with budget limits and approval gates. `claw`
already has review and approval artifacts, but there is no dedicated
filesystem-backed budget/governance layer that can soft-stop expensive or risky
execution paths before they become runaway loops.

## Goal

Add file-backed budget and governance guardrails for agent runs, so `claw` can
warn, pause, or require approval for risky execution paths while keeping every
decision visible in artifacts.

## Scope

- Add budget snapshot artifacts for agents/projects
- Define soft-limit warning and hard-stop pause semantics
- Route risky actions through existing approval artifacts where appropriate
- Expose guardrail status through CLI/status surfaces

## Constraints

- No hidden billing database or opaque quota service
- Approval flow should reuse existing artifact model
- Guardrails must be deterministic and reproducible from files
- Do not block all execution by default; policy should be configurable

## Acceptance Criteria

- Budget snapshots are written and readable from disk
- Soft-limit and hard-stop behavior is deterministic
- Approval-required actions create visible approval artifacts
- Status surfaces can explain why a run was paused or gated
- `bash tests/run_all.sh` passes

## Notes

- Start with policy and artifact flow; exact pricing/accounting can stay coarse.
