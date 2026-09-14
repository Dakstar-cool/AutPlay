# TASK: Android UI functional parity

## Статус

- **Состояние:** Ready
- **Приоритет:** P1 для Vault Search и Taste Profile exclusion; P2 для остальных частей
- **Baseline:** `7594f3d`, `2026-09-10`
- **Область:** Android product UI и существующие Android application boundaries
- **Источник требований:** [`AutPlay_UI_Contract_v1.md`](./AutPlay_UI_Contract_v1.md)

## Цель

Закрыть расхождения между уже реализованными пользовательскими возможностями AutPlay и их
Android UI-представлением. После выполнения пользователь должен иметь возможность увидеть результат
каждой доступной операции, выполнить все поддерживаемые lifecycle-действия и получить честное
состояние там, где действие недоступно.

Задача считается выполненной только при end-to-end проводке
`Compose -> application layer -> persistence/transport -> refreshed UI state`. Наличие кнопки без
реального результата либо экрана, который сводит полезные данные к одному счётчику, не считается
выполнением.

## Исходное состояние

Основные сценарии уже покрыты: Home, локальный Search, Library и detail-экраны, Media3 playback,
timeline, sleep timer, очередь, ручные плейлисты, pairing/admission, owner provisioning, social,
statistics, основные Wave-сценарии и loopback Web Admin.

Остаются следующие разрывы:

1. Vault Search получает `List<RemoteLibraryEntry>`, но сохраняет и показывает только `rows.size`.
2. Now Playing не позволяет исключить текущее прослушивание или сессию из Taste Profile.
3. Локальный импорт поддерживает `PAUSE`, `RESUME`, `CANCEL`, но UI не вызывает эти операции.
4. Wave поддерживает передачу роли хоста, но в UI нет списка допустимых получателей и действия.
5. History показывает только количество событий.
6. Downloads показывает количество, состояние первой записи и кнопку загрузки выбранного трека.
7. Server Features сводит результаты server search и recommendations к количеству элементов.

## Обязательный объём

### 1. P1 — полноценные результаты Vault Search

Текущие точки:

- `CoreCommandActions.kt`: результат `searchLibrary()` сводится к `rows.size`;
- `OfflineSearchBindingEffect.kt`: повторный поиск после смены binding делает то же самое;
- `CoreProductScreens.kt`: Vault-секция умеет показывать только loading/error/empty/count.

Требуется:

- хранить bounded typed collection удалённых результатов в состоянии Search;
- сохранять generation/binding guard: поздний ответ старого запроса не должен менять текущий экран;
- отображать для каждого результата минимум title, artist, availability/source и устойчивый identity;
- дать открыть/воспроизвести результат, если существующий контракт способен разрешить его в
  `UserTrackRef`/playback source;
- если конкретный результат нельзя воспроизвести, показать честное недоступное состояние или
  существующее действие добавления/восстановления, не создавая фиктивный успех;
- локальные результаты должны оставаться доступными независимо от ошибки Vault;
- поддержать loading, empty, offline/error и populated состояния отдельно от Local Search.

Acceptance criteria:

- непустой ответ Vault отображается как список, а не только как число;
- нажатие на доступный результат создаёт атрибутируемую durable queue и запускает существующий
  playback flow;
- смена query, scope, account или server binding отбрасывает устаревшие ответы;
- ошибка Vault не очищает локальные результаты;
- Compose-тест покрывает populated/empty/error и click path.

### 2. P1 — Exclude from Taste Profile в Now Playing

Текущие точки:

- `NowPlayingRouteActions` содержит Like/Dislike/Clear, но не taste exclusion;
- `PlaybackPersistenceRepository` при финализации передаёт `excluded = false`;
- Room/domain модели уже содержат `excluded_from_taste`.

Требуется:

- добавить отдельные действия `Exclude this listen` и `Exclude this session` либо эквивалентный
  понятный выбор;
- не смешивать exclusion с Like/Dislike и не менять preference при переключении exclusion;
- хранить выбранное состояние в owning playback/queue session, а не только в `remember` Compose;
- переживать recreation/process restart согласно существующей durable queue модели;
- передавать итоговое значение в listening event и Journal без изменения immutable identity;
- блокировать или честно объяснять действие в контексте, где оно не поддерживается;
- не отправлять network/persistence mutation на каждую recomposition;
- не заявлять playlist-level exclusion выполненным, если отдельный durable contract для него не
  реализован в рамках этой задачи.

Acceptance criteria:

- текущий listen можно исключить до финализации, и listening event получает
  `excluded_from_taste = true`;
- session-level выбор применяется к последующим событиям этой queue session;
- Like/Dislike остаются независимыми;
- состояние видно после Activity recreation и не сбрасывается при переходах между экранами;
- unit/instrumentation tests покрывают toggle, restart recovery и финализацию события.

### 3. P2 — lifecycle локального импорта

Текущие точки:

- `LocalImportReviewRepository.controlJob()` реализует `PAUSE`, `RESUME`, `CANCEL`;
- `LegacyImportRouteActions` содержит только choose/select/review.

Требуется:

- добавить callbacks для разрешённых job-control операций;
- показывать только допустимые действия для текущего состояния job;
- отключать повторную отправку, пока операция выполняется;
- после операции обновлять job/items из Room, не подменяя подтверждённое состояние optimistic UI;
- отображать стабильную recoverable ошибку при запрещённом transition или storage failure;
- сохранить текущие evidence-safe ограничения candidate review.

Acceptance criteria:

- `PENDING`/`REVIEW_REQUIRED` можно поставить на паузу;
- `PAUSED` можно продолжить;
- нетерминальный job можно отменить с явным подтверждением;
- запрещённые переходы не отображаются как доступные и не дают fake success;
- тесты проверяют state/action matrix и вызов реального repository boundary.

### 4. P2 — передача роли хоста Wave

Текущая точка: `WaveCoordinator.transferHost(targetDeviceId)` и transport реализованы, но UI их не
вызывает.

Требуется:

- добавить в bounded Wave UI state список допустимых активных устройств/участников без секретных
  данных;
- показывать действие только текущему хосту и только при наличии допустимого target;
- требовать явного подтверждения с именем/меткой получателя;
- вызывать существующий coordinator method и затем принимать authoritative room snapshot;
- не переключать роль локально до подтверждённого ответа сервера;
- корректно обрабатывать уход target, stale snapshot, offline и role rejection.

Acceptance criteria:

- хост может передать роль выбранному активному target;
- участник без роли хоста не видит активного действия;
- stale/failed transfer не меняет локально отображаемого хоста;
- успешный transfer обновляет role/state из authoritative snapshot;
- Compose и coordinator tests покрывают success, stale target и rejection.

### 5. P2 — содержательный History

Текущая точка: маршрут `History` выводит только `historyCount`.

Требуется:

- получить bounded/paginated projection listening history из существующего Room/server-sync
  состояния;
- показывать трек, исполнителя, время прослушивания и полезный статус без raw protocol fields;
- дать открыть трек и запустить playback через существующий durable queue path;
- определить loading/empty/populated/error состояния;
- не загружать всю историю в память и не раскрывать private diagnostic payload.

Acceptance criteria:

- History показывает реальные события в обратном хронологическом порядке;
- duplicate listens остаются отдельными событиями;
- элемент с доступным источником можно воспроизвести;
- пагинация/лимит проверены тестом;
- пустой экран объясняет состояние и предлагает одно валидное действие.

### 6. P2 — содержательный Downloads

Текущая точка: маршрут `Downloads` показывает count, состояние первой записи и кнопку скачивания
выбранного трека.

Требуется:

- показывать bounded список download intents с названием трека и состоянием каждого элемента;
- различать queued/downloading/paused/completed/failed/cancelled без вывода приватных URL;
- связывать completed item с Library/detail/playback;
- показывать safe failure reason и подходящее recovery-действие;
- cancel/retry/remove добавлять только через application-owned contract и Media3 ownership, без
  прямого управления Media3 из Compose;
- не сохранять byte progress в Room, если это нарушает существующую ownership-модель;
- сохранить полноценный Offline-раздел Library и не создавать две расходящиеся истины.

Acceptance criteria:

- одновременно отображаются все bounded download items, а не только первая запись;
- completed item открывается или воспроизводится;
- failed item даёт валидный recovery path;
- допустимые cancel/retry операции проходят через application boundary;
- screen state переживает recreation и корректно обновляется после Media3 reconciliation.

### 7. P2 — результаты Server Features

Текущие точки:

- server search показывает только `searchResults.size`;
- recommendations показывает только `result.items.size` и replay-кнопки.

Требуется:

- либо отобразить bounded rows с полезными полями и валидными действиями, либо направить
  пользователя на основной Search/Home с перенесённым запросом/результатом;
- не маркировать действие как `Refresh Home`, если оно обновляет только diagnostic snapshot;
- показывать recommendation section/reason и воспроизводимое действие только при существующем
  resolution contract;
- сохранить назначение Server Features как diagnostic/action surface, не превращая его во вторую
  несогласованную Library.

Acceptance criteria:

- результат пользовательского server search можно увидеть, а не только посчитать;
- результат recommendations можно осмысленно просмотреть или открыть в основном Home flow;
- exact/algorithmic replay явно показывают новый authoritative result;
- labels точно описывают эффект операции;
- populated/empty/error/replay состояния покрыты тестами.

## Сквозные требования

- Compose только отображает state и отправляет intent; SQL, HTTP, filesystem и Media3 ownership
  остаются в application/adapters слоях.
- Все remote UI-возможности должны быть capability-driven и скрыты/disabled при отсутствии реального
  контракта.
- Standalone local playback, Library и Search не должны зависеть от сервера.
- Никакие bearer, invitation secret, private URL, raw path или unrestricted payload не попадают в UI,
  logs, saved state или тестовые snapshots.
- Длительные операции имеют single-flight/debounce там, где повторный tap опасен.
- Ошибки используют стабильные безопасные коды и recovery-oriented пользовательский текст.
- Compact/medium/expanded layout сохраняет destination, input и playback state.
- `PrivacyAndData -> Settings` может остаться alias, пока privacy/export controls остаются доступны и
  маршрут не ведёт на placeholder.
- User-visible Sync Status может оставаться сводным согласно разделу 95 ТЗ; внутренние cursor/hash/
  lineage данные в обычный UI не добавлять.

## Не входит в задачу

- UI для acquisition CLI, GPU/training, migrations, CI, release tooling и worker internals;
- перенос bootstrap/recovery из обязательной локальной CLI в Web Admin;
- password login, password recovery, public TLS/domain/provider selection;
- новый внешний music provider или новый server API без отдельного принятого контракта;
- playlist-level Taste Profile exclusion без отдельного durable interaction contract;
- общий визуальный редизайн уже работающих экранов.

## Рекомендуемый порядок реализации

1. Vault Search state/result/action model.
2. Taste exclusion ownership и Now Playing UI.
3. Local import lifecycle.
4. Wave participant projection и host transfer.
5. History projection/screen.
6. Downloads projection/screen.
7. Server Features result presentation и финальный parity audit.

Каждый пункт должен завершаться собственными тестами до перехода к следующему. Не объединять все
state-model изменения в один root composable diff.

## Проверка

Минимальный локальный gate:

```powershell
.\gradlew.bat :apps:android:lintDebug :apps:android:testDebugUnitTest :apps:android:assembleDebug
.\gradlew.bat :apps:android:compileDebugAndroidTestKotlin
```

Connected gate на явно выбранном API 26+ устройстве/эмуляторе:

```powershell
.\gradlew.bat :apps:android:connectedDebugAndroidTest
```

Обязательная UI evidence matrix:

| Surface | Состояния |
| --- | --- |
| Search/Vault | loading, populated, empty, offline, stale response |
| Now Playing | included, listen-excluded, session-excluded, recreation |
| Import | running, paused, resumed, cancelled, forbidden transition |
| Wave | host with targets, member, success, stale target, rejection |
| History | empty, populated, duplicate listens, next page |
| Downloads | queued, downloading, completed, failed, cancelled |
| Server Features | populated, empty, error, exact replay, algorithmic replay |

## Definition of Done

- [ ] Все семь разделов обязательного объёма выполнены end-to-end.
- [ ] Ни один пользовательский remote search/recommendation result не теряется до одного count.
- [ ] Все lifecycle-действия проходят через application-owned boundary.
- [ ] Нет fake success, прямого SQL/HTTP/Media3 управления из Compose и утечки приватных значений.
- [ ] Unit, lint, debug build и androidTest compilation зелёные.
- [ ] Connected UI tests зелёные либо внешнее ограничение явно зафиксировано как pending evidence.
- [ ] Выполнена ручная проверка compact и expanded layout, light/dark и offline/server-unavailable.
- [ ] README/release notes скорректированы так, чтобы слово «реализовано» соответствовало реальному
      UI-покрытию.
- [ ] Финальный `git diff` не содержит несвязанных изменений.
