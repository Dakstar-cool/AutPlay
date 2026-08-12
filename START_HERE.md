# AutPlay Codex Build Pack v1

**Назначение:** поэтапное создание AutPlay через Codex  
**Дата сборки:** 2026-08-12  
**Язык инструкций:** русский  
**Язык code, scripts, identifiers и comments:** English/ASCII  

## Краткий вывод

ZIP с ТЗ и промтами - хороший формат передачи проекта Codex. Плохим был бы только вариант с одним гигантским промтом на все приложение.

AutPlay слишком большой для безопасной реализации одним запуском: Android local-first client, server, Vault, sync, import, playback, identity matching, recommendations, optional GPU и Wave имеют разные failure boundaries. Поэтому пакет делит работу на отдельные фазы с проверяемой точкой остановки.

## Как использовать пакет

1. Создайте пустую папку проекта или откройте существующий repository в Codex/VS Code.
2. Прикрепите ZIP или распакуйте его в workspace.
3. Отправьте Codex текст из `FIRST_MESSAGE_TO_CODEX.md`.
4. Дождитесь завершения только `P00` и проверьте handoff.
5. Затем передавайте по одному файлу из `docs/build-pack/prompts/` в порядке номеров.
6. Не переходите к следующей фазе при красных tests, unresolved migration conflict или потере данных.
7. После каждой фазы сохраняйте `docs/implementation/HANDOFF_Pxx.md` и локальный Git commit.
8. Если контекст чата потерян, начните новый чат с последнего handoff и текущего phase prompt. Не пересказывайте проект вручную.

Если в Codex доступен `/goal`, используйте его для одной текущей фазы, а не для всего backlog. Один goal должен иметь одну цель и проверяемую stopping condition.

## Два режима старта

### Пустой workspace

Корневая папка этого архива становится repository root. Фаза `P00` создает Git repository, `AGENTS.md`, implementation plan и начальный structure skeleton.

### Уже существующий repository

Codex сначала выполняет read-only audit. Он переносит только недостающие design/build-pack документы в `docs/`, не перезаписывает пользовательский code и не меняет architecture молча.

## Что находится внутри

```text
AutPlay_Codex_Build_Pack_v1/
  START_HERE.md
  FIRST_MESSAGE_TO_CODEX.md
  docs/
    design/                 # ТЗ, Architecture, ER, schemas, identity
    build-pack/
      BUILD_STRATEGY.md
      PHASE_INDEX.md
      PROMPT_PROTOCOL.md
      DECISION_REGISTER.md
      REFERENCE_PROJECTS.md
      VERSION_POLICY.md
      MVP_ACCEPTANCE_MATRIX.md
      USER_REVIEW_GUIDE.md
      prompts/              # P00..P14, отправлять по одному
      templates/            # ADR, handoff, progress, risk, traceability
```

## Milestones

| Milestone | Фазы | Результат |
| --- | --- | --- |
| M0: Engineering baseline | P00-P02 | Repository, CI, PostgreSQL schema и migrations |
| M1: Runnable foundations | P03-P05 | Server runtime, sync contract, Android local database |
| M2: Local/connected MVP | P06-P09 | Vault, library, playback и end-to-end sync |
| M3: Smart import | P10-P11 | Import, identity resolution и CPU recommendation baseline |
| M4: Optional capabilities | P12-P13 | GPU enrichment и Wave |
| M5: Release candidate | P14 | Security, backup/restore, performance и release evidence |

## Главные правила

- В один момент выполняется только одна phase.
- ТЗ и design documents являются source of truth, а не подсказкой.
- Scope следующей phase не реализуется заранее.
- Любая новая инфраструктура требует измеримой необходимости и ADR.
- Redis, RabbitMQ, NATS, отдельная vector database и microservices не входят в default architecture.
- GPU никогда не является условием запуска core server.
- Локальная Android operation не ожидает server round-trip.
- Vault bytes immutable; metadata и identity остаются отдельными сущностями.
- Не использовать destructive database fallback.
- Не копировать code из reference projects без отдельной проверки лицензии.
- Не публиковать repository, images или deployment без явного разрешения пользователя.

## Когда остановиться и спросить пользователя

Codex должен остановиться, если:

- найдено нормативное противоречие, меняющее data model или security boundary;
- требуется выбрать внешний provider, домен, TLS topology или backup target;
- действие удалит или необратимо мигрирует реальные данные;
- нужен secret/token/account;
- тест требует платного external resource;
- текущая phase не может пройти acceptance gate без расширения scope.

Обычные локальные edits, dependency installation, test containers и недеструктивные проверки внутри phase выполняются самостоятельно.

## Конечная точка

После `P14` результат считается release candidate только при наличии:

- воспроизводимых builds;
- clean migrations и restore drill;
- Android offline playback;
- server-connected Vault playback;
- crash-safe Offline Journal and sync;
- import report с ambiguous/unresolved results;
- CPU-only server path;
- optional GPU path, не влияющего на core readiness;
- security, performance и end-to-end evidence.

Документ `docs/build-pack/MVP_ACCEPTANCE_MATRIX.md` содержит проверяемые критерии.
