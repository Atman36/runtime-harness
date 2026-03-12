# EXECUTION FLOW — agent run, delivery, reconcile

Дата: 2026-03-12
Статус: working / current behavior

## Зачем нужен этот документ

`claw` уже умеет не только собирать `task/spec -> job`, но и:
- запускать реального агента по `job.json`
- сохранять финальные run artifacts
- создавать completion hook на диске
- доставлять hook сразу или повторять через reconcile

Этот файл фиксирует текущий контракт, чтобы следующие изменения не расползались обратно в ad-hoc shell.

---

## End-to-end lifecycle

```text
TASK.md + SPEC.md
  -> scripts/run_task.sh
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
  -> scripts/execute_job.sh
  -> selected agent CLI
  -> result/report/logs updated
  -> state/hooks/pending/<hook>.json
  -> immediate dispatch attempt
  -> state/hooks/sent/ or state/hooks/failed/
```

---

## Main commands

Create run artifacts only:

```bash
bash scripts/run_task.sh projects/demo-project/tasks/TASK-001.md
```

Create and execute immediately:

```bash
bash scripts/run_task.sh --execute projects/demo-project/tasks/TASK-001.md
```

Execute an existing run:

```bash
bash scripts/execute_job.sh projects/demo-project/runs/YYYY-MM-DD/RUN-0001
```

Dispatch pending hooks:

```bash
CLAW_HOOK_COMMAND='cat >/dev/null' python3 scripts/dispatch_hooks.py projects/demo-project
```

Retry failed hooks and stale pending hooks:

```bash
CLAW_HOOK_COMMAND='cat >/dev/null' python3 scripts/reconcile_hooks.py projects/demo-project
```

---

## Agent invocation model

Default agent invocation is configured in:

```text
_system/registry/agents.yaml
```

Current supported fields per agent:

- `command` — executable name
- `args` — command arguments template
- `prompt_mode` — `arg` or `stdin`
- `cwd` — `project_root` or `run_dir`
- `default_timeout_seconds` — execution timeout

Example shape:

```yaml
agents:
  codex:
    command: codex
    args: exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -C {project_root}
    prompt_mode: arg
    cwd: project_root
    default_timeout_seconds: 3600
```

Template variables currently supported in `args`:
- `{project_root}`
- `{run_dir}`

### Why this matters

Это убирает CLI-флаги Codex/Claude из тела `execute_job.py`.
Следующая правка режима запуска должна происходить в filesystem registry, а не вшиваться заново в код.

---

## Runtime overrides

### Agent execution

- `CLAW_AGENT_COMMAND`
  - override для любого агента
- `CLAW_AGENT_COMMAND_CODEX`
  - override только для Codex
- `CLAW_AGENT_COMMAND_CLAUDE`
  - override только для Claude
- `CLAW_AGENT_TIMEOUT_SECONDS`
  - override timeout поверх registry

Use case:
- smoke tests
- локальные заглушки
- временная подмена транспорта/CLI

### Hook delivery

- `CLAW_HOOK_COMMAND`
  - команда доставки hook payload
  - получает hook JSON через `stdin`
  - должна вернуть `0` при успехе
- `CLAW_HOOK_TIMEOUT_SECONDS`
  - timeout доставки hook-команды
  - timeout считается failed delivery с `exit_code: 124`
- `CLAW_HOOK_STALE_SECONDS`
  - порог stale для `reconcile_hooks.py`

---

## Hook storage model

Each project now has:

```text
projects/<slug>/state/hooks/
├── pending/
├── sent/
└── failed/
```

### Status semantics

- `pending`
  - hook создан, но ещё не доставлен
  - либо `CLAW_HOOK_COMMAND` не задан
- `sent`
  - команда доставки завершилась с exit code `0`
- `failed`
  - была попытка доставки, но команда завершилась non-zero или timeout

### Important behavior

- filesystem remains source of truth
- для одного `hook_id` в каждый момент времени существует один актуальный файл
- переход между статусами делается atomic-safe через temp file + `os.replace`
- если запись нового статуса падает, старый hook-файл не должен исчезнуть

---

## Hook payload expectations

Minimal payload contains:

- `hook_version`
- `hook_id`
- `event` (`run.completed`)
- `project`
- `run_id`
- `run_date`
- `task_id`
- `preferred_agent`
- `run_status`
- `summary`
- `artifacts.*`
- `delivery.*`
- `delivery_attempts[]`

Hook id format:

```text
YYYY-MM-DD--RUN-XXXX
```

Это упрощает deduplication и ручной аудит.

---

## Dispatch vs reconcile

### `dispatch_hooks.py`

Назначение:
- пробует доставить все `pending` hooks

Поведение:
- если `CLAW_HOOK_COMMAND` не задан, hooks остаются `pending`
- такой запуск считается `skipped`, не `failed`
- exit code команды остаётся `0`, если не было реальных failed deliveries

### `reconcile_hooks.py`

Назначение:
- повторяет `failed`
- повторяет `pending`, которые считаются stale

Поведение:
- stale определяется через `CLAW_HOOK_STALE_SECONDS`
- если hook command отсутствует, reconcile не должен разрушать состояние и не должен маскироваться под успешную доставку

---

## Current rules of thumb for Codex / Claude

Из практики текущей сессии:

### Codex
- лучше для implementation / shell glue / test-driven slices
- нужен более жёсткий non-interactive режим
- настройки запуска лучше держать в `agents.yaml`, а не размазывать по коду

### Claude
- полезнее как reviewer / architecture critic / risk finder
- лучше запускать через print-mode и просить структурированный output
- если использовать в фоне, ему особенно полезен явный callback footer

### Practical recommendation

Следующий шаг для orchestration layer:
- в generated prompt добавлять callback footer для OpenClaw system event
- совмещать:
  - file-backed hook на диске
  - chat/system-event wake как оперативный completion signal

---

## What is already covered by smoke tests

`tests/run_all.sh` now covers:
- foundation scaffold
- task -> job artifacts
- execution success path
- execution failure path
- registry-driven agent invocation
- hook immediate success
- hook pending when no command is configured
- manual dispatch from pending
- failed delivery + reconcile retry
- stale pending retry
- hook timeout -> failed
- atomic-safe hook rewrite behavior

---

## What is still not implemented

Not done yet:
- external webhook transport with schema/versioning guarantees
- OpenClaw wake/system-event bridge from run completion
- review cadence and opposite-model reviewer loop
- queue-based worker pool
- retry policy beyond simple reconcile re-run

So current slice should be treated as:
- deterministic local orchestration foundation
- not yet a full engine
