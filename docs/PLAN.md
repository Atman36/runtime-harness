# PLAN — дальнейшее развитие `claw`

Дата: 2026-03-12
Статус: working plan / updated after dual-agent verification of runtime hardening

## Цель
Собрать в `claw` рабочую инфраструктуру для управления проектами, спеками, задачами и агентными запусками через Codex / Claude с фиксированными артефактами, file-backed queue, hook/callback-механикой и review-циклом.

---

## Базовая идея
`claw` строится как **project shell + orchestration engine**.

- **Project shell**: структура проектов, `tasks/specs/docs`, шаблоны, registry, review policy
- **Engine**: queue, worker loop, result/report contracts, hooks, retry/reconcile, approvals
- **OpenClaw**: chat entrypoint, wake events, cron/reconcile, orchestration UX

Основной донор engine:
- `/Users/Apple/Developer/multi-agent-cli-orchestrator`

---

## Архитектурная модель

### 1. Project layer (`claw`)
```text
claw/
├── _system/
│   ├── registry/
│   ├── templates/
│   ├── scripts/
│   └── engine/
├── projects/
│   ├── _template/
│   └── <project-slug>/
│       ├── docs/
│       ├── specs/
│       ├── tasks/
│       ├── runs/
│       ├── reviews/
│       └── state/
└── skills/
```

### 2. Engine layer
Должен уметь:
- принимать `job.json`
- класть run/job в filesystem queue
- запускать Codex / Claude workers
- сохранять `result/report/logs`
- поддерживать callback / hook
- поддерживать retry / ask_human / review step

### 3. Integration layer
Должен уметь:
- превращать `TASK.md + SPEC.md` в `job.json`
- выбирать агента по эвристикам
- запускать pipeline `plan -> implement -> review`
- вызывать dispatch/reconcile hooks
- формировать review batch

---

## Что уже сделано

### Завершённые слои
- **Этап 1 завершён:** `_system/registry/`, `_system/templates/`, `projects/_template/`, `projects/demo-project/`
- **Этап 3 завершён:** `run_task.sh`, task→job адаптер, генерация `prompt.txt`, `meta.json`, `job.json`, `result.json`
- **Этап 4 завершён:** file-backed hooks (`state/hooks/{pending,sent,failed}`), `execute_job.py`, `dispatch_hooks.py`, `reconcile_hooks.py`, `hooklib.py`

### Частично закрытый engine import
- Добавлен slim file queue в `_system/engine/file_queue.py`
- Вынесены runtime helpers из `scripts/claw.py` в `_system/engine/runtime.py`
- Добавлен единый CLI entrypoint `scripts/claw.py`
- Поддержаны команды:
  - `claw create-project`
  - `claw run`
  - `claw enqueue`
  - `claw worker`
  - `claw dispatch`
  - `claw reconcile`
  - `claw approve`
  - `claw reclaim`
  - `claw status`
  - `claw launch-plan`
- `job.json` теперь хранит `run_path`, чтобы queue item мог детерминированно ссылаться на run artifacts
- `run_task.sh` валидирует `project.slug` из `state/project.yaml` против имени каталога
- Queue lifecycle поддерживает `awaiting_approval`, явный `approve` и reclaim stale `running` jobs
- Добавлены formal contracts в `_system/contracts/` и CLI-валидатор `scripts/validate_artifacts.py`
- Добавлен standalone review batch generator `scripts/generate_review_batch.py`
- Локальный запуск `codex` и `claude` из репозитория подтверждён smoke-проверкой
- **Race в `RUN-XXXX` устранена** (commit `fe11887`): `mkdir -p` → атомарный retry-loop с `mkdir`
- **Архитектурный дизайн runtime-интеграции** завершён: `projects/demo-project/reviews/REVIEW-runtime-integration-design.md`
- **Runtime validation встроена в execution lifecycle**: `execute_job.py` пишет `validation` в `result.json` и `meta.json`
- **Review cadence встроен в worker lifecycle**: `claw.py worker` ведёт `state/review_cadence.json` и автоматически вызывает review batch generation
- **Planner встроен в build path**: `scripts/build_run.py` теперь пишет `routing` / `execution` в `job.json` и `meta.json`
- **Добавлен `claw launch-plan`**: dry-run preview показывает routing decision, workspace mode и command preview до запуска

### Тестовое покрытие
- `foundation_scaffold_test.sh`
- `task_to_job_test.sh`
- `execute_job_test.sh`
- `hook_lifecycle_test.sh`
- `queue_cli_test.sh`
- `queue_lifecycle_test.sh`
- `contracts_validation_test.sh`
- `launch_plan_test.sh`
- `review_batch_test.sh`
- `review_runtime_integration_test.sh`

---

## Что берём из донора
Источник:
- `/Users/Apple/Developer/multi-agent-cli-orchestrator`

### Копировать / адаптировать
- `fsqueue/` идеи и атомарные переходы состояний
- `contracts/` для formal `job/result` schema
- части `workers/` для более чистого разделения исполнителей
- части `orchestrator/` только там, где они реально нужны `claw`
- идеи из `docs/OPENCLAW_INTEGRATION.md`

### Не переносить как есть
- `.venv`
- `var/`
- `artifacts/`
- `workspaces/`
- `.pytest_cache`
- deploy-обвязку (`systemd`, `nginx`, `logrotate`) на первом этапе

---

## Правила выбора агентов

### Claude использовать, когда
- дизайн / UX / flow
- неоднозначная спека
- архитектурная развилка
- исследование / нормализация требований
- review проблемных запусков Codex

### Codex использовать, когда
- чёткая спека
- реализация / фиксы / тесты
- shell/python glue
- локальные кодовые изменения с понятным DoD

### Базовые пайплайны
1. **Claude -> Codex -> Claude**
   - нормализовать spec
   - реализовать
   - сделать review
2. **Codex solo**
   - если задача инженерная и чёткая
3. **Claude solo**
   - если задача исследовательская / дизайн / архитектура

---

## План реализации

## Этап 2 — engine import
**Статус:** ✅ завершён

### Уже сделано
- добавлен minimal file queue
- добавлен project-scoped worker loop
- добавлен единый CLI поверх существующих shell/python скриптов
- worker/queue runtime вынесен в более чистый engine API
- добавлен reclaim stale running jobs
- поддержан lifecycle `awaiting_approval`
- формализованы `job.json` / `result.json` schema
- **race в `RUN-XXXX` устранена**: атомарный `mkdir`-loop (commit `fe11887`)

### DoD
- engine запускается локально
- job можно подать через CLI / file queue
- worker забирает job из queue и завершает её детерминированно
- структура артефактов не зависит от способа запуска

---

## Этап 5 — reviewer system
**Статус:** ✅ завершён локально

### Уже сделано
- счётчик successful runs реализован через cadence batch size из policy
- добавлен review batch generator
- opposite-model reviewer mapping применяется из policy registry
- immediate triggers работают для `failed`, `needs_review`, `risky_area`, `uncertainty`, `large_diff`
- **архитектурный дизайн** встройки validation + review batch в runtime lifecycle готов (`REVIEW-runtime-integration-design.md`)
- реализован `run_post_artifact_validation()` в `execute_job.py`
- реализован `maybe_trigger_review()` в `claw.py`
- добавлен `state/review_cadence.json` как project-level state для cadence review
- `result.schema.json` расширен под `validation`
- worker lifecycle автоматически запускает review batch generation после завершения run
- добавлен интеграционный тест на cadence + immediate review trigger

### Правила
- review после каждых 5 successful runs
- review сразу при:
  - `failed`
  - `needs_review`
  - `risky_area`
  - `uncertainty`
  - `large_diff`

### DoD
- review batch формируется автоматически
- список кандидатов прозрачно виден
- reviewer по умолчанию — opposite model

---

## Этап 6 — Runtime hardening before OpenClaw
**Цель:** довести planner/routing/workspace contracts до реального execution path

### Уже сделано
- `task_planner.py` встроен в `build_run.py`, а `routing` / `execution` теперь реально попадают в `job.json` / `meta.json`
- `routing_rules.yaml` теперь влияет на созданный run через planner, а не висит отдельно от runtime path
- добавлен `claw launch-plan` для dry-run preview: агент, routing rule, workspace mode, concurrency group, command preview

### Осталось сделать
- привязать `execute_job.py` к `job.execution`, а не только к registry/env overrides
- материализовать workspace backends: `shared_project`, `git_worktree`, `isolated_checkout`
- обновить template/demo artifacts под `preferred_agent: auto` и project-level execution defaults
- закрыть тестами planner -> launch-plan -> execute path
- добавить unified `claw review-batch`

### DoD
- planner используется в основном runtime path, а не только импортируется как helper
- `routing_rules.yaml` реально влияет на созданный run
- execution/workspace policy читается из job artifacts и воспроизводима при worker execution
- demo/template сценарии покрывают routing + execution defaults

---

## Этап 7 — OpenClaw integration
**Цель:** управлять runtime из чата после стабилизации execution layer

### Сделать
- команды / сценарии для:
  - создать проект
  - добавить task/spec
  - поставить run в queue
  - узнать status
  - сделать review batch
- cron/reconcile каждые 15 минут или event-driven wake
- callback summary обратно в чат

### DoD
- задачу можно ставить из OpenClaw
- completion summary приходит обратно
- review можно инициировать без ручной возни

---

## Что сделано в последней сессии

- `scripts/build_run.py` переведён на `task_planner.py`; planner routing/execution теперь сохраняются в `job.json` и `meta.json`
- `scripts/claw.py` получил `launch-plan`, который показывает dry-run execution decision и `command_preview`
- обновлены `job.schema.json` и `meta.schema.json` под persisted `routing` / `execution`
- добавлен `tests/launch_plan_test.sh`, а `task_to_job_test.sh` и `contracts_validation_test.sh` усилены под planner contract
- подтверждён параллельный workflow orchestration: Codex сделал implementation slice (`6.1`), Claude — CLI/dry-run preview slice (`6.3`) в отдельных worktree
- после cherry-pick и ручной проверки на основной ветке `tests/run_all.sh` снова зелёный

## Следующие незавершённые задачи

- довести execution contract до фактического workspace selection/materialization в `execute_job.py`
- обновить template/demo project под `preferred_agent: auto` и execution defaults
- добавить unified `claw review-batch`
- формализовать clean-worktree parity для `docs/` и template docs artifacts
- после этого возвращаться к OpenClaw bridge

---

## Форматы сущностей

## Task
Должен содержать frontmatter:
- `id`
- `title`
- `status`
- `spec`
- `preferred_agent`
- `review_policy`
- `priority`
- `project`
- `needs_review`
- `risk_flags`

## Spec
Должен содержать:
- `goal`
- `scope`
- `constraints`
- `acceptance criteria`
- `notes`

## Run artifacts
Минимальный набор:
- `meta.json`
- `prompt.txt`
- `stdout.log`
- `stderr.log`
- `result.json`
- `report.md`
- hook payload
- queue item

---

## Риски
- переусложнить `claw` и превратить его в generic platform вместо project OS
- перетащить из донора слишком много ненужного operational ballast
- смешать runtime-артефакты и source files
- оставить queue и hooks как два разрозненных механизма без общего lifecycle
- не зафиксировать правила выбора Claude vs Codex и снова уйти в ручной хаос

---

## Принципы
- filesystem = source of truth
- deterministic paths > магия
- artifacts first
- push hook + reconcile fallback
- file queue > in-memory orchestration
- opposite-model review by default
- shell/project layer отделён от engine layer
- OpenClaw — front door, не место хранения истины

---

## Инсайты после запуска

### Что подтвердилось
- Разделение на run artifacts, queue state и hooks оказалось удачным: состояние системы читается с диска без скрытой магии.
- `run_path` как стабильная связь между queue item и run artifacts снимает большую часть lookup-хаоса.
- Вынесение runtime helpers из `scripts/claw.py` в engine-модуль упрощает дальнейшую сборку API без shell-спагетти.
- Standalone `validate_artifacts.py` и `generate_review_batch.py` позволяют проверять систему независимо от worker loop, что хорошо для smoke/debug сценариев.

### Что проявилось как слабое место
- `execute_job.py` пока ещё не исполняет persisted `job.execution` как first-class contract: часть поведения всё ещё берётся из registry/env overrides.
- demo/template tasks ещё не переведены на `preferred_agent: auto`, поэтому routing coverage пока держится на тестовых fixtures, а не на living examples.
- длинные orchestration prompts для Claude хрупки при прямой shell-цитировке; для обвязки безопаснее prompt file / stdin, чем giant inline command.
- clean-worktree verification вскрыл drift в документации и template scaffold: `projects/_template/docs/README.md` был в dirty tree, но отсутствовал в чистом worktree.
- review cadence встроен, но отсутствует formal review decision lifecycle: findings, approvals, waivers, follow-up actions.
- lease renewal API и dead-letter state появились в queue, но worker пока не использует heartbeat/retry policy как first-class operational contract.
- Worker остаётся project-scoped; для реальной эксплуатации может понадобиться scheduler над несколькими проектами.

### Практический вывод
- Архитектура уже жизнеспособна как local-first orchestration shell.
- Следующий потолок сложности теперь не в queue/hook mechanics, а в orchestration policy: routing, execution isolation, review decisions и queue maturity.

### Инсайты после параллельного запуска Codex + Claude
- Лучший рабочий паттерн для orchestration-сессий — давать агентам узкие, почти не пересекающиеся slices и запускать их в отдельных git worktree.
- `Codex -> implementation`, `Claude -> orchestration/review/preview` снова подтвердился как практичный split: меньше конфликтов по файлам, проще cherry-pick и финальная верификация.
- `launch-plan` оказался не просто удобной CLI-командой, а важным human/agent checkpoint перед реальным запуском worker'а.
- Проверка результата в clean worktree обязательна: она ловит скрытые проблемы индексации/шаблонов, которые на грязной ветке выглядят «как будто всё ок».

---

## Что улучшить в проекте

### Высокий приоритет
- ~~Убрать race в генерации `RUN-XXXX`~~ — **✅ сделано** (commit `fe11887`)
- ~~Реализовать дизайн из `REVIEW-runtime-integration-design.md`: встроить validation в `execute_job.py`, review batch cadence в `claw.py worker`.~~ — **✅ сделано**
- ~~Встроить planner в runtime path: `build_run.py` должен использовать `task_planner.py` и сохранять `routing` / `execution` в artifacts.~~ — **✅ сделано** (`0caec7c`)
- ~~Применять `routing_rules.yaml` в runtime при создании job, а не держать rules только в registry.~~ — **✅ сделано** через planner wiring (`0caec7c`)
- ~~Добавить `claw launch-plan` для dry-run preview execution decision.~~ — **✅ сделано** (`b8053ef`)
- Подчинить workspace execution контракту из job artifacts и довести backends `shared_project` / `git_worktree` / `isolated_checkout`.
- Обновить demo/template artifacts под `preferred_agent: auto` и execution defaults.

### Средний приоритет
- Ввести formal review decision artifacts: `review_decision.json`, `findings.json`, approvals, waivers, follow-up queue.
- Довести queue maturity: retry/backoff policy, poison-job threshold, DLQ handling, lease renewal heartbeat в worker loop.
- Формализовать hook delivery contract: idempotency, event versioning, retry semantics.
- Добавить явный queue/job contract versioning и migration story для будущих изменений схем.
- Сделать `claw review-batch` как часть unified CLI вместо standalone entrypoint-only usage.
- Добавить multi-project worker/reconciler loop с безопасным fair scheduling.
- Обновить template/demo artifacts под `preferred_agent: auto`, execution defaults и routing coverage tests.
- Исправить `.gitignore` политику для `docs/`, чтобы проектная документация не терялась из индекса по умолчанию.

### Низкий приоритет, но полезно
- Сохранить summary/metrics по runs и review batches в отдельный state snapshot для status/dashboard сценариев.
- Добавить richer status view: последние ошибки, awaiting approval jobs, pending hooks, pending reviews.
- Уточнить policy для `ask_human` и approval UX, чтобы `awaiting_approval` стало частью реального сценария, а не только queue state.

---

## Ближайшие шаги
1. ~~Убрать race в нумерации `RUN-XXXX`~~ — **✅ сделано**
2. ~~Встроить `task_planner.py` в `scripts/build_run.py` и расширить `job.schema.json` / `meta.schema.json` под `routing` + `execution`~~ — **✅ сделано**
3. ~~Добавить `claw launch-plan`~~ — **✅ сделано**
4. Переключить `scripts/execute_job.py` на `job.execution.workspace_mode` и материализацию workspace backend'ов
5. Обновить demo/template project так, чтобы routing проверялся через `preferred_agent: auto`
6. Добавить unified `claw review-batch`
7. Закрыть clean-worktree parity для `docs/` и template docs artifacts
8. После стабилизации runtime вернуться к OpenClaw bridge

---

## Критерий успеха v1
`claw` считается достаточно собранным для v1, если пользователь может:
- создать проект
- добавить spec и task
- поставить задачу в queue или выполнить сразу
- получить `result.json`, `report.md`, `stdout.log`, `stderr.log`
- не потерять completion signal
- посмотреть status run без ручного поиска по каталогу
- собрать review batch по cadence и risk triggers
