# Полная настройка проекта AutPlay v0.4.0

Это руководство покрывает все поддерживаемые контуры: Android без сервера, личный сервер в
доверенной локальной сети, Web Admin, разработку из исходников, загрузчик музыки и опциональный
GPU-процесс. Для обычной установки начните с готовых файлов GitHub Release; исходники нужны только
для разработки и воспроизводимой проверки.

> [!IMPORTANT]
> `v0.4.0` — development pre-release. APK подписаны сохранённым development key. Trusted-LAN
> сервер использует HTTP и предназначен только для одного оператора в доверенной RFC1918-сети.
> Не публикуйте его порты в Интернет и не запускайте public-edge candidate как production.

## 1. Выберите нужный контур

| Контур | Что требуется | Когда использовать |
| --- | --- | --- |
| Только Android | `autplay-0.4.0-dev-signed.apk` | Локальная музыка, плейлисты, поиск и воспроизведение без аккаунта и сервера. |
| Android + личный сервер | `autplay-0.4.0-trusted-lan.apk` и installer ZIP | Vault, синхронизация, импорт, рекомендации, Wave и локальный Web Admin. |
| Разработка | Git, uv, JDK, Android SDK, Docker, FFmpeg | Изменение кода, полная проверка и сборка. |
| Загрузчик музыки | uv, FFmpeg/ffprobe, при необходимости браузер/Node.js | Обработка разрешённых TXT-плейлистов в отдельном локальном контуре. |
| GPU worker | NVIDIA Container Toolkit и проверенная модель | Только опциональная производная обработка; основной продукт от GPU не зависит. |

## 2. Готовая установка из Release

Скачайте со страницы [v0.4.0](https://github.com/Dakstar-cool/AutPlay/releases/tag/v0.4.0):

- `autplay-0.4.0-dev-signed.apk` — основное приложение `app.autplay`;
- `autplay-0.4.0-trusted-lan.apk` — отдельное приложение `app.autplay.lan` для HTTP в LAN;
- `autplay-server-v0.4.0-installer.zip` — сервер для `linux/amd64`;
- `SHA256SUMS` — контрольные суммы;
- `sbom/*.cdx.json` — SBOM Android, Python-проектов и серверного образа.

Проверьте файлы до запуска:

```powershell
Get-FileHash .\autplay-0.4.0-dev-signed.apk -Algorithm SHA256
Get-FileHash .\autplay-0.4.0-trusted-lan.apk -Algorithm SHA256
Get-FileHash .\autplay-server-v0.4.0-installer.zip -Algorithm SHA256
```

```bash
sha256sum autplay-0.4.0-dev-signed.apk \
  autplay-0.4.0-trusted-lan.apk \
  autplay-server-v0.4.0-installer.zip
```

Значения должны совпасть с `SHA256SUMS`. Полная установка сервера, firewall, создание OWNER,
вход в Web Admin и церемония подключения телефона описаны в
[INSTALL_AND_PAIR.md](INSTALL_AND_PAIR.md). Краткая последовательность:

1. Установите основной APK для автономной работы или LAN-вариант для личного HTTP-сервера.
2. На компьютере установите Docker Engine/Desktop с Linux containers и Compose `2.24.4+`.
3. Распакуйте installer ZIP в постоянную папку и укажите конкретный LAN IPv4, не `0.0.0.0`.
4. Разрешите TCP `18787` и `18788` только для частной сети и локальной подсети.
5. Получите fingerprint локальной командой, создайте первого OWNER и одноразовый browser invite.
6. Откройте Web Admin только на сервере: `http://127.0.0.1:8787/admin/login`.
7. На телефоне запросите подключение, полностью сравните fingerprint и 12-значный код, затем
   одобрите exact device key в Web Admin.

Основной и LAN APK могут стоять рядом, но имеют разные локальные базы. Не удаляйте существующий
пакет ради обновления, если в нём есть важные локальные данные: используйте обновление поверх
совместимо подписанной версии.

## 3. Требования для разработки

Репозиторий проверяет версии жёстко и завершает bootstrap ошибкой при несовпадении:

| Компонент | Требование |
| --- | --- |
| ОС | Windows 10/11 с PowerShell или Linux/WSL для shell-сценариев |
| Git | Актуальная версия с поддержкой long paths на Windows |
| uv / Python | uv `0.12.3`; bootstrap установит CPython `3.14.7` |
| Java | Microsoft OpenJDK `17.0.20+8-LTS`; путь в `JAVA_HOME` |
| Android SDK | Platform `36.1`, Build Tools `36.1.0`, platform-tools; путь в `ANDROID_HOME` |
| Gradle | Wrapper `9.3.1` из репозитория |
| Docker | Linux containers; Compose `2.24.4+` |
| FFmpeg | `ffmpeg` и `ffprobe` в `PATH` |
| Node.js | `22+` только для реального yt-dlp-контура |

Проверьте базовое окружение:

```powershell
git --version
uv --version
& "$env:JAVA_HOME\bin\java.exe" -version
& "$env:ANDROID_HOME\platform-tools\adb.exe" version
docker version
docker compose version
ffmpeg -version
ffprobe -version
```

Не храните токены, keystore, пароли или серверные secret-файлы в checkout. Для локальных секретов
используйте отдельную owner-only папку; реальные значения не передавайте в аргументах команд,
логах, issue или чатах.

## 4. Checkout и bootstrap

```powershell
git clone https://github.com/Dakstar-cool/AutPlay.git
Set-Location .\AutPlay
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
uv run --project tools/local_music_acquisition --frozen playwright install firefox
```

```bash
git clone https://github.com/Dakstar-cool/AutPlay.git
cd AutPlay
bash scripts/bootstrap.sh
uv run --project tools/local_music_acquisition --frozen playwright install --with-deps firefox
```

Bootstrap создаёт независимые locked-окружения для root contracts, server, GPU, training и
acquisition. Он не устанавливает Docker, JDK, Android SDK или FFmpeg.

Если `.venv` нельзя хранить в checkout, Windows-скрипт поддерживает внешний каталог:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1 `
  -PythonEnvironmentRoot D:\AutPlayEnvironments
```

## 5. Сборка и установка Android

Обычная debug-сборка:

```powershell
.\gradlew.bat --no-daemon --console=plain --max-workers=1 `
  --dependency-verification=strict :apps:android:assembleDebug
```

LAN-вариант с отдельным application id:

```powershell
.\gradlew.bat --no-daemon --console=plain --max-workers=1 `
  --dependency-verification=strict :apps:android:assembleTrustedLan
```

QA debug рядом с основным приложением (`app.autplay.qa`):

```powershell
.\gradlew.bat --no-daemon --console=plain --max-workers=1 `
  --dependency-verification=strict -Pautplay.qaSideBySide=true :apps:android:assembleDebug
```

Установка на подключённое устройство без очистки данных:

```powershell
$adbTool = Join-Path $env:ANDROID_HOME 'platform-tools\adb.exe'
& $adbTool devices -l
& $adbTool install -r .\apps\android\build\outputs\apk\debug\android-debug.apk
```

`-r` сохраняет данные при совместимой подписи и не позволяет автоматически обойти downgrade.
Перед установкой проверьте package name, signer и `versionCode`; удаление пакета очищает его базу.
Connected-тесты выполняйте на отдельном тестовом устройстве или одноразовом эмуляторе: некоторые
сценарии намеренно сбрасывают тестовое состояние.

Production signing включается только когда заданы все четыре переменные:
`AUTPLAY_ANDROID_KEYSTORE_PATH`, `AUTPLAY_ANDROID_KEYSTORE_PASSWORD`,
`AUTPLAY_ANDROID_KEY_ALIAS`, `AUTPLAY_ANDROID_KEY_PASSWORD`. Частичный набор отклоняется. Политика
ключей описана в [ANDROID_SIGNING_CUSTODY.md](ANDROID_SIGNING_CUSTODY.md).

## 6. Полная проверка

Из корня репозитория:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1
```

```bash
bash scripts/check.sh
```

Gate проверяет lock-файлы, Ruff/format/mypy/pytest пяти Python-проектов, Android lint/unit/debug,
trusted-LAN и release/R8, реальный временный PostgreSQL/pgvector и серверные браузерные тесты. Его
Compose project имеет уникальное имя и удаляется вместе с тестовыми volume/network.

Быстрый CPU-server контур без Android/GPU/acquisition:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1 -ServerOnly
```

```bash
bash scripts/check.sh --server-only
```

## 7. Сервер из исходников: только одноразовая разработка

Для локальной разработки создайте **два разных** файла вне репозитория, каждый минимум из 32
случайных символов. Первый подписывает access tokens, второй — public-access source tokens.

```powershell
$env:AUTPLAY_RUNTIME_AUTH_SECRET_FILE = 'D:\AutPlaySecrets\auth.txt'
$env:AUTPLAY_RUNTIME_PUBLIC_ACCESS_SOURCE_SECRET_FILE = 'D:\AutPlaySecrets\source-hmac.txt'
docker compose -f deploy/compose/compose.yaml `
  -f deploy/compose/compose.runtime.yaml --profile runtime up --build --wait `
  postgres vault-init migrate api stream music-po-token
```

```bash
export AUTPLAY_RUNTIME_AUTH_SECRET_FILE=/srv/autplay-secrets/auth
export AUTPLAY_RUNTIME_PUBLIC_ACCESS_SOURCE_SECRET_FILE=/srv/autplay-secrets/source-hmac
docker compose -f deploy/compose/compose.yaml \
  -f deploy/compose/compose.runtime.yaml --profile runtime up --build --wait \
  postgres vault-init migrate api stream music-po-token
```

Порты по умолчанию привязаны к loopback. Этот Compose использует disposable development volume;
остановка с `down --volumes` удаляет его:

```text
docker compose -f deploy/compose/compose.yaml -f deploy/compose/compose.runtime.yaml --profile runtime down --volumes
```

Для постоянного личного сервера используйте installer из Release. Он проверяет image/revision,
создаёт постоянные secrets и P-256 identity вне bundle и применяет release overlay в правильном
порядке. Installer сначала запускает основной контур без CPU-worker. Worker включается полным
`server-control start` только после применения проверенных resource-report v1 и internal-I/O
report v3; без них он намеренно fail-closed. Не используйте синтетические test fixtures на
постоянном сервере. Схема отчётов и аргументы команд описаны в
[AutPlay Resource Measurement Report](../design/AutPlay_Resource_Measurement_Report_v1.md).

## 8. Web Admin и аккаунты

Готовый installer включает loopback Web Admin. Управление выполняется из его распакованной папки:

```powershell
.\server-control.ps1 -Action status
.\server-control.ps1 -Action start-core
.\server-control.ps1 -Action fingerprint
.\server-control.ps1 -Action bootstrap-owner -DisplayName "Owner"
.\server-control.ps1 -Action invite-browser -UserId <owner UUID>
.\server-control.ps1 -Action logs
```

```bash
./server-control.sh status
./server-control.sh start-core
./server-control.sh fingerprint
./server-control.sh bootstrap-owner "Owner"
./server-control.sh invite-browser <owner UUID>
./server-control.sh logs
```

Первый bootstrap допускается только на пустой базе. Invitation bearer живёт пять минут; вводите
его только в masked-поле `/admin/login`. Не сохраняйте чувствительный JSON bootstrap и bearer в
файл или историю терминала. Browser session имеет 30-минутный idle и 12-часовой absolute timeout.

Admin Web управляет аккаунтами/ролями, устройствами и запросами подключения, browser sessions и
passkey, музыкой/передачами и серверным состоянием. Сам Web не публикуется в LAN: `8787` остаётся
literal loopback.

## 9. Внешние источники и загрузчик музыки

PO-token provider входит в runtime как отдельный digest-pinned контейнер без host-порта и доступен
только worker-сети. Интернет-поиск/загрузка включаются оператором и всегда требуют сети; локальная
библиотека, Vault и синхронизация после получения образов работают без внешнего Интернета.

Переносимый CLI находится в `tools/local_music_acquisition`:

```powershell
Push-Location .\tools\local_music_acquisition
uv sync --frozen
uv run local-music-acquire --help
uv run python -m pytest -q
Pop-Location
```

Провайдеры включаются отдельно и требуют явного подтверждения прав. Токены Yandex/Jamendo и
прочие credentials храните в файлах вне репозитория; не передавайте их в командной строке.
Настройка очередей, восстановления и каталогов проверенных ссылок подробно описана в
[README загрузчика](../../tools/local_music_acquisition/README.md) и
[SERVER_QUEUE.md](../../tools/local_music_acquisition/SERVER_QUEUE.md).

## 10. Резервные копии и обновление

Не копируйте live PostgreSQL volume как обычную папку. Следуйте
[BACKUP_RESTORE.md](BACKUP_RESTORE.md): остановите изменяющие сервисы или используйте согласованный
database dump, сохраните Vault, identity key, обе независимые privacy/consent ledgers и их key IDs.
Обычное обновление не должно заменять P-256 identity или ledger keys.

Перед обновлением:

1. проверьте SHA-256 нового installer и прочитайте release notes;
2. сделайте проверенную резервную копию БД, Vault и operator state;
3. сохраните прежний installer/image для rollback;
4. запускайте новый installer поверх той же state-папки;
5. проверьте `status`, health, fingerprint, Web Admin и подключение тестового устройства;
6. только после проверки удаляйте старый image.

## 11. Диагностика

- `server-control ... status` — состояние контейнеров;
- `server-control ... logs` — журналы без ручного поиска имён Compose-контейнеров;
- `http://127.0.0.1:8787/health/ready` — локальная готовность API;
- сравните фактический LAN IPv4 с адресом при установке;
- проверьте firewall, отсутствие client isolation и доступность `18787/18788` с телефона;
- при identity/fingerprint mismatch не обходите блокировку — восстановите прежний state либо
  проведите явную новую церемонию доверия;
- при проблеме Android соберите `adb logcat`, не публикуя токены, URL с bearer или личные названия
  треков.

Наблюдаемость и безопасные поля журналов описаны в [OBSERVABILITY.md](OBSERVABILITY.md), удаление и
экспорт данных — в [PRIVACY_DELETE_EXPORT.md](PRIVACY_DELETE_EXPORT.md).

## 12. Упаковка релиза

Release package создаётся только из clean checkout на exact tag и заново выполняет полный gate:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\package-release.ps1 `
  -ReleaseTag v0.4.0 -JavaHome $env:JAVA_HOME -AndroidHome $env:ANDROID_HOME
```

Результат находится в `dist/release/v0.4.0`: два APK, server image archive, installer ZIP,
manifest, SBOM и `SHA256SUMS`. Публикуйте только этот проверенный набор. Полный процесс и CI — в
[CI_RELEASE.md](CI_RELEASE.md).

## 13. Что пока нельзя делать

`compose.public-edge.yaml` — квалифицированный кандидат топологии, а не разрешение открыть WAN.
Публичный запуск заблокирован до принятой custody production Android signer, проверенного
off-host backup generation/restore и закрытия deployment decisions. Детали — в
[DEPLOYMENT.md](DEPLOYMENT.md) и [PUBLIC_EDGE_PA3.md](PUBLIC_EDGE_PA3.md).
