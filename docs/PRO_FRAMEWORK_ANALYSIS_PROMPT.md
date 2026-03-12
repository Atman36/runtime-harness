# PRO Prompt — framework analysis and project hardening

Используй этот prompt в PRO-модели, когда нужен полный аудит и доработка `claw` как AI orchestration framework.

## Prompt

```text
Выступи в роли senior-level Environment AI Engineering / Harness Engineering for LMM / LLM / Agent Runtime Architecture.

Ты анализируешь локальный framework-проект, который управляет задачами для Codex и Claude через filesystem-first orchestration runtime.

Контекст проекта:
- Это локальный orchestration framework / workspace для запуска агентных задач.
- В проекте уже есть project shell, task/spec workflow, run artifacts, file-backed queue, worker loop, hooks, reconcile, schema validation и review cadence.
- Codex и Claude используются как исполняющие агенты и reviewer-модели.
- Источник истины: filesystem, а не база данных.
- Основная цель: сделать framework надёжным, масштабируемым, предсказуемым и удобным для дальнейшей автоматизации из чата.

Твоя задача:
1. Сделать полный архитектурный и продуктовый анализ проекта.
2. Найти слабые места framework-level дизайна.
3. Предложить доработки и, где уместно, сразу реализовать их.
4. Довести проект до более зрелого состояния именно как runtime/framework, а не как разовый pet-script.
5. Подготовить итог так, чтобы было понятно, как дальше использовать framework для запуска задач Codex и Claude.

Работай как инженер фреймворков, а не как общий ассистент.
Смотри на проект через призму:
- environment architecture
- harness/runtime design
- agent execution contracts
- orchestration reliability
- artifact contracts
- review and quality loops
- operability and debuggability
- DX для пользователя framework
- расширяемость под новые агенты и новые execution backends

Что нужно проанализировать:

1. Product framing
- Насколько у проекта понятная продуктовая граница
- Что именно является core abstraction: project shell, run, queue item, review batch, hook event, orchestration session
- Где сейчас смешаны product-level и implementation-level ответственности

2. Runtime architecture
- Насколько чисто разделены task preparation, execution, queueing, hook dispatch, reconcile, review
- Есть ли скрытая связность между скриптами
- Какие узкие места помешают росту проекта
- Где lifecycle разорван или недостаточно формализован

3. Contracts and state model
- Достаточно ли формализованы job/result/meta/hook/review artifacts
- Какие поля отсутствуют для надёжной эксплуатации
- Где есть риск несовместимости версий
- Нужны ли versioning/migration rules

4. Queue and worker design
- Насколько надёжна filesystem queue
- Какие race conditions, failure windows и recovery gaps ещё возможны
- Что стоит улучшить в claim/ack/fail/reclaim semantics
- Нужны ли richer states, poison-job handling, dead-letter pattern, lease renewal, retry policy

5. Agent execution layer
- Насколько хорошо абстрагированы Codex и Claude
- Что нужно, чтобы добавить третьего агента или другой transport/backend
- Достаточно ли чисто оформлены prompt transport, cwd policy, timeout policy, executor metadata

6. Hook and event delivery
- Насколько текущий hook lifecycle пригоден как integration surface
- Какие гарантии доставки реально есть
- Что нужно для более сильного delivery contract
- Как лучше строить event model для будущего OpenClaw/chat bridge

7. Review system
- Насколько хорош текущий cadence/immediate review design
- Что ещё нужно для reviewer orchestration
- Как лучше хранить reviewer decisions, findings, approvals, waivers и follow-up actions

8. Developer experience
- Насколько CLI и docs понятны
- Какие команды или сценарии отсутствуют
- Что мешает локальному онбордингу и быстрой эксплуатации

9. Testing strategy
- Что покрыто хорошо
- Какие тесты отсутствуют
- Где нужны stress tests, golden tests, failure injection, concurrency tests, compatibility tests

10. Roadmap quality
- Насколько реалистичен текущий план
- Какие этапы надо пересобрать
- Что стоит делать следующим приоритетом

Требования к работе:
- Не ограничивайся общими советами.
- Опирайся на реальные файлы и текущую структуру проекта.
- Если видишь архитектурный запах, укажи конкретное место и причину.
- Если предлагаешь изменение, объясни tradeoff.
- Если можешь безопасно улучшить проект прямо сейчас, сделай это.
- Не ломай существующие рабочие сценарии без необходимости.
- Сохраняй filesystem-first модель.
- Избегай абстрактных рассуждений без привязки к коду и артефактам.

Формат ответа:

Сначала дай structured review:
1. Executive summary
2. Что уже сделано хорошо
3. Основные архитектурные проблемы
4. Риски эксплуатации
5. Предлагаемый target architecture
6. Приоритетный roadmap

Потом перейди к практической части:
7. Какие изменения ты внёс в проект
8. Какие файлы изменил
9. Какие тесты добавил или обновил
10. Что осталось сделать позже

Требования к deliverables:
- Обнови проект прямо в рабочей директории, если это уместно.
- Если вносишь изменения, обнови документацию и план.
- Подготовь итоговый архив обновлённого проекта.
- Назови архив предсказуемо, например `claw-updated-<date>.tar.gz`.
- В чате кратко перечисли, что именно поменялось.

Если каких-то данных не хватает:
- сначала исследуй репозиторий
- потом делай разумные выводы
- и явно помечай assumptions

Критерий качества:
После твоей работы проект должен выглядеть как более зрелый framework для orchestration Codex/Claude runs, а не как набор разрозненных shell/python скриптов.
```

## Что ожидать от ответа модели

- глубокий framework-level аудит, а не косметический обзор
- приоритизацию по риску и value
- предложения по runtime contracts, operability и extensibility
- конкретные кодовые изменения и обновление docs
- итоговый summary изменений для чата
