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

## Что сделано в текущей сессии (2026-03-13, scheduler + hardening)

- **9.6:** добавлены `tests/concurrency_stress_test.sh` и `tests/runtime_hardening_test.sh`; queue/worker/hooks теперь покрыты concurrency, stress и failure-injection regression cases
- **9.7:** shell env overrides переведены на trusted argv contract; `CLAW_HOOK_COMMAND` и `CLAW_AGENT_COMMAND*` больше не идут через raw `bash -lc`; запрещён shell-eval (`bash -c`, redirection tokens, command substitution)
- **9.8:** `claw status` читает битый JSON безопасно; `git_worktree`/isolated checkout materialization защищены lock'ом и повторным check; `CLAW_AGENT_TIMEOUT_SECONDS` clamp'ится через `max(1, ...)`
- **9.9:** `_system/engine/agent_exec.py` исправлен для `stdin` mode; reviewer policy валидируется против `agents.yaml`; `reconcile_hooks.is_dead_letter()` больше не пишет в payload
- **8.1:** добавлен `claw scheduler` для fair multi-project worker scheduling
- **8.2:** добавлен richer `claw dashboard` + `openclaw status` extension (`pending_approvals`, `retry_backlog`, `recent_failures`, `ready_tasks`, `current_run`)
- **8.3:** добавлены filesystem-backed approval requests: `claw ask-human` и `claw resolve-approval`
- **8.4:** добавлен `claw orchestrate` с циклом ready-task select → enqueue → worker → review/approval decision → next task

## Следующие незавершённые задачи

- `TASK-002` — ✅ закрыт (2026-03-13): `task-lint` теперь эмитирует `task_parse_failed` вместо traceback; regression test добавлен
- `TASK-003` — ✅ закрыт (2026-03-13): `contract_version != 1` теперь отклоняется loader и validator; regression tests добавлены
- Следующие slices: `11.1` workflow graph artifact и `11.2` event snapshot + replay

**Закрыто в текущей сессии:**
- **9.1:** queue/job contract versioning + migration story → `docs/CONTRACT_VERSIONING.md`
- **9.2:** worker reliability maturity → retry/backoff + lease heartbeat + `dead_letter` wired into `cmd_worker`
- **9.6–9.9:** runtime hardening + stress coverage → trusted command boundary, safe status JSON, worktree locks, timeout clamp, reviewer validation, side-effect free dead-letter checks
- **8.1–8.4:** scheduler/orchestration layer → `scheduler`, `dashboard`, `ask-human`, `resolve-approval`, `orchestrate`
- **v2 autonomy closure:** worker автоматически запускает reviewer agent по pending decision stubs; `needs_follow_up` reviewer decisions материализуются в новые `TASK-*` и сразу ставятся в queue; `state/orchestration_state.json` хранит failure budget между вызовами `orchestrate`
- **10.1:** architecture doc → `docs/ARCHITECTURE.md`
- **10.2:** parallel execution guide → `docs/PARALLEL_EXECUTION.md`
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
- **Для непрерывного цикла недостаточно просто “после run запускать reviewer”.** Нужны ещё task selector, review gate, decision engine, queue chaining, stop-conditions и видимость состояния цикла — иначе получится бесконечный шум, а не orchestration.

### Инсайты: запуск агентов из Claude Code как оркестратора (сессия 2026-03-13)

**Контекст:** Epic 12 запускался через `python scripts/claw.py run --execute` прямо внутри активной сессии Claude Code, а не через отдельный процесс.

- **`claw run --execute` из Claude Code работает нативно.** TASK-004 выполнился в том же shell-окружении, Codex получил полный spec через prompt.txt, написал код, запустил тесты, сделал commit — всё без ручного вмешательства. `claw` как CLI-оркестратор поверх вложенных агентов оказался вполне рабочим паттерном.
- **Задержка vs. прозрачность.** `run --execute` занял ~5 минут и вернул одно итоговое JSON с `summary`. Промежуточных сигналов нет — контролировать процесс можно только через отдельный `git diff` или `tail stdout.log`. Для sync-режима это приемлемо; для длинных runs нужен отдельный фоновый процесс с polling.
- **Workspace_mode = shared_project: агент работает в `projects/_claw-dev/`, но правит файлы в repo root.** Spec с абсолютными путями (`/Users/Apple/progect/claw/scripts/claw.py`) решает это. Без абсолютных путей агент может интерпретировать relative refs относительно своего cwd и промахнуться.
- **Codex выполнил commit сам.** Это удобно и соответствует DoD, но означает, что orchestrator не имеет возможности "отфильтровать" коммит перед его созданием. Для задач с риском (TASK-005, TASK-006) лучше явно прописывать в spec: "НЕ делай commit, оркестратор проверит и закоммитит сам".
- **Параллельный запуск P0 задач невозможен без worktree:** оба TASK-004 и TASK-005 правят `scripts/claw.py`. При shared_project режиме второй агент получит dirty tree от первого. Решение: либо sequential, либо `workspace_mode: git_worktree` + ручной merge.
- **`claw launch-plan` — критически важный шаг перед execute.** Он показал workspace_mode и cwd ещё до запуска — что позволило обнаружить потенциальную проблему с путями и добавить абсолютные refs в spec до делегирования.

### Инсайты после запуска двух Claude-оркестраторов по `.local/symphony-ideas.md` и `.local/dify-ideas.md` (сессия 2026-03-13)
- **Оба агента выбрали разумные first slices**: из Symphony — typed `WORKFLOW.md` loader, из Dify — typed `TriggerEnvelope` для `openclaw enqueue`. Значит, сами заметки в `.local/*-ideas.md` достаточно конкретны для agent-driven decomposition.
- **Оба агента одинаково промахнулись по project path**: создали `projects/claw-dev/` вместо существующего `projects/_claw-dev/`. Это не случайный баг одной ветки, а системная неоднозначность naming convention. Оркестратор должен явно фиксировать target project slug/path в prompt и/или валидировать, что новые task/spec артефакты кладутся только в разрешённый проект.
- **Автогенерация project scaffold агентом опасна даже при хорошем кодовом результате.** Оба прогона принесли полезный engine/contracts код, но попутно изменили shape репозитория и даже расслабили тест (`project_count >= 2`) под свой accidental scaffold. Вывод: scaffold-изменения должны считаться sensitive и требовать отдельного подтверждения/правила.
- **Summary агента нельзя считать доказательством использования orchestration path.** В одном отчёте `launch-plan` был указан в подозрительном формате; проверять надо по реальным командам, артефактам run dir и diff, а не по финальному рассказу.
- **Нужен orchestration guardrail:** перед коммитом агент должен пройти простую проверку: “не создал ли я новый `projects/<slug>` вместо согласованного project root; не ослабил ли я тесты только ради нового scaffold”. Это cheap check, который сэкономит ручной review.
- **Guardrail-check живёт на `edit_scope` из `docs/WORKFLOW.md`.** Если агентский slice сознательно расширяет файловую поверхность, а контракт не обновлён, standalone guardrail даст ложный `edit_scope_violation` даже при корректном diff. Для run-driven задач scope надо синхронизировать с реальным DoD до запуска, а не после review.
- **Prompt-footer notify нельзя считать delivery contract.** Реальный сбой уже был: Codex успешно закончил run, но не выполнил финальную `openclaw system event ...` команду из prompt. Значит, обязательный completion signal должен жить в orchestrator-managed state/hooks, а не в памяти вложенного агента. Это вынесено в `TASK-011` / `SPEC-011`.
- **2026-03-13: TASK-011 закрыт.** Теперь completed run пишет machine-verifiable `delivery` state в `result/meta`, а `claw openclaw status|summary` показывает `pending_delivery` до reconcile/wake. Практический вывод: агенту можно доверять implementation slice, но не финальный notify-step; завершённость должна определяться по runtime state, а не по footer-команде.
- **2026-03-14: разница между Codex и Claude по completion signal сохранилась.** Claude на `TASK-010` корректно прислал completion notification, а Codex на `TASK-008` снова завершил slice без явного user-facing hook/callback. Значит, watchdog и runtime-level delivery contract остаются обязательными даже после улучшения orchestration path; нельзя считать, что nested agent стабильно закроет последний notify-step.
- **Sequential execution для slices, трогающих `scripts/claw.py`, пока правильнее параллели в shared tree.** `TASK-008` и `TASK-010` оба меняли один и тот же CLI-файл; запуск по очереди дал два чистых коммита (`f9a1311`, `6990123`) без ручного merge. До нормального worktree discipline параллель тут — просто дорогой способ купить конфликт.

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
- ~~Добавить richer status view: последние ошибки, awaiting approval jobs, pending hooks, pending reviews.~~ — **✅ сделано** (8.2, 2026-03-13)
- ~~Уточнить policy для `ask_human` и approval UX, чтобы `awaiting_approval` стало частью реального сценария, а не только queue state.~~ — **✅ сделано** (8.3, 2026-03-13)

---

## Ближайшие шаги

Roadmap снова открыт уже после закрытия reopened regression slices `TASK-002` и `TASK-003`; post-v2 расширения можно добивать отдельными узкими implementation slices.

### Post-v2 slice из note review (2026-03-13)

- **11.1 `workflow graph artifact`** — ✅ сделано (2026-03-13)
  - добавлен portable artifact `state/workflow_graph.json` с `nodes + edges`
  - зафиксированы stable schema/version в `_system/contracts/workflow_graph.schema.json`
  - добавлен CLI `claw workflow-graph` и regression coverage; `task-snapshot` теперь тоже refresh-ит graph artifact
- **11.2 `event snapshot + replay`** — ✅ сделано (2026-03-13)
  - добавлены append-only run events `events.jsonl` и derived `event_snapshot.json`
  - добавлен replay helper `claw openclaw replay-events` и `event_snapshot` в `openclaw summary`
  - enqueue / worker / wake теперь пишут event trail для live-status слоя

### Epic 13 — live agent feedback loop (2026-03-14)

- **TASK-012 `agent_stream.jsonl`** — ✅ сделано (2026-03-14)
  - `execute_job.py` переведён на `Popen` + threaded stdout/stderr drain без потери timeout-semantics
  - run artifacts теперь включают `agent_stream.jsonl`, а `openclaw summary` отдаёт `stream_tail`
  - regression coverage добавлена для classify/tail/timeout/backward-compat path

- **TASK-013 `approval_checkpoint.json`** — ✅ сделано (2026-03-14)
  - pending `approval_checkpoint.json` завершает runner с exit code `2`
  - worker переводит job в `awaiting_approval`, а `resolve-checkpoint` принимает решение accept/reject
  - добавлен regression coverage для pause/resume потока

- **TASK-014 listener registry for orchestrator events** — ✅ сделано (2026-03-14)
  - добавлены `_system/registry/listeners.yaml` и `_system/engine/listener_dispatch.py`
  - `claw.py` теперь диспатчит trusted listeners на `run_started`, `run_finished`, `review_created`, `approval_requested`
  - side effects логируются в `state/listener_log.jsonl`, а listener failures не роняют основной orchestration path

- **TASK-015 advisory patch-only review mode** — ✅ сделано (2026-03-14)
  - task front matter теперь поддерживает `mode: advisory`, runner выставляет `CLAW_ADVISORY=1`, а `meta.json` помечает advisory intent
  - post-run path предупреждает о пропавших `advice.md`, `patch.diff`, `review_findings.json`, не переводя run в failed только из-за missing artifacts
  - добавлен `claw apply-patch <project_root> <run_id> [--confirm]` с dry-run preview diff/findings и `git apply` + `patch_applied` event после подтверждения

- **TASK-016 orchestrator decision log + enriched workflow graph metadata** — ✅ сделано (2026-03-15)
  - добавлен append-only `state/decision_log.jsonl` и CLI `claw decision-log <project_root> [--last N]`
  - routing / retry / approval_requested / follow_up_created теперь сохраняются как typed decisions с `reason_code`, `details` и `outcome`
  - `workflow_graph.json` edges получили `edge_type`, `trigger`, `reason_code`, `approval_gate` с backward-compatible schema для legacy artifacts

Epic 13 закрыт. Следующий порядок определяется эпиками 14 и 15: coordination foundation (`TASK-017`/`TASK-018`) и отдельно operator live status slice (`TASK-022`).

### Epic 14 — PaperClip-inspired coordination primitives (2026-03-14)

Цель эпика: перенести в `claw` сильные operational patterns из `PaperClip`,
не перетаскивая его Node/Postgres/UI control plane. Сохраняем текущий принцип:
filesystem остаётся source of truth, а новые coordination-механики живут в
артефактах и project state.

- **TASK-017 `Heartbeat wake queue and coalescing`** — ✅ сделано (2026-03-15)
  - добавлен file-backed wake contract в `state/wakes/pending/*.json` + schema `_system/contracts/wake_item.schema.json`
  - `claw wake-enqueue` детерминированно coalesce-ит wake-события по `agent/task` scope с типами `timer | assignment | mention | manual | approval`
  - `claw wake-status`, `openclaw status` и `openclaw wake` показывают pending/coalesced wake state без hidden runtime state
- **TASK-018 `Agent inbox and atomic task claim/release`** — ✅ сделано (2026-03-15)
  - добавлены `task-claim` / `task-release` / `inbox` CLI и file-backed claims в `state/claims/`
  - claim идемпотентен для текущего владельца, конфликтует для других, release пишет reason trail
  - claim влияет на routing и будит wake queue; `blocked` / `in_progress` / `released` отражаются в task front matter
- **TASK-019 `Resumable agent session state`** — ✅ сделано (2026-03-15)
  - file-backed session continuity в `state/sessions` + schema `session_state.schema.json`
  - CLI `session-status|session-update|session-reset|session-rotate` хранит resume handle + handoff summary
  - inbox/wake/task-claim теперь возвращают session summary для resume
- **TASK-020 `Org graph and delegation policy`** — ✅ сделано (2026-03-15)
  - добавлен `org_graph.yaml` в registry + loader/validation в `_system/engine/org_graph.py`
  - CLI `org-graph`, `task-delegate`, `task-escalate` создают child tasks с parent linkage + delegation metadata
  - project-level `docs/ORG_GRAPH.yaml` теперь может частично override-ить agent policy без потери базовых `reports_to/capabilities`, а `delegation.allow_self_delegate` реально влияет на policy
  - blocked задачи эскалируются вверх по `reports_to` chain с explicit diagnostics при запрете
- **TASK-021 `Budget and governance guardrails`** — ✅ сделано (2026-03-16)
  - file-backed `guardrail_snapshot.json` per run + project `state/guardrails/budget_snapshot.json`
  - soft-limit warnings / hard-stop pause semantics wired into worker and `claw run --execute`
  - approval-required actions reuse `state/approvals/` and expose guardrail state through `status` / `dashboard` / `openclaw status`

Не переносим как есть:
- React UI / mobile UX
- Postgres multi-company control plane
- runtime-specific adapter code из PaperClip без filesystem adaptation
- "компанию из агентов" как отдельную продуктовую оболочку поверх `claw`

Рекомендуемый порядок после Epic 13: `TASK-017` → `TASK-018` → параллельно `TASK-019` и `TASK-020` → `TASK-021`.

---

### Epic 15 — operator transport and session UX (2026-03-15)

Цель эпика: добрать операторский слой поверх уже существующего filesystem-first
engine: live status, контекстные директивы, session continuity, безопасный
file exchange и transport extensibility. Сохраняем текущий принцип:
filesystem остаётся source of truth, а transport state и resume handles живут в
явных артефактах, а не в памяти процесса.

- **TASK-022 `Live status feed for operators`** ✅ done
  - CLI/polling feed поверх `events.jsonl`, `event_snapshot.json` и `agent_stream.jsonl`
  - оператор видит progress/status без ручного tail по run directory
  - первый slice без SSE/websocket; transport может poll-ить уже готовые артефакты
- **TASK-023 `Message directives and context binding`** ✅ done
  - нормализованный парсер директив `/agent`, `/project`, `@branch`
  - `ctx:` footer для reply-based context carry-over без hidden transport state
  - единые правила precedence: reply context > explicit directives > defaults
- **TASK-024 `Operator session memory and resume handles`** ✅ done
  - repo-scoped operator session state per scope и engine с inspectable artifacts
  - `openclaw session-status|session-update|session-reset|session-new-thread` + auto-resume resolution в `bind-context`
  - provider-neutral handle contract с derived resume lines через agent registry templates
- **TASK-025 `Safe file exchange for project roots`** ✅ done
  - `openclaw file-put|file-fetch` добавляют upload/download contract для файлов и директорий в active project/worktree
  - deny-globs, path normalization, atomic write и zip-on-fetch зафиксированы в reusable engine helper
  - worktree-targeted exchange требует явный `--run`, чтобы transport не адресовал произвольные raw FS paths
- **TASK-026 `Transport plugin surface and setup checks`** ✅ done
  - transport/command backend contract вместо hardcoded единственного ingress path
  - setup/doctor checks для конфигурации transport layer
  - новый transport можно подключать как narrow extension, не раздувая `scripts/claw.py`

Не переносим как есть:
- transport-specific UX и команды, которые требуют уже готового внешнего chat UI
- provider-specific resume line как canonical source of truth
- in-memory scheduler как замену существующей file-backed queue
- фреймворк-плагины без минимального filesystem contract и config validation

Epic 15 закрыт: transport/session operator surface теперь покрывает status feed, context binding, session continuity, safe file exchange и explicit transport plugin/setup contract.

---

## Отчёт сессии (2026-03-14) — Epic 12 закрыт, хук верифицирован

### Статус на конец сессии

**Epic 12 — External Project Autonomy: 100% done (8/8 задач)**

| Task | Статус | Что реализовано |
|------|--------|-----------------|
| TASK-004 | ✅ done | `claw import-project` — scaffold внешнего repo |
| TASK-005 | ✅ done | Structural guardrails engine |
| TASK-006 | ✅ done | WORKFLOW.md enforcement: `allowed_agents` gate + `scope_warnings` |
| TASK-007 | ✅ done | `claw task-graph-lint` + file-overlap warnings |
| TASK-008 | ✅ done | WORKFLOW.md command registry + `claw run-checks` |
| TASK-009 | ✅ done | `claw decompose-epic` — LLM-assisted task decomposition |
| TASK-010 | ✅ done | `claw epic-status` + `orchestrate --scope epic:N` |
| TASK-011 | ✅ done | Mandatory completion signal — artifact-level delivery contract |

### Hook verification (2026-03-14)

Проведена end-to-end верификация хукового механизма:

- `openclaw wake` — обрабатывает pending хуки и возвращает callback payloads
- `delivery` поле в `result.json` / `meta.json` — `pending_delivery` → `delivered` после wake/reconcile
- Все 9 openclaw тестов (`openclaw_test.sh`) зелёные
- `execute_job_test.sh`: проверяет `"status": "pending_delivery"` сразу после run, до wake
- Итог: **Codex может не запускать prompt-footer `openclaw system event`, но completion всё равно видна оркестратору через artifact state**

Текущее состояние проекта `_claw-dev`:
- Queue: 1 stale pending item (RUN-0001 / TASK-001, создан 2026-03-12 — мусор)
- Hooks: 0 pending, 3 sent (все delivered)
- `pending_approvals: 1` — approval request ещё висит

### Тесты

Полный suite: **159 пассов**, 0 реальных падений. Контракт-тест `FAIL` — ожидаемое поведение (тестирует что невалидные артефакты отклоняются).

---

## Критерий успеха v1 — ✅ достигнут (commit `172bf5b`)
`claw` считается достаточно собранным для v1, если пользователь может:
- создать проект
- добавить spec и task
- поставить задачу в queue или выполнить сразу
- получить `result.json`, `report.md`, `stdout.log`, `stderr.log`
- не потерять completion signal
- посмотреть status run без ручного поиска по каталогу
- собрать review batch по cadence и risk triggers
