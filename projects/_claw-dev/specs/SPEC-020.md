# SPEC-020 — Org graph and delegation policy

## Context

The claw project is at `/Users/Apple/progect/claw`.

PaperClip models agents as a reporting tree where managers delegate work to
reports and blocked work escalates upward. `claw` already has routing rules and
review policy, but it does not yet have a file-backed org/delegation layer for
multi-agent chains of command.

## Goal

Add a file-backed org graph and delegation/escalation policy, so manager-style
agents can create child tasks for reports with explicit parent linkage and
blocked work can escalate through a defined chain.

## Scope

- Add org graph metadata (`reports_to`, capabilities, delegation permissions)
- Validate delegation against allowed reporting lanes
- Materialize delegated child tasks with parent linkage and reason metadata
- Add escalation path for blocked work to move upward

## Constraints

- Filesystem-first policy only; no server-side org DB
- Delegation rules must be explicit and reviewable in registry/project files
- Cross-team forbidden delegation must fail clearly
- Existing simple agent routing must remain supported

## Acceptance Criteria

- Org graph can be loaded from files and validated
- Manager/report delegation creates traceable child tasks
- Blocked work can escalate using a defined chain of command
- Policy violations produce structured diagnostics
- `bash tests/run_all.sh` passes

## Notes

- This is about operational hierarchy, not a graphical org-chart UI.
