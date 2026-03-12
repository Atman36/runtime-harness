# Что можно взять из `symphony-main` для `claw`

Дата: 2026-03-13
Статус: локальная заметка

## Короткий вывод

`Symphony` не стоит подключать к `claw` как отдельный runtime рядом с ним. Но из архива
`/Users/Apple/Downloads/symphony-main` можно взять несколько сильных идей:

1. `WORKFLOW.md` как repo-owned workflow contract;
2. optional runner для `codex app-server`;
3. orchestrator loop с reconciliation и retry;
4. status/observability surface для live runs;
5. per-task workspace lifecycle hooks.

`claw` уже сильнее в file-backed artifacts и queue. `Symphony` полезен как донор policy/runtime идей.

## Что брать как идею

### 1. `WORKFLOW.md` как верхний policy layer

Почему полезно:

- у `claw` policy сейчас размазана между registry, task frontmatter и runtime defaults;
- `Symphony` держит workflow contract прямо в репо;
- это хороший overlay для `claw`, особенно для `openclaw`.

Где смотреть:

- `/Users/Apple/Downloads/symphony-main/SPEC.md`
- `/Users/Apple/Downloads/symphony-main/elixir/WORKFLOW.md`
- `/Users/Apple/Downloads/symphony-main/elixir/lib/symphony_elixir/workflow.ex`

Что подсмотреть:

- YAML front matter + prompt body;
- typed runtime settings поверх markdown contract;
- reload/last-known-good подход к workflow file.

Как адаптировать в `claw`:

- использовать `WORKFLOW.md` как project-level overlay;
- не заменять им `job.json`, `task/spec` и filesystem queue;
- хранить execution policy, hooks, limits, agent defaults, approval posture.

### 2. Optional `codex app-server` runner

Почему полезно:

- сейчас `claw` запускает агент как обычную CLI-команду;
- `Symphony` показывает, как жить с long-lived Codex session и turn loop;
- это пригодится, если нужен richer live runtime, а не только один subprocess per run.

Где смотреть:

- `/Users/Apple/Downloads/symphony-main/elixir/lib/symphony_elixir/agent_runner.ex`
- `/Users/Apple/Downloads/symphony-main/elixir/lib/symphony_elixir/codex/app_server.ex`
- `/Users/Apple/Downloads/symphony-main/elixir/README.md`

Что подсмотреть:

- session start/stop;
- continuation turns;
- message callback в orchestrator;
- agent max turns как явный runtime limit.

Как адаптировать в `claw`:

- не менять текущий runner;
- добавить новый backend, например `agent_runtime: codex_app_server`;
- использовать только для тех задач, где нужен streaming/live status.

### 3. Reconciliation loop поверх tracker state

Почему полезно:

- `Symphony` хорошо разделяет polling, dispatch, retry и stop-on-state-change;
- это близко к `claw openclaw wake`, но даёт идеи для более строгого reconcile слоя.

Где смотреть:

- `/Users/Apple/Downloads/symphony-main/SPEC.md`
- `/Users/Apple/Downloads/symphony-main/elixir/lib/symphony_elixir/orchestrator.ex`

Что подсмотреть:

- claimed/running/retry state как единая runtime-модель;
- exponential backoff;
- stop active run when issue no longer eligible;
- bounded concurrency per orchestrator.

Как адаптировать в `claw`:

- не уводить state в память как source of truth;
- брать только decision logic;
- authoritative state оставить в filesystem artifacts и queue files.

### 4. Live status surface

Почему полезно:

- `claw` уже умеет status snapshots;
- `Symphony` даёт хорошую идею operator-facing live dashboard/API.

Где смотреть:

- `/Users/Apple/Downloads/symphony-main/elixir/lib/symphony_elixir/status_dashboard.ex`
- `/Users/Apple/Downloads/symphony-main/elixir/lib/symphony_elixir_web/controllers/observability_api_controller.ex`
- `/Users/Apple/Downloads/symphony-main/elixir/lib/symphony_elixir_web/live/dashboard_live.ex`

Что подсмотреть:

- human-readable runtime summary;
- отдельный JSON state endpoint;
- live token/run telemetry.

Как адаптировать в `claw`:

- строить это поверх текущих `state/*.json`, queue и run artifacts;
- не тянуть Phoenix/Elixir;
- максимум сделать `openclaw status --live` или простой SSE/JSON feed.

### 5. Workspace lifecycle hooks

Почему полезно:

- у `Symphony` workspace живёт как first-class runtime entity;
- hooks на create/run/remove удобно ложатся на `claw` workspace modes.

Где смотреть:

- `/Users/Apple/Downloads/symphony-main/elixir/lib/symphony_elixir/workspace.ex`
- `/Users/Apple/Downloads/symphony-main/elixir/WORKFLOW.md`

Что подсмотреть:

- `after_create`, `before_run`, `after_run`, `before_remove`;
- safe path handling;
- deterministic workspace naming per issue/task.

Как адаптировать в `claw`:

- ввести project-level workspace hooks только если реально нужны;
- привязать их к `shared_project`, `git_worktree`, `isolated_checkout`;
- результат hook execution писать в run artifacts.

## Что не брать

- Linear-centric orchestration как основу `claw`;
- in-memory orchestrator state как единственный runtime source;
- целый Elixir service / Phoenix dashboard;
- ticket workflow semantics (`Todo`, `Human Review`, `Merging`) один в один;
- чужие repo skills и PR process как обязательную модель.

Это уже отдельный продуктовый слой, а не engine `claw`.

## Практический порядок внедрения

Если использовать идеи `Symphony` без лишнего scope, порядок нормальный такой:

1. закончить/утвердить `WORKFLOW.md` contract в `claw`;
2. добавить typed workflow loader + validation;
3. потом опционально добавить `codex app-server` runner;
4. только после этого думать про live status surface.
