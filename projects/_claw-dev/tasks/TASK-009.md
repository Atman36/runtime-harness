---
id: TASK-009
title: "claw decompose-epic via LLM"
status: todo
spec: ../specs/SPEC-009.md
preferred_agent: claude
review_policy: standard
priority: medium
project: _claw-dev
needs_review: false
risk_flags: [llm-output]
tags: [cli, decomposition, llm, epic-12]
dependencies: [TASK-004]
epic: 12
---

# Task

## Goal
Add `claw decompose-epic --project <slug> --input <roadmap.md>` as a two-phase
LLM-assisted decomposition flow: default dry-run returns a validated task plan as JSON,
and file materialization happens only with explicit `--write`.

## Notes
- `_system/engine/decomposer.py` — LLM call via claude CLI or anthropic SDK
- Default mode is dry-run / plan-only; writing TASK/SPEC files requires `--write`
- Each generated spec should be scoped to ≤2-3h of work
- Dependencies validated: no cycles, no broken refs
- No file overlap without explicit shared_files flag
- Writes `state/sprint_index.json` only during materialization phase
