# STATUS.md — Claw Live Journal

Обновлять после каждой завершённой задачи.

---

## Текущая фаза
**Этап 9 — Reliability & Observability**

## Статус этапов

| Этап | Название | Статус |
|------|----------|--------|
| 1 | Project scaffold | ✅ done |
| 2 | Engine import | ✅ done |
| 3 | Task→Job adapter | ✅ done |
| 4 | Hooks / callbacks | ✅ done |
| 5 | Reviewer system | ✅ done |
| 6 | Runtime hardening before OpenClaw | ✅ done |
| 7 | OpenClaw integration | ✅ done |

---

## Done

- `_system/registry/`, `_system/templates/`, `projects/_template/`, `projects/demo-project/`
- `run_task.sh`, task→job adapter, `prompt.txt`, `meta.json`, `job.json`, `result.json`
- file-backed hooks (`state/hooks/{pending,sent,failed}`), `execute_job.py`, `dispatch_hooks.py`, `reconcile_hooks.py`, `hooklib.py`
- slim file queue `_system/engine/file_queue.py`
- runtime helpers → `_system/engine/runtime.py`
- unified CLI `scripts/claw.py` (create-project, run, enqueue, worker, dispatch, reconcile, approve, reclaim, status, launch-plan)
- `job.json` хранит `run_path` для детерминированных ссылок на артефакты
- `awaiting_approval` lifecycle + `approve` + `reclaim`
- formal contracts `_system/contracts/` + `scripts/validate_artifacts.py`
- standalone `scripts/generate_review_batch.py`
- **race в `RUN-XXXX` устранена** (commit `fe11887`): атомарный mkdir-loop
- runtime validation встроена в `execute_job.py` → `result.json` / `meta.json`
- cadence state `state/review_cadence.json` + автоматический review batch trigger в `claw.py worker`
- planner wiring в `scripts/build_run.py` → persisted `routing` / `execution` в `job.json` и `meta.json`
- `claw launch-plan` для dry-run preview execution decision + `command_preview`
- интеграционный тест `review_runtime_integration_test.sh`
- `launch_plan_test.sh`
- **`execute_job.py` читает `job.execution.workspace_mode` первым** (приоритет над registry/env); `shared_project` alias для `project_root`; `isolated_checkout` backend добавлен
- **demo-project и _template tasks** переведены на `preferred_agent: auto`; тесты обновлены под routing через `default-codex` rule
- **`_system/contracts/review_decision.schema.json`** — formal schema для review decisions (findings, approvals, waivers, follow-up actions)
- **`generate_review_batch.py`** создаёт decision stubs в `reviews/decisions/` при генерации batch
- **`hooklib.py`** — `event_version`, `idempotency_key`, `delivery_attempts`, `max_delivery_attempts`; `reconcile_hooks.py` — dead-letter skip; `_system/contracts/hook_payload.schema.json`

- **`claw openclaw status/enqueue/review-batch/summary/callback/wake`** — OpenClaw JSON bridge для chat callbacks и cron/event wake; `openclaw_test.sh` (6/6 ✅)
- stdout агрегатора перехвачен в stderr внутри `cmd_openclaw_review_batch` — JSON остаётся чистым
- **`claw openclaw callback`** читает hook payload из stdin и возвращает completion summary для чата
- **`claw openclaw wake`** прогоняет pending hooks и retry для failed hooks, возвращая JSON-сводку для cron/event bridge
- **`.gitignore` policy для docs исправлена**: `docs/` и `projects/*/docs/` больше не теряются из clean worktree; добавлен guard test `docs_tracking_test.sh`
- **Добавлен `state/metrics_snapshot.json`**: queue/hooks/runs/reviews summary теперь сохраняется в state и переиспользуется в `claw openclaw status`

## In Progress

_(9.1 — queue/job contract versioning + migration story)_

## Next

1. Добавить queue/job contract versioning + migration story (9.1)
2. Довести queue maturity: retry/backoff, poison threshold, DLQ, heartbeat (9.2)
3. Добавить richer status view: последние ошибки, approvals, pending reviews (следом за 9.4/8.2)

---

## Decisions made

- filesystem = source of truth; артефакты первичны
- `run_path` как стабильная связь между queue item и run artifacts
- opposite-model review by default (registry policy)
- worker lifecycle project-scoped; multi-project scheduler — следующий порог
- OpenClaw — front door, не место хранения истины
- runtime hardening идёт перед chat bridge, если execution contract ещё не доведён до end-to-end
- docs/ и `projects/*/docs/` должны быть trackable; это проверяется `tests/docs_tracking_test.sh`

## Assumptions in force

- Codex и Claude доступны локально (`codex`, `claude` CLI)
- Python 3.x + bash в PATH
- `projects/demo-project/` используется как живой тест-полигон

---

## Команды для smoke-check

```bash
# Полный test suite
bash tests/run_all.sh

# Dry-run preview execution decision
python3 scripts/claw.py launch-plan projects/demo-project/tasks/TASK-001.md

# Валидация артефактов конкретного run
python scripts/validate_artifacts.py projects/demo-project/runs/<RUN>

# Review batch
python scripts/claw.py review-batch projects/demo-project

# Queue status
python scripts/claw.py status projects/demo-project

# Worker (один цикл)
python scripts/claw.py worker projects/demo-project
```

---

## Текущие блокеры

- `isolation=worktree` в orchestrator не изолирует агентов от основного рабочего дерева, если им передаётся абсолютный путь — агенты пишут напрямую в main directory. Нужно передавать путь к worktree, а не к main repo.

---

## Audit log

<!-- Формат: YYYY-MM-DD | задача | файлы | команда | результат | следующая -->

| Дата | Задача | Ключевые файлы | Команда | Результат | Следующая |
|------|--------|----------------|---------|-----------|-----------|
| 2026-03-12 | planner wiring в build path (`6.1`) | `build_run.py`, `job.schema.json`, `meta.schema.json`, `task_to_job_test.sh` | `bash tests/task_to_job_test.sh`; `bash tests/contracts_validation_test.sh` | ✅ planner `routing/execution` persisted into artifacts | `execute_job.py` on `job.execution` |
| 2026-03-12 | `claw launch-plan` dry-run preview (`6.3`) | `claw.py`, `launch_plan_test.sh` | `python3 scripts/claw.py launch-plan ...`; `bash tests/launch_plan_test.sh` | ✅ command preview + routing/workspace summary visible before launch | demo/template auto routing |
| 2026-03-12 | parallel Codex + Claude orchestration verification | worktrees + cherry-pick into `master` | `git worktree add`; `git cherry-pick`; `bash tests/run_all.sh` | ✅ узкие slices смёржились без конфликтов; clean-worktree drift surfaced | docs/template parity |
| 2026-03-12 | runtime validation + review cadence | `execute_job.py`, `claw.py`, `result.schema.json` | `bash tests/run_all.sh` | ✅ all pass | OpenClaw bridge |
| 2026-03-12 | audit последних 2 коммитов vs отчёт агента | `docs/PLAN.md`, `docs/STATUS.md`, `docs/BACKLOG.md` | `git log -2`; `git show --stat -2`; `bash tests/run_all.sh` | ✅ report gaps mapped into roadmap | planner -> runtime wiring |
| 2026-03-13 | Epic 6 closure: 6.2+6.4 (Codex) + 6.5+6.6 (Claude) параллельно | `execute_job.py`, task files, `hooklib.py`, `reconcile_hooks.py`, `generate_review_batch.py`, `_system/contracts/` | `bash tests/run_all.sh` | ✅ all pass; `shared_project` bug caught + fixed by orchestrator | OpenClaw bridge (Этап 7) |
| 2026-03-13 | OpenClaw 7.1: `claw openclaw status/enqueue/review-batch/summary` | `scripts/claw.py`, `tests/openclaw_test.sh`, `tests/run_all.sh` | `bash tests/run_all.sh` | ✅ 10/10; stdout→stderr fix for review-batch | 7.2 callback + 7.3 cron |
| 2026-03-13 | OpenClaw 7.2+7.3: callback summary + wake bridge | `scripts/claw.py`, `tests/openclaw_test.sh`, `docs/PLAN.md`, `docs/STATUS.md`, `docs/BACKLOG.md` | `bash tests/openclaw_test.sh`; `bash tests/run_all.sh` | ✅ callback JSON из hook payload и `wake` bridge для cron/event reconcile добавлены | 9.3 unified review-batch CLI |
| 2026-03-13 | 9.3 unified `claw review-batch` CLI | `scripts/claw.py`, `tests/review_batch_cli_test.sh`, `tests/run_all.sh`, `docs/PLAN.md`, `docs/STATUS.md`, `docs/BACKLOG.md` | `bash tests/review_batch_cli_test.sh`; `bash tests/openclaw_test.sh`; `bash tests/run_all.sh` | ✅ top-level `claw review-batch` добавлен; общий helper переиспользован без ломки OpenClaw JSON | 9.5 docs/template clean-worktree parity |
| 2026-03-13 | 9.5 docs/template clean-worktree parity | `.gitignore`, `tests/docs_tracking_test.sh`, `projects/_template/docs/README.md`, `projects/demo-project/docs/README.md`, `docs/PRO_FRAMEWORK_ANALYSIS_PROMPT.md`, `docs/PLAN.md`, `docs/STATUS.md`, `docs/BACKLOG.md` | `bash tests/docs_tracking_test.sh`; `bash tests/run_all.sh` | ✅ docs/ и project docs больше не скрываются `.gitignore`; parity проверяется тестом | 9.4 metrics snapshot |
| 2026-03-13 | 9.4 metrics snapshot in state | `scripts/claw.py`, `tests/metrics_snapshot_test.sh`, `tests/openclaw_test.sh`, `tests/run_all.sh`, `docs/PLAN.md`, `docs/STATUS.md` | `bash tests/metrics_snapshot_test.sh`; `bash tests/openclaw_test.sh`; `bash tests/run_all.sh` | ✅ `state/metrics_snapshot.json` сохраняет queue/hooks/runs/reviews summary; `openclaw status` переиспользует snapshot и отдаёт metrics | 9.1 contract versioning |
