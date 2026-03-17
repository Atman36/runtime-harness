# BACKLOG.md — Claw

Источник правды: `docs/PLAN.md`. Этот файл — human-readable нарезка по эпикам для планирования и GitHub Issues.

---

## Epic 6 — Runtime Hardening Before OpenClaw
**Приоритет:** P0
**Статус:** ✅ done

| # | Задача | Зависит от | Phase | Параллельность |
|---|--------|------------|-------|----------------|
| 6.1 | ✅ Встроить `task_planner.py` в `build_run.py` и сохранять `routing` / `execution` в artifacts | Этап 2 done | 6 | done |
| 6.2 | ✅ Переключить `execute_job.py` на `job.execution` и workspace backends (`shared_project`, `git_worktree`, `isolated_checkout`) | 6.1 | 6 | done |
| 6.3 | ✅ Добавить `claw launch-plan` с preview агента, routing rule, workspace mode, concurrency group и command preview | 6.1 | 6 | done |
| 6.4 | ✅ Обновить demo/template artifacts под `preferred_agent: auto` и project execution defaults; покрыть тестами | 6.1 | 6 | done |
| 6.5 | ✅ Ввести formal review decision artifacts: findings, approvals, waivers, follow-up actions | Этап 5 done | 6 | done |
| 6.6 | ✅ Формализовать hook delivery contract: idempotency, event versioning, retry semantics | Этап 4 done | 6 | done |

**Предлагаемые GitHub issue titles:**
- `feat: wire task planner into build_run artifacts`
- `feat: execute jobs from persisted execution contract`
- `feat: add claw launch-plan preview command`
- `feat: update demo and template tasks for auto routing`
- `feat: add review decision artifacts`
- `feat: formalize hook delivery contract`

---

## Epic 7 — OpenClaw Integration
**Приоритет:** P1
**Статус:** ✅ done

| # | Задача | Зависит от | Phase | Параллельность |
|---|--------|------------|-------|----------------|
| 7.1 | ✅ Реализовать команды OpenClaw: `status`, `enqueue`, `summary`, `review-batch` | Этап 6 done | 7 | done |
| 7.2 | ✅ Callback summary обратно в чат (completion signal) | 7.1 | 7 | done |
| 7.3 | ✅ Event-driven wake или cron reconcile (каждые 15 мин) | 7.1 | 7 | done |

**Предлагаемые GitHub issue titles:**
- `feat: OpenClaw commands for queue submit / status / review-batch`
- `feat: completion callback summary to chat`
- `feat: cron/event-driven reconcile for OpenClaw`

---

## Epic 8 — Multi-project Scheduler
**Приоритет:** P1
**Статус:** ✅ done

| # | Задача | Зависит от | Phase | Параллельность |
|---|--------|------------|-------|----------------|
| 8.1 | ✅ Multi-project worker loop с fair scheduling | Этап 7 done | 8 | done |
| 8.2 | ✅ Cross-project status view (ошибки, approvals, pending reviews) | 8.1 | 8 | done |
| 8.3 | ✅ Approval UX: `ask_human` как реальный сценарий (не только queue state) | 8.1 | 8 | done |
| 8.4 | ✅ Continuous orchestration loop: task selector → implement → validate → review → decide → enqueue next task | 8.1, 8.2, 8.3, 9.6-9.9 | 8 | done |

**Предлагаемые GitHub issue titles:**
- `feat: multi-project worker loop with fair scheduling`
- `feat: richer status view across projects`
- `feat: approval UX for ask_human flow`
- `feat: autonomous run-review-next-task orchestration loop`

---

## Epic 9 — Reliability & Observability
**Приоритет:** P1
**Статус:** ✅ done

| # | Задача | Зависит от | Phase | Параллельность |
|---|--------|------------|-------|----------------|
| 9.1 | ✅ Queue/job contract versioning + migration story | Этап 2 done | 9 | done |
| 9.2 | ✅ Wire retry/backoff + dead_letter + lease heartbeat в worker loop | Этап 2 done | 9 | done |
| 9.3 | ✅ `claw review-batch` как unified CLI (не standalone только) | Этап 5 done | 9 | done |
| 9.4 | ✅ Run/review metrics snapshot в state (для status/dashboard) | Этап 5 done | 9 | done |
| 9.5 | ✅ Исправить `.gitignore`/индексацию для `docs/` и template docs parity | — | 9 | done |
| 9.6 | ✅ Concurrency / stress / failure-injection тесты для queue + worker + hooks | 9.2 | 9 | done |
| 9.7 | ✅ Harden shell-command trust boundary для hooks и executor overrides (`CLAW_HOOK_COMMAND`, `CLAW_AGENT_COMMAND*`) | 9.2 | 9 | done |
| 9.8 | ✅ Execution robustness fixes: safe JSON reads, idempotent `git_worktree`, clamp timeout override | 9.2 | 9 | done |
| 9.9 | ✅ Cleanup latent runtime edge cases: `stdin` mode, reviewer registry validation, side-effect free dead-letter checks | 9.2 | 9 | done |

**Предлагаемые GitHub issue titles:**
- `feat: job contract versioning and schema migration`
- `fix: wire dead_letter, retry backoff, and lease heartbeat into worker loop`
- `feat: claw review-batch as first-class CLI command`
- `feat: run/review metrics state snapshot`
- `fix: docs/ and template docs are tracked in clean worktrees`
- `test: concurrency and failure-injection tests for queue/worker/hooks`
- `hardening: replace raw shell overrides with trusted argv contract`
- `fix: make runtime edge cases deterministic under retries/worktrees`

**Что было распараллелено:** 9.6, 9.7, 9.8 и 9.9 закрыты общим hardening slice после 9.1/9.2; regression закреплён отдельными stress/runtime tests.

**Уточнение по 9.2:** worker теперь реально использует `renew_lease`, `retry`, `dead_letter` и backoff metadata; поведение зафиксировано в `tests/worker_reliability_test.sh`.

**Инсайт после параллельного запуска Codex + Claude:** implementation slice и docs/architecture slice тоже хорошо параллелятся в отдельных worktree, но planning docs надо мерджить выборочно — blind cherry-pick легко затирает более свежий roadmap.

---

## Epic 10 — Docs & Architecture
**Приоритет:** P2
**Статус:** ✅ done

| # | Задача | Зависит от | Phase | Параллельность |
|---|--------|------------|-------|----------------|
| 10.1 | ✅ Architecture doc: Run lifecycle, entity map, agent execution backends | — | 10 | done |
| 10.2 | ✅ Parallel execution guide: git_worktree isolation, edit scope, concurrency groups | 9.2 | 10 | done |
| 10.3 | ✅ README realignment под актуальную архитектуру (убрать shell-first описание) | — | 10 | done |

**Примечание:** `agent_exec.py`, `scripts/run_task.py` и `_system/contracts/queue_item.schema.json` реализованы в коде, но не отслеживались в backlog — считаются delivered в рамках Epic 6.

---

## Завершённые эпики (reference)

| Epic | Название | Статус |
|------|----------|--------|
| 1 | Project scaffold | ✅ done |
| 2 | Engine import | ✅ done |
| 3 | Task→Job adapter | ✅ done |
| 4 | Hooks / callbacks | ✅ done |
| 5 | Reviewer system | ✅ done |
| 6 | Runtime hardening before OpenClaw | ✅ done |

---

## Dependency graph (упрощённый)

```
E1 → E2 → E3 → E4 → E5 → E6 → E7 → E8
                                   ↘
                               E9 (независимо от E7/E8, но после E2/E5)
```

---

## Критерий v1 (DoD) — ✅ достигнут (commit `172bf5b`)

Пользователь может:
- создать проект
- добавить spec и task
- поставить задачу в queue или выполнить сразу
- получить `result.json`, `report.md`, `stdout.log`, `stderr.log`
- посмотреть status run без ручного поиска по каталогу
- собрать review batch по cadence и risk triggers
- поставить задачу из OpenClaw и получить completion summary обратно
- запустить `claw orchestrate` для непрерывного цикла задач до idle / approval

## Следующая фаза (v2) — ✅ завершена

Закрыто для полной автономии:
- follow-up task auto-enqueue из `needs_follow_up` reviewer decisions
- failure budget в orchestrate loop (N consecutive fails → stop)
- auto-review executor (reviewer стартует автоматически после run)

---

## Epic 11 — Note-Driven Extensions
**Приоритет:** P2
**Статус:** 🟡 in progress

Источник: `.local/dify-ideas.md`, `.local/symphony-ideas.md`

| # | Задача | Зависит от | Phase | Параллельность |
|---|--------|------------|-------|----------------|
| 11.1 | ✅ Добавить переносимый `workflow graph artifact` (`nodes + edges`) как file-backed contract | v2 done | 11 | выполнено 2026-03-13 |
| 11.2 | ✅ Добавить file-backed `event snapshot + replay` для live run state | 11.1 | 11 | выполнено 2026-03-13 |

**Замечание по порядку:** live status transport (`status --live`, SSE/feed) не начинать до появления стабильного event artifact/snapshot слоя.

---

## Epic 14 — PaperClip-Inspired Coordination Primitives
**Приоритет:** P2
**Статус:** ⏳ planned

Источник: локальный анализ `/Users/Apple/Downloads/paperclip-master` с адаптацией
идей под filesystem-first модель `claw`.

| # | Задача | Зависит от | Phase | Параллельность |
|---|--------|------------|-------|----------------|
| 14.1 | Добавить file-backed heartbeat wake queue + coalescing (`timer`, `assignment`, `mention`, `manual`, `approval`) | Epic 13 done enough для `agent_stream` | 14 | 14.2 blocker |
| 14.2 | Добавить agent inbox + atomic task claim/release semantics | 14.1 | 14 | 14.3 / 14.4 fan-out |
| 14.3 | Добавить resumable agent session state + reset/rotate path | 14.2 | 14 | parallel после 14.2 |
| 14.4 | Добавить file-backed org graph + delegation / escalation policy | 14.2 | 14 | parallel после 14.2 |
| 14.5 | Добавить budget/governance guardrails и approval-required actions | 14.3, 14.4 | 14 | terminal slice |

**Предлагаемые GitHub issue titles:**
- `feat: add file-backed heartbeat wake queue with coalescing`
- `feat: add agent inbox and atomic claim-release lifecycle`
- `feat: persist resumable agent sessions across heartbeats`
- `feat: add org graph and delegation-escalation policy`
- `feat: add budget and governance guardrails for agent runs`

---

## Epic 15 — Operator Transport & Session UX
**Приоритет:** P2
**Статус:** ⏳ planned

Источник: локальный анализ внешнего chat/worktree orchestration reference с
адаптацией под filesystem-first модель `claw`.

| # | Задача | Зависит от | Phase | Параллельность |
|---|--------|------------|-------|----------------|
| 15.1 | ✅ Добавить live status feed для операторов поверх `events.jsonl` / `event_snapshot.json` / `agent_stream.jsonl` | Epic 13 done enough | 15 | done |
| 15.2 | ✅ Добавить message directives + `ctx:` context binding contract | 15.1 | 15 | done |
| 15.3 | ✅ Добавить operator session memory + resume handles | 15.2 | 15 | done |
| 15.4 | ✅ Добавить safe file exchange для active project/worktree | 15.2 | 15 | done |
| 15.5 | ✅ Добавить transport plugin surface + setup/doctor checks | 15.3, 15.4 | 15 | done |

**Предлагаемые GitHub issue titles:**
- `feat: add live status feed for operator polling`
- `feat: add message directives and ctx context binding`
- `feat: persist operator session memory and resume handles`
- `feat: add safe file exchange for active project roots`
- `feat: add transport plugin contract and setup checks`

---

## Epic 16 — Native Subagent Enablement
**Приоритет:** P2
**Статус:** ✅ done

Источник: официальный релиз tool-native subagents в Codex и Claude Code.

| # | Задача | Зависит от | Phase | Параллельность |
|---|--------|------------|-------|----------------|
| 16.1 | ✅ Добавить repo-scoped Codex starter pack (`.codex/config.toml`, `.codex/agents/*`) | — | 16 | done |
| 16.2 | ✅ Добавить repo-scoped Claude starter pack (`.claude/agents/*`) | — | 16 | done |
| 16.3 | ✅ Добавить template-scoped starter packs для новых scaffold/imported projects | 16.1, 16.2 | 16 | done |
| 16.4 | ✅ Задокументировать decision rules: subagents vs Claude agent teams vs `claw` orchestration | 16.1, 16.2 | 16 | done |
| 16.5 | ✅ Задокументировать first-run chat onboarding для выбора доступных CLI | 16.4 | 16 | done |

**Предлагаемые GitHub issue titles:**
- `feat: add project-scoped Codex subagent starter pack`
- `feat: add project-scoped Claude subagent starter pack`
- `docs: clarify when to use subagents, Claude agent teams, and claw orchestration`
- `docs: add first-run CLI onboarding flow`
