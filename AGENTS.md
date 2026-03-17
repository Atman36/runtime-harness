# AGENTS.md — Claw Execution Policy

## Что это за репозиторий
`claw` — project shell + orchestration engine для управления AI-агентными запусками.
Filesystem = source of truth. Артефакты первичны. Состояние живёт в файлах, не в памяти.

## Source of truth
- `docs/PLAN.md` — milestones и задачи; главный ориентир для работы
- `docs/STATUS.md` — живой журнал; обновлять после каждой завершённой задачи
- `docs/BACKLOG.md` — полный бэклог по эпикам с dependency notes
- `_system/registry/` — схемы, политики, routing rules

## Execution loop

```
select task → gather context → implement → validate → fix → mark done → log → next task
```

1. Взять первую незавершённую задачу из `docs/PLAN.md`, у которой зависимости закрыты.
2. Собрать только минимальный контекст для этой задачи.
3. Реализовать с scope diff — не трогать файлы вне задачи.
4. Запустить validation: `bash tests/run_all.sh` или команды из milestone.
5. Если validation красный — починить и перезапустить. Не переходить дальше.
6. Отметить задачу done в `docs/PLAN.md`.
7. Дописать лог-строку в `docs/STATUS.md` (задача / файлы / команды / результат / следующая).
8. Сразу перейти к следующей задаче без ожидания ответа.

## Правила работы

- Не останавливаться после interim-сводки.
- Не просить разрешения продолжить.
- Не переписывать что не сломано.
- Не делать unrelated рефакторинг.
- Не считать задачу done без прошедшего validation.
- Делать разумные локальные предположения; записывать их в `docs/STATUS.md`.
- Если board/issue sync не работает — логировать и продолжать. Это не блокер.

## Правила выбора агента

### Claude — когда
- дизайн / UX / flow
- неоднозначная спека
- архитектурная развилка
- исследование / нормализация требований
- review проблемных запусков Codex

### Codex — когда
- чёткая спека
- реализация / фиксы / тесты
- shell/python glue
- локальные изменения с понятным DoD

## Native subagents

Tool-native subagents в Codex и Claude не отменяют `claw`.

- subagent = bounded delegation внутри одного родительского запуска
- `claw` = durable orchestration: queue, artifacts, retries, approvals, hooks, review cadence

Использовать Codex/Claude subagents, когда:

- нужен быстрый read-only explorer/reviewer без засорения основного контекста
- задача естественно режется на несколько коротких подзадач внутри одного сеанса
- родительский агент остаётся главным владельцем результата и интеграции

Использовать `claw`, когда:

- работа должна пережить процесс / сессию
- нужен file-backed state и inspectable artifacts
- требуется review/approval/delivery lifecycle, а не только локальная делегация

Стартовые project-scoped subagents уже лежат в `.codex/agents/` и `.claude/agents/`.

### Claude agent teams

Если нужны именно несколько параллельных Claude-сессий с обменом результатами,
использовать agent teams, а не обычные subagents. Обычные subagents подходят для
локальной изоляции внутри одной Claude-сессии.

## First-run onboarding

На первом запуске сначала спросить в чате, какой CLI человек реально использует:
`codex`, `claude`, оба или пока ни одного.

Дальше:

1. Проверить `command -v codex` и `command -v claude`.
2. Если доступен только один CLI — не строить поток вокруг второго.
3. Если доступны оба — оставлять `preferred_agent: auto`.
4. Если пользователь не уверен насчёт подписки или auth — сделать только cheap capability probe (`--help`/status), затем подтвердить выбор в чате, а не гадать молча.

## Validation commands

```bash
bash tests/run_all.sh              # полный suite
python scripts/validate_artifacts.py projects/demo-project/runs/<RUN>
python scripts/generate_review_batch.py projects/demo-project
```

## Stop conditions

Остановиться только если:
1. все задачи в `docs/PLAN.md` завершены; или
2. реальный блокер после 3 попыток починки; или
3. нужен секрет / credentials / ручное действие; или
4. действие необратимо и требует явного подтверждения.

## Blocker format

При блокере — только это, без нарратива:

```
## Где застряли
- milestone:
- задача:
- ветка / worktree (если применимо):

## Что уже работает
- (≤7 пунктов)

## Что не работает
- точная команда / проверка / условие:
- одна фраза о причине:

## Что пробовали
- (список попыток)

## Варианты разблокировки
1. требуется выбор пользователя
2. нужен секрет / credential
3. проблема окружения / прав
4. проблема внешней зависимости / пакета

## Рекомендуемый следующий шаг
(одно действие)
```
