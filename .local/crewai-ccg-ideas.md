# Что можно взять из `crewAI-main` и `ccg-workflow-main` для `claw`

Дата: 2026-03-14
Статус: локальная заметка

## Короткий вывод

Целиком ни `crewAI`, ни `ccg-workflow` в `claw` переносить не нужно.

Для `claw` полезнее всего:

1. из `ccg-workflow` взять **нормализованный agent-wrapper с потоковыми event-ами**;
2. из `crewAI` взять **аккуратную модель listener/human-feedback поверх уже существующих artifacts**;
3. частично взять **repo-owned context/session log** в сильно упрощённом виде.

`ccg-workflow` ближе к текущему `claw` по практической ценности.  
`crewAI` полезен скорее как reference на execution patterns, а не как донор архитектуры.

---

## Что в `claw` уже есть и не нужно дублировать

По сути, часть идей из этих проектов уже реализована:

- project-level `WORKFLOW.md` contract;
- `allowed_agents`, `commands` registry, `run-checks`;
- file-backed queue, retries, approvals, review cadence;
- `workflow_graph.json`;
- `events.jsonl` + `event_snapshot.json`;
- `dashboard`, `openclaw status/summary/wake`;
- `git_worktree` и `isolated_checkout`.

Поэтому смотреть надо только на то, чего пока нет или что можно сделать заметно лучше.

---

## Что реально стоит взять из `ccg-workflow-main`

### 1. Normalized agent wrapper с потоковыми событиями

Это самый полезный кусок для `claw`.

Где смотреть:

- `/Users/Apple/Downloads/ccg-workflow-main/codeagent-wrapper/main.go`
- `/Users/Apple/Downloads/ccg-workflow-main/codeagent-wrapper/parser.go`
- `/Users/Apple/Downloads/ccg-workflow-main/codeagent-wrapper/server.go`
- `/Users/Apple/Downloads/ccg-workflow-main/codeagent-wrapper/filter.go`

Что там хорошего:

- wrapper прячет различия между Codex / Claude / Gemini;
- stdout модели разбирается в единый event format;
- отдельно выделяются `message`, `reasoning`, `command`;
- шум stderr фильтруется;
- можно показывать live output, а не только финальный `stdout.log`.

Как адаптировать в `claw` без лишнего scope:

- не тащить Go wrapper и web UI как есть;
- добавить простой Python-side streaming layer вокруг `execute_job.py`;
- во время run писать `agent_stream.jsonl` рядом с `stdout.log`;
- в `openclaw summary` и `dashboard` показывать последние event-ы.

Почему это стоит делать первым:

- сейчас `claw` видит run в основном по финальным файлам;
- для live-status это узкое место;
- это улучшение не ломает filesystem-first модель.

### 2. Patch-only advisory mode для внешнего review

Где смотреть:

- `/Users/Apple/Downloads/ccg-workflow-main/README.md`
- `/Users/Apple/Downloads/ccg-workflow-main/templates/commands/review.md`
- `/Users/Apple/Downloads/ccg-workflow-main/templates/commands/codex-exec.md`

Что там полезно:

- внешний агент может вернуть не прямые правки, а findings + patch;
- оркестратор отдельно решает, применять это или нет.

Как адаптировать в `claw`:

- не переводить весь runtime в patch-only;
- добавить только новый режим для reviewer/advisor run;
- артефакты: `advice.md`, `patch.diff`, `review_findings.json`.

Зачем это нужно:

- для рискованных изменений это безопаснее, чем прямой write access;
- хорошо ложится на существующий review cycle.

Приоритет:

- средний, после streaming wrapper.

### 3. Лёгкий session/context log

Где смотреть:

- `/Users/Apple/Downloads/ccg-workflow-main/templates/commands/context.md`

Что там полезно:

- локальный decision trail;
- сжатие session notes в историю изменений;
- связь commit ↔ причины решений.

Как адаптировать в `claw`:

- не делать `.context/` целиком;
- добавить только `state/context_log.jsonl` или `state/decision_log.jsonl`;
- писать туда ключевые оркестраторские решения: routing, approval, retry, follow-up creation.

Почему это полезно:

- сейчас большая часть runtime state есть, но rationale размазан между `STATUS.md`, reports и diff;
- один append-only log упростит audit/debug.

---

## Что реально стоит взять из `crewAI-main`

### 1. Human-feedback provider model поверх `ask-human`

Это самый практичный кусок из `crewAI`.

Где смотреть:

- `/Users/Apple/Downloads/crewAI-main/lib/crewai/src/crewai/flow/human_feedback.py`
- `/Users/Apple/Downloads/crewAI-main/lib/crewai/src/crewai/flow/persistence/sqlite.py`

Что там хорошего:

- pause/resume оформлен как нормальный execution primitive;
- feedback не привязан к консоли;
- outcome можно сводить к typed decision.

Как адаптировать в `claw`:

- не брать их `Flow` и не брать SQLite persistence;
- использовать уже существующие `claw ask-human` / `resolve-approval`;
- добавить typed checkpoint artifact в run, например `approval_checkpoint.json`;
- resume должен продолжать run детерминированно через filesystem state.

Почему это полезно:

- у `claw` approvals уже есть, но они больше project/queue-level;
- этот паттерн даст step-level HITL без смены архитектуры.

### 2. Listener model поверх existing events

Где смотреть:

- `/Users/Apple/Downloads/crewAI-main/docs/en/concepts/event-listener.mdx`

Что там полезно:

- явная модель “подписаться на событие и выполнить интеграцию”;
- это лучше, чем разрастающийся ad hoc hook code.

Как адаптировать в `claw`:

- не делать глобальный event bus;
- взять только интерфейсный паттерн;
- добавить простой registry для listeners на события `run_started`, `run_finished`, `review_created`, `approval_requested`.

Минимальный формат:

- `docs/WORKFLOW.md` или `_system/registry/listeners.yaml`;
- trusted command или internal handler name;
- side effects писать обратно в artifacts.

### 3. Обогащение workflow graph metadata

Где смотреть:

- `/Users/Apple/Downloads/crewAI-main/lib/crewai/src/crewai/flow/visualization/builder.py`
- `/Users/Apple/Downloads/crewAI-main/docs/en/concepts/flows.mdx`

Что там полезно:

- graph хранит не просто nodes/edges, а причины переходов;
- отдельно видны start/router/listener semantics.

Как адаптировать в `claw`:

- не строить их Flow DSL;
- расширить текущий `workflow_graph.json` полями вроде `edge_type`, `trigger`, `reason_code`, `approval_gate`.

Почему это полезно:

- graph станет не просто картинкой, а пригодным debug artifact.

### 4. Planning feature как reference, но не как direct import

Где смотреть:

- `/Users/Apple/Downloads/crewAI-main/docs/en/concepts/planning.mdx`

Вывод:

- в `claw` отдельный planner уже есть;
- переносить их planning mode не нужно;
- можно только подсмотреть, как они добавляют step-by-step plan в execution context.

То есть:

- брать идею enrich prompt/job plan;
- не брать их runtime model.

---

## Что лучше не брать

### Из `ccg-workflow`

- весь installer/bootstrap слой;
- глобальные slash-commands;
- MCP setup UI;
- browser UI и desktop-like оболочку;
- multi-model orchestration ради самой orchestration.

Это уже отдельный продукт, а не минимальный `claw`.

### Из `crewAI`

- `Crew` / `Flow` framework как основу runtime;
- SQLite persistence;
- memory/training/knowledge stack;
- tool ecosystem;
- enterprise tracing/observability suite.

Это слишком тяжело и меняет центр архитектуры.

---

## Рекомендуемый порядок внедрения

Если брать только то, что реально усилит `claw` и не раздует scope:

1. **Сначала**: streaming agent wrapper / normalized run events из идей `ccg-workflow`.
2. **Потом**: step-level human feedback checkpoint из идей `crewAI`.
3. **Потом**: простой listener registry.
4. **Опционально**: advisory patch-only review mode.
5. **В конце**: context/decision log и richer graph metadata.

---

## Самый практичный v1.1 slice

Если выбрать только один следующий кусок, я бы брал такой:

**`live agent stream` для `claw`**

Минимальный scope:

- `execute_job.py` пишет `agent_stream.jsonl`;
- event types: `message`, `reasoning`, `command`, `status`;
- `openclaw summary` отдаёт последние N event-ов;
- `dashboard` показывает live-tail;
- tests проверяют, что stream создаётся и не ломает текущие artifacts.

Это даст самый заметный прирост полезности при минимальном архитектурном риске.
