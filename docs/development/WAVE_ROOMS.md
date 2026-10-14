# Разработка комнат совместного прослушивания

Навигация по актуальным исходникам на **14 октября 2026 года**. Последние изменения
комнат требуют согласованных обновлений Android-клиента и сервера; опубликованный APK
1.0.8 имеет отдельный состав. Пользовательский сценарий описан в [README](../../README.md#rooms).

## Последние изменения

- Экран комнаты позволяет создать комнату, войти по коду, поделиться приглашением,
  выбрать музыку из медиатеки и увидеть текущую очередь с локальной доступностью треков.
- Ведущий запускает воспроизведение, ставит его на паузу, готовит следующий трек
  и закрывает комнату с подтверждением; участник видит очередь и может выйти.
- Снимки обычной и гостевой комнаты содержат `playback_command`: сохранённую команду
  с `sequence`, `kind` и `payload`. После переподключения клиент восстанавливает
  воспроизведение, учитывая уже применённую последовательность команд.
- Позднее подключение учитывает прошедшее время трека; обновление срока действия
  синхронизации сохраняет ранее принятую монотонную точку старта.
- При выходе восстанавливаются прежняя личная очередь и позиция. Если пользователь
  уже включил новую обычную очередь, восстановление её не заменяет.

## Где находится код

| Область | Точка входа |
| --- | --- |
| Экран, ввод кода, выбор треков и действия | [MainWaveUi.kt](../../apps/android/src/main/kotlin/app/autplay/MainWaveUi.kt) |
| Создание, вступление, очередь и восстановление | [WaveCoordinator.kt](../../apps/android/src/main/kotlin/app/autplay/application/wave/WaveCoordinator.kt) |
| Авторизованный HTTP/WS transport | [OkHttpWaveTransport.kt](../../apps/android/src/main/kotlin/app/autplay/application/wave/OkHttpWaveTransport.kt) |
| Подготовка Media3 и привязка к очереди | [WavePlaybackExecutor.kt](../../apps/android/src/main/kotlin/app/autplay/application/wave/WavePlaybackExecutor.kt), [WaveMediaAdapters.kt](../../apps/android/src/main/kotlin/app/autplay/playback/WaveMediaAdapters.kt) |
| Часы и коррекция позиции | [WaveCore.kt](../../apps/android/src/main/kotlin/app/autplay/domain/wave/WaveCore.kt), [WaveDriftMonitor.kt](../../apps/android/src/main/kotlin/app/autplay/domain/wave/WaveDriftMonitor.kt) |
| Гостевая сессия Android | [GuestRoomRuntime.kt](../../apps/android/src/main/kotlin/app/autplay/application/guestroom/GuestRoomRuntime.kt) |
| Серверный runtime и сохранённая команда | [wave.py](../../server/src/autplay/adapters/postgresql/wave.py), [wave_timeline.py](../../server/src/autplay/application/wave_timeline.py) |
| Обычный и гостевой HTTP/WS API | [wave_http.py](../../server/src/autplay/entrypoints/wave_http.py), [guest_room_http.py](../../server/src/autplay/entrypoints/guest_room_http.py) |

## Контракты и границы

Начните с [Wave Protocol v1](../design/AutPlay_Wave_Protocol_v1.md),
[ADR-026](../adr/ADR-026-p13-wave-room-clock-and-recovery.md),
[OpenAPI](../../contracts/openapi/v1/autplay-wave.openapi.json) и
[ADR-043 о гостевом доступе](../adr/ADR-043-s1d-guest-room-capability-runtime.md).
Протокол содержит исторический статус этапа P13; наличие текущего маршрута или поля
сверяйте с OpenAPI, кодом и тестами соответствующего изменения.

PostgreSQL владеет участниками, версией очереди и упорядоченными командами.
REST принимает изменения и возвращает снимки состояния; WebSocket сообщает об изменении,
но не заменяет сохранённую команду. Android Room хранит локальную проекцию,
Media3 исполняет воспроизведение и загрузки, WorkManager обслуживает отложенные задачи.
Журнал offline-синхронизации не используется как журнал живой комнаты.

Сохраняйте следующие свойства при изменениях:

1. **Разделяйте доступ к комнате и музыке.** Десятисимвольный код — идентификатор
   для входа приглашённого аккаунта, а не разрешение на чужой Vault. Гость использует
   отдельную capability и [формат приглашения](../../contracts/social/v1/guest-document.schema.json).
   Гостевые секреты не восстанавливаются из локальной Room-проекции.
2. **Сохраняйте порядок и повторяемость команд.** Учитывайте `room_epoch`, `queue_version`
   и последовательность; повторное уведомление или снимок не должны запускать один `PLAY`
   повторно. Пропуск событий требует нового снимка.
3. **Проверяйте источник перед стартом.** Подходят читаемый локальный файл, готовая загрузка
   или заново авторизованный поток Vault. Готовность привязана к устройству, версии очереди
   и её элементу. Недоступная музыка или нестабильные часы не должны давать успешный старт.
4. **Планируйте по монотонным часам.** Коррекция использует свежую позицию Media3;
   она допустима только для текущего элемента активной очереди `WAVE` или `GUEST_WAVE`.
   Не переносите её на личную очередь и не сдвигайте принятую точку старта при продлении lease.
5. **Сохраняйте личное воспроизведение.** Выход, закрытие, смена привязки или поздний callback
   не должны перезаписать более новую очередь, библиотеку или состояние другой комнаты.

Ограничения v1: **8 активных устройств**, **100 элементов очереди**, срок комнаты
**до 6 часов**; предварительная подготовка — текущий и до трёх следующих треков,
не более двух одновременных предварительных загрузок. Ведущий управляет очередью;
голосование и редактирование очереди участниками в этот сценарий не входят.

## API для навигации

Все пути ниже имеют префикс `/api/v1`; тела и ответы сверяйте с OpenAPI.

| Действие | Метод и путь |
| --- | --- |
| Создать / войти по коду | `POST /wave/rooms`, `POST /wave/rooms/join` |
| Восстановить снимок | `GET /wave/rooms/{room_id}/snapshot` |
| Изменить очередь или отправить команду | `POST /wave/rooms/{room_id}/commands` |
| Передать готовность / согласовать старт | `POST /wave/rooms/{room_id}/availability`, `POST /wave/rooms/{room_id}/start` |
| Согласовать часы / передать timing | `POST /wave/clock`, `POST /wave/rooms/{room_id}/timing` |
| Выйти / закрыть / передать роль ведущего | `POST /wave/rooms/{room_id}/leave`, `/close`, `/host-transfer` |
| Получить уведомления | WebSocket `/wave/rooms/{room_id}/events` |
| Создать гостевое приглашение | `POST /social/guest-documents` |
| Активировать приглашение | `POST /wave/guest/redeem` |
| Гостевой снимок / уведомления | `GET /wave/guest/rooms/{room_id}/snapshot`, WebSocket `/wave/guest/rooms/{room_id}/events` |

Обычные маршруты требуют сессию устройства; гостевые маршруты имеют отдельную проверку
capability. Коды комнат, приглашения, токены и приватные адреса не должны попадать в логи
или примеры документации.

<a id="checks"></a>

## Проверки

Команды запускаются из корня репозитория с закреплённым окружением из
[README](../../README.md#developers). Для полного серверного контура:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1 -ServerOnly
```

```bash
bash scripts/check.sh --server-only
```

Эти команды используют собственный временный Compose-проект PostgreSQL и удаляют
его ресурсы после проверки. Не подставляйте production-базу в тестовое окружение.

Для быстрой проверки формата и HTTP/WS поведения перед полным прогоном:

```powershell
uv run --frozen python -m pytest tests/contract/test_wave_contract_v1.py
uv run --project server --frozen python -m pytest -c server/pyproject.toml server/tests/test_wave.py server/tests/runtime/test_wave_http.py server/tests/runtime/test_guest_room_http.py
```

Те же команды `uv` подходят для Bash. Они не заменяют PostgreSQL-тесты
[test_wave_runtime.py](../../server/tests/postgresql/test_wave_runtime.py) и
[test_guest_room_s1d.py](../../server/tests/postgresql/test_guest_room_s1d.py).

Android gate для unit-тестов, lint и debug-сборки:

```powershell
.\gradlew.bat --no-daemon --console=plain --max-workers=1 --dependency-verification=strict lintDebug testDebugUnitTest assembleDebug
```

```bash
./gradlew --no-daemon --console=plain --max-workers=1 --dependency-verification=strict lintDebug testDebugUnitTest assembleDebug
```

После изменения экранов или исполнения Media3 нужны instrumentation-тесты на устройстве
либо эмуляторе; unit-тесты не доказывают реальное воспроизведение. В соответствующем
тестовом окружении запускайте `connectedDebugAndroidTest` через wrapper с теми же
ограничениями одного worker и строгой проверки зависимостей. Канонический пример —
[CI / Android connected](../../.github/workflows/ci-android-connected.yml),
который проверяет устройство с Android API 26.

| Что проверять | Тесты |
| --- | --- |
| Ввод кода, ID участников, transport и привязка коррекции | [WaveRoomInputTest](../../apps/android/src/test/java/app/autplay/WaveRoomInputTest.kt), [OkHttpWaveTransportTest](../../apps/android/src/test/java/app/autplay/application/wave/OkHttpWaveTransportTest.kt), [WavePlaybackAnchorTest](../../apps/android/src/test/java/app/autplay/application/wave/WavePlaybackAnchorTest.kt), [WaveDriftMonitorTest](../../apps/android/src/test/java/app/autplay/domain/wave/WaveDriftMonitorTest.kt) |
| Создание, выбор музыки, роли и подтверждение закрытия | [WaveRoomScreenTest](../../apps/android/src/androidTest/kotlin/app/autplay/ui/WaveRoomScreenTest.kt) |
| Старт и пауза на реальном Media3, следующий трек, переподключение и личная очередь | [WaveRoomLifecycleTest](../../apps/android/src/androidTest/kotlin/app/autplay/application/wave/WaveRoomLifecycleTest.kt) |
| Модель трёх устройств, часы и порядок команд | [P13WaveRuntimeTest](../../apps/android/src/androidTest/kotlin/app/autplay/domain/wave/P13WaveRuntimeTest.kt) |
| Снимок с `playback_command`, WS и гостевой доступ | [test_wave_http.py](../../server/tests/runtime/test_wave_http.py), [test_guest_room_http.py](../../server/tests/runtime/test_guest_room_http.py), [test_wave_contract_v1.py](../../tests/contract/test_wave_contract_v1.py) |

Результаты старых прогонов относятся к их исходникам и окружению. После изменения
контракта, transport или исполнения проверяйте соответствующие сценарии заново.
Подпись, упаковка и выпуск описаны в [CI_RELEASE](../operations/CI_RELEASE.md);
развёртывание — в [DEPLOYMENT](../operations/DEPLOYMENT.md).
