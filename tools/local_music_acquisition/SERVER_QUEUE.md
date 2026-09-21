# Server file queue

Очередь скачивает файлы отдельно от базы и Vault AutPlay. Лимит входа — 2 MiB и 10 000 строк,
поэтому плейлист из 4 591 трека не нужно делить на пакеты. Повторяющиеся artist/title/album
после нормализации пробелов и регистра объединяются. Версии и альбомы не объединяются нечётко.

## Что гарантирует очередь

- Результат сохраняется после каждого трека. SIGINT/SIGTERM прекращает выдачу новых задач и
  даёт уже выполняющимся задачам завершиться. После жёсткого обрыва готовая квитанция
  восстанавливается без сетевого запроса; незавершённая попытка повторяется в пределах бюджета.
- Перед публикацией выполняется полное декодирование ffmpeg, сверяются размер, полный SHA-256
  и отпечаток, возвращённый провайдером. Каталог трека публикуется атомарным переименованием.
  На Linux локальная файловая система получает fsync файлов и родительских каталогов.
- Повторный запуск проверяет квитанции и метаданные завершённых файлов через локальный индекс.
  Изменение файла или истечение срока кеша требует полной проверки SHA-256. Повреждённый или
  удалённый файл получает `needs_review`; автоматической перезаписи и удаления нет.
- По умолчанию временные ошибки повторяются до 3 попыток, с интервалами 60 и 120 секунд.
  Источник после двух сбоев получает паузу, затем допускается пробный запрос. Если все источники
  отключились, оставшаяся очередь ждёт следующего запуска, не расходуя попытки на тысячи строк.
  Если параллельная задача дождалась уже отключённого источника, такой пропуск тоже не расходует
  бюджет. При сочетании отсутствия совпадений и отключённого источника трек остаётся в `retry`.
  Настоящая ошибка любого источника расходует попытку, даже если следующий источник отключён.
- `not_found`, `failed`, `retry` и `needs_review` считаются отдельно. Ненайденные треки повторяются
  только по явной команде. Отсутствие совпадения не означает успешную загрузку.
- Два процесса не могут одновременно работать с одной очередью или одним каталогом результатов.
  Параллельность — 1–4 строки; по умолчанию обращения внутри каждого источника сериализованы.
  В очереди можно разрешить по два изолированных запроса YouTube/SoundCloud флагами
  `--yt-dlp-concurrency 2` и `--soundcloud-concurrency 2`; Hitmo остаётся последовательным.

Предполагаются приватные каталоги на обычной локальной файловой системе. Гарантии fsync/rename
не распространяются на NFS/SMB/FUSE. Windows проверяет восстановление после сбоя процесса,
но стандартный Python не обеспечивает здесь fsync каталогов при потере питания.

## Команды

Все прежние настройки источников работают и с очередью:

```sh
uv run --frozen local-music-acquire /input/playlist.txt \
  --output-dir /music --queue-dir /queue \
  --jamendo-client-id-file /run/secrets/jamendo-client-id \
  --hitmo-rights-confirmed --disable-yt-dlp \
  --workers 2 --normalize-numbered
```

Ту же команду можно повторять: существующий прогресс подхватывается автоматически. Флаги
`--queue-max-attempts` и `--queue-retry-seconds` задают бюджет повторов. В режиме очереди
fallback после ограниченной ошибки источника включён. Команда завершает один проход;
код `75` означает, что осталась работа для следующего запуска, `0` — всё скачано либо стоит пауза,
`1` — проход завершён с пропусками/ошибками, `2` — проблема конфигурации или файловой системы.

```sh
local-music-acquire queue status --queue-dir /queue
local-music-acquire queue pause --queue-dir /queue
local-music-acquire queue resume --queue-dir /queue
local-music-acquire queue retry --queue-dir /queue
local-music-acquire queue retry --queue-dir /queue --include-not-found
local-music-acquire queue verify --queue-dir /queue
```

`resume` снимает паузу; исполнитель запускается прежней командой или systemd. `retry` выдаёт
новый бюджет только ошибочным/ожидающим строкам; скачанные и `needs_review` не изменяются.
`--miss-cache-ttl-seconds 21600` позволяет шесть часов не повторять подтверждённые безрезультатные
поиски при следующей попытке того же трека. Ошибки не кэшируются. Кэш по умолчанию выключен;
`retry` очищает его для повторяемых строк. Изменение кода, каталогов или настроек источников
также отменяет записи. Подробности — в разделе производительности [README](README.md).
Для изменённого плейлиста нужен новый `--queue-dir`; тот же `--output-dir` позволяет использовать
ранее завершённые квитанции по совпадающему ключу трека.

Результаты: `/music/tracks/<ключ>/<источник>/<аудиофайл>` и приватная `receipt.json` с
artist/title/album, размером, полным SHA-256 и источником. Незавершённые/отклонённые байты остаются
в `/music/.acquire/`, отдельно от готовой музыки. Каталог `/music/tracks` можно импортировать
позже, отдельной операцией. Очередь не утверждает, что эти файлы уже находятся в AutPlay.

## Проверка зависимостей

Добавьте `--check-runtime` к команде запуска: она выведет наличие ffmpeg/ffprobe, версию Node,
состояние файлов настройки и включённые источники, без скачивания и вывода секретов. Это
локальная проверка, не доказательство доступности внешнего сервиса.

В Docker закреплён Node.js 22: [yt-dlp требует поддерживаемый JavaScript runtime](https://github.com/yt-dlp/yt-dlp/wiki/EJS).
В прежнем серверном окружении Node 20 не подходил. Шаблон службы пока сохраняет `--disable-yt-dlp`,
поскольку доступность YouTube с сервера не подтверждена. После успешной сетевой проверки можно
заменить его на `--yt-dlp-rights-confirmed`. Yandex включается прежними параметрами
`--yandex-token-file` и `--yandex-rights-confirmed`, с отдельным read-only mount файла токена.

## Linux / Docker / systemd

Из каталога модуля:

```sh
docker build --target checks -f docker/Dockerfile -t autplay-acquisition:checks .
docker build --target runtime -f docker/Dockerfile -t autplay-acquisition:queue-20260914 .
```

Базовые образы закреплены digest, Python-зависимости — `uv.lock`. Сборка устанавливает
Chromium и системные пакеты из репозитория Debian; их точный набор фиксируется построенным
image ID. Образ не содержит базу AutPlay, музыку или реальные секреты.

Служба `docker/autplay-acquisition.service` запускает изолированный контейнер с read-only root,
непривилегированным UID/GID, ограничением CPU/RAM/PID и временной папкой. Chromium использует
существующий проверенный seccomp-профиль сервера; отключение sandbox не предусмотрено.

Создайте `/etc/autplay-acquisition.env` с реальными абсолютными путями (значения — пример):

```ini
ACQUISITION_UID=1000
ACQUISITION_GID=1000
ACQUISITION_CPUS=6
ACQUISITION_WORKERS=2
ACQUISITION_INDEX_RECHECK_SECONDS=86400
ACQUISITION_IMAGE=autplay-acquisition:queue-20260914
ACQUISITION_PLAYLIST=/srv/autplay/operator/acquisition/playlist.txt
ACQUISITION_QUEUE=/srv/autplay/operator/acquisition/queue-v2
ACQUISITION_OUTPUT=/srv/autplay/music-downloads-v2
ACQUISITION_JAMENDO_ID=/srv/autplay/secrets/production/jamendo-client-id
ACQUISITION_SECCOMP=/srv/autplay/operator/acquisition/seccomp_profile.json
```

Каталоги очереди и результата должны существовать и быть доступны заданному UID/GID.
Файл client ID и seccomp-профиль должны существовать до запуска. Mount использует `--mount`,
поэтому отсутствующий файл не превращается автоматически в пустую директорию.

После проверки путей, образа и остановки старого загрузчика установите новую службу:

```sh
sudo install -m 0644 docker/autplay-acquisition.service /etc/systemd/system/
sudo install -D -m 0755 docker/run-queue.sh /usr/local/lib/autplay-acquisition/run-queue.sh
sudo systemctl daemon-reload
sudo systemctl enable --now autplay-acquisition.service
journalctl -u autplay-acquisition.service -n 30 --no-pager
```

systemd повторяет временно незавершённую очередь каждые 60 секунд. Завершение и конфигурационные
ошибки не порождают бесконечный перезапуск; политика соответствует
[RestartPreventExitStatus](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html#RestartPreventExitStatus=).
Остановка службы ждёт сохранения текущих результатов; после превышения лимита следующий запуск
восстановится по последним квитанциям. После исправления конфигурации используйте `systemctl restart`.

## Переход со старого загрузчика

Старые MP3 и старые журналы не изменяются. Их 12-значные отпечатки не являются полноценными
доказательствами целостности и автоматически не принимаются за квитанции новой очереди.
Перед запуском полного старого плейлиста нужно на доступном сервере сопоставить существующие
файлы и строки по метаданным и полным SHA-256, затем сформировать остаток очереди. До этого
шаблон службы не следует запускать на полном плейлисте, иначе уже скачанные ранее треки
могут быть получены повторно. Доступ к серверу необходим для этой сверки и живого smoke-test.

## Additional music sources

Add `--enable-soundcloud --soundcloud-rights-confirmed` and
`--enable-bandcamp --bandcamp-rights-confirmed` to the acquisition command to include these
sources after YouTube and before Yandex. They use the same normalization catalog, durable
queue, receipt verification and retry policy. They need no additional dependencies or browser.
Bandcamp uses artist-enabled free original files; paid account downloads are not supported.
See [provider behavior and examples](README.md) for availability and format rules.

## Generic operator launcher

`docker/run-queue.sh PLAYLIST QUEUE_DIRECTORY MUSIC_DIRECTORY [acquisition options]`
starts the verified container for any TXT playlist. It enables SoundCloud and Bandcamp in
addition to Hitmo; Jamendo is enabled when its client ID file is configured. YouTube retains
the server default (disabled). `ACQUISITION_ENABLE_HITMO`, `ACQUISITION_ENABLE_YOUTUBE`,
`ACQUISITION_ENABLE_SOUNDCLOUD` and `ACQUISITION_ENABLE_BANDCAMP` accept `0` or `1` to select
sources for a particular run. No playlists, track corrections or
user catalog entries are built into the image or launcher.

The launcher defaults to a six-CPU quota (`ACQUISITION_CPUS=1..64`), not six dedicated cores.
`ACQUISITION_WORKERS=1..4` separately controls concurrent tracks (default 2). Each provider
remains serialized. Compare throughput and cgroup throttling before increasing workers;
the container memory limit remains 2 GiB. These settings take effect on the next launch;
changing them does not resume a paused queue.

## Download index and performance checks

`/music/.download-index.sqlite3` is a disposable SQLite index keyed by the existing SHA-256
of normalized artist, title and album. Unicode NFKC, case folding and whitespace normalization
retain compatibility with existing receipts. Track versions, featured artists and distinct albums
remain distinct. Reviewed spelling corrections still come from the normalization catalog.

The first encounter with an existing receipt verifies its full audio hash. Later lookups use
the primary key and compare receipt/file identity, size, modification time and change time.
Changed files and entries older than `--index-recheck-seconds` (default 86400, maximum one day)
are hashed again. Set this option to 0 for full verification on every run. Metadata caching cannot
detect silent disk corruption without metadata changes until the next full check.
`queue verify` forces full verification now without network access or clearing the pause marker.

Only artifacts published by the queue and backed by valid receipts enter this index. New queues
sharing the same output directory reuse it. Legacy loose audio files or files in another output
directory still require explicit reconciliation; matching a filename alone is not sufficient.
Deleting the cache while the worker is stopped is safe: verified receipts rebuild it on demand.
A corrupt/unwritable cache falls back to full verification and reports `unavailable`; it never
marks an unverified file downloaded. This cache is not the AutPlay library database or Vault.

`/queue/runtime.json` contains aggregate metrics from the latest completed pass: actual provider
requests, misses, failures, downloads, cooldown skips, time inside provider calls (including audio
validation), time waiting for provider locks, and index hits/full checks. No artist names, URLs or
credentials are included. It is a completed-pass snapshot, not a live speed counter.

Run this from the acquisition environment on both machines for a comparable baseline:

```sh
python -m local_music_acquisition.diagnostics
python -m local_music_acquisition.diagnostics --network
python -m local_music_acquisition.diagnostics --network --proxy-url socks5h://127.0.0.1:10808
```

The optional network probes request only public page headers, without redirects, cookies or
environment proxy settings. The explicit SOCKS proxy must already be running; diagnostics never
start Xray. Reports contain tool versions, logical/affinity CPU counts, container cgroup quota and
throttling counters when available, HTTP status and header latency. Homepage success does not
prove that media URLs are downloadable or measure track throughput.

For the server comparison, first record CPU topology, affinity and cgroup limits: host core count
and the container's quota are different quantities. Then use the same provider versions and
authorized sample on both machines, comparing tracks/hour, provider times, network errors and
the change in throttling counters. Keep the production queue paused until an explicit resume.

Set `ACQUISITION_IMAGE` to the tested image ID and `ACQUISITION_SECCOMP` to the existing
Chromium profile. Optional settings are `ACQUISITION_JAMENDO_ID`,
`ACQUISITION_NORMALIZATION_CATALOG` and `ACQUISITION_SOURCE_CATALOG`. Catalogs are mounted
read-only. Without explicit catalog settings the CLI looks for the usual catalog filenames
inside the music directory. Run only playlists for which the configured source rights apply.

`ACQUISITION_SOUNDCLOUD_CLIENT_ID` optionally mounts the public SoundCloud client ID file
read-only. Prepare it with `local-music-acquire soundcloud-client-id --output PATH` in a network
where the SoundCloud homepage is reachable, then transfer it with mode `0600`. This is portable
source configuration, independent of playlists; refresh it if SoundCloud rotates the web client.

Add `--check-runtime` for a configuration check without starting Chromium or downloading.
Use a separate queue for a new or corrected playlist. Existing queues retain their saved
budgets and receipts. Files from the legacy downloader must be reconciled separately before
using their full playlist as new queue input.

## On-demand Xray for acquisition

Proxy use is opt-in per provider: `--yt-dlp-requires-proxy`,
`--soundcloud-requires-proxy`, and `--bandcamp-requires-proxy`. Search, metadata,
and audio requests for each selected provider use the same SOCKS5 transport.
External network downloaders (including implicit FFmpeg HLS fallback) are rejected
for proxied requests, because FFmpeg cannot honor this SOCKS transport. Unsupported
streams fail through the provider's normal error path; local FFmpeg processing remains available.
Jamendo, Hitmo and Yandex retain their direct transports. No system proxy,
TUN, routing, iptables or proxy environment variables are configured.

```sh
local-music-acquire /input/playlist.txt --output-dir /music --queue-dir /queue \
  --disable-jamendo --disable-hitmo --disable-yt-dlp \
  --enable-soundcloud --soundcloud-rights-confirmed --soundcloud-requires-proxy \
  --enable-bandcamp --bandcamp-rights-confirmed --bandcamp-requires-proxy \
  --xray-binary /usr/local/bin/xray \
  --xray-config /usr/local/etc/xray/config.json \
  --xray-proxy-url socks5h://127.0.0.1:10808 \
  --xray-startup-timeout 15 --xray-idle-timeout 60 --xray-stop-timeout 5
```

The CLI creates one manager without launching Xray. The first selected provider
starts the existing binary with `run -config PATH` and waits for the configured
loopback port. Concurrent provider calls hold leases on that child process.
The last lease starts the idle timer; a new lease cancels it and reuses Xray.
CLI exit stops the child immediately, without waiting for the idle timeout.
SIGINT/SIGTERM stop scheduling and allow bounded active work to finish before
cleanup. A child ignoring termination is killed by its retained process handle
after the stop timeout and reaped. Uncatchable SIGKILL cannot run Python cleanup;
the container's init/runtime owns final process cleanup in that case.

An external listener is rejected with `xray_port_in_use`; it is never adopted or
killed. Startup/readiness failures become stable errors of the affected provider
and follow the existing per-track fallback/retry policy. Binary/config paths and
raw child output are absent from diagnostics. The existing Xray config is never
printed, copied into the image or rewritten. `--check-runtime` checks file
availability without starting Xray or making network requests.

The Docker launcher and systemd environment file accept:

```ini
ACQUISITION_PROXY_PROVIDERS=soundcloud,bandcamp
ACQUISITION_XRAY_BINARY=/usr/local/bin/xray
ACQUISITION_XRAY_CONFIG=/path/to/existing/xray-client.json
ACQUISITION_XRAY_PROXY_URL=socks5h://127.0.0.1:10808
ACQUISITION_XRAY_STARTUP_TIMEOUT=15
ACQUISITION_XRAY_IDLE_TIMEOUT=60
ACQUISITION_XRAY_STOP_TIMEOUT=5
ACQUISITION_ENABLE_SOUNDCLOUD=1
ACQUISITION_ENABLE_BANDCAMP=1
```

For YouTube add `yt_dlp` to the list and set `ACQUISITION_ENABLE_YOUTUBE=1`.
The existing source rights flags remain required. `ACQUISITION_XRAY_ASSETS` may
point to an existing directory with geo assets if the config uses them.
Binary, config and assets are mounted read-only. The existing container UID must
be able to execute the binary and read the protected config. File-based Xray log
destinations must be disabled in the supplied config or point to writable tmpfs.
Xray runs inside the downloader container, so its `127.0.0.1:10808` is in the same
network namespace as providers; no host networking or port publication is needed.

During deployment, retire only the old manually managed SOCKS client and its
startup mechanism, if any. A separate Xray server may use another config and must
remain independent. This code does not control systemd or terminate external
Xray processes. For native execution, the old client must release the configured
port. Select the existing client config with the loopback SOCKS inbound; do not
assume the default systemd Xray config belongs to that client.

Implementation references: [Xray command line](https://xtls.github.io/en/document/command),
[Requests SOCKS and remote DNS](https://requests.readthedocs.io/en/stable/user/advanced/#socks).
