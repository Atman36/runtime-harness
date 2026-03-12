# EXECUTION FLOW — run, queue, hooks, reconcile

Дата: 2026-03-12
Статус: working / current behavior after engine + review slice

## Зачем нужен этот документ

`claw` уже умеет:
- собирать `task/spec -> job`
- показывать dry-run execution decision через `claw launch-plan`
- запускать агента напрямую
- ставить run в filesystem queue
- забирать queued job worker'ом
- сохранять `result/report/logs`
- валидировать `job/result/meta` после записи артефактов
- создавать completion hook на диске
- доставлять hook сразу или повторять через reconcile
- автоматически запускать review batch generation по cadence и immediate triggers

Этот файл фиксирует текущий контракт, чтобы последующие изменения не возвращали систему в ad-hoc shell.

---

## End-to-end lifecycle

### Прямой запуск
```text
TASK.md + SPEC.md
  -> scripts/run_task.sh --execute
  -> planner routing/execution persisted into job/meta
  -> projects/<slug>/runs/YYYY-MM-DD/RUN-XXXX/
      - task.md
      - spec.md
      - prompt.txt
      - meta.json
      - job.json
      - result.json
      - report.md
      - stdout.log
      - stderr.log
  -> scripts/execute_job.py
  -> selected agent CLI
  -> result/report/logs updated
  -> artifact validation embedded into result/meta
  -> state/hooks/pending/<hook>.json
  -> immediate dispatch attempt
  -> state/hooks/sent/ or state/hooks/failed/
```

### Queue-based запуск
```text
TASK.md + SPEC.md
  -> scripts/claw.py enqueue
  -> planner routing/execution persisted into job/meta
  -> projects/<slug>/runs/YYYY-MM-DD/RUN-XXXX/
  -> projects/<slug>/state/queue/pending/RUN-XXXX.json
  -> scripts/claw.py worker <project-root> --once
  -> state/queue/running/ -> done/failed/
  -> scripts/execute_job.py
  -> hooks pending/sent/failed
  -> state/review_cadence.json updated
  -> automatic review batch generation when trigger fires
```

---

## Основные команды

Посмотреть execution decision до запуска:

```bash
python3 scripts/claw.py launch-plan projects/demo-project/tasks/TASK-001.md
```

Создать run artifacts без исполнения:

```bash
bash scripts/run_task.sh projects/demo-project/tasks/TASK-001.md
```

Создать run и исполнить сразу:

```bash
bash scripts/run_task.sh --execute projects/demo-project/tasks/TASK-001.md
```

Создать run и поставить его в queue:

```bash
python3 scripts/claw.py enqueue projects/demo-project/tasks/TASK-001.md
python3 scripts/claw.py enqueue --awaiting-approval projects/demo-project/tasks/TASK-001.md
```

Создать run через unified CLI:

```bash
python3 scripts/claw.py run projects/demo-project/tasks/TASK-001.md
python3 scripts/claw.py run --execute projects/demo-project/tasks/TASK-001.md
python3 scripts/claw.py run --enqueue projects/demo-project/tasks/TASK-001.md
python3 scripts/claw.py run --enqueue --awaiting-approval projects/demo-project/tasks/TASK-001.md
```

Исполнить queued job:

```bash
python3 scripts/claw.py worker projects/demo-project --once
python3 scripts/claw.py worker projects/demo-project --once --stale-after-seconds 900
python3 scripts/claw.py worker projects/demo-project --once --skip-review
```

Подтвердить job, ожидающий approval:

```bash
python3 scripts/claw.py approve projects/demo-project RUN-0001
```

Вернуть stale `running` jobs обратно в `pending`:

```bash
python3 scripts/claw.py reclaim projects/demo-project --stale-after-seconds 900
```

Проверить run artifacts по formal schema:

```bash
python3 scripts/validate_artifacts.py projects/demo-project/runs/2026-03-12/RUN-0001
python3 scripts/validate_artifacts.py --project projects/demo-project
```

Сформировать review batch:

```bash
python3 scripts/generate_review_batch.py projects/demo-project
python3 scripts/generate_review_batch.py --dry-run projects/demo-project
```

Показать статус run:

```bash
python3 scripts/claw.py status projects/demo-project RUN-0001
```

Доставить pending hooks:

```bash
CLAW_HOOK_COMMAND='cat >/dev/null' python3 scripts/claw.py dispatch projects/demo-project
```

Повторить failed hooks и stale pending hooks:

```bash
CLAW_HOOK_COMMAND='cat >/dev/null' python3 scripts/claw.py reconcile projects/demo-project
```

---

## Agent invocation model

Default agent invocation задаётся в:

```text
_system/registry/agents.yaml
```

Поддерживаемые поля на агента:
- `command`
- `args`
- `prompt_mode`
- `cwd`
- `default_timeout_seconds`

Пример:

```yaml
agents:
  codex:
    command: codex
    args: exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -C {project_root}
    prompt_mode: arg
    cwd: project_root
    default_timeout_seconds: 3600
```

Поддерживаемые шаблонные переменные в `args`:
- `{project_root}`
- `{source_project_root}`
- `{workspace_root}`
- `{run_dir}`

Зачем это нужно:
- CLI-флаги Codex/Claude не размазываются по нескольким скриптам
- smoke tests могут подменять transport через env
- runtime-конфигурация хранится как filesystem registry

---

## Runtime overrides

### Agent execution
- `CLAW_AGENT_COMMAND`
- `CLAW_AGENT_COMMAND_CODEX`
- `CLAW_AGENT_COMMAND_CLAUDE`
- `CLAW_AGENT_TIMEOUT_SECONDS`

Use case:
- smoke tests
- локальные заглушки
- временная подмена транспорта/CLI

### Hook delivery
- `CLAW_HOOK_COMMAND`
- `CLAW_HOOK_TIMEOUT_SECONDS`
- `CLAW_HOOK_STALE_SECONDS`

### Queue execution
Специальных queue-env пока нет.
Сейчас queue worker использует тот же `execute_job.py`, поэтому наследует agent-related env overrides полностью.

### Review execution
Worker после завершения run:
- читает `result.json`
- обновляет cadence counter
- вызывает review batch generation при `failed`, `needs_review`, `risky_area`, `uncertainty`, `large_diff`
- вызывает cadence batch после каждых 5 успешных run

---

## Run contract

### Run directory
Каждый запуск живёт в:

```text
projects/<slug>/runs/YYYY-MM-DD/RUN-XXXX/
```

### Важные поля
- `meta.json`
  - текущий статус исполнения
  - persisted `routing` / `execution` summary
  - executor metadata
  - validation snapshot
  - hook snapshot
- `job.json`
  - описание запуска
  - persisted `routing` / `execution` contract
  - `run_path` для связки с queue item
- `result.json`
  - итоговый машиночитаемый результат
  - validation snapshot with `valid/errors`

### Дополнительная валидация
`run_task.sh` теперь:
- читает `project.slug` из `state/project.yaml`
- валидирует, что он совпадает с именем каталога проекта

Это уменьшает риск quietly запускать task в криво переименованной структуре.

### Post-artifact validation
`execute_job.py` после записи `stdout/stderr/report` валидирует:
- `job.json`
- `result.json`
- `meta.json`

И пишет снимок валидации в:
- `result.json.validation`
- `meta.json.validation`

---

## Queue storage model

Каждый project теперь имеет:

```text
projects/<slug>/state/queue/
├── pending/
├── running/
├── done/
├── failed/
└── awaiting_approval/
```

Дополнительно project-level review cadence хранится в:

```text
projects/<slug>/state/review_cadence.json
```

### Queue item
Минимальный queue payload содержит:
- `job_id`
- `run_id`
- `run_path`
- `project`
- `preferred_agent`
- `review_policy`
- `created_at`
- `task.id`
- `task.title`
- `task.priority`

### Состояния
- `pending`
  - job создан и ждёт claim
- `running`
  - worker атомарно забрал job
- `done`
  - `execute_job.py` завершился с `0`
- `failed`
  - `execute_job.py` завершился non-zero
- `awaiting_approval`
  - job поставлен в queue, но не может быть claimed worker'ом до явного `approve`

### Поведение
- queue — project-scoped, не глобальная
- переходы между состояниями атомарны через `os.replace`
- duplicate `job_id` в queue не допускается
- worker ищет run через `run_path`, а не через эвристики по stdout
- stale `running` items можно явно вернуть в `pending` через `claw reclaim`
- `claw worker` умеет сначала reclaim'ить stale `running` items, если передан `--stale-after-seconds`

---

## Hook storage model

Each project has:

```text
projects/<slug>/state/hooks/
├── pending/
├── sent/
└── failed/
```

### Semantics
- `pending`
  - hook создан, но не доставлен
  - либо `CLAW_HOOK_COMMAND` не задан
- `sent`
  - команда доставки завершилась `0`
- `failed`
  - была попытка доставки, но команда завершилась non-zero или timeout

### Important behavior
- filesystem remains source of truth
- для одного `hook_id` в каждый момент времени существует один актуальный файл
- переход между статусами делается через temp file + `os.replace`
- если запись нового статуса падает, старый hook-файл не должен исчезнуть

---

## Dispatch vs reconcile

### `dispatch`
Назначение:
- пробует доставить все `pending` hooks

Поведение:
- если `CLAW_HOOK_COMMAND` не задан, hooks остаются `pending`
- такой запуск считается `skipped`, а не `failed`
- exit code остаётся `0`, если не было реальных failed deliveries

### `reconcile`
Назначение:
- повторяет `failed`
- повторяет `pending`, которые считаются stale

Поведение:
- stale определяется через `CLAW_HOOK_STALE_SECONDS`
- отсутствие hook command не должно разрушать состояние

---

## Smoke coverage

`tests/run_all.sh` сейчас покрывает:
- foundation scaffold
- task -> job artifacts
- execution success path
- execution failure path
- post-artifact validation embedding
- registry-driven agent invocation
- hook immediate success
- hook pending when no command is configured
- manual dispatch from pending
- failed delivery + reconcile retry
- stale pending retry
- hook timeout -> failed
- atomic-safe hook rewrite behavior
- queue enqueue -> worker -> status flow
- automatic review cadence + immediate review trigger flow

---

## Что ещё не реализовано

Пока не сделано:
- OpenClaw wake/system-event bridge
- multi-project worker / scheduler

Текущее состояние стоит воспринимать как:
- уже рабочий локальный orchestration foundation
- уже не просто shell scripts
- уже с dry-run planner preview перед запуском
- ещё не полный orchestration engine v1
