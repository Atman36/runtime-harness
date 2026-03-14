---
id: TASK-020
title: "Org graph and delegation policy"
status: ready
spec: ../specs/SPEC-020.md
preferred_agent: codex
review_policy: standard
priority: medium
project: _claw-dev
needs_review: true
risk_flags: [delegation, policy, agent-registry]
tags: [paperclip-import, org-graph, delegation, epic-14]
dependencies: [TASK-018]
epic: 14
---

# Task

## Goal
Add a file-backed org graph and delegation/escalation policy, so manager-style
agents can create child tasks for reports and blocked work can move up a clear
chain of command.

## Notes
- Keep the policy in registry/project artifacts, not hidden runtime state.
- Delegated tasks must preserve `parent` linkage and reason metadata.
- Cross-team or forbidden delegation should fail with explicit diagnostics.
- This is about orchestration policy, not about building a UI org chart.
