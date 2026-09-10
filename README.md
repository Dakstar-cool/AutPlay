<p align="center">
  <img src="./assets/readme/hero-hybrid.png" width="100%" alt="AutPlay — local-first Android-плеер: слушать сразу, синхронизировать потом; справа показан реальный экран приложения, а Resonance Lens — локальная playback-reactive визуализация">
</p>

<p align="center">
  <strong>Слушайте свою медиатеку на Android без сети. Подключайте личный Linux-сервер только когда нужны синхронизация, Vault, импорт и совместные функции.</strong>
</p>

<p align="center">
  <a href="https://github.com/Dakstar-cool/AutPlay/releases/tag/v0.3.0"><strong>Скачать Android v0.3.0</strong></a> ·
  <a href="./docs/operations/INSTALL_AND_PAIR.md">Установка и связывание</a> ·
  <a href="#proof">Реальные экраны</a> ·
  <a href="#architecture">Архитектура</a> ·
  <a href="#development">Сборка и тесты</a>
</p>

> [!IMPORTANT]
> `v0.3.0` — development pre-release. APK подписаны сохранённым development key, а готовый серверный installer рассчитан только на доверенную домашнюю RFC1918-сеть. Это не store-ready Android distribution и не public-Internet production deployment.

> [!NOTE]
> Текущая ветка разработки содержит post-v0.3.0 audit remediation. Эти изменения ещё не опубликованы
> отдельным релизом: ссылки ниже по-прежнему ведут на проверенные артефакты `v0.3.0`.

AutPlay — local-first Android-плеер для личной музыкальной коллекции. Воспроизведение, поиск,
медиатека, плейлисты, очередь и загрузки остаются на устройстве и не ждут ответа сервера.
Необязательный личный Linux-сервер добавляет синхронизацию, неизменяемый файловый Vault, импорт,
рекомендации, друзей и Wave. PostgreSQL хранит метаданные и задания, а обязательного облачного
аккаунта, GPU/CUDA, Redis, Kafka и внешней аналитики нет.

## Быстрый выбор

| Нужно | Артефакт | Важная граница |
| --- | --- | --- |
| Слушать локальную музыку | [`autplay-0.3.0-dev-signed.apk`](https://github.com/Dakstar-cool/AutPlay/releases/download/v0.3.0/autplay-0.3.0-dev-signed.apk) | Hardened `app.autplay`; HTTP запрещён, сервер требует отдельно настроенный HTTPS |
| Проверить личный сервер дома | [`autplay-0.3.0-trusted-lan.apk`](https://github.com/Dakstar-cool/AutPlay/releases/download/v0.3.0/autplay-0.3.0-trusted-lan.apk) | Отдельный `app.autplay.lan`; debuggable, HTTP только для loopback/RFC1918 |
| Поднять CPU-сервер | [`autplay-server-v0.3.0-installer.zip`](https://github.com/Dakstar-cool/AutPlay/releases/download/v0.3.0/autplay-server-v0.3.0-installer.zip) | `linux/amd64`, Docker Compose 2.24.4+, trusted LAN, без production backup/TLS |

Проверьте скачанные файлы по `SHA256SUMS`. Два Android-варианта изолированы разными application
id и могут стоять рядом; их локальные базы автоматически не объединяются.

<a id="proof"></a>

## Продукт, а не макет

<p align="center">
  <img src="./assets/readme/product-board.png" width="100%" alt="Реальные экраны AutPlay: Android Home с локальным воспроизведением, offline Vault search, локальная библиотека и loopback Web Admin личного сервера">
</p>

- **Слушать без сети.** Локальная библиотека, поиск, плейлисты, очередь и playback остаются доступны без сервера.
- **Продолжать после process death.** Media3 и Room восстанавливают текущий элемент, позицию и будущую очередь.
- **Подключать сервер осознанно.** Android сверяет owner-controlled identity fingerprint, затем владелец подтверждает exact device key в loopback Web Admin.
- **Сохранять неоднозначность.** Неуверенная identity evidence уходит на review; probabilistic auto-merge остаётся выключен.
- **Расширять приватно.** Sync, Vault, импорт, рекомендации, друзья, статистика и Wave добавляются отдельными полномочиями, а не одним «доступом ко всему».

## Что изменилось после v0.3.0

- **Android стал устойчивее к гонкам и перезапускам.** Binding/credential writes сериализованы,
  unbind очищает привязанный к профилю recommendation context, а playback и deferred work получили
  дополнительные lifecycle/recovery regressions.
- **Sync и retention закрыты локальными regressions.** Server-side sync projections, terminal ACK
  semantics и temporal snapshot retention усилены без destructive migration fallback.
- **Recommendation evidence теперь причинно связано.** Android хранит bounded temporal delta,
  parent-pack identity, eligible set и impression mapping атомарно; это не объявляет R1B готовым.
- **Acquisition и SONA fail closed.** Загрузки ограничены по размеру и времени, публикуются без
  перезаписи существующих файлов; SONA shadow runtime ограничивает admission/deadline и запрещает
  CPU fallback для quality evidence.
- **Проверка разделена по реальным контурам.** Root, server, GPU, SONA training и acquisition имеют
  отдельные lock/static/test gates; Gradle использует strict dependency verification, а connected
  Android, training и acquisition получили собственные CI workflows.

| Evidence на текущем snapshot | Статус |
| --- | --- |
| Fresh Windows host gate: root `168 passed`; GPU `33 passed, 2 skipped`; training `37 passed`; acquisition `66 passed`; Android `144 actionable tasks`; PostgreSQL `878 passed, 1 skipped` | **PASS · 2026-09-10** |
| Server S1–S5, retention G1, acquisition T1–T2, reproducibility Q1/Q2/Q5/Q6 | **Fixed with local evidence** |
| Android A1–A8 / Q3 connected API 26, Q4 final release audit, G2 Linux/CUDA hardware proof | **Pending external/final evidence** |
| R1B evaluation / R1C activation | **BLOCKED / inactive** |

Полная граница утверждений и machine-readable registry:
[audit remediation review pack](docs/release/AUDIT_REMEDIATION_AFTER_R1B_V1.md) ·
[status JSON](docs/release/AUDIT_REMEDIATION_AFTER_R1B_V1.json).

## Первый успешный запуск

### Только Android

1. Скачайте hardened APK и сравните SHA-256 с релизным `SHA256SUMS`.
2. Установите APK, откройте AutPlay и выберите папку с музыкой через системный Android picker.
3. Начните воспроизведение. Учётная запись и сервер для этого не нужны.

### Android + личный сервер

1. Установите Docker Engine/Linux containers и Docker Compose `2.24.4+` на `linux/amd64` компьютере.
2. Распакуйте server installer и запустите `install-server.ps1 -BindHost <LAN IPv4>` или `install-server.sh --bind-host <LAN IPv4>`.
3. Получите fingerprint локальной командой `server-control … fingerprint`, один раз создайте OWNER и войдите в Web Admin только через `127.0.0.1`.
4. Установите `AutPlay LAN`, введите адрес mobile API, целиком сравните fingerprint и 12-значный admission code.
5. Одобрите устройство в Web Admin и дождитесь состояния `Подключено` на Android.

Полные команды для Windows/Linux, firewall scope, bootstrap, browser invite, pairing и диагностика:
**[Установка AutPlay и подключение личного сервера](docs/operations/INSTALL_AND_PAIR.md)**.

<a id="architecture"></a>

## Как это устроено

<p align="center">
  <img src="./assets/readme/system-map.svg" width="100%" alt="Android фиксирует локальное действие в Room и Journal, Media3 воспроизводит доступный источник, а необязательный сервер добавляет PostgreSQL, Vault, синхронизацию, рекомендации и Wave">
</p>

Одна Android-транзакция сохраняет доменное изменение вместе с Journal/outbox-фактом. WorkManager
повторяет отложенную синхронизацию, а Media3 независимо владеет воспроизведением и загрузками. На
сервере PostgreSQL хранит метаданные, права, события и задания; filesystem/NAS — байты Vault.
Server-rendered Web Admin использует отдельную browser-session authority и остаётся на loopback.

Ключевые инварианты:

- Android local actions не требуют синхронного server trip.
- `VaultObject`, `AudioVariant`, `Recording`, `ReleaseTrack` и `UserTrackRef` — разные сущности; знание SHA-256 не является разрешением.
- Profile, device, library, Vault, media, friendship и Wave проверяют полномочия независимо.
- Private-by-default статистика доступна другу только по явному opt-in и повторной проверке friendship/block.
- CPU-путь не импортирует и не устанавливает GPU/CUDA-код; optional GPU project физически изолирован.
- Неизвестные persisted/API values сохраняются; destructive Room/Alembic fallback запрещён.

## Что входит

| Контур | Реализовано |
| --- | --- |
| Android | Home, Search, Library, Track/Release/Playlist/Artist details, Media3 playback/downloads, ручные плейлисты и очередь, import review, Profile, statistics, sync status |
| Pairing | Signed discovery, owner-controlled fingerprint, exact-key enrollment, Web-approved admission, recovery/reenrollment без plaintext credential persistence |
| Server | CPU modular monolith, PostgreSQL metadata/jobs/sync, immutable filesystem Vault, Range streaming, imports, deterministic recommendations |
| Admin | Loopback SSR Web Admin: devices, sessions, trust, Vault, jobs/imports, review, recovery, diagnostics and audit |
| Social | Same-server friends, private coarse presence, Wave invitations and capability-limited Android guest access |
| Discovery | Manual TXT/Jamendo flow и default-off 24-hour automation с отдельным подтверждением `AUTO_IMPORT` |
| Local tools | Переносимый последовательный acquisition-модуль для явно разрешённых пользователем Jamendo, Hitmo и yt-dlp загрузок; он изолирован от server runtime и Vault authority |

## Проверяемая граница

P00–P14 закрыты как локальный CPU release candidate. Frontend M1–M4, Product M5, Server M6,
Discovery A1, Social S1, Privacy S2 и Library L1 ведутся как отдельная post-RC линия и не создают
P15. Текущий release gate проверяет оба Android APK, подписи, installer contract, CPU image identity,
archive reload, media/config smoke и disposable combined Compose runtime.

Основные evidence-документы:

- [v0.3.0 release notes](docs/release/RELEASE_NOTES_0.3.0.md)
- [RC test evidence](docs/release/TEST_EVIDENCE.md)
- [security review](docs/release/SECURITY_REVIEW.md)
- [performance report](docs/release/PERFORMANCE_REPORT.md)
- [Versioned RC checklist](docs/release/RC1_CHECKLIST.md)
- [Post-v0.3.0 audit remediation](docs/release/AUDIT_REMEDIATION_AFTER_R1B_V1.md)

## Resonance Lens

Resonance Lens имеет первый Android-only runtime в Now Playing: оптическая пара локально реагирует
на bounded process-local PCM energy/contour и не требует сервера. Размер, небольшое смещение и
оттенок pupil отражают только доступную playback dynamics — это не pitch, тембровый или mood-анализ.

Девять continuous reference-поз и их спектральные семейства остаются debug/evidence fixtures.
Production timeline, модель и глубокий анализ музыкального характера не активированы и не входят
в `v0.3.0`; визуальная глубина создаётся aperture-ribbons, световыми кольцами и orbital echoes без
прицельных осей и сквозного filament.

См. [Resonance Lens exploration](docs/design/explorations/AutPlay_Face_Resonance_Lens_Exploration_v1.md)
и [reviewed implementation plan](docs/design/explorations/AutPlay_Face_Resonance_Lens_Plan.md).

<a id="development"></a>

## Сборка и тесты

### Требования

- `uv 0.12.3` и зафиксированный CPython `3.14.7`;
- Microsoft OpenJDK `17.0.20+8-LTS` в `JAVA_HOME`;
- Android SDK Platform `36.1`, Build Tools `36.1.0` и `ANDROID_HOME`;
- Docker Engine + Docker Compose `2.24.4+`.

Gradle Wrapper загружает Gradle `9.3.1` и проверяет checksum дистрибутива.
`gradle/verification-metadata.xml` включает strict SHA-256 verification всех Gradle plugins,
metadata и транзитивных Android-зависимостей. Обновляйте его только явным
`--write-verification-metadata sha256` и проверяйте diff повторной резолюцией из пустого
`GRADLE_USER_HOME`.

Bootstrap не требует junction или фиксированного пути для Python-окружений. Чтобы полностью
пересоздать их в отдельном расположении, передайте абсолютный либо новый относительный корень:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 `
  -PythonEnvironmentRoot D:\AutPlay-Environments
```

```bash
bash scripts/bootstrap.sh --python-environment-root /var/tmp/autplay-environments
```

Без параметра uv использует обычные project-local `.venv`. Локальное предупреждение Android SDK
об XML schema v4 означает несовпадение поколений установленных command-line tools и SDK reader;
оно не подавляется. Bootstrap всё равно fail-closed проверяет точные `android-36.1/android.jar` и
Build Tools `36.1.0`, а CI собирает отдельный SDK root. Экспериментальный
`android.overridePathCheck` больше не используется в каноническом ASCII-пути репозитория.

### Канонические команды

Запускайте из корня репозитория. Этот README — источник истины для bootstrap/check порядка. Полный
check охватывает пять изолированных Python projects, strict Gradle dependency verification,
Android lint/unit/build variants и disposable PostgreSQL suite; `--server-only` сохраняет
root/server CPU-границу.

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

Точечные host-команды:

```powershell
uv run --frozen pytest tests/contract tests/release
.\gradlew.bat --no-daemon --console=plain --max-workers=1 `
  :apps:android:lintDebug `
  :apps:android:testDebugUnitTest `
  :apps:android:assembleDebug `
  :apps:android:assembleTrustedLan `
  :apps:android:assembleRelease
```

Для connected gate нужен явно выбранный Android API 26+:

```powershell
.\gradlew.bat --no-daemon --console=plain --max-workers=1 `
  :apps:android:connectedDebugAndroidTest
```

Release bundle создаётся только из clean tagged `HEAD` и сам запускает полный gate:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\package-release.ps1 `
  -ReleaseTag v0.3.0 `
  -JavaHome $env:JAVA_HOME `
  -AndroidHome $env:ANDROID_HOME
```

## Карта репозитория

| Путь | Ответственность |
| --- | --- |
| `apps/android` | Compose UI, Room, Journal, Media3, sync, pairing/admission, social, statistics, import, playlists and queue |
| `server/src/autplay` | CPU API, optional SSR admin, workers, stream, PostgreSQL/Vault, sync, discovery, social, recommendations and Wave |
| `server/migrations` | Линейные Alembic migrations; без destructive fallback |
| `contracts` | OpenAPI 3.1, JSON Schema и cross-language vectors |
| `deploy/compose` | Digest-pinned PostgreSQL и runtime/admin overlays |
| `deploy/installer` | Проверяемый server installer/control scripts для Windows/Linux |
| `tools/local_music_acquisition` | Локальный portable acquisition-модуль с точным сопоставлением, явным подтверждением прав и fail-closed provider fallback |
| `gpu` | Изолированный optional NVIDIA/ONNX enrichment project; моделей в релизе нет |
| `tests` | Contract, release-policy и end-to-end evidence fixtures |
| `docs` | Design contracts, ADR, handoffs, operations and release evidence |

## Границы v0.3.0

- Stable production signing custody and an exact version-code 3 to 4 data-preserving update proof
  are verified for `app.autplay`; the evidence APKs are not published, and the final schema-v2
  hardening workflow has not been run interactively after review.
- Bundled server — CPU `linux/amd64`, single-operator, trusted-LAN development topology.
- Public TLS activation, external scan/renewal/rollback/mobile Range evidence, registry push,
  production secret delivery and rollout policy remain separately gated; backup custody and
  retention are already accepted and verified.
- Web Admin доступен только на literal loopback; password login и public registration отсутствуют.
- Automatic probabilistic Recording merge выключен; ambiguous evidence требует review.
- P12 model activation и Face Timeline отсутствуют; локальный нейтральный Resonance Lens не заявляет наличие музыкального анализа.
- Не используйте `docker compose down --volumes` для данных, которые нужно сохранить: bundled installer не является системой резервного копирования.

Перед эксплуатацией прочитайте [release notes](docs/release/RELEASE_NOTES_0.3.0.md),
[installation guide](docs/operations/INSTALL_AND_PAIR.md),
[deployment boundary](docs/operations/DEPLOYMENT.md) и
[backup/restore guide](docs/operations/BACKUP_RESTORE.md).
