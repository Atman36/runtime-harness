# PRD — `claw` project orchestration workspace

Дата: 2026-03-12
Статус: draft / v0.1

## 1. Product summary
`claw` — это локальный workspace и orchestration-слой для управления проектами, спеками, задачами и агентными запусками через Codex / Claude.

Цель продукта — убрать хаос ручных запусков, чтобы каждая задача имела:
- стабильный путь на диске
- понятный источник контекста (`task + spec + docs`)
- воспроизводимый запуск
- артефакты выполнения
- hook / callback по завершению
- review-механику

---

## 2. Problem
Сейчас агентные запуски часто живут как разрозненные промпты и временные диалоги:
- задачи теряются в чате
- спеки не привязаны к конкретным запускам
- нет единого run-log
- completion может потеряться
- review и контроль качества не стандартизированы
- выбор между Codex и Claude происходит вручную и непредсказуемо

Нужна файловая система + orchestration engine, где проектная работа становится наблюдаемой и воспроизводимой.

---

## 3. Goals

### Основные цели
1. Дать единую структуру для проектов, задач, спеков и run-артефактов
2. Автоматизировать запуск Codex / Claude по spec-driven workflow
3. Сохранять итог каждого запуска в фиксированном формате
4. Обеспечить completion notification через hook/callback + fallback reconciliation
5. Добавить review cadence и opposite-model review

### Не-цели v0.1
- полноценный SaaS / web UI
- многопользовательская ролевая модель
- удалённый distributed execution
- production-grade deployment layer с systemd/nginx на первом шаге

---

## 4. Target user
Основной пользователь:
- solo founder / AI operator / product builder
- ведёт несколько проектов параллельно
- работает через specs, docs и агентные CLI
- хочет запускать задачи быстро, но без потери контроля

---

## 5. Core user stories
1. **Как пользователь**, я хочу создать проект с понятной структурой папок, чтобы все docs/specs/tasks/runs лежали предсказуемо.
2. **Как пользователь**, я хочу добавить task и spec, чтобы потом запускать агента не из головы, а из файлов.
3. **Как пользователь**, я хочу выбрать Codex или Claude автоматически, чтобы не гадать каждый раз.
4. **Как пользователь**, я хочу запускать задачу и получать run directory с отчётом, логами и результатом.
5. **Как пользователь**, я хочу получать completion signal, даже если основной callback не сработал.
6. **Как пользователь**, я хочу периодический и risk-based review, чтобы не накапливать скрытые ошибки.

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
- ссылаться на spec по относительному/стабильному пути
- содержать frontmatter с `id`, `title`, `status`, `preferred_agent`, `priority`, `review_policy`

Каждая spec должна:
- описывать goal
- иметь constraints
- иметь acceptance criteria

## 6.3 Agent routing
Система должна поддерживать автоматический выбор исполнителя:
- **Claude**: дизайн, UX, ambiguity, architecture, research
- **Codex**: implementation, tests, fixes, scripts, shell/python glue

## 6.4 Run orchestration
Для каждой задачи должен существовать launcher, который:
1. читает task/spec
2. определяет pipeline
3. создаёт run directory
4. генерирует prompt
5. запускает CLI агента
6. сохраняет артефакты выполнения
7. формирует hook/callback payload

## 6.5 Run artifacts
Минимальный набор артефактов:
- `meta.json`
- `prompt.txt`
- `stdout.log`
- `stderr.log`
- `result.json`
- `report.md`
- hook payload / queue item

## 6.6 Hook + reconcile
Система должна поддерживать:
- push hook сразу после завершения
- fallback reconcile job, который повторно отправляет недоставленные завершения

## 6.7 Review system
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
Каждый запуск должен сохраняться в предсказуемый путь типа:
`projects/<slug>/runs/YYYY-MM-DD/RUN-XXXX/`

### FR-4. Agent execution
Система должна уметь вызывать локально:
- `codex`
- `claude`

### FR-5. Status + result
Каждый run должен завершаться машиночитаемым `result.json` и человекочитаемым `report.md`.

### FR-6. Delivery resilience
Если hook не отправился сразу, система не должна терять событие завершения.

### FR-7. Review batching
Система должна уметь собрать review batch по cadence и risk triggers.

---

## 8. Non-functional requirements
- filesystem = source of truth
- deterministic file paths
- минимальная магия, максимум прозрачности
- возможность локального запуска без внешней инфраструктуры
- артефакты должны быть пригодны для ручной проверки
- runtime-артефакты должны быть отделены от исходников проекта

---

## 9. Trial run learnings
Пробный прогон через Codex уже дал важные выводы.

### Что подтверждено
- можно положить spec в репозиторий и запускать Codex по ней
- минимальный launcher может создавать run-артефакты локально
- smoke test с `scripts/run_demo_task.sh` успешно отработал
- базовая идея `spec -> run folder -> report/meta` жизнеспособна

### Что выявлено
- одного demo runner недостаточно для реального orchestration workflow
- placeholders в `report.md` пока не интерполируются автоматически
- completion/hook pipeline пока ещё не встроен
- ACP route для Codex сейчас не настроен, поэтому текущий путь — локальные CLI
- нужен отдельный project layer и engine layer, иначе всё снова скатится в ad-hoc scripts

### Вывод из прогона
Пробный прогон подтвердил правильность направления, но также показал, что следующий шаг — не усложнять demo script, а строить системный orchestration layer поверх стабильной файловой структуры.

---

## 10. Proposed architecture
`claw` должен состоять из двух частей:

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
- runner
- workers
- result contracts
- callbacks
- retries
- approvals

Донор engine:
- `/Users/Apple/Developer/multi-agent-cli-orchestrator`

---

## 11. Success criteria for v1
v1 считается успешной, если пользователь может:
1. создать проект
2. добавить spec и task
3. автоматически выбрать агента
4. запустить задачу
5. получить `result.json` + `report.md`
6. не потерять completion signal
7. сформировать review batch

---

## 12. Open questions
1. Нужен ли отдельный planner-step всегда, или только для ambiguous tasks?
2. Делать ли `Claude -> Codex -> Claude` pipeline дефолтом или только шаблоном?
3. Когда именно вызывать review: строго после 5 запусков или по rolling window + risk score?
4. Нужен ли webhook server в `claw` v1, или достаточно file-queue + OpenClaw bridge?

---

## 13. Next step
Следующий практический шаг после этого PRD:
- реализовать foundation layer (`_system/registry`, `_system/templates`, `projects/_template`)
- затем интегрировать slim subset engine из донора
