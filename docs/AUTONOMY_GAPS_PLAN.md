# AUTONOMY GAPS PLAN
# Верификация анализа агента + конкретный план

Дата: 2026-03-13
Основан на: анализе агента (gap-analysis до режима full autonomy)

> Исторический документ. На 2026-03-14 основные gaps из этого плана уже закрыты:
> `import-project`, `decompose-epic`, `workflow-validate`, `task-graph-lint`,
> `run-checks`, `epic-status`, `orchestrate --scope epic:N` и artifact-level
> completion delivery уже реализованы. Читай этот файл как record того, почему
> появились Epic 12 / TASK-004..011, а не как источник текущего статуса.

---

## 1. Оценка анализа агента

### Что агент написал верно

**Gap A — нет import/bootstrap внешнего проекта**
Подтверждено. `create-project` есть, но нет `import-project` / `attach-existing-repo`.
Workflow contract уже есть как шаблон, но заполняется вручную.

**Gap B — нет industrial decomposition pipeline**
Исторически было верно на момент анализа. Сейчас gap закрыт частично CLI-командой
`claw decompose-epic`; `claw create-spec` как отдельная команда по-прежнему не выделялась.

**Gap C — нет guardrails против structural drift**
Подтверждено и подтверждено живым прецедентом в этой же сессии:
- оба агента создали `projects/claw-dev/` вместо `projects/_claw-dev/`
- тест был ослаблен (`project_count >= 2`) под accidental scaffold
Задокументировано в `docs/PLAN.md` как инсайт, но защиты нет.

**Gap D — WORKFLOW.md не влияет на planner**
Исторически было верно. На 2026-03-14 gap закрыт: `workflow-validate`,
`allowed_agents` gate и `scope_warnings`/contract enforcement уже реализованы.

**Gap E — task graph не является обязательным gate**
Исторически было верно. На 2026-03-14 gap закрыт через `claw task-graph-lint`
и pre-orchestrate blocking checks.

**Общая оценка автономии (6/10 для большого внешнего проекта)**
Принята, хотя с поправкой (см. ниже).

---

### Что агент написал ошибочно или устарело

**Gap F — "нет completion criteria"**
Частично неверно. `claw orchestrate` уже имеет:
- `failure_budget` — стоп при N consecutive fails
- idle stop — когда очередь пуста
- auto follow-up task enqueue из `needs_follow_up` reviewer decisions

Это основа completion logic. Что реально отсутствует — уровень epic/roadmap "весь scope done",
а не уровень "очередь пуста". Это более узкий gap.

**Gap H — "нет operator dashboard"**
Уже на момент написания было неверно. Epic 8.2 (`claw dashboard`) закрыт и включает:
- `pending_approvals`, `retry_backlog`, `recent_failures`, `ready_tasks`, `current_run`
- `openclaw status` расширение

Агент либо не знал о выполненном epic, либо работал с устаревшим контекстом.

**"v2 autonomy не завершена"**
НЕВЕРНО. `docs/BACKLOG.md` явно: "Следующая фаза (v2) — ✅ завершена".
Закрыто: auto follow-up enqueue, failure budget, auto-review executor.

**"Навыки нужно перенести/адаптировать"**
Избыточно. `claw-epic-sprint`, `claw-task-spec`, `claw-orchestrate`, `claw-run-debugger` —
уже живые нативные skills в системе. Они не требуют "переноса".

**Оценка 6/10 для external project**
Должна быть 6.5–7/10 с учётом того, что `claw orchestrate` (Epic 8.4) уже замкнул loop.

---

## 2. Реальные гэпы (верифицированные)

| Gap | Название | Реален? | Приоритет |
|-----|----------|---------|-----------|
| A | Import/bootstrap внешнего проекта | ✅ закрыт | P0 |
| B | Decomposition pipeline как first-class CLI | ✅ закрыт | P0 |
| C | Structural guardrails against drift | ✅ закрыт | P0 |
| D | WORKFLOW.md enforcement на planner/orchestrate | ✅ закрыт | P1 |
| E | Task graph lint как mandatory pre-orchestrate gate | ✅ закрыт | P1 |
| F* | Epic/roadmap completion criteria (не только queue empty) | ✅ закрыт | P1 |
| G | Project command registry (test/lint/build в WORKFLOW) | ✅ закрыт | P1 |
| H | Operator dashboard | ❌ уже был закрыт | — |

---

## 3. Plan: задачи для закрытия гэпов

### Epic 12 — External Project Autonomy

**Цель:** из "repo + roadmap" → полный orchestration run без ручного вмешательства.

---

#### TASK-12.1 — `claw import-project` (Gap A) — P0

**Цель:** подключить внешний repo в claw одной командой.

**Scope:**
- `scripts/claw.py` — новая команда `import-project`
- `projects/_template/` — базовый шаблон (уже есть, использовать)

**Acceptance criteria:**
- `claw import-project --slug my-app --path /path/to/repo` создаёт `projects/my-app/`
- Копирует template scaffold
- Генерирует `docs/WORKFLOW.md` с заполненными `edit_scope` (из repo root dirs) и `allowed_agents`
- Создаёт `state/project.yaml` с `slug: my-app` и `source_path`
- Не трогает сам внешний repo

**DoD:** один вызов CLI, проверяется тестом в `tests/`

---

#### TASK-12.2 — `claw decompose-epic` (Gap B) — P0

**Цель:** из большого текста (roadmap/epic/backlog) генерировать TASK + SPEC пары.

**Scope:**
- `scripts/claw.py` — новая команда `decompose-epic`
- `_system/engine/decomposer.py` — логика разбивки через LLM

**Acceptance criteria:**
- `claw decompose-epic --project my-app --input roadmap.md` создаёт `TASK-NN.md` + `SPEC-NN.md`
- Каждая спека ограничена 2–3 часами работы
- Dependencies валидны (нет циклов, нет broken refs)
- Нет пересечений по файлам без явного `shared_files` флага
- Создаётся `state/sprint_index.json` — список задач и их статус

**DoD:** тест с sample roadmap.md → проверить что задачи созданы корректно

---

#### TASK-12.3 — Structural guardrails против drift (Gap C) — P0

**Цель:** запретить агенту создавать неожиданный scaffold и ослаблять тесты.

**Scope:**
- `_system/engine/guardrails.py` — новый модуль
- `scripts/claw.py` — вызов guardrails перед commit/enqueue/orchestrate

**Acceptance criteria:**
- Проверка 1: если агент создал `projects/<new-slug>` без разрешения → fail с reason
- Проверка 2: если diff содержит ослабление assert (e.g. `>= N` → `>= N-1`) → warning
- Проверка 3: если task правит файлы вне `edit_scope` из WORKFLOW.md → warning/fail
- `claw guardrail-check --project slug --diff-path diff.txt` — standalone вызов
- `claw orchestrate` вызывает guardrail check автоматически после каждого run

**DoD:** тест с намеренно плохим diff → guardrail должен его поймать

---

#### TASK-12.4 — WORKFLOW.md enforcement на planner (Gap D) — P1

**Цель:** `allowed_agents` и `edit_scope` из WORKFLOW.md реально блокируют orchestration.

**Scope:**
- `_system/engine/workflow_contract.py` — новый модуль (это и есть TASK-003 из _claw-dev!)
- `_system/contracts/workflow.schema.json` — JSON schema
- `scripts/claw.py` — вызов validator в `orchestrate` и `launch-plan`

**Acceptance criteria:**
- `claw orchestrate` читает `docs/WORKFLOW.md` перед выбором агента
- Если task требует агента не из `allowed_agents` → reason code `contract_violation`
- Если spec указывает файлы вне `edit_scope` → warning в launch-plan, fail в orchestrate
- `claw workflow-validate --project slug` — standalone check

**Связь:** закрывает TASK-003 в `projects/_claw-dev/tasks/`

**DoD:** тест с WORKFLOW.md ограничивающим агентов → orchestrate должен выбирать правильно

---

#### TASK-12.5 — Task graph lint как обязательный gate (Gap E) — P1

**Цель:** `claw orchestrate` не стартует на битом task graph.

**Scope:**
- `_system/engine/task_graph.py` — lint + snapshot
- `scripts/claw.py` — pre-orchestrate gate

**Acceptance criteria:**
- `claw task-graph-lint --project slug` → cycle detection, missing dependency refs
- `claw orchestrate` вызывает lint и прерывается при cycle / broken refs
- `claw status` показывает `blockers` и `ready_tasks` как топ-уровень
- Параллелизация разрешается только для задач без file-overlap

**DoD:** тест с намеренно циклическими зависимостями → orchestrate должен остановиться

---

#### TASK-12.6 — Project command registry (Gap G) — P1

**Цель:** orchestrator знает как тестировать/собирать/lint-ить каждый проект.

**Scope:**
- `projects/_template/docs/WORKFLOW.md` — добавить секцию `commands`
- `_system/engine/workflow_contract.py` — читать commands registry
- `scripts/claw.py` — `claw run-checks --project slug` вызывает registry

**WORKFLOW.md additions:**
```yaml
commands:
  test: "bash tests/run_all.sh"
  lint: "npx tsc --noEmit"
  build: "npm run build"
  smoke: "bash tests/smoke.sh"
```

**Acceptance criteria:**
- `claw run-checks` читает registry и запускает нужные команды
- Worker/orchestrator использует `commands.test` после каждого run вместо hardcoded "bash tests/run_all.sh"
- Если `commands` отсутствует → graceful fallback с предупреждением

**DoD:** тест с кастомным test_command → worker должен использовать его

---

#### TASK-12.7 — Epic/roadmap completion criteria (Gap F*) — P1

**Цель:** orchestrator умеет сказать не только "очередь пуста", а "scope X done".

**Scope:**
- `state/sprint_index.json` (из TASK-12.2) — основа
- `scripts/claw.py` — `claw epic-status --project slug --epic 12`

**Acceptance criteria:**
- Каждый TASK содержит `epic` tag (уже в frontmatter)
- `claw epic-status` показывает: total tasks / done / blocked / pending + % completion
- `claw orchestrate` имеет флаг `--scope epic:12` — останавливается когда epic завершён
- В `orchestration_state.json` добавляется `scope_completion` field

**DoD:** тест — `orchestrate --scope epic:12` должен остановиться после завершения задач с `epic: 12`

---

## 4. Рекомендуемый порядок реализации

```
12.1 (import)
  ↓
12.2 (decompose)  ←── после 12.1 т.к. нужен project slug
  ↓
12.3 (guardrails) ←── можно параллельно с 12.4
12.4 (WORKFLOW enforcement)
  ↓
12.5 (task graph gate) ←── после 12.4 т.к. использует contract
12.6 (command registry) ←── можно параллельно с 12.5
  ↓
12.7 (epic completion) ←── после 12.5 + 12.2
```

**Можно запустить параллельно:**
- `12.1 + 12.3` (import и guardrails независимы)
- `12.5 + 12.6` (graph lint и command registry не пересекаются)

---

## 5. Оценка готовности после закрытия эпика 12

| Режим | Сейчас | После Epic 12 |
|-------|--------|---------------|
| Single task execution | 9/10 | 9/10 |
| Серия связанных задач | 8/10 | 8.5/10 |
| Multi-run orchestration внутри проекта | 7.5/10 | 8.5/10 |
| Большой внешний проект почти без надзора | 6.5/10 | **8.5/10** |

---

## 6. Что НЕ нужно делать (не gap, агент ошибся)

- `claw dashboard` — уже есть (Epic 8.2 done)
- v2 autonomy loop — уже завершён (follow-up enqueue, failure budget, auto-review)
- Переносить/адаптировать skills — они уже нативные в системе
- Копировать claw-epic-sprint/claw-orchestrate как "reference assets" — они живые
