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
**Статус:** 📋 backlog

| # | Задача | Зависит от | Phase | Параллельность |
|---|--------|------------|-------|----------------|
| 8.1 | Multi-project worker loop с fair scheduling | Этап 7 done | 8 | — |
| 8.2 | Cross-project status view (ошибки, approvals, pending reviews) | 8.1 | 8 | после 8.1 |
| 8.3 | Approval UX: `ask_human` как реальный сценарий (не только queue state) | 8.1 | 8 | параллельно 8.2 |

**Предлагаемые GitHub issue titles:**
- `feat: multi-project worker loop with fair scheduling`
- `feat: richer status view across projects`
- `feat: approval UX for ask_human flow`

---

## Epic 9 — Reliability & Observability
**Приоритет:** P1
**Статус:** 📋 backlog

| # | Задача | Зависит от | Phase | Параллельность |
|---|--------|------------|-------|----------------|
| 9.1 | Queue/job contract versioning + migration story | Этап 2 done | 9 | независимо |
| 9.2 | Retry/backoff policy + poison-job threshold + DLQ handling + lease heartbeat в worker loop | Этап 2 done | 9 | независимо |
| 9.3 | `claw review-batch` как unified CLI (не standalone только) | Этап 5 done | 9 | независимо |
| 9.4 | Run/review metrics snapshot в state (для status/dashboard) | Этап 5 done | 9 | независимо |
| 9.5 | Исправить `.gitignore`/индексацию для `docs/` и template docs parity | — | 9 | независимо |

**Предлагаемые GitHub issue titles:**
- `feat: job contract versioning and schema migration`
- `feat: queue retry, dlq, and lease heartbeat maturity`
- `feat: claw review-batch as first-class CLI command`
- `feat: run/review metrics state snapshot`
- `fix: docs/ and template docs are tracked in clean worktrees`

**Что можно параллелить:** 9.1, 9.2, 9.3, 9.4, 9.5 независимы друг от друга.

**Инсайт после параллельного запуска Codex + Claude:** 6.1 и 6.3 хорошо режутся на независимые slices при запуске в отдельных git worktree; без изоляции такой параллелизм быстро превращается в merge-шум.

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

## Критерий v1 (DoD)

Пользователь может:
- создать проект
- добавить spec и task
- поставить задачу в queue или выполнить сразу
- получить `result.json`, `report.md`, `stdout.log`, `stderr.log`
- посмотреть status run без ручного поиска по каталогу
- собрать review batch по cadence и risk triggers
- поставить задачу из OpenClaw и получить completion summary обратно
