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
