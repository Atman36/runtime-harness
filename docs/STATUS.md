# STATUS.md — Claw Live Journal

Обновлять после каждой завершённой задачи.

---

## Текущая фаза
**Этап 6 — Runtime hardening before OpenClaw**

## Статус этапов

| Этап | Название | Статус |
|------|----------|--------|
| 1 | Project scaffold | ✅ done |
| 2 | Engine import | ✅ done |
| 3 | Task→Job adapter | ✅ done |
| 4 | Hooks / callbacks | ✅ done |
| 5 | Reviewer system | ✅ done |
| 6 | Runtime hardening before OpenClaw | 🔄 in progress |
| 7 | OpenClaw integration | 📋 backlog |

---

## Done

- `_system/registry/`, `_system/templates/`, `projects/_template/`, `projects/demo-project/`
- `run_task.sh`, task→job adapter, `prompt.txt`, `meta.json`, `job.json`, `result.json`
- file-backed hooks (`state/hooks/{pending,sent,failed}`), `execute_job.py`, `dispatch_hooks.py`, `reconcile_hooks.py`, `hooklib.py`
- slim file queue `_system/engine/file_queue.py`
- runtime helpers → `_system/engine/runtime.py`
- unified CLI `scripts/claw.py` (create-project, run, enqueue, worker, dispatch, reconcile, approve, reclaim, status)
- `job.json` хранит `run_path` для детерминированных ссылок на артефакты
- `awaiting_approval` lifecycle + `approve` + `reclaim`
- formal contracts `_system/contracts/` + `scripts/validate_artifacts.py`
- standalone `scripts/generate_review_batch.py`
- **race в `RUN-XXXX` устранена** (commit `fe11887`): атомарный mkdir-loop
- runtime validation встроена в `execute_job.py` → `result.json` / `meta.json`
- cadence state `state/review_cadence.json` + автоматический review batch trigger в `claw.py worker`
- интеграционный тест `review_runtime_integration_test.sh`

## In Progress

- Встраивание planner/routing/execution contracts в основной runtime path
- Выравнивание roadmap: runtime hardening before OpenClaw

## Next

1. Встроить `task_planner.py` в `scripts/build_run.py` и сохранить `routing` / `execution` в `job.json` и `meta.json`
2. Переключить `scripts/execute_job.py` на execution contract из job artifacts и workspace backends
3. Добавить `claw launch-plan` и unified `claw review-batch`
4. Обновить demo/template artifacts под `preferred_agent: auto` и execution defaults
5. После этого возвращаться к OpenClaw commands / callback summary / wake model

---

## Decisions made

- filesystem = source of truth; артефакты первичны
- `run_path` как стабильная связь между queue item и run artifacts
- opposite-model review by default (registry policy)
- worker lifecycle project-scoped; multi-project scheduler — следующий порог
- OpenClaw — front door, не место хранения истины
- runtime hardening идёт перед chat bridge, если execution contract ещё не доведён до end-to-end
- docs/ должны быть в git индексе (проблема .gitignore — открытая)

## Assumptions in force

- Codex и Claude доступны локально (`codex`, `claude` CLI)
- Python 3.x + bash в PATH
- `projects/demo-project/` используется как живой тест-полигон

---

## Команды для smoke-check

```bash
# Полный test suite
bash tests/run_all.sh

# Валидация артефактов конкретного run
python scripts/validate_artifacts.py projects/demo-project/runs/<RUN>

# Review batch
python scripts/generate_review_batch.py projects/demo-project

# Queue status
python scripts/claw.py status projects/demo-project

# Worker (один цикл)
python scripts/claw.py worker projects/demo-project
```

---

## Текущие блокеры

_нет_

---

## Audit log

<!-- Формат: YYYY-MM-DD | задача | файлы | команда | результат | следующая -->

| Дата | Задача | Ключевые файлы | Команда | Результат | Следующая |
|------|--------|----------------|---------|-----------|-----------|
| 2026-03-12 | runtime validation + review cadence | `execute_job.py`, `claw.py`, `result.schema.json` | `bash tests/run_all.sh` | ✅ all pass | OpenClaw bridge |
| 2026-03-12 | audit последних 2 коммитов vs отчёт агента | `docs/PLAN.md`, `docs/STATUS.md`, `docs/BACKLOG.md` | `git log -2`; `git show --stat -2`; `bash tests/run_all.sh` | ✅ report gaps mapped into roadmap | planner -> runtime wiring |
