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

Engine архитектура вдохновлена паттернами filesystem-first queue и formal artifact contracts.

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

## Что берём из engine-паттернов

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
**Статус:** ✅ завершён

### Всё сделано
- `task_planner.py` встроен в `build_run.py`, а `routing` / `execution` теперь реально попадают в `job.json` / `meta.json`
- `routing_rules.yaml` теперь влияет на созданный run через planner, а не висит отдельно от runtime path
- добавлен `claw launch-plan` для dry-run preview: агент, routing rule, workspace mode, concurrency group, command preview
- `execute_job.py` читает `job.execution.workspace_mode` первым (приоритет над registry/env); `shared_project` alias; `isolated_checkout` backend
- demo/template tasks переведены на `preferred_agent: auto`; routing идёт через `default-codex` fallback rule
- `_system/contracts/review_decision.schema.json` — formal review decision schema (findings, approvals, waivers, follow-up)
- `generate_review_batch.py` пишет decision stubs в `reviews/decisions/`
- `hooklib.py` — `event_version`, `idempotency_key`, `delivery_attempts`, `max_delivery_attempts`; dead-letter в `reconcile_hooks.py`
- `_system/contracts/hook_payload.schema.json` — formal hook payload schema

### DoD — выполнен
- planner используется в основном runtime path ✅
- `routing_rules.yaml` реально влияет на созданный run ✅
- execution/workspace policy читается из job artifacts ✅
- demo/template сценарии покрывают routing + execution defaults ✅

---

## Этап 7 — OpenClaw integration
**Статус:** ✅ завершён
**Цель:** управлять runtime из чата после стабилизации execution layer

### Сделать
- команды / сценарии для:
  - создать проект
  - добавить task/spec
  - поставить run в queue
  - узнать status
  - сделать review batch
- cron/reconcile каждые 15 минут или event-driven wake ✅
- callback summary обратно в чат ✅

### DoD
- задачу можно ставить из OpenClaw
- completion summary приходит обратно
- review можно инициировать без ручной возни

---

## Что сделано в последней сессии (2026-03-13)

Параллельный запуск двух агентов для закрытия Epic 6:

**Codex (6.2 + 6.4):**
- `execute_job.py` привязан к `job.execution.workspace_mode` как first priority; добавлен `isolated_checkout` backend; `shared_project` alias → `project_root`
- demo-project и _template tasks переведены на `preferred_agent: auto`; тесты обновлены

**Claude (6.5 + 6.6):**
- `_system/contracts/review_decision.schema.json` — formal schema для review decisions
- `generate_review_batch.py` пишет decision stubs в `reviews/decisions/`
- `hooklib.py` — `event_version`, `idempotency_key`, `delivery_attempts`, `max_delivery_attempts`, dead-letter
- `_system/contracts/hook_payload.schema.json` создан

**Оркестратор поймал баг:** `task_planner.py` дефолтит `workspace_mode: "shared_project"`, который executor не знал → `run_all.sh` упал на `execute_job_test` → исправлено добавлением alias `shared_project` → `project_root`.

## Что сделано в текущей сессии (2026-03-13, reliability + docs)

Параллельный запуск двух агентов для закрытия runtime reliability и docs layer:

**Codex (9.2):**
- `claw.py worker` теперь продлевает lease во время выполнения `execute_job.py`
- при fail ниже `max_attempts` job получает exponential backoff и возвращается в `pending`
- при исчерпании попыток job уходит в `dead_letter`, а worker JSON отражает `queue_state`, retry metadata и heartbeat warnings
- добавлен `tests/worker_reliability_test.sh`

**Claude (9.1 + 10.1 + 10.3):**
- добавлены `docs/ARCHITECTURE.md` и `docs/CONTRACT_VERSIONING.md`
- `README.md` переписан под актуальную архитектуру
- planning docs приведены в соответствие с текущим состоянием системы

**Оркестраторский вывод:** docs-ветка из параллельного worktree быстро устаревает относительно живого roadmap, поэтому merge для `PLAN/BACKLOG/STATUS` должен быть selective, а не blind cherry-pick.

## Следующие незавершённые задачи

- **9.6:** concurrency / stress / failure-injection тесты (queue + worker + hooks)
- **9.7:** harden shell-command trust boundary для hooks и executor overrides (`CLAW_HOOK_COMMAND`, `CLAW_AGENT_COMMAND*`): уйти от raw `bash -lc` где возможно, валидировать формат команды, явно задокументировать trusted-only env overrides
- **9.8:** execution robustness fixes: safe JSON reads в `claw status`, idempotent/concurrency-safe `git_worktree` materialization, валидация `CLAW_AGENT_TIMEOUT_SECONDS` через `max(1, ...)`
- **9.9:** cleanup latent runtime edge cases: починить `stdin` mode в `_system/engine/agent_exec.py`, валидировать reviewer против agents registry, сделать `is_dead_letter()` side-effect free
- **10.2:** parallel execution guide (git_worktree isolation, edit scope, concurrency groups)
- **8.2:** richer cross-project status view (ошибки, approvals, pending reviews)

**Закрыто в текущей сессии:**
- **9.1:** queue/job contract versioning + migration story → `docs/CONTRACT_VERSIONING.md`
- **9.2:** worker reliability maturity → retry/backoff + lease heartbeat + `dead_letter` wired into `cmd_worker`
- **10.1:** architecture doc → `docs/ARCHITECTURE.md`
- **10.3:** README realignment → `README.md`

**Реализовано, но не отслеживалось в плане:**
- `_system/engine/agent_exec.py` — agent execution abstraction (registry parse, cwd policy, workspace root, command rendering)
- `scripts/run_task.py` — Python entrypoint для task→run (тонкий wrapper поверх planner)
- `_system/contracts/queue_item.schema.json` — formal queue item schema

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

### Инсайты после параллельного запуска Codex + Claude (сессия 2026-03-12)
- Лучший рабочий паттерн для orchestration-сессий — давать агентам узкие, почти не пересекающиеся slices и запускать их в отдельных git worktree.
- `Codex -> implementation`, `Claude -> orchestration/review/preview` снова подтвердился как практичный split: меньше конфликтов по файлам, проще cherry-pick и финальная верификация.
- `launch-plan` оказался не просто удобной CLI-командой, а важным human/agent checkpoint перед реальным запуском worker'а.
- Проверка результата в clean worktree обязательна: она ловит скрытые проблемы индексации/шаблонов, которые на грязной ветке выглядят «как будто всё ок».

### Инсайт: stdout pollution при оборачивании модулей (сессия 2026-03-13)
- **Агент, оборачивающий Python-модуль с `print()` side effects, должен изолировать stdout через `contextlib.redirect_stdout`.** `generate_batches()` печатает прогресс в stdout; `cmd_openclaw_review_batch` вызывал его напрямую — JSON output загрязнялся. Правило: любой `openclaw_*` command должен возвращать чистый JSON, все side-effect logs идут в stderr.
- Этот баг не виден при code review — проявляется только при запуске теста с реальными данными. `run_all.sh` поймал его за секунды.

### Инсайты после параллельного запуска Codex + Claude (сессия 2026-03-13)
- **`isolation=worktree` не изолирует агентов, если промпт содержит абсолютный путь к main repo.** Чтобы получить реальную изоляцию — передавать агенту путь к worktree, а не к main directory.
- **Строгое файловое разделение заменяет изоляцию worktree** при условии, что файлы не пересекаются: два агента параллельно писали в один каталог без конфликтов, потому что каждый трогал свой набор файлов.
- **Плановый дефолт и runtime дефолт должны быть синхронизированы.** `task_planner.py` дефолтит `shared_project`, но `execute_job.py` его не знал → баг. Всякий раз, когда planner добавляет новое значение в enum — executor должен его обрабатывать. Это нужно проверять в `execute_job_test.sh`.
- **`run_all.sh` — обязательный финальный шаг оркестратора.** Первый запуск поймал regression раньше, чем мёрж или ревью. Без автотестов баг ушёл бы в main branch незаметно.
- **Оркестратор должен исправлять баги самостоятельно до передачи результата.** Агент закончил, тест упал, оркестратор нашёл причину и починил — это нормальный цикл, не требующий участия пользователя.

### Инсайты после параллельного запуска Codex + Claude (сессия 2026-03-13, reliability + docs)
- **Разделение `implementation/runtime` и `docs/architecture` хорошо параллелится**, если агентам дать непересекающиеся зоны ответственности и отдельные worktree.
- **Planning docs — merge-sensitive слой.** `PLAN/BACKLOG/STATUS` живут быстрее, чем feature-ветка агента, поэтому их нельзя мёржить blind cherry-pick без сверки с live roadmap.
- **Completion summary агента — это не merge criterion.** Перед интеграцией оркестратор должен смотреть реальный `git show`/diff, иначе легко принять устаревшую или слишком широкую документационную правку.
- **Тесты + selective merge — лучшая связка для двухагентного режима.** Codex может закрывать runtime slice, Claude — narrative/docs slice, а оркестратор сводит их только после `run_all.sh` и ручной проверки конфликтных документов.

---

## Что улучшить в проекте

### Высокий приоритет
- ~~Убрать race в генерации `RUN-XXXX`~~ — **✅ сделано** (commit `fe11887`)
- ~~Реализовать дизайн из `REVIEW-runtime-integration-design.md`: встроить validation в `execute_job.py`, review batch cadence в `claw.py worker`.~~ — **✅ сделано**
- ~~Встроить planner в runtime path: `build_run.py` должен использовать `task_planner.py` и сохранять `routing` / `execution` в artifacts.~~ — **✅ сделано** (`0caec7c`)
- ~~Применять `routing_rules.yaml` в runtime при создании job, а не держать rules только в registry.~~ — **✅ сделано** через planner wiring (`0caec7c`)
- ~~Добавить `claw launch-plan` для dry-run preview execution decision.~~ — **✅ сделано** (`b8053ef`)
- ~~Подчинить workspace execution контракту из job artifacts и довести backends `shared_project` / `git_worktree` / `isolated_checkout`.~~ — **✅ сделано** (2026-03-13)
- ~~Обновить demo/template artifacts под `preferred_agent: auto` и execution defaults.~~ — **✅ сделано** (2026-03-13)

### Средний приоритет
- ~~Ввести formal review decision artifacts: `review_decision.json`, `findings.json`, approvals, waivers, follow-up queue.~~ — **✅ сделано** (2026-03-13)
- ~~Довести queue maturity: retry/backoff policy, poison-job threshold, DLQ handling, lease renewal heartbeat в worker loop.~~ — **✅ частично закрыто** (2026-03-13: retry/backoff + `dead_letter` + heartbeat в worker; дальше нужен stress/failure-injection слой)
- Harden shell-command trust boundary для hooks и executor overrides (`CLAW_HOOK_COMMAND`, `CLAW_AGENT_COMMAND*`): argv/registry contract вместо raw shell где возможно, trusted-only env override policy.
- Исправить execution robustness gaps: safe JSON reads в `claw status`, concurrency-safe/idempotent `git_worktree` creation, clamp timeout override `CLAW_AGENT_TIMEOUT_SECONDS >= 1`.
- ~~Формализовать hook delivery contract: idempotency, event versioning, retry semantics.~~ — **✅ сделано** (2026-03-13)
- ~~Добавить явный queue/job contract versioning и migration story для будущих изменений схем.~~ — **✅ сделано** (`docs/CONTRACT_VERSIONING.md`, 2026-03-13)
- ~~Сделать `claw review-batch` как часть unified CLI вместо standalone entrypoint-only usage.~~ — **✅ сделано** (2026-03-13)
- Добавить multi-project worker/reconciler loop с безопасным fair scheduling.
- Убрать latent runtime inconsistencies: сломанный `stdin` mode в `_system/engine/agent_exec.py`, side-effect predicate в `reconcile_hooks.py`, отсутствие reviewer registry validation в `generate_review_batch.py`.
- ~~Исправить `.gitignore` политику для `docs/`, чтобы проектная документация не терялась из индекса по умолчанию.~~ — **✅ сделано** (2026-03-13)

### Низкий приоритет, но полезно
- ~~Сохранить summary/metrics по runs и review batches в отдельный state snapshot для status/dashboard сценариев.~~ — **✅ сделано** (2026-03-13)
- Добавить richer status view: последние ошибки, awaiting approval jobs, pending hooks, pending reviews.
- Уточнить policy для `ask_human` и approval UX, чтобы `awaiting_approval` стало частью реального сценария, а не только queue state.

---

## Ближайшие шаги
1. **9.6** — добавить concurrency / stress / failure-injection тесты для queue + worker + hooks
2. **9.7** — ужесточить trust boundary для `CLAW_HOOK_COMMAND` и `CLAW_AGENT_COMMAND*`
3. **9.8** — закрыть execution robustness gaps (`claw status`, `git_worktree`, timeout clamp)
4. **9.9** — убрать latent runtime edge cases (`stdin` mode, reviewer validation, dead-letter predicate)
5. **10.2** — оформить отдельный parallel execution guide по worktree isolation и merge discipline
6. **8.2** — собрать richer status view по ошибкам, approvals, hooks и pending reviews

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
