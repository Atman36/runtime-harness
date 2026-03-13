# STATUS.md — Claw Live Journal

Обновлять после каждой завершённой задачи.

---

## Текущая фаза
**post-v2 note-driven delegation in progress**

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
| 8 | Multi-project scheduler | ✅ done |
| 9 | Reliability & observability | ✅ done |

---

## Done

- `_system/registry/`, `_system/templates/`, `projects/_template/`, `projects/demo-project/`
- `run_task.sh`, task→job adapter, `prompt.txt`, `meta.json`, `job.json`, `result.json`
- file-backed hooks (`state/hooks/{pending,sent,failed}`), `execute_job.py`, `dispatch_hooks.py`, `reconcile_hooks.py`, `hooklib.py`
- slim file queue `_system/engine/file_queue.py`
- runtime helpers → `_system/engine/runtime.py`
- unified CLI `scripts/claw.py` (create-project, run, enqueue, worker, dispatch, reconcile, approve, reclaim, status, dashboard, scheduler, ask-human, resolve-approval, orchestrate, launch-plan)
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
- **`claw.py worker` теперь продлевает lease, делает retry с exponential backoff и переводит job в `dead_letter` при исчерпании попыток**; покрыто `tests/worker_reliability_test.sh`
- **Добавлены `docs/ARCHITECTURE.md`, `docs/CONTRACT_VERSIONING.md` и актуальный `README.md`**: архитектура, versioning/migration story и реальная модель системы теперь описаны явно
- **Добавлен `docs/PARALLEL_EXECUTION.md`**: зафиксированы worktree isolation, edit scope discipline, merge rules и требования к непрерывному run→review→next-task циклу
- **Hardening slice `9.6–9.9` закрыт**: trusted argv contract для env overrides, safe JSON fallback в `claw status`, lock-based `git_worktree` materialization, timeout clamp, reviewer registry validation, side-effect free `is_dead_letter()`
- **Scheduler/orchestration slice `8.1–8.4` закрыт**: `claw scheduler`, `claw dashboard`, filesystem-backed `ask-human` approvals и `claw orchestrate`
- **Auto-review executor закрыт**: `claw.py worker` автоматически запускает reviewer agent по pending decision stubs сразу после batch generation
- **Follow-up task auto-enqueue закрыт**: `needs_follow_up` reviewer decisions материализуются в новые `TASK-*` и сразу ставятся в queue
- **Failure budget закрыт**: `state/orchestration_state.json` хранит consecutive failures между вызовами `orchestrate`, а retry approval очищает stale queued retry
- **Project control surface зафиксирован в документации**: описаны `docs/WORKFLOW.md`, `state/tasks_snapshot.json`, `claw task-lint` и structured `reason_code` diagnostics; demo-project получил валидный workflow contract
- **OpenClaw completion bridge закрыт поверх file-backed hooks**: `CLAW_OPENCLAW_SYSTEM_EVENT_COMMAND` будит чат через `openclaw system event`, а `claw openclaw wake` умеет сам материализовать callback payload и переводить hook в `sent`
- **`claw guardrail-check` добавлен как standalone drift gate**: `_system/engine/guardrails.py` ловит unauthorized `projects/<slug>/`, assert weakening и `edit_scope` violations по diff-файлу; `tests/guardrails_test.sh` держит crafted negative cases
- **TASK-002 закрыт**: `collect_task_records()` теперь перехватывает `yaml.YAMLError` per-task и добавляет запись с `_parse_error`; `lint_task_graph()` эмитирует `task_parse_failed` вместо traceback; добавлен `tests/task_graph_lint_test.sh` (5 regression cases)
- **TASK-003 закрыт**: `load_workflow_contract()` бросает `WorkflowLoadError` если `contract_version != 1`; `validate_workflow_contract()` проверяет версию для `WorkflowContract` instances; Tests 9–10 добавлены в `workflow_contract_test.sh`

## In Progress

- **Epic 12 (External Project Autonomy)** — активен; TASK-006, TASK-007 и TASK-008 закрыты, TASK-010 ждёт
- `live status feed` — следующий слой поверх `events.jsonl` / `event_snapshot.json`; transport/SSE пока сознательно не поднимались

## Next

- **TASK-010** (claude): epic-status + `claw orchestrate --scope epic:N`
- Разделить `run_all.sh` на быстрый (unit) и медленный (integration) прогоны — сейчас весь suite занимает ~40 сек

## Рефлексия сессии 2026-03-14

### Что сделано

- **TASK-001** — статус исправлен на `done` (реализовано в предыдущей сессии, не была отмечена)
- **TASK-006** — реализован напрямую (claude в текущей сессии): `allowed_agents` gate + `scope_warnings` в launch-plan + `claw workflow-validate`
- **TASK-007** — выполнен codex через `claw run --execute` (7 мин): `claw task-graph-lint` + file-overlap warnings + `unknown_dependency` abort; все тесты зелёные
- **TASK-008** — реализован напрямую: `commands` registry в WORKFLOW contract, `claw run-checks`, `test_command` в `orchestrate`, shell coverage на registry/fallback
- **hook delivery** — верифицирован end-to-end: run создаёт `state/hooks/pending/<id>.json`, `openclaw wake` диспатчит и возвращает callback payloads

### Что выяснили про hook → Claude цикл

Полный путь: `execute_job.py` → `build_hook_payload()` + `write_hook_payload(project, payload, "pending")` + `dispatch_hook_file()` — если `CLAW_HOOK_COMMAND` не установлен, hook помечается `sent` через `deliver_hook_via_callback_bridge` (in-process). Реальный внешний триггер требует `CLAW_HOOK_COMMAND=<скрипт>`. Через `openclaw wake` оркестратор может читать callbacks и принимать следующее решение — это и есть механизм "Claude поднялся по хуку".

### CLAUDECODE=1 ограничение

`claude -p` внутри Claude Code заблокирован (`CLAUDECODE=1`). Решение: claude-задачи выполняет сам оркестратор в текущей сессии; codex-задачи запускаются через `claw run --execute` (codex не блокирован). TASK-001 из hooks/pending показал `status: failed` с сообщением `"Claude Code cannot be launched inside another Claude Code session"` — это ожидаемо.

### Ключевые находки

- `load_workflow_contract()` никогда не возвращает `None` — при отсутствии WORKFLOW.md возвращает дефолт с `source="defaults"`; правильная проверка: `contract.source == "defaults"`, а не `contract is None`
- Хуки хранятся в `state/hooks/{pending,sent,failed}/`, а не в `hooks/` — смотреть нужно туда
- `task-graph-lint` добавлен codex с backward-compat: `task-lint` (старый) продолжает работать
- `run-checks` shell-тест нельзя наивно запускать через `run_all.sh` без guard: зарегистрированная `commands.test` команда по умолчанию тоже ведёт в `bash tests/run_all.sh`, поэтому nested execution должен явно пропускать сам тест registry

### Статус Epic 12

| Задача | Статус |
|--------|--------|
| TASK-004 import-project | ✅ done |
| TASK-005 guardrail-check | ✅ done |
| TASK-006 workflow enforcement | ✅ done |
| TASK-007 task graph lint gate | ✅ done (codex) |
| TASK-008 command registry | ✅ done |
| TASK-009 decompose-epic | ✅ done |
| TASK-010 epic completion criteria | 🔲 todo |

---

## Decisions made

- filesystem = source of truth; артефакты первичны
- `run_path` как стабильная связь между queue item и run artifacts
- opposite-model review by default (registry policy)
- worker lifecycle project-scoped; multi-project scheduler — следующий порог
- multi-project scheduling теперь живёт отдельной командой `claw scheduler`, а не внутри project worker
- approval requests вынесены в `projects/<slug>/state/approvals/` как отдельный artifacts-first слой над queue
- continuous loop принимает run без review только когда нет pending decision stubs и нет активных approval requests
- OpenClaw — front door, не место хранения истины
- runtime hardening идёт перед chat bridge, если execution contract ещё не доведён до end-to-end
- docs/ и `projects/*/docs/` должны быть trackable; это проверяется `tests/docs_tracking_test.sh`
- planning/docs changes из параллельных worktree нужно мерджить выборочно против live roadmap, а не слепым cherry-pick
- для nested-agent режима длинный prompt резко повышает latency до первого полезного diff; narrow DoD и явный file scope обязательны
- `codex exec` в этом окружении шумит служебными skill/analytics сообщениями и даёт слабый signal-to-noise ratio для live supervision
- `claude -p` в этом окружении почти не даёт промежуточной телеметрии; контроль приходится вести по `git status`/`git diff`, а не по stdout
- reviewer cadence должен оставаться policy-driven: если агент меняет risk/review semantics, он обязан менять `reviewer_policy.yaml` и runtime tests вместе, иначе worker и backfill CLI начинают расходиться
- standalone guardrail зависит от актуального `scope.edit_scope` в `docs/WORKFLOW.md`; если task расширяет файловую поверхность, а контракт не обновлён до запуска, агент получит ложный `edit_scope_violation` на корректный diff

## Assumptions in force

- Codex и Claude доступны локально (`codex`, `claude` CLI)
- Python 3.x + bash в PATH
- `projects/demo-project/` используется как живой тест-полигон
- Для slice `TASK-004` источником истины был `projects/_claw-dev/tasks/TASK-004.md`; отдельной строки для `import-project` в root roadmap (`docs/PLAN.md` / `docs/BACKLOG.md`) пока нет.

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

# Rich project / cross-project status
python scripts/claw.py dashboard projects/demo-project
python scripts/claw.py dashboard --all

# Fair multi-project scheduling
python scripts/claw.py scheduler --once --max-jobs 2

# Continuous orchestration
python scripts/claw.py orchestrate projects/demo-project --max-steps 2

# Worker (один цикл)
python scripts/claw.py worker projects/demo-project
```

---

## Текущие блокеры

- блокеров нет

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
| 2026-03-13 | triage внешнего code review и актуализация roadmap | `docs/PLAN.md`, `docs/STATUS.md` | `rg`; `sed`; `nl`; `bash tests/run_all.sh` | ✅ подтверждены hardening gaps по hook/override shell boundary, worktree concurrency и runtime edge cases; в план добавлены 9.7-9.9 | 9.1 contract versioning |
| 2026-03-13 | 9.2 worker reliability maturity | `_system/engine/file_queue.py`, `_system/contracts/queue_item.schema.json`, `scripts/claw.py`, `tests/worker_reliability_test.sh`, `tests/run_all.sh` | `bash tests/worker_reliability_test.sh`; `bash tests/run_all.sh` | ✅ worker renews lease, retries with backoff, dead-letters exhausted jobs; JSON output now exposes retry/heartbeat metadata | 9.6 stress/failure injection |
| 2026-03-13 | 9.1 + 10.1 + 10.3 docs realignment after dual-agent run | `README.md`, `docs/ARCHITECTURE.md`, `docs/CONTRACT_VERSIONING.md`, `docs/PLAN.md`, `docs/STATUS.md`, `docs/BACKLOG.md` | `git show`; selective merge from parallel worktrees; `bash tests/run_all.sh` | ✅ architecture/versioning story documented; roadmap kept in sync without losing newer 9.7-9.9 items; dual-agent merge insights captured in docs | 10.2 parallel execution guide |
| 2026-03-13 | 10.2 parallel execution guide + continuous loop requirements | `docs/PARALLEL_EXECUTION.md`, `README.md`, `docs/PLAN.md`, `docs/STATUS.md`, `docs/BACKLOG.md` | `bash tests/run_all.sh` | ✅ worktree isolation, merge discipline, concurrency groups and requirements for autonomous run→review→next-task loop documented; backlog extended with 8.4 continuous orchestration loop | 9.6 stress/failure injection |
| 2026-03-13 | 9.6–9.9 runtime hardening + 8.1–8.4 scheduler/orchestration | `scripts/claw.py`, `scripts/execute_job.py`, `scripts/hooklib.py`, `scripts/generate_review_batch.py`, `scripts/reconcile_hooks.py`, `_system/engine/trusted_command.py`, `_system/engine/agent_exec.py`, `tests/concurrency_stress_test.sh`, `tests/runtime_hardening_test.sh`, `tests/scheduler_dashboard_test.sh`, `tests/orchestration_loop_test.sh`, `tests/run_all.sh`, `.gitignore`, `docs/*.md`, `README.md` | `bash tests/runtime_hardening_test.sh`; `bash tests/concurrency_stress_test.sh`; `bash tests/scheduler_dashboard_test.sh`; `bash tests/orchestration_loop_test.sh`; `bash tests/run_all.sh` | ✅ trusted argv overrides, safe status/worktree/runtime fixes, fair multi-project scheduler, richer dashboard, ask-human approvals and continuous task loop implemented end-to-end | auto-review executor |
| 2026-03-13 | Верификация закрытия эпиков 9.6–9.9, 8.1–8.4 и анализ оставшихся дыр в orchestrate loop | `scripts/claw.py` (`cmd_orchestrate`, `evaluate_run_decision`), `docs/PLAN.md`, `docs/STATUS.md`, `docs/BACKLOG.md` | `bash tests/run_all.sh`; code audit `cmd_orchestrate` + decision engine | ✅ все тесты зелёные; confirmed: follow_up_task не материализуется, failure budget отсутствует — зафиксированы как следующие задачи | follow-up task auto-enqueue |
| 2026-03-13 | v2 autonomy closure: auto-review executor + follow-up materialization + failure budget | `scripts/claw.py`, `tests/orchestration_autonomy_test.sh`, `tests/review_runtime_integration_test.sh`, `tests/worker_reliability_test.sh`, `tests/run_all.sh`, `docs/PLAN.md`, `docs/STATUS.md`, `docs/BACKLOG.md` | `bash tests/orchestration_autonomy_test.sh`; `bash tests/orchestration_loop_test.sh`; `bash tests/review_runtime_integration_test.sh`; `bash tests/worker_reliability_test.sh`; `bash tests/run_all.sh` | ✅ reviewer agent auto-starts from worker, `needs_follow_up` creates and enqueues new tasks, failure budget persists across orchestrate invocations, retry approval drops stale queued retries | — |
| 2026-03-13 | Документация project control surface и проверка runtime-механик | `README.md`, `docs/ARCHITECTURE.md`, `docs/EXECUTION_FLOW.md`, `docs/contracts.md`, `docs/CONTRACT_VERSIONING.md`, `docs/STATUS.md`, `projects/demo-project/docs/WORKFLOW.md` | `python3 scripts/claw.py task-snapshot projects/demo-project`; `python3 scripts/claw.py task-lint projects/demo-project`; `python3 scripts/validate_artifacts.py --workflow projects/demo-project`; `bash tests/run_all.sh` | ✅ подтверждены и задокументированы workflow contract, task graph snapshot/lint и structured diagnostics; demo-project contract валиден | — |
| 2026-03-13 | note-driven dual-agent delegation for 11.1/11.2 | `docs/PLAN.md`, `docs/BACKLOG.md`, `docs/STATUS.md`, agent worktrees `codex/graph-artifact*`, `codex/event-replay*` | `git worktree add`; `codex exec ...`; `claude -p ...`; `git status`; `git diff` | ⚠️ задачи поставлены и проверены, но в main ничего не принято: Codex дал test-only partial without implementation/commit; Claude дал partial event-log skeleton without CLI/test/commit | перепоставить 11.1 и 11.2 меньшими slices |
| 2026-03-13 | TASK-004 `claw import-project` | `scripts/claw.py`, `tests/import_project_test.sh`, `tests/run_all.sh`, `projects/_claw-dev/tasks/TASK-004.md`, `docs/STATUS.md` | `bash tests/import_project_test.sh`; `bash tests/run_all.sh` | ✅ добавлен `claw import-project`: scaffold из `_template`, `state/project.yaml`, `WORKFLOW.md` с discovered `edit_scope`, duplicate slug reject | `TASK-005` |
| 2026-03-13 | OpenClaw system-event bridge for completion hooks | `scripts/hooklib.py`, `scripts/claw.py`, `tests/openclaw_test.sh`, `docs/EXECUTION_FLOW.md`, `README.md`, `docs/STATUS.md` | `bash tests/openclaw_test.sh`; `bash tests/hook_lifecycle_test.sh`; `bash tests/run_all.sh` | ✅ pending completion hooks can wake OpenClaw via `system event`; `openclaw wake` can emit callback payloads directly from hook files and mark them sent | next event snapshot slice |
| 2026-03-13 | reviewer cadence policy wiring after OpenClaw bridge | `scripts/generate_review_batch.py`, `scripts/claw.py`, `tests/reviewer_policy_runtime_test.sh`, `tests/runtime_hardening_test.sh`, `tests/run_all.sh`, `docs/PLAN.md`, `docs/STATUS.md` | `bash tests/reviewer_policy_runtime_test.sh`; `bash tests/runtime_hardening_test.sh`; `bash tests/review_batch_test.sh`; `bash tests/review_batch_cli_test.sh`; `bash tests/review_runtime_integration_test.sh`; `bash tests/run_all.sh` | ✅ immediate triggers and cadence threshold now come from `reviewer_policy.yaml`; invalid reviewer cadence config fails fast; worker and batch CLI stay aligned | `TASK-005` |
| 2026-03-13 | TASK-005 standalone guardrail-check | `_system/engine/guardrails.py`, `scripts/claw.py`, `tests/guardrails_test.sh`, `tests/run_all.sh`, `projects/_claw-dev/tasks/TASK-005.md`, `docs/PLAN.md`, `docs/STATUS.md` | `bash tests/guardrails_test.sh`; `bash tests/import_project_test.sh`; `bash tests/run_all.sh` | ✅ added diff-driven `claw guardrail-check` for unauthorized scaffold, assert weakening and edit-scope drift; noted that stale `WORKFLOW.md` scope creates false positives before review | next event snapshot slice |
| 2026-03-13 | TASK-011 mandatory orchestrator completion signal | `scripts/hooklib.py`, `scripts/execute_job.py`, `scripts/claw.py`, `_system/contracts/{meta,result}.schema.json`, `tests/execute_job_test.sh`, `tests/openclaw_test.sh`, `docs/EXECUTION_FLOW.md`, `README.md`, `projects/_claw-dev/tasks/TASK-011.md`, `docs/STATUS.md` | `bash tests/execute_job_test.sh`; `bash tests/openclaw_test.sh`; `bash tests/run_all.sh` | ✅ completed runs now persist machine-verifiable `delivery` state; missing footer notify stays visible as `pending_delivery` until `claw openclaw wake` moves hook delivery to `sent` | next event snapshot slice |
| 2026-03-13 | TASK-002 + TASK-003 reopened regression closure | `scripts/claw.py`, `_system/engine/workflow_contract.py`, `tests/task_graph_lint_test.sh`, `tests/workflow_contract_test.sh`, `tests/run_all.sh`, `projects/_claw-dev/tasks/TASK-002.md`, `projects/_claw-dev/tasks/TASK-003.md`, `docs/STATUS.md`, `docs/PLAN.md` | `bash tests/task_graph_lint_test.sh`; `bash tests/workflow_contract_test.sh`; `bash tests/run_all.sh` | ✅ malformed YAML now yields `task_parse_failed` JSON instead of traceback; `contract_version != 1` now rejected by loader and validator; 7 new regression tests | 11.1 / 11.2 |
| 2026-03-13 | 11.1 workflow graph artifact + 11.2 event snapshot/replay | `scripts/claw.py`, `_system/engine/event_log.py`, `_system/contracts/workflow_graph.schema.json`, `tests/workflow_graph_artifact_test.sh`, `tests/event_replay_test.sh`, `tests/openclaw_test.sh`, `tests/run_all.sh`, `docs/PLAN.md`, `docs/BACKLOG.md`, `docs/STATUS.md` | `bash tests/workflow_graph_artifact_test.sh`; `bash tests/event_replay_test.sh`; `bash tests/openclaw_test.sh`; `bash tests/task_graph_lint_test.sh`; `bash tests/workflow_contract_test.sh`; `bash tests/run_all.sh` | ✅ added portable `workflow_graph.json`, append-only `events.jsonl` + `event_snapshot.json`, `claw workflow-graph`, `openclaw replay-events`, and event wiring in enqueue/worker/wake; replay reuses existing delivery status names (`pending_delivery`, `delivered`) instead of inventing aliases | live status feed |
| 2026-03-14 | TASK-007 task graph lint as mandatory pre-orchestrate gate | `scripts/claw.py`, `_system/engine/error_codes.py`, `tests/task_graph_lint_test.sh`, `projects/_claw-dev/tasks/TASK-007.md`, `docs/STATUS.md` | `bash tests/task_graph_lint_test.sh`; `bash tests/run_all.sh` | ✅ added `claw task-graph-lint` with `blocking_count`/`warning_count`, warning-only file-overlap detection, `unknown_dependency` abort in `claw orchestrate`, and ready-task filtering that skips overlapping specs; assumptions: overlap is inferred from backticked file paths in specs and any truthy `shared_files` front matter allows shared access | TASK-008 |
| 2026-03-14 | TASK-006 WORKFLOW.md enforcement in orchestrate + launch-plan + workflow-validate | `scripts/claw.py`, `tests/workflow_enforcement_test.sh`, `tests/run_all.sh`, `projects/_claw-dev/tasks/TASK-006.md` | `bash tests/workflow_enforcement_test.sh`; `bash tests/run_all.sh` | ✅ `allowed_agents` gate in `cmd_orchestrate` (reason_code: contract_violation); `scope_warnings` in `launch-plan` output; `claw workflow-validate` standalone command; TASK-001 stale status fixed | TASK-008 |
| 2026-03-14 | TASK-008 command registry in WORKFLOW contract + `claw run-checks` | `_system/engine/workflow_contract.py`, `_system/engine/__init__.py`, `scripts/claw.py`, `projects/_template/docs/WORKFLOW.md`, `projects/demo-project/docs/WORKFLOW.md`, `tests/workflow_contract_test.sh`, `tests/command_registry_test.sh`, `tests/orchestration_loop_test.sh`, `tests/run_all.sh`, `projects/_claw-dev/tasks/TASK-008.md`, `docs/STATUS.md` | `bash tests/workflow_contract_test.sh`; `bash tests/command_registry_test.sh`; `bash tests/orchestration_loop_test.sh`; `bash tests/run_all.sh` | ✅ typed `commands` registry added to workflow contract/template, `claw run-checks` executes registered `test|lint|build|smoke` commands with default fallback to `bash tests/run_all.sh`, and `claw orchestrate` now surfaces `test_command`; assumption: registry regression test skips itself under nested `run_all.sh` to avoid recursion from the default test command | TASK-010 |
