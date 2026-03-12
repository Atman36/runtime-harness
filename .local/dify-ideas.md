# Что реально можно взять из `dify-main` для `claw`

Дата: 2026-03-13
Статус: локальная заметка

## Короткий вывод

Из `Dify` не стоит тащить код или архитектуру целиком. Для `claw` полезны только отдельные идеи:

1. формализованный workflow graph как переносимый артефакт;
2. typed contracts для внешних trigger/run событий;
3. snapshot + replay для live event stream;
4. аккуратная модель tool/MCP provider без жёсткой привязки к UI.

Важно: подсматривать можно, копировать код не стоит. У `Dify` модифицированная лицензия, не чистый Apache.

## Что брать как идею

### 1. Workflow graph как файл-артефакт

Почему полезно:

- `claw` уже живёт на файловых артефактах;
- можно добавить переносимый graph contract для более сложных execution plans;
- это даст основу для будущего `plan -> graph -> run artifacts`, не ломая текущие `task/spec/job`.

Где смотреть:

- `/Users/Apple/Downloads/dify-main/api/tests/fixtures/workflow/basic_chatflow.yml`
- `/Users/Apple/Downloads/dify-main/api/tests/fixtures/workflow/http_request_with_json_tool_workflow.yml`
- `/Users/Apple/Downloads/dify-main/api/tests/fixtures/workflow/conditional_parallel_code_execution_workflow.yml`
- `/Users/Apple/Downloads/dify-main/api/services/workflow/workflow_converter.py`

Что именно подсмотреть:

- формат `nodes + edges`;
- явные `node.type`;
- связи через `source/sourceHandle/target/targetHandle`;
- декларативные `outputs` у terminal node;
- тестовые fixtures как golden corpus.

Как адаптировать в `claw`:

- не делать canvas-first систему;
- не повторять их app/workspace/UI слой;
- если вводить graph, то только как execution artifact или contract рядом с `job.json`.

### 2. Typed trigger/run contracts

Почему полезно:

- у `claw` уже есть `openclaw` JSON surface;
- типизированные payload-модели упростят webhook/schedule/callback/retry paths;
- меньше неявных словарей, проще валидация и reason codes.

Где смотреть:

- `/Users/Apple/Downloads/dify-main/api/services/workflow/entities.py`

Что именно подсмотреть:

- разделение `TriggerData`, `ScheduleTriggerData`, `WebhookTriggerData`, `WorkflowResumeTaskData`;
- лёгкие payload-модели для очереди;
- явный `status/result/error`.

Как адаптировать в `claw`:

- расширять текущие file-backed contracts;
- не привязывать модель к Celery, tenant_id и billing;
- держать минимальный набор полей под `openclaw enqueue/callback/wake`.

### 3. Snapshot + replay для event stream

Почему полезно:

- это лучший кусок Dify для будущего live status в `openclaw`;
- при переподключении можно сначала отдать snapshot run state, потом перейти в live stream;
- это хорошо ложится на текущий filesystem-first подход.

Где смотреть:

- `/Users/Apple/Downloads/dify-main/api/services/workflow_event_snapshot_service.py`

Что именно подсмотреть:

- сначала snapshot известных событий, потом buffered live events;
- `ping` и `idle timeout`;
- resume без потери контекста;
- terminal event handling.

Как адаптировать в `claw`:

- строить snapshot из `meta.json`, `result.json`, queue item и hook state;
- live feed можно делать поверх append-only run events file;
- не тянуть их DB repository layer.

### 4. Tool/MCP provider model

Почему полезно:

- для `claw` может появиться слой внешних tools/MCP endpoints;
- в Dify аккуратно разведены provider record, credentials, discovery и runtime metadata.

Где смотреть:

- `/Users/Apple/Downloads/dify-main/api/models/tools.py`
- `/Users/Apple/Downloads/dify-main/api/services/tools/mcp_tools_manage_service.py`

Что именно подсмотреть:

- уникальность через hash endpoint;
- encrypted credentials/headers;
- отдельное хранение discovered tools;
- reconnect/revalidate flow при изменении URL.

Как адаптировать в `claw`:

- хранить provider state в файлах, не в SQLAlchemy;
- вводить только если реально нужен shared tool registry;
- начать с простого `state/tool_providers/*.json`.

## Что использовать только как reference corpus

- fixtures из `api/tests/fixtures/workflow/` как примеры branching, tool-call и terminal outputs;
- naming node types;
- минимальные идеи для schema validation будущего workflow contract.

Это полезно именно как набор примеров для тестов, а не как спецификация один в один.

## Что не брать

- multi-tenant/billing/plan queues;
- app builder/UI canvas;
- RAG platform и model-provider ORM;
- их database-centric runtime;
- код frontend и console UX;
- plugin migration и enterprise-обвязку.

Для `claw` это лишний вес и чужой центр архитектуры.

## Практический next step для `claw`

Если использовать идеи Dify без расползания scope, то нормальный порядок такой:

1. взять workflow fixtures как reference tests;
2. добавить отдельный typed contract для external triggers;
3. позже добавить snapshot/replay feed для `openclaw`;
4. MCP/tool registry трогать только когда появится реальная потребность.
