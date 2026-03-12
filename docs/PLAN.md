# PLAN — дальнейшее развитие `claw`

Дата: 2026-03-12
Статус: draft / working plan

## Цель
Собрать в `claw` рабочую инфраструктуру для управления проектами, спеками, задачами и агентными запусками через Codex / Claude с фиксированными артефактами, hook/callback-механикой и review-циклом.

---

## Базовая идея
`claw` строится не с нуля, а как **project shell + orchestration engine**.

- **Project shell** — структура проектов, tasks/specs/docs/plans, правила роутинга, шаблоны, review policy
- **Engine** — очередь, runner, workers, artifacts, callbacks, retry, approvals
- **OpenClaw** — чатовый интерфейс, cron, wake events, orchestration entrypoint

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
    └── project-orchestrator/
```

### 2. Engine layer
Должен уметь:
- принимать `job.json`
- класть job в filesystem queue
- запускать Codex / Claude workers
- сохранять result/report/logs
- поддерживать callback / hook
- поддерживать retry / ask_human / review step

### 3. Integration layer
Должен уметь:
- превращать `TASK.md + SPEC.md` в `job.json`
- выбирать агента по эвристикам
- запускать pipeline `plan -> implement -> review`
- вызывать reconcile hooks
- формировать review batch

---

## Что уже сделано
- Создан репозиторий `claw`
- Проведён smoke test через Codex
- Добавлены базовые файлы:
  - `README.md`
  - `templates/report.template.md`
  - `scripts/run_demo_task.sh`
  - `CODEx_TEST_REPORT.md`
- Есть первый успешный commit с demo runner
- **Этап 1 завершён:** `_system/registry/`, `_system/templates/`, `projects/_template/`, `projects/demo-project/`
- **Этап 3 завершён:** `run_task.sh`, task→job адаптер, генерация `prompt.txt`, `meta.json`, `job.json`, `result.json`
- **Этап 4 завершён:** file-backed hook slice (`state/hooks/{pending,sent,failed}`), `execute_job.py`, `dispatch_hooks.py`, `reconcile_hooks.py`, `hooklib.py`
- **Тесты:** 4 smoke-test сьюта (`foundation_scaffold_test.sh`, `task_to_job_test.sh`, `execute_job_test.sh`, `hook_lifecycle_test.sh`)

---

## Что берём из донора
Источник:
- `/Users/Apple/Developer/multi-agent-cli-orchestrator`

### Копировать / адаптировать
- `fsqueue/`
- `orchestrator/`
- `workers/`
- `contracts/`
- части `prompts/`
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

### Claude использовать, когда:
- дизайн / UX / flow
- неоднозначная спека
- архитектурная развилка
- исследование / нормализация требований

### Codex использовать, когда:
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

## Этап 1 — foundation
**Цель:** создать стабильную файловую структуру и source of truth.

### Сделать
- `_system/registry/`
- `_system/templates/`
- `_system/scripts/`
- `projects/_template/`
- `projects/demo-project/`

### Артефакты
- `agents.yaml`
- `routing_rules.yaml`
- `reviewer_policy.yaml`
- `task.template.md`
- `spec.template.md`
- `prompt.template.md`
- `report.template.md`

### DoD
- можно создать новый проект по шаблону
- можно положить spec/task в предсказуемые пути
- есть единые шаблоны для дальнейшей автоматизации

---

## Этап 2 — engine import
**Цель:** встроить ядро orchestration из донора.

### Сделать
- перенести slimmed subset донора в `_system/engine/`
- сохранить filesystem queue model
- сохранить job/result contracts
- сохранить workers под codex / claude

### DoD
- engine запускается локально
- job можно подать через CLI / file queue
- job пишет артефакты в предсказуемую структуру

---

## Этап 3 — task/spec adapter
**Цель:** запускать агентные задания уже из project layer.

### Сделать
- скрипт `task -> job`
- launcher `run_task`
- генерацию `prompt.txt`
- генерацию `meta.json`
- создание `runs/YYYY-MM-DD/RUN-XXXX/`

### DoD
- `TASK-001.md` можно превратить в job
- launcher создаёт run-dir
- run-dir содержит task/spec/prompt/meta/result/report

---

## Этап 4 — hooks / callback / reconcile
**Цель:** чтобы завершения не терялись.

Статус на 2026-03-12:
- минимальный file-based hook slice реализован в `state/hooks/{pending,sent,failed}`
- completion hook создаётся из `execute_job.py`
- есть отдельные `dispatch_hooks.py` и `reconcile_hooks.py`
- filesystem остаётся source of truth, а delivery выполняется через локальную команду из `CLAW_HOOK_COMMAND`

### Сделать
- `pending_hooks/`
- `sent_hooks/`
- `failed_hooks/`
- dispatcher
- reconcile job

### Политика
- основной путь: push hook
- резерв: reconcile по расписанию

### DoD
- после завершения run создаётся hook
- hook можно доставить или повторить
- состояние доставки прозрачно видно на диске

---

## Этап 5 — reviewer system
**Цель:** автоматизировать review cadence.

### Сделать
- счётчик successful runs
- review batch generator
- opposite-model reviewer mapping

### Правила
- review после каждых 5 successful runs
- review сразу при:
  - failed
  - needs_review
  - risky_area
  - uncertainty
  - large_diff

### DoD
- review batch формируется автоматически
- список кандидатов прозрачно виден
- reviewer по умолчанию — opposite model

---

## Этап 6 — OpenClaw integration
**Цель:** управлять всем этим из чата.

### Сделать
- команды / сценарии для:
  - создать проект
  - добавить task/spec
  - запустить run
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
- goal
- scope
- constraints
- acceptance criteria
- notes

## Run artifacts
Минимальный набор:
- `meta.json`
- `prompt.txt`
- `stdout.log`
- `stderr.log`
- `result.json`
- `report.md`
- `hook.json` / queue item

---

## Риски
- переусложнить `claw` и превратить его в generic platform вместо project OS
- перетащить из донора слишком много ненужного operational ballast
- смешать runtime-артефакты и source files
- завязать completion только на один callback без reconcile fallback
- не зафиксировать правила выбора Claude vs Codex и снова уйти в ручной хаос

---

## Принципы
- filesystem = source of truth
- deterministic paths > магия
- artifacts first
- push hook + reconcile fallback
- opposite-model review by default
- shell/project layer отделён от engine layer
- OpenClaw — front door, не место хранения истины

---

## Ближайшие шаги
1. Создать `_system/registry/` и базовые yaml-файлы
2. Создать `_system/templates/` под task/spec/prompt/report
3. Подготовить `projects/_template/`
4. Подготовить `projects/demo-project/`
5. Составить карту переноса engine-файлов из донора
6. Интегрировать slim subset в `_system/engine/`
7. Написать adapter `task/spec -> job`
8. Прогнать первый end-to-end run

---

## Критерий успеха v1
Система считается рабочей, когда можно:
1. создать проект
2. добавить spec и task
3. выбрать агента автоматически
4. запустить run
5. получить report/result/hook
6. сделать reconcile
7. собрать review batch без ручного шаманства

---

## Наблюдения из ревью кода (2026-03-12)

### Критические баги

**B1. `run_task.sh` — `json_escape` не экранирует newlines**
- Файл: `scripts/run_task.sh`, функция `json_escape`
- Проблема: `sed` экранирует `\` и `"`, но символ новой строки в значении ломает heredoc → невалидный `job.json`
- Сценарий: `task_title` с переносом строки или мультистрочное `spec_reference`
- Фикс: заменить на `python3 -c "import json,sys; print(json.dumps(sys.stdin.read()), end='')"`

**B2. `execute_job.py` — кастомный YAML-парсер `parse_agents_registry` хрупкий**
- Файл: `scripts/execute_job.py`, строки 36–56
- Проблема: парсер основан на точном отступе (2 vs 4 пробела). Комментарий, list-значение, или нестандартный отступ — агент не распознаётся, запуск молча падает с "Unsupported preferred_agent"
- Фикс: использовать `PyYAML` (`import yaml`) или конвертировать `agents.yaml` → `agents.json`

**B3. `run_task.sh` — `needs_review` и `risk_flags` вставляются в JSON без валидации**
- Файл: `scripts/run_task.sh`, heredoc `job.json`
- Проблема: значения инъектируются unquoted. Если `needs_review` содержит что угодно кроме `true`/`false`, или `risk_flags` — не валидный JSON-массив, `job.json` сломается
- Фикс: явно валидировать оба значения перед вставкой

### Средние проблемы

**M1. `run_task.sh` — TOCTOU race в нумерации runs**
- Файл: `scripts/run_task.sh`, строки 144–157
- Проблема: при параллельных вызовах оба прочитают одинаковый `last_run_number` → коллизия `RUN-XXXX`
- Фикс: lock-файл или атомарный `mkdir` с retry-loop

**M2. `execute_job.py` — `trim_summary` дублирует `hooklib.trim_text`**
- Файл: `scripts/execute_job.py`, строки 137–143
- Проблема: идентичная функция уже есть в `hooklib.py` как `trim_text`
- Фикс: `from hooklib import trim_text as trim_summary`

**M3. `run_task.sh` — project slug из имени папки, не из `state/project.yaml`**
- Файл: `scripts/run_task.sh`, строка 29
- Проблема: при переименовании папки slug рассинхронизируется с `state/project.yaml`
- Фикс: читать slug из yaml и сравнивать с именем папки

**M4. `routing_rules.yaml` и `reviewer_policy.yaml` не используются в коде**
- Ни один скрипт не читает эти файлы. Роутинг берётся из `preferred_agent` в frontmatter задачи
- Это ОК для v0.1, но вводит в заблуждение. Пометить в README как "defined, not yet active"

**M5. `iter_hook_files` использует пустой `hook_id` как трюк для получения директории**
- Файл: `scripts/hooklib.py`, строка 312–313
- Фикс: заменить на `sorted((hook_root(project_root) / status).glob("*.json"))`

### Minor

**N1. Нет `.gitignore`**
- Runtime-артефакты попадают в git: `.demo-runs/`, `projects/*/runs/`, `projects/*/state/hooks/`

**N2. Hook-команда запускается как login shell (`-l`)**
- Файл: `scripts/hooklib.py`, строка 229
- `["/bin/bash", "-lc", command]` загружает `.bash_profile`/`.bashrc` — медленно, непредсказуемо
- Задокументировать поведение или дать env-переменную для отключения `-l`

**N3. `execute_job.py` возвращает exit code агента напрямую**
- Строка 375: `return 0 if status == "success" else exit_code`
- Если агент завершился с кодом 143 (SIGTERM), `execute_job.py` тоже вернёт 143 — может запутать вызывающий скрипт
- Рассмотреть нормализацию: возвращать `1` при любом failure

**N4. `run_demo_task.sh` — deprecated runner без маркировки**
- Пишет в `.demo-runs/` в корне репо, тогда как новый runner пишет в `projects/<slug>/runs/`
- Добавить комментарий в файл и README о статусе deprecated

**N5. Тесты хардкодят путь `demo-project`**
- Файлы: `tests/hook_lifecycle_test.sh`, `tests/execute_job_test.sh`
- Тесты лучше создавать временный проект через `create_project.sh` и не зависеть от `demo-project`

### Архитектурные наблюдения

- **CLI entry point отсутствует**: сейчас нужно вручную вызывать `bash scripts/run_task.sh` и `python3 scripts/dispatch_hooks.py`. Нужен единый `claw` CLI (`claw run <task>`, `claw dispatch`, `claw reconcile`, `claw status`)
- **`routing_rules.yaml` должен применяться в `run_task.sh`**: читать файл и переопределять `preferred_agent` если task не задал явно, иначе файл — мёртвый код
- **Нет `claw status`**: нет агрегированного вида всех runs, их статусов и hook-статусов
- **Нет `--dry-run`**: для `run_task.sh` полезен флаг, который показывает что было бы запущено без запуска
- **Отсутствует JSON Schema** для `job.json` и `result.json` — контракт между скриптами нигде не зафиксирован формально
