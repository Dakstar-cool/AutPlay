<p align="center">
  <img src="./assets/readme/hero.svg" width="100%" alt="AutPlay — локальная Android-музыка, управляемая очередь, друзья и приватная статистика с необязательным личным сервером">
</p>

<p align="center">
  <strong>Локальная медиатека и воспроизведение не ждут сеть. Личный сервер добавляет синхронизацию, Vault, друзей, статистику по выбору и совместную Wave.</strong>
</p>

<p align="center">
  <a href="#product">Что готово</a> ·
  <a href="#evidence">Доказательства</a> ·
  <a href="#android">Android</a> ·
  <a href="#architecture">Архитектура</a> ·
  <a href="#quick-start">Запуск</a> ·
  <a href="#release-boundary">Границы</a>
</p>

AutPlay — Android-first, local-first музыкальная система с необязательным личным сервером. Действия в медиатеке, плейлистах и очереди сначала фиксируются на устройстве; Media3 продолжает воспроизведение из доступного локального источника. Сервер не находится в синхронном пути локального действия.

Подключённый CPU-сервер добавляет авторизованную синхронизацию устройств, PostgreSQL-проекции, неизменяемый файловый Vault, HTTP Range-стриминг, импорт, рекомендации и Hybrid Wave. Для базового контура не нужны CUDA, облачная учётная запись, Redis, Kafka или внешняя аналитика.

<a id="product"></a>

## Что уже работает

<p align="center">
  <img src="./assets/readme/product-proof.svg" width="100%" alt="Работающие контуры AutPlay: офлайн-музыка, ручные плейлисты и очередь, друзья для Wave и приватная статистика">
</p>

- **Медиатека без сети.** Room хранит локальную истину и Journal/outbox; FTS ищет по кириллице и латинице; Media3 владеет воспроизведением и загрузками.
- **Ручные плейлисты.** Создание, открытие, переименование, удаление и точное изменение порядка сохраняют дубликаты как отдельные записи.
- **Своя очередь.** `Play next`, добавление в конец, удаление и перестановка будущих треков, очистка upcoming и обычные previous/next; текущий трек остаётся стабильным.
- **Друзья и Wave.** Подтверждённые друзья, опциональное coarse presence и быстрые приглашения в текущую совместную комнату без передачи клиенту серверной власти над Wave-очередью.
- **Статистика по выбору.** Владелец видит собственные агрегаты в профиле. Публикация выключена по умолчанию и открывается в настройках только для подтверждённых, не заблокированных друзей — не для всего интернета.
- **Личный сервер и Vault.** Идемпотентная синхронизация, возобновляемый импорт, проверяемые immutable-байты, авторизованный стриминг, детерминированные рекомендации и резервное восстановление.

<a id="evidence"></a>

## Проверено, а не заявлено

AutPlay остаётся локальным кандидатом в релиз, а не опубликованным production-продуктом. Текущее подтверждённое состояние:

| Контур | Последний зафиксированный результат |
| --- | --- |
| Android host | `lintDebug`, все JVM unit tests, debug APK и minified release/R8 — PASS |
| Samsung Galaxy M52 / API 33 | Полный QA side-by-side connected gate: **160 тестов, 0 failures, 3 ожидаемых skips** |
| Очередь после process death | Отдельный stage1 → PID → `adb force-stop` → отсутствие PID → stage2: порядок, current item, позиция и режимы восстановлены |
| Server / PostgreSQL | **600 серверных тестов** на PostgreSQL 18.4 + pgvector 0.8.6 для последнего серверного среза S2 |
| Contracts / release policy | **111 root tests**; OpenAPI/JSON Schema, release inventory и privacy/security assertions |
| Производительность RC | PostgreSQL 100k search p95 **6.403 ms**; Android FTS 10k p95 **12.555 ms** |

Подробности: [release notes 0.2.0](docs/release/RELEASE_NOTES_0.2.0.md), [RC test evidence](docs/release/TEST_EVIDENCE.md), [security review](docs/release/SECURITY_REVIEW.md) и [performance report](docs/release/PERFORMANCE_REPORT.md).

<a id="android"></a>

## Android-интерфейс

<p align="center">
  <img src="./assets/readme/adaptive-frontend.svg" width="100%" alt="Адаптивный Compose-интерфейс AutPlay для телефона, складного устройства и широкого окна">
</p>

Один Compose-контур адаптируется от нижней навигации телефона до общей боковой панели на складных устройствах и планшетах. Мини-плеер и Now Playing остаются доступными, а Home, Search, Library, Wave, Profile и Settings используют owner-scoped локальные проекции.

В профиле находятся собственная статистика и друзья. В настройках профиля владелец явно включает или выключает доступ друзей к статистике; default — private. Плейлисты открываются как полноценные редактируемые коллекции, а очередь управляется из Now Playing и релевантных track/detail-поверхностей.

Темы: system/light/dark и акценты Coral, Violet, Green, Blue. Секреты, приватные server origins, raw paths и персональные payloads не входят в обычные логи или экспорт настроек.

<a id="architecture"></a>

## Как это устроено

<p align="center">
  <img src="./assets/readme/system-map.svg" width="100%" alt="Локальное действие AutPlay сохраняется в Room и Journal, а необязательный сервер синхронизирует PostgreSQL, Vault, друзей, статистику и Wave">
</p>

Одна Android-транзакция сохраняет доменное изменение и его Journal/outbox-факт. WorkManager повторяет отложенную синхронизацию, а Media3 независимо владеет исполнением playback/download. На сервере PostgreSQL хранит метаданные, события и jobs; filesystem/NAS хранит bytes Vault.

Ключевые границы:

- `VaultObject`, `AudioVariant`, `Recording`, `ReleaseTrack` и `UserTrackRef` остаются разными сущностями.
- Знание SHA-256 никогда не является разрешением на чтение Vault.
- Неуверенная идентичность не приводит к тихому auto-merge.
- Обычная очередь редактируется локально; Wave-очередь остаётся server-authoritative и fail-closed.
- Статистика private by default; дружба и отсутствие block повторно проверяются на каждом friend-read.
- CPU-путь не импортирует GPU/CUDA-код. Опциональный GPU-проект физически изолирован.

<a id="quick-start"></a>

## Запуск репозитория

AutPlay поставляется как воспроизводимый development repository и локальный release candidate. Публичного установщика, production signing key, рабочего container registry и готовой публичной TLS-топологии пока нет.

### Требования

- `uv 0.12.3` и зафиксированный CPython `3.14.7`;
- Microsoft OpenJDK `17.0.20+8-LTS` в `JAVA_HOME`;
- Android SDK Platform `36.1`, Build Tools `36.1.0` и `ANDROID_HOME`;
- Docker Engine + Compose с поддержкой `up --wait`.

Gradle Wrapper загружает Gradle `9.3.1` и проверяет checksum дистрибутива.

### Канонические команды

Запускайте из корня репозитория. README — источник истины для порядка bootstrap и проверок.

Windows PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1 -ServerOnly
```

Linux, macOS или настроенная WSL:

```bash
bash scripts/bootstrap.sh
bash scripts/check.sh
bash scripts/check.sh --server-only
```

`bootstrap` синхронизирует frozen Python-окружения, разрешает Gradle Wrapper и проверяет Compose. `check` выполняет contract/release, server, dependency-policy и Android host gates и поднимает одноразовый PostgreSQL только на случайном loopback-порту.

Точечные команды:

```powershell
uv run --frozen pytest tests/contract tests/release
.\gradlew.bat --no-daemon --console=plain --max-workers=1 `
  :apps:android:lintDebug `
  :apps:android:testDebugUnitTest `
  :apps:android:assembleDebug `
  :apps:android:assembleRelease
```

Для connected gate нужен авторизованный Android API 26+:

```powershell
.\gradlew.bat --no-daemon --console=plain --max-workers=1 `
  :apps:android:connectedDebugAndroidTest
```

L1 process-death gate запускается отдельно, потому что две стадии должны быть разделены внешним `force-stop`:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\test-l1-process-death.ps1 `
  -AndroidHome $env:ANDROID_HOME `
  -DeviceSerial <adb-serial> `
  -QaSideBySide
```

<details>
<summary><strong>Одноразовый CPU runtime</strong></summary>

Создайте secret-файл минимум из 32 случайных символов вне репозитория. Runtime-профиль запускает migration, API, CPU-worker и direct streaming; API/streaming по умолчанию доступны только через loopback.

```powershell
$env:AUTPLAY_RUNTIME_AUTH_SECRET_FILE = 'C:\path\outside\repo\autplay-auth-secret.txt'
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime up --build --wait
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime down --volumes
```

```bash
export AUTPLAY_RUNTIME_AUTH_SECRET_FILE=/path/outside/repo/autplay-auth-secret.txt
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime up --build --wait
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime down --volumes
```

Перед использованием прочитайте [runtime Compose guide](deploy/compose/README.md).

</details>

<details>
<summary><strong>Изолированный GPU-проект</strong></summary>

У `gpu/` отдельные lock, image и worker. Канонический CPU-контур его не устанавливает и не импортирует. Ни одна GPU-модель в текущем RC не упакована и не активирована.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-p12-gpu.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\test-p12-gpu.ps1 `
  -DeviceSelector 'uuid:GPU-...'
```

См. [ADR-025](docs/adr/ADR-025-p12-isolated-gpu-enrichment-and-model-rollout.md) и [ADR-027](docs/adr/ADR-027-p14-conditional-phase-reachability.md).

</details>

## Карта репозитория

| Путь | Ответственность |
| --- | --- |
| `apps/android` | Compose UI, Room v12, Journal, Media3 playback/download, sync, friends, statistics privacy, playlists, queue and Wave recovery |
| `server/src/autplay` | Modular-monolith CPU API, workers, streaming, PostgreSQL/Vault adapters, sync, social, statistics, import, recommendations and Wave |
| `server/migrations` | Linear Alembic revisions `0001`–`0023`; без destructive fallback |
| `contracts` | OpenAPI 3.1, Draft 2020-12 schemas and cross-language vectors |
| `deploy/compose` | Digest-pinned PostgreSQL and loopback-only runtime profiles |
| `gpu` | Physically isolated optional NVIDIA/ONNX enrichment project |
| `tests` | Contract, release-policy and end-to-end evidence fixtures |
| `docs/design` | Product, system, API, privacy and persistence contracts |
| `docs/adr` | Accepted architectural decisions and boundaries |
| `docs/operations` | CI/release, deployment, recovery and observability guides |
| `docs/release` | RC checklist, evidence, security, performance, SBOM and release notes |

<a id="release-boundary"></a>

## Границы релиза

- Это проверенный локальный RC, но не опубликованный GitHub Release и не production deployment.
- Production signing, registry push, public domain/TLS topology, backup target and registration/legal policy intentionally remain operator decisions.
- Friend-visible statistics are not Internet-public; collaborative playlists and cross-device active-queue sync are not delivered.
- Wave evidence covers the declared trusted-local single-API-process topology; public-internet multi-instance fan-out remains deferred.
- Automatic probabilistic Recording merge remains disabled; ambiguous evidence requires review.
- Lyrics, Party Mode/member voting and guest queue editing are outside the delivered scope.
- P12 real RTX/model evidence remains `DEFERRED_WITH_APPROVAL`; the deterministic CPU baseline stays authoritative.

Перед эксплуатацией прочитайте [release notes 0.2.0](docs/release/RELEASE_NOTES_0.2.0.md), [CI/release guide](docs/operations/CI_RELEASE.md), [deployment boundary](docs/operations/DEPLOYMENT.md) и [backup/restore guide](docs/operations/BACKUP_RESTORE.md).
