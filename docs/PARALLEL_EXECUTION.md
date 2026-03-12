# Parallel Execution Guide — claw

Date: 2026-03-13
Status: authoritative / operational guide for multi-agent runs in separate worktrees

---

## Зачем это нужно

Параллельный запуск агентов в `claw` полезен только тогда, когда он:
- ускоряет delivery,
- не ломает source of truth,
- не создаёт merge-шум больше, чем экономит времени.

Практический вывод после нескольких dual-agent сессий: **параллелить нужно не агентов вообще, а независимые slices работы**.

Надёжный базовый паттерн:
- **Codex** → implementation / runtime / tests
- **Claude** → review / architecture / docs / design decisions

---

## Когда параллелить безопасно

Параллельный запуск оправдан, если одновременно выполняются все условия:

1. **Файлы почти не пересекаются**
   - пример: один агент меняет `scripts/` и `_system/engine/`, другой — `docs/` и `README.md`
2. **Есть явная граница ответственности**
   - implementation slice
   - docs/architecture slice
   - review slice
3. **Каждый агент работает в отдельном git worktree**
4. **Оркестратор делает selective merge** вместо слепого cherry-pick всего подряд
5. **После интеграции прогоняется полный suite** (`bash tests/run_all.sh`)

Если хотя бы одно условие не выполняется — лучше serial execution, а не красивая, но бесполезная многорукая шизофрения.

---

## Основной паттерн

### 1. Нарезка задачи

Режь работу на узкие slices:

| Slice | Типичный владелец | Примеры |
|------|--------------------|---------|
| Runtime / implementation | Codex | `scripts/`, `_system/engine/`, tests |
| Docs / architecture | Claude | `docs/`, `README.md`, roadmap alignment |
| Review / critique | Claude | findings, risks, design critique |
| Focused bugfix | Codex | regression fix, shell/python glue |

### 2. Изоляция

Для каждого агента:
- отдельная ветка,
- отдельный `git worktree`,
- отдельный prompt,
- отдельный edit scope.

### 3. Интеграция

Оркестратор:
1. читает реальный diff каждого агента,
2. cherry-pick / копирует только нужные изменения,
3. вручную проверяет planning docs,
4. прогоняет тесты,
5. только потом коммитит результат в main branch.

---

## Worktree discipline

### Обязательное правило

**Агенту нужно передавать путь к worktree, а не путь к main repo.**

Иначе ты как бы сделал изоляцию, но сам же её и убил: агент увидит абсолютный путь до основного дерева и начнёт писать туда напрямую.

### Хорошо

- prompt содержит путь текущего worktree
- `cwd` агента = worktree
- относительные пути резолвятся внутри worktree

### Плохо

- prompt содержит `/Users/Apple/progect/claw`
- агенту разрешено "сходить посмотреть main repo"
- orchestration prompt смешивает worktree path и main repo path

---

## Edit scope discipline

Даже при worktree isolation лучше явно задавать edit scope.

### Хорошие пары slices

- `scripts/` + `_system/engine/` **vs** `docs/` + `README.md`
- `tests/` **vs** `docs/`
- `contracts/` **vs** `project docs`

### Плохие пары slices

- два агента оба пишут `docs/PLAN.md`
- два агента оба трогают `scripts/claw.py`
- один агент меняет schema, другой параллельно меняет consumer того же schema без общей координации

Если файл конфликтно-центральный (`PLAN.md`, `STATUS.md`, `BACKLOG.md`, `scripts/claw.py`) — считай его merge-sensitive.

---

## Concurrency groups

Для зрелой orchestration policy полезно мыслить задачами как concurrency groups.

Примеры групп:
- `docs`
- `runtime-worker`
- `contracts`
- `hooks`
- `review-system`
- `project:<slug>`

Принцип простой:
- внутри одной concurrency group — serial,
- между независимыми группами — parallel.

Это пока ещё не fully automated policy в runtime, но как operational rule уже работает.

---

## Merge discipline

### Самое опасное место: planning docs

`docs/PLAN.md`, `docs/BACKLOG.md`, `docs/STATUS.md` живут быстрее, чем ветка отдельного агента.

Поэтому для planning docs действует отдельное правило:

> **Не делать blind cherry-pick planning-изменений из agent branch.**

Нужно:
1. сравнить diff агента с live `master`,
2. понять, какие пункты уже изменились после запуска агента,
3. вручную перенести только актуальные строки,
4. убедиться, что новые пункты roadmap не затёрлись.

### Почему

Потому что summary агента может быть правдивым локально, но уже устаревшим относительно репозитория к моменту merge.

---

## Completion summary ≠ source of truth

Completion message агента полезен как сигнал, но не как merge criterion.

Перед интеграцией оркестратор обязан смотреть:
- `git show --stat <commit>`
- полный diff по ключевым файлам
- статус дерева
- результаты тестов

Правило:
- **summary говорит, что агент думает, что сделал**
- **diff показывает, что реально изменилось**

---

## Validation sequence

После сведения параллельных slices:

```bash
bash tests/run_all.sh
```

Если изменялись только docs и ты хочешь быстрый sanity check, минимум:

```bash
bash tests/docs_tracking_test.sh
```

Но для merge результата после двух агентов нормой должен быть именно полный suite.

---

## Антипаттерны

### 1. Parallel ради parallel

Если два агента оба ковыряют один и тот же участок runtime — ты не ускоряешься, ты покупаешь merge-ад.

### 2. Worktree без реальной изоляции

Сделать `git worktree add`, а потом в prompt дать путь к main repo — это театральная постановка, не isolation.

### 3. Blind merge docs

Planning docs нельзя тянуть без сверки с актуальным состоянием ветки.

### 4. Нет финальной валидации

Если после двух агентов не прогоняется `run_all.sh`, значит регрессия уже где-то рядом, просто ещё не постучалась.

---

## Что нужно для непрерывающегося цикла

Чтобы система могла крутить цикл без ручной паузы:

```text
select next task
  -> implement
  -> validate
  -> review
  -> decide (accept / retry / ask_human)
  -> enqueue next task
  -> repeat
```

нужны ещё следующие слои.

### 1. Task selector / scheduler

Нужен компонент, который умеет выбирать следующую задачу не по вдохновению, а по правилам:
- priority
- dependencies
- blocked / ready state
- concurrency group
- project scope
- retry cooldown / failure budget

### 2. Review gate как first-class lifecycle step

Сейчас review уже умеет генерироваться, но для непрерывного цикла нужен именно gate:
- run completed
- reviewer agent стартует автоматически
- review decision сохраняется как артефакт
- система решает: `accept`, `retry`, `follow_up`, `ask_human`

### 3. Decision engine

Нужна политика, которая по результатам implementation + validation + review решает, что делать дальше:
- принять run и двигаться к следующей задаче
- создать follow-up task
- отправить тот же task на retry
- эскалировать человеку
- остановить цикл при risk threshold

### 4. Queue chaining / next-task trigger

После закрытия одного run система должна уметь сама поставить следующий:
- из ready queue
- из follow-up queue
- из review-created tasks

### 5. Failure budget и стоп-условия

Непрерывный цикл без стоп-условий — путь к автоматизированной глупости.

Нужны правила остановки:
- N failed runs подряд
- repeated review rejection
- risky diff / dangerous area
- missing dependency
- approval required
- unknown schema / invalid artifact

### 6. Scheduler visibility

Нужен status view, который показывает не только один run, а весь orchestration loop:
- current task
- next ready tasks
- pending reviews
- retries waiting on backoff
- blocked tasks
- ask_human queue

### 7. Event bridge

Нужен устойчивый bridge:
- run finished → review starts
- review finished → next task selected
- failure → ask_human / stop
- important milestone → callback in chat

### 8. Trusted execution boundary

Перед настоящим autonomous loop нужно закрыть hardening gaps:
- shell override trust boundary (`CLAW_HOOK_COMMAND`, `CLAW_AGENT_COMMAND*`)
- deterministic worktree materialization
- safe JSON reads / runtime robustness
- reviewer registry validation

Иначе получится не автономный цикл, а автономный способ сломать себе день.

---

## Практический roadmap к такому циклу

Минимальный разумный порядок:

1. **9.6** — stress / failure-injection tests
2. **9.7–9.9** — hardening и cleanup runtime edge cases
3. **8.2** — richer status view
4. **8.1** — multi-project / ready-task scheduler
5. **8.3** — approval UX / ask_human flow
6. **новый orchestration step**: auto-review gate
7. **новый orchestration step**: next-task selector + queue chaining

То есть сначала сделать систему устойчивой, а уже потом пытаться превращать её в вечный двигатель.

---

## Короткий operational checklist

Перед dual-agent run:
- [ ] slices не пересекаются по ключевым файлам
- [ ] каждому агенту выдан отдельный worktree path
- [ ] planning docs помечены как merge-sensitive
- [ ] понятен финальный validation path

Перед merge:
- [ ] прочитан реальный diff каждого агента
- [ ] planning docs сведены вручную
- [ ] `bash tests/run_all.sh` зелёный
- [ ] итоговый commit описывает уже сведённый результат, а не мечты одного агента
