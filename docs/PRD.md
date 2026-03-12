# PRD — `claw` project orchestration workspace

Дата: 2026-03-12
Статус: working draft / after local queue slice

## 1. Product summary
`claw` — локальный workspace и orchestration-слой для управления проектами, спеками, задачами и агентными запусками через Codex / Claude.

Цель продукта:
- убрать хаос ручных запусков
- сделать каждый run воспроизводимым
- хранить source-of-truth в filesystem
- стандартизировать queue, execution artifacts, hooks и review cadence

Каждая задача в целевой модели должна иметь:
- стабильный путь на диске
- понятный источник контекста (`task + spec + docs`)
- воспроизводимый запуск
- артефакты выполнения
- queue/hook lifecycle
- review-механику

---

## 2. Problem
Сейчас агентные запуски слишком часто живут как разрозненные промпты и временные диалоги:
- задачи теряются в чате
- спеки не привязаны к конкретным запускам
- нет единого run-log
- completion может потеряться
- review и контроль качества не стандартизированы
- выбор между Codex и Claude происходит вручную и непредсказуемо

Нужна файловая модель + orchestration engine, где проектная работа становится наблюдаемой, воспроизводимой и пригодной для очередей/ретраев.

---

## 3. Goals

### Основные цели
1. Дать единую структуру для проектов, задач, спеков и run-артефактов
2. Автоматизировать запуск Codex / Claude по spec-driven workflow
3. Поддержать как direct-run, так и file-backed queue execution
4. Сохранять итог каждого запуска в фиксированном формате
5. Обеспечить completion notification через hook/callback + fallback reconcile
6. Добавить review cadence и opposite-model review

### Не-цели v0.x
- полноценный SaaS / web UI
- многопользовательская ролевая модель
- удалённый distributed execution
- production-grade deployment layer с `systemd/nginx`

---

## 4. Target user
Основной пользователь:
- solo founder / AI operator / product builder
- ведёт несколько проектов параллельно
- работает через specs, docs и агентные CLI
- хочет запускать задачи быстро, но без потери контроля

---

## 5. Core user stories
1. Как пользователь, я хочу создать проект с понятной структурой, чтобы `docs/specs/tasks/runs/reviews/state` лежали предсказуемо.
2. Как пользователь, я хочу добавить `task` и `spec`, чтобы запускать агента из файлов, а не из памяти.
3. Как пользователь, я хочу выполнить задачу сразу или поставить её в queue.
4. Как пользователь, я хочу получить run directory с отчётом, логами и результатом.
5. Как пользователь, я хочу видеть статус run без ручного поиска по папкам.
6. Как пользователь, я хочу получать completion signal, даже если основной callback не сработал.
7. Как пользователь, я хочу периодический и risk-based review, чтобы не накапливать скрытые ошибки.

---

## 6. Functional scope

## 6.1 Project structure
Система должна поддерживать структуру вида:

```text
claw/
├── _system/
├── projects/
│   └── <project-slug>/
│       ├── docs/
│       ├── specs/
│       ├── tasks/
│       ├── runs/
│       ├── reviews/
│       └── state/
└── skills/
```

## 6.2 Task + spec workflow
Каждая задача должна:
- храниться как отдельный файл
- ссылаться на spec по стабильному пути
- содержать frontmatter с `id`, `title`, `status`, `preferred_agent`, `priority`, `review_policy`, `needs_review`, `risk_flags`

Каждая spec должна:
- описывать goal
- иметь constraints
- иметь acceptance criteria

## 6.3 Agent routing
Система должна поддерживать выбор исполнителя:
- **Claude**: дизайн, UX, ambiguity, architecture, research
- **Codex**: implementation, tests, fixes, scripts, shell/python glue

## 6.4 Run orchestration
Для каждой задачи должен существовать launcher, который:
1. читает task/spec
2. валидирует project identity
3. создаёт run directory
4. генерирует prompt
5. либо исполняет run сразу, либо ставит его в queue
6. сохраняет артефакты выполнения
7. формирует hook payload

## 6.5 Queue orchestration
Система должна поддерживать:
- file-backed queue per project
- `pending/running/done/failed/awaiting_approval`
- atomic claim worker'ом
- deduplication по `job_id`
- lookup run через `run_path`

## 6.6 Run artifacts
Минимальный набор артефактов:
- `meta.json`
- `prompt.txt`
- `stdout.log`
- `stderr.log`
- `result.json`
- `report.md`
- hook payload
- queue item

## 6.7 Hook + reconcile
Система должна поддерживать:
- push hook сразу после завершения
- fallback reconcile job, который повторно отправляет недоставленные завершения

## 6.8 Review system
Система должна поддерживать:
- review после каждых 5 successful runs
- review сразу при fail / risk / uncertainty
- opposite-model reviewer по умолчанию

---

## 7. Functional requirements

### FR-1. Project shell
Система должна позволять создать project scaffold из шаблона.

### FR-2. Task/spec to run
Система должна уметь превращать `TASK.md + SPEC.md` в воспроизводимый run.

### FR-3. Stable run path
Каждый запуск должен сохраняться в предсказуемый путь:
`projects/<slug>/runs/YYYY-MM-DD/RUN-XXXX/`

### FR-4. Queue path
Каждый queued run должен иметь queue item, который ссылается на run через `run_path`.

### FR-5. Agent execution
Система должна уметь вызывать локально:
- `codex`
- `claude`

### FR-6. Status + result
Каждый run должен завершаться машиночитаемым `result.json` и человекочитаемым `report.md`, а также быть доступным через status lookup.

### FR-7. Delivery resilience
Если hook не отправился сразу, система не должна терять событие завершения.

### FR-8. Review batching
Система должна уметь собрать review batch по cadence и risk triggers.

---

## 8. Non-functional requirements
- filesystem = source of truth
- deterministic file paths
- минимальная магия, максимум прозрачности
- возможность локального запуска без внешней инфраструктуры
- артефакты пригодны для ручной проверки
- runtime-артефакты отделены от исходников проекта
- queue transitions атомарны в пределах одного filesystem

---

## 9. Current product state

### Уже подтверждено кодом
- project scaffold работает
- `task/spec -> run` работает
- direct execution работает
- file-backed hooks работают
- queue enqueue/worker/status работают
- queue approval/reclaim lifecycle работает
- formal contracts/schema validation доступны локально
- review batch generation работает поверх run artifacts
- post-artifact validation автоматически встраивается в `execute_job.py`
- worker автоматически ведёт review cadence state и триггерит review batch
- planner routing/execution сохраняются в `job.json` и `meta.json`
- `claw launch-plan` показывает dry-run execution decision до реального запуска
- `codex` и `claude` запускаются локально из репозитория

### Уже подтверждено тестами
- foundation scaffold
- task-to-job generation
- execute success/failure
- hook lifecycle
- queue CLI flow
- queue approval/reclaim flow
- contracts validation
- review batch generation
- runtime integration for validation + review cadence
- launch-plan dry-run preview

### Ещё не подтверждено
- multi-project scheduling
- OpenClaw bridge

---

## 10. Proposed architecture
`claw` состоит из двух частей:

### A. Project shell
Отвечает за:
- структуру проектов
- docs/specs/tasks
- registry
- routing rules
- templates
- review policy

### B. Orchestration engine
Отвечает за:
- queue
- worker loop
- result contracts
- hooks/callbacks
- retries/reconcile
- approvals

Донор engine:
- `/Users/Apple/Developer/multi-agent-cli-orchestrator`

---

## 11. Success criteria for v1
v1 считается успешной, если пользователь может:
1. создать проект
2. добавить spec и task
3. выбрать direct run или queue
4. запустить задачу через Codex/Claude
5. получить `result.json` + `report.md`
6. не потерять completion signal
7. увидеть status run и queue state
8. сформировать review batch

---

## 12. Open questions
1. Нужен ли planner-step всегда, или только для ambiguous tasks?
2. Делать ли `Claude -> Codex -> Claude` pipeline дефолтом или только шаблоном?
3. Когда именно вызывать review: строго после 5 запусков или по rolling window + risk score?
4. Нужен ли отдельный scheduler для multi-project queue, или project-scoped worker достаточно долго?
5. Нужен ли webhook server в `claw` v1, или достаточно file-queue + OpenClaw bridge?

---

## 13. Next step
Следующий практический шаг после текущего состояния:
- переключить `execute_job.py` на persisted `job.execution` и workspace backends
- обновить demo/template artifacts под `preferred_agent: auto` и execution defaults
- добавить unified `claw review-batch`
- закрыть clean-worktree parity для `docs/` и template docs artifacts
- после этого возвращаться к OpenClaw bridge
