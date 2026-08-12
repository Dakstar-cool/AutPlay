# User Review Guide

## После каждой phase

Попросите Codex показать не code целиком, а:

1. Outcome одним абзацем.
2. Какие acceptance criteria закрыты.
3. Какие commands реально запускались.
4. Что не проверено на реальном runtime/device.
5. Новые dependencies и зачем каждая нужна.
6. ADR и unresolved risks.
7. Git diff summary/status.
8. Handoff path.

## Green flags

- tests доказывают behavior, а не только existence class;
- DB migrations проверены на real PostgreSQL/SQLite;
- ошибки и retries имеют bounded states;
- no secrets and machine-specific paths;
- logs/metrics имеют stable reason codes;
- следующий prompt ссылается на handoff, а не пересказывает проект;
- Codex остановился на границе phase.

## Red flags

- "tests should pass" без фактического запуска;
- skipped critical integration test;
- `fallbackToDestructiveMigration`;
- Redis/MinIO/Kafka добавлены "на будущее";
- GPU library импортируется API process;
- один `Track` class смешивает Recording, file и playlist position;
- Room дублирует Media3 DownloadIndex;
- sync использует timestamps как единственный order/dedup key;
- import silently chooses uncertain candidate;
- phase создала много пустых layers будущих features;
- Codex начал следующую phase без команды.

## Если phase не прошла

Используйте короткий corrective prompt:

```text
Продолжай только текущую phase Pxx. Прочитай HANDOFF_Pxx.md и исправь перечисленные FAIL/blockers. Не расширяй scope и не начинай следующую phase. Повтори обязательные проверки, обнови evidence и handoff. Остановись, когда exit gate Pxx подтвержден или требуется мое решение.
```

## Если начался новый чат

```text
Продолжи AutPlay phase Pxx в текущем repository. Сначала прочитай AGENTS.md, docs/build-pack/PROMPT_PROTOCOL.md, prompt Pxx и последний HANDOFF_Pxx.md. Проверь git status и реальные test results. Не повторяй подтвержденную работу и не переходи к Pyy.
```

## Когда нужен ручной выбор

Просите Codex дать 2-3 варианта в таблице:

- reliability;
- complexity;
- operational cost;
- migration risk;
- recommendation;
- what becomes irreversible.

Не выбирайте по количеству features. Для personal server v1 обычно выигрывает минимальная dependency surface.
