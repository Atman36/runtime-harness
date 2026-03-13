# claw

> Filesystem-first orchestration shell for agent-driven project execution.

`claw` управляет проектами, задачами, спеками и агентными запусками через
дисковые артефакты. Здесь нет демона, БД и скрытого runtime state: файлы в
`projects/<slug>/` и `_system/` являются source of truth.

## Что здесь главное

Сейчас система в основном строится вокруг четырёх ролей:

- **OpenClaw** — chat/operator entrypoint. Из чата ставит задачи в очередь, запрашивает статус и получает callback-результаты.
- **claw** — оркестратор и CLI. Создаёт run artifacts, маршрутизирует задачу, запускает worker loop, гоняет проверки, review и delivery hooks.
- **Codex** — основной implementation-агент для понятных инженерных задач, фиксов, glue-кода и локальных изменений с чётким DoD.
- **Claude** — агент для decomposition, ambiguous specs, архитектурных решений, review и orchestration-heavy slices.

Базовый принцип один: **filesystem is the system**. Любое решение, переход
состояния, hook, review batch или статус задачи должен быть виден на диске.

## Основной Workflow

```text
OpenClaw / operator
        │
        ▼
claw import-project / decompose-epic / create task+spec
        │
        ▼
claw launch-plan / workflow-validate / task-graph-lint
        │
        ▼
claw run | enqueue | orchestrate
        │
        ▼
Codex or Claude execution
        │
        ▼
runs/YYYY-MM-DD/RUN-XXXX/ + queue/ + hooks/ + reviews/
        │
        ▼
run-checks / validation / review-batch / openclaw wake
```

### Как это обычно используется

1. **Подключить проект.**
   - Новый scaffold: `python3 scripts/claw.py create-project my-project`
   - Внешний repo: `python3 scripts/claw.py import-project --slug my-project --path /abs/path/to/repo`
2. **Разложить работу.**
   - Руками через `tasks/` + `specs/`
   - Или через `python3 scripts/claw.py decompose-epic --project projects/my-project --input roadmap.md`
3. **Проверить, что оркестратор примет задачу.**
   - `python3 scripts/claw.py launch-plan projects/my-project/tasks/TASK-001.md`
   - `python3 scripts/claw.py workflow-validate projects/my-project`
   - `python3 scripts/claw.py task-graph-lint projects/my-project`
4. **Запустить исполнение.**
   - Один run: `python3 scripts/claw.py run --execute ...`
   - Через очередь: `python3 scripts/claw.py run --enqueue ...` + `python3 scripts/claw.py worker projects/my-project --once`
   - Непрерывный цикл: `python3 scripts/claw.py orchestrate projects/my-project --max-steps 3`
5. **Проверить результат и доставку.**
   - `python3 scripts/claw.py run-checks projects/my-project --type test`
   - `python3 scripts/validate_artifacts.py --project projects/my-project`
   - `python3 scripts/claw.py review-batch projects/my-project`
   - `python3 scripts/claw.py openclaw wake projects/my-project`

## Что умеет `claw`

- Task/spec workflow с canonical templates и YAML front matter
- File-backed queue с atomic transitions: `pending -> running -> done|failed|dead_letter`
- Worker loop с lease heartbeat, backoff и retry exhaustion
- `launch-plan` для dry-run routing и execution preview
- Project workflow contract в `docs/WORKFLOW.md`
- `task-snapshot`, `task-lint`, `task-graph-lint`, `workflow-graph`
- `run-checks` и registry проектных команд `test|lint|build|smoke`
- `decompose-epic` и `epic-status` для epic-driven orchestration
- OpenClaw JSON bridge: `status`, `enqueue`, `summary`, `callback`, `wake`, `replay-events`
- Review cadence и decision stubs для opposite-model review
- Formal schema validation для run artifacts и workflow contracts

## Как выбирается агент

| Claude | Codex |
|---|---|
| Декомпозиция эпиков и требований | Реализация по чёткой спецификации |
| Неоднозначные спеки, UX, architecture | Багфиксы, тесты, shell/python glue |
| Review проблемных run'ов и risky slices | Локальные кодовые изменения с понятным DoD |

Если в задаче указан `preferred_agent: auto`, выбор делает routing policy из
`_system/registry/routing_rules.yaml`.

## Run artifacts

Каждый запуск создаёт неизменяемый каталог:

```text
projects/my-project/runs/YYYY-MM-DD/RUN-0001/
├── job.json
├── meta.json
├── result.json
├── prompt.txt
├── task.md
├── spec.md
├── stdout.log
├── stderr.log
└── report.md
```

Смежное mutable state живёт рядом:

```text
projects/my-project/state/
├── queue/
├── hooks/
├── approvals/
├── metrics_snapshot.json
├── review_cadence.json
├── tasks_snapshot.json
└── workflow_graph.json
```

## OpenClaw bridge

`openclaw` нужен как внешний операторский слой поверх `claw`. Он не владеет
состоянием сам, а читает/двигает файловые артефакты оркестратора.

- `python3 scripts/claw.py openclaw status projects/my-project`
- `python3 scripts/claw.py openclaw enqueue projects/my-project/tasks/TASK-001.md`
- `python3 scripts/claw.py openclaw summary projects/my-project RUN-0001`
- `python3 scripts/claw.py openclaw wake projects/my-project`

Completion signal не завязан на prompt footer. Даже если nested agent не
отправил финальный notify, completed run остаётся видимым через `delivery`
state в `result.json` / `meta.json` и hook-файлы.

## Быстрый набор команд

```bash
# Create/import project
python3 scripts/claw.py create-project my-project
python3 scripts/claw.py import-project --slug my-project --path /abs/path/to/repo

# Plan and validate
python3 scripts/claw.py launch-plan projects/my-project/tasks/TASK-001.md
python3 scripts/claw.py workflow-validate projects/my-project
python3 scripts/claw.py task-graph-lint projects/my-project

# Execute
python3 scripts/claw.py run --execute projects/my-project/tasks/TASK-001.md
python3 scripts/claw.py run --enqueue projects/my-project/tasks/TASK-001.md
python3 scripts/claw.py worker projects/my-project --once
python3 scripts/claw.py orchestrate projects/my-project --scope epic:12 --max-steps 3

# Inspect and validate
python3 scripts/claw.py dashboard projects/my-project
python3 scripts/claw.py epic-status projects/my-project --epic 12
python3 scripts/claw.py run-checks projects/my-project --type test
python3 scripts/claw.py review-batch projects/my-project
python3 scripts/validate_artifacts.py --project projects/my-project
```

## Структура репозитория

```text
claw/
├── _system/
│   ├── registry/          # agents.yaml, routing_rules.yaml, reviewer_policy.yaml
│   ├── templates/         # task/spec/project templates
│   ├── contracts/         # JSON Schema contracts
│   └── engine/            # queue, planner, runtime, guardrails, decomposer
├── projects/
│   ├── _template/
│   └── <slug>/
│       ├── docs/
│       ├── specs/
│       ├── tasks/
│       ├── runs/
│       ├── reviews/
│       └── state/
├── scripts/
└── tests/
```

## Документация

| Doc | Зачем читать |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Слои системы, сущности, lifecycle run и queue |
| [`docs/EXECUTION_FLOW.md`](docs/EXECUTION_FLOW.md) | Командный путь `task/spec -> run -> hook -> review` |
| [`docs/contracts.md`](docs/contracts.md) | Schema contracts и validator tooling |
| [`docs/CONTRACT_VERSIONING.md`](docs/CONTRACT_VERSIONING.md) | Versioning и migration story для артефактов |
| [`docs/PARALLEL_EXECUTION.md`](docs/PARALLEL_EXECUTION.md) | Worktree isolation и правила параллельного исполнения |
| [`docs/AUTONOMY_GAPS_PLAN.md`](docs/AUTONOMY_GAPS_PLAN.md) | Исторический план закрытия autonomy gaps; читать как record, не как текущий статус |

## Требования

- Python 3.9+
- Bash
- `codex` и/или `claude` CLI в `PATH`

## License

MIT
