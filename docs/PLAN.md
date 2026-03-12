# PLAN — дальнейшее развитие `claw`

Дата: 2026-03-12
Статус: working plan / updated after engine + contracts + review slice

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
- `job.json` теперь хранит `run_path`, чтобы queue item мог детерминированно ссылаться на run artifacts
- `run_task.sh` валидирует `project.slug` из `state/project.yaml` против имени каталога
- Queue lifecycle поддерживает `awaiting_approval`, явный `approve` и reclaim stale `running` jobs
- Добавлены formal contracts в `_system/contracts/` и CLI-валидатор `scripts/validate_artifacts.py`
- Добавлен standalone review batch generator `scripts/generate_review_batch.py`
- Локальный запуск `codex` и `claude` из репозитория подтверждён smoke-проверкой

### Тестовое покрытие
- `foundation_scaffold_test.sh`
- `task_to_job_test.sh`
- `execute_job_test.sh`
- `hook_lifecycle_test.sh`
- `queue_cli_test.sh`
- `queue_lifecycle_test.sh`
- `contracts_validation_test.sh`
- `review_batch_test.sh`

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
**Статус:** in progress / run id race remains

### Уже сделано
- добавлен minimal file queue
- добавлен project-scoped worker loop
- добавлен единый CLI поверх существующих shell/python скриптов
- worker/queue runtime вынесен в более чистый engine API
- добавлен reclaim stale running jobs
- поддержан lifecycle `awaiting_approval`
- формализованы `job.json` / `result.json` schema

### Осталось
- решить race в генерации `RUN-XXXX`

### DoD
- engine запускается локально
- job можно подать через CLI / file queue
- worker забирает job из queue и завершает её детерминированно
- структура артефактов не зависит от способа запуска

---

## Этап 5 — reviewer system
**Статус:** in progress / standalone batch generator added

### Уже сделано
- счётчик successful runs реализован через cadence batch size из policy
- добавлен review batch generator
- opposite-model reviewer mapping применяется из policy registry
- immediate triggers работают для `failed`, `needs_review`, `risky_area`, `uncertainty`, `large_diff`

### Осталось
- встроить генерацию batch в runtime cadence без ручного вызова
- связать batch generation с chat/OpenClaw flow

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

## Этап 6 — OpenClaw integration
**Цель:** управлять всем этим из чата

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
- Нумерация `RUN-XXXX` всё ещё уязвима к race при параллельном создании run.
- Validation и review batch пока живут как отдельные CLI, а не как часть обязательного runtime lifecycle.
- `docs/` глобально заигнорен в репозитории, из-за чего изменения в документации легко не попадают в коммиты случайно.
- `review batch` сейчас формируется по существующим run artifacts, но не имеет автоматического триггера после завершения worker/hook cycle.
- Worker остаётся project-scoped; для реальной эксплуатации может понадобиться scheduler над несколькими проектами.

### Практический вывод
- Архитектура уже жизнеспособна как local-first orchestration shell.
- Следующий потолок сложности теперь не в queue/hook mechanics, а в orchestration policy: routing, automatic review cadence, approval UX и multi-project scheduling.

---

## Что улучшить в проекте

### Высокий приоритет
- Убрать race в генерации `RUN-XXXX`: lock file, atomic counter или UUID + human-friendly alias.
- Встроить schema validation в `run_task.sh`/`execute_job.py`/worker path, чтобы невалидные артефакты отлавливались сразу, а не только отдельным CLI.
- Автоматически запускать review batch generation после завершения run или по reconcile cadence.
- Применять `routing_rules.yaml` в runtime при создании job, а не держать rules только в registry.

### Средний приоритет
- Добавить явный queue/job contract versioning и migration story для будущих изменений схем.
- Сделать `claw review-batch` как часть unified CLI вместо standalone entrypoint-only usage.
- Добавить multi-project worker/reconciler loop с безопасным fair scheduling.
- Исправить `.gitignore` политику для `docs/`, чтобы проектная документация не терялась из индекса по умолчанию.

### Низкий приоритет, но полезно
- Сохранить summary/metrics по runs и review batches в отдельный state snapshot для status/dashboard сценариев.
- Добавить richer status view: последние ошибки, awaiting approval jobs, pending hooks, pending reviews.
- Уточнить policy для `ask_human` и approval UX, чтобы `awaiting_approval` стало частью реального сценария, а не только queue state.

---

## Ближайшие шаги
1. Убрать race в нумерации `RUN-XXXX`
2. Встроить schema validation и review batch в основной runtime без ручного вызова отдельных CLI
3. Применить `routing_rules.yaml` и `reviewer_policy.yaml` в runtime, а не только хранить их в registry
4. Решить, нужен ли multi-project worker loop или пока достаточно project-scoped worker
5. Добавить bridge в OpenClaw для queue submit / run status / completion summary

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
