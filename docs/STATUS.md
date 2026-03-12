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

## In Progress

- Подчинение `execute_job.py` persisted `job.execution` и workspace backends
- Обновление demo/template artifacts под `preferred_agent: auto` и execution defaults
- Выравнивание docs/index hygiene после clean-worktree проверки

## Next

1. Переключить `scripts/execute_job.py` на execution contract из job artifacts и workspace backends
2. Обновить demo/template artifacts под `preferred_agent: auto` и execution defaults
3. Добавить unified `claw review-batch`
4. Закрыть clean-worktree parity для `docs/` и template docs artifacts
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

# Dry-run preview execution decision
python3 scripts/claw.py launch-plan projects/demo-project/tasks/TASK-001.md

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

- clean-worktree parity для `docs/` и `projects/_template/docs/README.md` ещё не формализована; на dirty tree это легко не заметить.

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
