# AutPlay - техническое задание

**Статус:** архитектурное ТЗ после технического review  
**Версия:** Draft 0.3  
**Рабочее название:** AutPlay  
**Основной клиент первой версии:** Android  
**Основная production-платформа сервера:** Linux, headless, x86_64  
**Кроссплатформенность серверного ядра:** Linux / Windows / macOS без обязательной зависимости от GPU  
**Доступный ускоритель домашнего сервера:** NVIDIA GeForce RTX 3060 12 GB, опциональный  
**Целевая аудитория:** владелец системы и небольшой круг доверенных пользователей

---

## Статус требований и терминология

В этом документе используются уровни обязательности:

- **MUST** - обязательное требование;
- **SHOULD** - рекомендуемое требование, отступление требует обоснования;
- **MAY** - необязательное расширение.

Если уровень явно не указан, формулировки «должен» и «необходимо» трактуются как MUST, а «желательно», «может» и «в перспективе» - как SHOULD или MAY по контексту.

ТЗ фиксирует границы продукта, ключевые архитектурные решения и проверяемые требования. Детальные схемы БД, протоколы синхронизации, OpenAPI, форматы событий и модели угроз должны выпускаться отдельными версионируемыми спецификациями и не дублироваться в коде без единого источника истины.

---

## Основные изменения Draft 0.3

- исправлена модель идентичности: Recording, Release, ReleaseTrack, AudioVariant и VaultObject разделены;
- fingerprint перестал быть единственным автоматическим merge key;
- production deployment оставлен на Linux, server core зафиксирован кроссплатформенным;
- NVIDIA RTX 3060 12 GB выделена в опциональный изолированный GPU-worker;
- добавлены model registry, benchmark gate и versioned embeddings;
- уточнены CAS, atomic ingest, quarantine и безопасный garbage collection;
- формализованы Offline Journal, idempotency, sync cursor, tombstone и conflict resolution;
- добавлены job leases/checkpoints, API versioning и HTTP Range streaming;
- введены SLO, observability, backup RPO/RTO, security и test strategy;
- добавлены product-функции из review практик Spotify, Яндекс Музыки и self-hosted ecosystem.
- добавлено post-MVP продуктовое направление AutPlay Face без запуска реализации.

---

# 1. Концепция проекта

AutPlay - local-first музыкальная платформа для хранения, поиска, загрузки, воспроизведения, восстановления, анализа и обмена музыкальными коллекциями.

По пользовательскому сценарию приложение должно быть похоже на Spotify, Яндекс Музыку и аналогичные сервисы, но принципиально отличаться моделью владения данными.

Основная идея:

> Музыкальная библиотека принадлежит пользователю, а внешние музыкальные сервисы и сайты являются только источниками данных.

Пользователь не должен потерять свою музыкальную коллекцию из-за:

- удаления трека сторонним сервисом;
- прекращения работы сервиса;
- региональной блокировки;
- изменения API;
- политических или коммерческих ограничений;
- временного отсутствия интернета;
- временной недоступности домашнего сервера;
- потери или замены телефона;
- удаления конкретного внешнего источника.

AutPlay должен хранить достаточно информации для восстановления библиотеки даже в случае отсутствия части физических аудиофайлов.

Ключевое ограничение проекта:

> Отказ внешнего источника, ML-модели, GPU, VPN или отдельного фонового worker не должен блокировать локальное воспроизведение уже доступной музыки.

---

# 2. Основные архитектурные принципы

## 2.1. LOCAL-FIRST

Android-приложение должно оставаться полноценным музыкальным проигрывателем без подключения к домашнему серверу.

Без сервера должны работать:

- локальная библиотека;
- воспроизведение локальной музыки;
- локальный поиск;
- плейлисты;
- история;
- пользовательские настройки;
- локальные fingerprints;
- уже рассчитанные embeddings;
- локальные рекомендации;
- импорт локальной музыки;
- экспорт профиля;
- работа с ранее скачанными Track.

---

## 2.2. SERVER-OPTIONAL

Домашний сервер расширяет возможности AutPlay, но не является обязательным для базового функционирования Android-приложения.

Система должна поддерживать режимы:

### Standalone

```text
Android
```

### Personal Server

```text
Android
   |
AutPlay Server
```

### Multi-user Server

```text
             AutPlay Server
              /    |    \
             /     |     \
          User A User B User C
```

---

# 3. AutPlay и VPN

VPN не является компонентом AutPlay.

VPN представляет собой отдельную программу или сервис, который в будущем будет работать на том же физическом домашнем сервере.

Предварительная схема:

```text
Home Server
|
+-- AutPlay
|   |
|   +-- Backend API
|   +-- Music Vault
|   +-- Database
|   +-- Streaming
|   +-- Wave
|   +-- Workers
|   +-- ML services
|
+-- VPN Service
|
+-- Other independent services
```

AutPlay:

- не управляет VPN;
- не хранит его настройки;
- не запускает VPN;
- не контролирует состояние VPN;
- не зависит от конкретной реализации VPN;
- не должен прекращать работу при остановке VPN.

VPN и AutPlay используют общую серверную машину, но являются функционально независимыми системами.

---

# 4. Сетевая архитектура

С точки зрения AutPlay соединение с сервером представляет собой стандартное сетевое соединение:

```text
HTTPS API
WebSocket
HTTP(S) Streaming
```

AutPlay не должен зависеть от того, каким образом клиент получил маршрут до сервера:

```text
LAN
Wi-Fi
VPN
Internet
Reverse Proxy
MikroTik routing
```

Пример инфраструктуры:

```text
Internet
   |
MikroTik
   |
Home Server
   |
   +-- AutPlay
   |
   +-- VPN
   |
   +-- Other Services
```

В перспективе для AutPlay может быть открыт отдельный защищенный порт через MikroTik.

При прямом доступе из интернета обязательны:

- TLS;
- authentication;
- authorization;
- rate limiting;
- безопасное управление токенами;
- ограничение административных API;
- журналирование событий безопасности.

---

# 5. Изоляция серверных сервисов

Желательно контейнеризировать компоненты.

Например:

```text
Docker / Podman
|
+-- autoplay-api
+-- autoplay-worker-cpu
+-- autoplay-ml-gpu        optional profile
+-- autoplay-db
+-- autoplay-vault
+-- autoplay-stream
+-- autoplay-observability optional profile
|
+-- vpn-service
|
+-- other-services
```

Перезапуск AutPlay не должен нарушать работу VPN.

Перезапуск VPN не должен нарушать работу AutPlay.

GPU device MUST передаваться только `autoplay-ml-gpu`. Persistent volumes AutPlay и VPN не должны пересекаться.

---

# 6. Общая архитектура AutPlay

```text
External Sources
       |
       v
Source Adapters
       |
       v
Search / Resolution
       |
       v
Ingest Pipeline
       |
       v
+----------------------+
|     Music Vault      |
|                      |
| Audio                |
| Metadata             |
| Fingerprints         |
| Embeddings           |
| Source history       |
| Canonical variants   |
+----------+-----------+
           |
      AutPlay Server
           |
      REST / WS / HTTP
           |
    +------+------+
    |             |
 Android A     Android B
    |             |
 Local DB      Local DB
 Local Audio   Local Audio
```

Долгие и ресурсоемкие операции не выполняются внутри API request:

```text
API / Scheduler
      |
 PostgreSQL Job Queue
      |
  +---+----------------+
  |                    |
CPU Workers       GPU ML Worker
  |                    |
Ingest/Import     Embeddings/Tags
```

Core ingest завершается до необязательного ML enrichment.

---

# 7. Основные компоненты

AutPlay должен быть разделен на независимые модули:

1. Android Client.
2. Local Library.
3. Audio Player.
4. Source Adapter System.
5. Search Engine.
6. Download Manager.
7. Music Vault.
8. Ingest Pipeline.
9. Fingerprint Engine.
10. Metadata Engine.
11. Embedding Engine.
12. Recommendation Engine.
13. Taste Profile.
14. Playlist Engine.
15. Sync Engine.
16. Offline Journal.
17. Library Migration.
18. Release Watcher.
19. Wave Coordinator.
20. Streaming Service.
21. Sharing.
22. Backup and Recovery.
23. Web UI в будущих версиях.
24. Job Scheduler and Worker Pool.
25. GPU ML Worker.
26. Observability and Audit.
27. Administration CLI / Minimal Admin UI.

---

# 8. Базовая модель данных

Основной принцип:

> Track != Audio File.

Логическая аудиозапись, ее место в конкретном релизе и физический файл являются разными сущностями.

Целевая модель:

```text
Work (optional musical composition)
  |
Recording / Track
  |
  +-- ReleaseTrack --> Release --> ReleaseGroup
  |
  +-- AudioVariant --> VaultObject
```

- `Work` - музыкальное произведение как композиция; MAY и не требуется для MVP.
- `Recording` - конкретная студийная, концертная, remix, radio edit или иная аудиозапись.
- `Track` - пользовательское имя `Recording` внутри AutPlay; в API и БД термин должен быть определен однозначно.
- `ReleaseGroup` - концептуальный альбом, сингл или EP.
- `Release` - конкретное издание с датой, страной, лейблом и barcode.
- `ReleaseTrack` - позиция Recording на конкретном диске/релизе, с номером и отображаемым названием.
- `AudioVariant` - физическое кодирование Recording.
- `VaultObject` - неизменяемый blob в content-addressable storage.

Такое разделение MUST предотвращать ошибочное объединение концертной версии, ремикса, ремастера и обычной студийной записи, а также не дублировать одну Recording только потому, что она вошла в сингл и альбом.

---

# 9. Track / Recording

Track представляет конкретную логическую аудиозапись. До появления отдельного `Work` он не должен трактоваться как абстрактная музыкальная композиция.

Пример:

```text
track_id: UUID

title
artists[]
duration
genres[]
isrc[]
recording_kind
disambiguation
explicit
created_at
updated_at
```

Track может существовать даже если физического аудиофайла сейчас нет.

Альбом, номер диска, номер дорожки, дата конкретного издания и обложка релиза MUST храниться через `Release` / `ReleaseTrack`, а не как единственные поля Track.

---

# 10. AudioVariant

Один Track / Recording может иметь несколько физических представлений одной и той же записи.

Например:

```text
Track
|
+-- FLAC
+-- MP3 320
+-- MP3 192
+-- AAC
+-- Opus
```

AudioVariant содержит:

```text
variant_id
track_id
vault_object_id

codec
container
bitrate
bit_depth
sample_rate
channels
duration

file_size
sha256
fingerprint

source
source_url

quality_score
validation_status

created_at
```

Ремастер, radio edit, live, remix или materially different duration не должны автоматически становиться AudioVariant исходного Track только из-за похожего названия.

Физическое размещение относится к VaultObject:

```text
vault_object_id
sha256
size
storage_backend
storage_key
commit_status
created_at
```

`storage_key` генерируется сервером из content hash и не зависит от исходного filename.

---

# 11. Canonical Audio Variant

Для Track может быть назначен эталонный физический вариант:

```text
Track
|
+-- Canonical: FLAC
+-- MP3 320
+-- Opus
```

Эталонная версия выбирается по политике качества.

Предварительный порядок:

1. соответствие нужной аудиозаписи;
2. fingerprint;
3. целостность файла;
4. надежность происхождения;
5. lossless;
6. качество кодирования;
7. bitrate.

Появление более качественного варианта может изменить Canonical Variant.

---

# 12. Идентификаторы и безопасное сопоставление

Необходимо разделять уровни идентификации.

## Internal UUID

Отдельный логический идентификатор каждой сущности AutPlay: Recording, Release, ReleaseTrack, AudioVariant и VaultObject.

## External ID

Идентификатор всегда хранится вместе с namespace источника:

```text
provider
entity_type
external_id
market
fetched_at
```

Одинаковые строки ID разных сервисов не считаются одинаковой сущностью.

## ISRC

Сильный metadata-сигнал для Recording, но не абсолютный идентификатор файла или мастеринга. Один Track MAY иметь несколько известных ISRC с provenance.

## SHA-256

Идентификатор конкретного файла.

Два разных кодирования одной композиции будут иметь разные SHA-256.

## Audio Fingerprint

Сигнал сходства и кандидат на идентификацию near-identical audio.

```text
FLAC ---\
MP3 -----+--> Decode -> PCM -> Fingerprint
AAC ----/
```

Fingerprint должен позволять распознавать одну и ту же запись при:

- разных codec;
- разных bitrate;
- разных filenames;
- разных ID3 tags.

Fingerprint MUST NOT использоваться как единственное безусловное основание для автоматического merge. Например, Chromaprint оптимизирован для near-identical audio и высокой скорости поиска, а не для универсального распознавания всех музыкальных версий.

Финальное решение сопоставления должно учитывать:

1. exact SHA-256 для идентичного файла;
2. fingerprint similarity и длительность;
3. source-specific ID и ISRC;
4. artist/title/version metadata;
5. признаки live/remix/remaster/edit;
6. измеренный confidence и пороги;
7. ручное подтверждение для пограничных случаев.

Все автоматические merge MUST быть обратимыми через операции `merge`, `split` и audit trail.

---

# 13. Metadata

Для Track / Recording желательно хранить:

- title;
- artists;
- genre;
- duration;
- ISRC при наличии;
- recording kind;
- version/disambiguation;
- explicit;
- BPM при наличии;
- lyrics reference.

Для Release / ReleaseTrack:

- release group;
- release title;
- album artists;
- country;
- release date и precision даты;
- label и catalog number;
- barcode;
- medium/disc number;
- track number и position;
- credited title/artists;
- artwork references.

Для AudioVariant:

- codec;
- bitrate;
- sample rate;
- channels;
- filesize;
- SHA-256;
- fingerprint;
- source URL;
- source adapter;
- original filename;
- download time.

Нормализованное значение MUST храниться отдельно от исходного значения источника. Исправление metadata не должно уничтожать raw provenance.

---

# 14. История происхождения

Для каждого Track / AudioVariant должна сохраняться provenance history.

Например:

```text
Source A
URL A
downloaded_at

Source B
URL B
found_at

Source C
external_id
```

Один Track может иметь несколько известных источников.

Это необходимо для последующего восстановления.

Минимальная запись provenance:

```text
provenance_id
entity_type
entity_id
provider
external_id
source_url
adapter_name
adapter_version
observed_at
retrieved_at
rights_capability
raw_metadata_hash
confidence
```

`source_url` и raw metadata могут содержать чувствительные данные. Они MUST быть исключены из обычных логов и экспорта, если пользователь явно не включил их.

---

# 15. Локальная Android-библиотека

Android должен поддерживать:

- выбор каталогов;
- сканирование музыки;
- импорт существующих файлов;
- отслеживание локальных AudioVariant;
- извлечение metadata;
- fingerprints;
- hashes;
- embeddings;
- поиск;
- фильтрацию;
- сортировку;
- плейлисты;
- поиск отсутствующих файлов.

---

# 16. Player

Необходимые возможности:

- Play;
- Pause;
- Previous;
- Next;
- Seek;
- Queue;
- Shuffle;
- Repeat Track;
- Repeat Playlist;
- background playback;
- lock-screen control;
- notification control;
- Bluetooth control.

Дополнительно:

- gapless playback;
- crossfade;
- ReplayGain;
- loudness normalization по ReplayGain / EBU R128 с защитой от clipping;
- sleep timer.

Player также должен поддерживать:

- сохранение текущей queue как versioned snapshot;
- восстановление queue и позиции после process death;
- перенос `Continue Listening` между устройствами без блокировки offline-сценария;
- deterministic shuffle по seed;
- `Pure Random` и `Fresh Shuffle`;
- pin/download for offline;
- отдельный управляемый offline cache с квотой.

---

# 17. Поиск внешней музыки

Пользователь вводит:

```text
Track title
Artist + Track
или произвольный запрос
```

AutPlay выполняет поиск по включенным Source Adapter.

Результат должен содержать:

- название;
- автора;
- альбом;
- длительность;
- источник;
- формат;
- bitrate;
- размер;
- качество совпадения.

---

# 18. Source Adapter System

Каждый внешний источник реализуется как независимый адаптер.

Концептуальный интерфейс:

```text
MusicSource
|
+-- search()
+-- getMetadata()
+-- resolve()
+-- getDownloadOptions()
+-- download()
```

Для каждого адаптера настраиваются:

- Enabled;
- Priority;
- timeout;
- retry;
- минимальное качество;
- допустимые форматы.

Adapter manifest MUST объявлять capabilities:

```text
METADATA_SEARCH
METADATA_RESOLVE
USER_EXPORT_IMPORT
AUTHORIZED_STREAM
AUTHORIZED_DOWNLOAD
ARTWORK
LYRICS
```

Наличие metadata URL не означает право на скачивание audio. Download Manager вызывает `download()` только для адаптера и объекта с разрешенной capability.

Адаптеры должны иметь собственные timeout, bounded concurrency, retry/backoff, circuit breaker, health state и кэш. Ошибка или изменение одного источника не должно замедлять остальные источники и Vault.

Изменение одного сайта не должно требовать изменения Search Engine или Vault.

Использование источников должно соответствовать правам пользователя и правилам соответствующих ресурсов. Архитектура не должна строиться вокруг обхода DRM.

---

# 19. Приоритет получения Track

## При скачивании на Android при доступном сервере

```text
Music Vault
     |
     | not found
     v
External Sources
     |
     v
Server Ingest
     |
     v
Music Vault
     |
     v
Android
```

Vault имеет более высокий приоритет для получения однородных и уже проверенных файлов.

---

# 20. Приоритет воспроизведения

```text
1. Local Android
2. Music Vault stream
3. Optional external recovery
```

---

# 21. Music Vault

Music Vault - серверное долговременное хранилище музыкальной коллекции.

В пользовательской терминологии может называться:

> Черный ящик.

Vault хранит:

- AudioVariant;
- Track;
- fingerprints;
- SHA-256;
- metadata;
- embeddings;
- обложки;
- provenance;
- canonical variants;
- связи с пользовательскими библиотеками.

---

# 22. Content-addressable storage и дедупликация

Если одну композицию используют несколько пользователей, физический файл не должен копироваться без необходимости.

```text
              Track
                |
          Canonical Audio
            /       \
           /         \
      User A       User B
```

Пользовательские библиотеки должны хранить ссылки на общие Track.

Физический blob MUST адресоваться SHA-256 содержимого и быть неизменяемым после commit.

Безопасная запись:

```text
staging upload
  -> size/hash/decode validation
  -> atomic move into CAS
  -> database transaction creates reference
```

Требования:

- частично загруженный файл не виден как готовый VaultObject;
- повторная доставка того же blob идемпотентна;
- путь хранения не формируется из пользовательского filename;
- reference count не является единственным источником истины и может быть пересчитан;
- удаление blob выполняется только после grace period и повторной проверки ссылок;
- перед окончательным удалением используется quarantine;
- ACL проверяется по пользовательской связи с Track, а не по знанию hash или URL;
- derived-файлы transcoding хранятся в отдельном кэше и могут быть безопасно пересозданы.

---

# 23. Ingest Pipeline

Любой новый аудиофайл, поступающий в Vault, проходит:

```text
Audio
 |
Validation
 |
Decode test
 |
Metadata extraction
 |
SHA-256
 |
Fingerprint
 |
Duplicate detection
 |
Canonical matching
 |
Metadata normalization
 |
Loudness analysis
 |
Commit immutable VaultObject
 |
Create AudioVariant
 |
Queue optional ML enrichment
 |
Embedding / mood / tags
```

После этого файл считается полноценным AudioVariant.

Критический ingest до создания AudioVariant MUST выполняться без GPU. Embedding и другие производные ML-данные не должны блокировать доступность валидного аудиофайла.

Каждый шаг pipeline должен иметь:

- version;
- idempotency key;
- status;
- attempt count;
- lease/heartbeat для worker;
- checkpoint;
- structured error;
- retry policy;
- возможность продолжения после падения процесса.

Повторный запуск pipeline не должен создавать второй Track, AudioVariant или VaultObject.

---

# 24. Работа при недоступном сервере

Если сервер недоступен:

```text
External Source
       |
       v
Android Download
       |
       v
Local Ingest
       |
       v
Local Library
       |
       v
Offline Journal
```

Пользователь продолжает пользоваться приложением как обычно.

---

# 25. Offline Journal

Все операции, которые необходимо позднее передать серверу, записываются в устойчивый журнал.

Пример:

```text
event_id
device_id
user_id
aggregate_type
aggregate_id
device_sequence
occurred_at
type
schema_version
payload
idempotency_key
status
retry_count
```

Примеры событий:

```text
TRACK_ADDED
TRACK_REMOVED
TRACK_METADATA_UPDATED

TRACK_NEEDS_VAULT_INGEST
TRACK_FILE_REMOVED

PLAYLIST_CREATED
PLAYLIST_UPDATED
PLAYLIST_DELETED

LIKE_ADDED
LIKE_REMOVED

FINGERPRINT_CREATED
EMBEDDING_CREATED
```

Журнал должен переживать:

- перезапуск приложения;
- отсутствие интернета;
- отсутствие сервера;
- перезагрузку телефона.

Семантика доставки - at-least-once. Сервер MUST дедуплицировать события по `event_id` / `idempotency_key` и возвращать подтвержденный sync cursor. Клиент удаляет или архивирует событие только после ACK.

События удаления MUST передаваться tombstone-записями с retention period. Физическое исчезновение строки не является корректной синхронизацией удаления.

---

# 26. Автоматическое пополнение Vault после offline-загрузок

При скачивании Track во время недоступности сервера Android сохраняет:

```text
Track metadata
Source URL
Source adapter
Downloaded at
Fingerprint
SHA-256
Codec
Bitrate
Duration
Local file URI
```

После появления сервера:

```text
Pending Track
     |
     v
Vault Lookup
    / \
   /   \
MATCH  MISSING
 |       |
Link    Upload file
         |
         v
       Ingest
```

Если файл с телефона уже удален, сервер должен попытаться повторно получить Track по сохраненному SourceReference.

Если восстановление временно невозможно:

```text
PENDING_RESTORE
```

---

# 27. Every Acquisition -> Vault

Базовый принцип системы:

> Любой успешно полученный пользователем Track должен со временем быть предложен Music Vault.

Источником нового Track может быть:

- Android;
- сервер;
- Library Migration;
- локальный импорт;
- Wave;
- восстановление backup.

---

# 28. Политика качества

Пользователь может выбрать профиль.

Например:

### Storage Saver

Минимальный размер.

### Mobile High

Высокое качество для телефона.

### Maximum

Максимально доступное качество.

### Original

Использование оригинального Vault AudioVariant.

Дополнительные параметры:

```text
Preferred codec
Preferred bitrate
Minimum bitrate
Prefer lossless
Maximum file size
```

---

# 29. Upgrade локального файла

После синхронизации сервер может определить, что Vault содержит более качественную версию.

Режимы:

```text
Never replace local files

Upgrade lower-quality files

Always use preferred Vault variant
```

Предварительный default:

```text
Upgrade lower-quality files
```

---

# 30. Импорт локальной музыкальной коллекции

Пользователь выбирает один или несколько каталогов.

Для каждого файла:

1. прочитать metadata;
2. рассчитать SHA-256;
3. рассчитать fingerprint;
4. проверить локальные дубликаты;
5. проверить Vault;
6. создать Track / AudioVariant;
7. добавить в библиотеку;
8. поставить расчет embedding в background queue.

Ошибка или отсутствие embedding не отменяет успешный импорт и воспроизведение файла.

---

# 31. Library Migration

AutPlay должен поддерживать массовый перенос существующей библиотеки из сторонних музыкальных сервисов.

Пользователь может загрузить экспорт своей коллекции.

Поддерживаемые виды данных:

- HTML;
- CSV;
- JSON;
- M3U;
- M3U8;
- другие структурированные форматы.

Приоритет способов миграции:

1. официальный пользовательский экспорт;
2. официальный OAuth/API с минимальными read-only scopes;
3. локальный HTML/CSV/JSON, полученный пользователем;
4. ручной Generic Import mapping.

Undocumented/private API не должен быть единственной реализацией импорта конкретного сервиса.

---

# 32. Library Import Adapters

Модульная архитектура:

```text
LibraryImporter
|
+-- SpotifyImporter
+-- YandexMusicImporter
+-- AppleMusicImporter
+-- YouTubeMusicImporter
+-- GenericHTMLImporter
+-- CSVImporter
+-- JSONImporter
+-- M3UImporter
```

Поддержка конкретного сервиса определяется доступностью пользовательского экспорта и его форматом.

Каждый importer MUST иметь manifest:

```text
adapter_id
adapter_version
input_schema_versions[]
capabilities[]
auth_type
rate_limit_policy
rights_policy
fixture_version
```

Для каждого поддерживаемого формата нужны обезличенные golden fixtures и contract tests. Неизвестные поля должны игнорироваться безопасно, а несовместимое изменение формата - завершаться понятным `UNSUPPORTED_SCHEMA`, а не частичным тихим импортом.

Исходный файл импорта SHOULD сохраняться неизменяемым до завершения job либо до явного удаления пользователем. Отдельно сохраняются его SHA-256, parser version и отчет.

---

# 33. Что импортируется

При наличии данных необходимо переносить:

- title;
- artist;
- album;
- duration;
- source service;
- source track ID;
- URL;
- liked/favorite;
- playlists;
- playlist order;
- duplicate playlist entries;
- added_at;
- original playlist name/description/visibility;
- original position;
- доступную metadata.

Порядок и повтор одного Track в плейлисте являются пользовательскими данными и не должны исчезать из-за дедупликации каталога.

---

# 34. Vault-first Library Migration

Каждый Track из импорта обязательно сначала проверяется в Vault.

```text
Imported Track
     |
Normalize metadata
     |
Vault Search
    / \
   /   \
MATCH  NO MATCH
 |       |
Link   External Search
         |
       Download
         |
        Ingest
```

Запрещено без необходимости искать и получать внешний файл, если нужный Track уже находится в Vault.

---

# 35. Metadata Matching

На этапе Library Migration fingerprint отсутствует.

Поэтому используется последовательность:

1. source-specific ID;
2. ISRC;
3. artist + title + album;
4. artist + title + duration;
5. normalized metadata;
6. fuzzy match.

Результат:

```text
MATCH
NO_MATCH
AMBIGUOUS
```

Также сохраняется:

```text
Match Confidence
```

Matching должен быть двухэтапным:

1. candidate generation по external ID, ISRC, индексам metadata и длительности;
2. scoring/ranking кандидатов с объяснимыми причинами совпадения.

Порог `AUTO_MATCH`, диапазон `REVIEW_REQUIRED` и порог `NO_MATCH` определяются на размеченном validation-наборе. Значения не должны быть зашиты без benchmark.

Отчет для неоднозначной записи должен показывать сравниваемые поля и причину confidence. Пользователь может подтвердить, отклонить, объединить или разделить сущности; решение сохраняется как reusable mapping rule для повторного импорта.

---

# 36. Финальный fingerprint match

Если файл пришлось получить извне, перед помещением в Vault выполняется fingerprint.

Может обнаружиться:

```text
Metadata Match: NO MATCH

Audio Fingerprint:
Existing Track found
```

Тогда новый Track не создается, а файл связывается с существующей записью.

---

# 37. Предварительный отчет Library Migration

Перед фактической materialization пользователь должен увидеть:

```text
Found in export:          2483
Already in library:        414
Available in Vault:       1526
Need external resolution:  491
Ambiguous:                  38
Not recognized:             14

Playlists:                  12
```

---

# 38. Режимы Library Migration

## Library Only

Импортируется структура библиотеки без массовой загрузки файлов.

## Library + Materialize

AutPlay пытается физически наполнить коллекцию.

```text
Imported Track
     |
Vault
     |
External Search if missing
     |
Vault Ingest
```

---

# 39. Выбор materialization

Пользователь выбирает:

```text
Everything
Liked only
Selected playlists
Nothing
```

Отдельно сервер может получать недостающие Track в Vault согласно своей политике.

---

# 40. Повторный импорт

Library Migration должна быть:

- idempotent;
- resumable;
- pausable;
- crash-resistant.

Повторный импорт одного файла не должен создавать вторую копию всей библиотеки.

Для API-адаптеров SHOULD использоваться provider cursor/version, если он доступен. Например, версия плейлиста может предотвращать повторное полное чтение неизменившегося списка.

Обязательны pagination, bounded concurrency, exponential backoff с jitter и уважение `Retry-After`. Состояние страницы/cursor сохраняется в checkpoint ImportJob.

---

# 41. Объединение библиотек

Должно поддерживаться:

```text
Spotify
+
Yandex Music
+
Local files
+
M3U
+
AutPlay profile
```

Результат:

```text
One User Library
```

Track дедуплицируются, но плейлисты и информация об источниках сохраняются.

---

# 42. Статусы доступности

LibraryEntry должен иметь состояния:

```text
AVAILABLE_LOCAL
AVAILABLE_VAULT
AVAILABLE_EXTERNAL

PENDING_SEARCH
PENDING_RESTORE

NOT_FOUND
AMBIGUOUS
```

---

# 43. Экспорт профиля

AutPlay должен экспортировать библиотеку пользователя без обязательной передачи самих аудиофайлов.

Профиль включает:

- Track references;
- metadata;
- playlists;
- Likes / Dislikes;
- source references;
- settings;
- preference profile;
- taste clusters;
- recommendation settings;
- model versions;
- import history.

Опционально:

- embeddings;
- fingerprint index.

---

# 44. Импорт профиля

После импорта профиля:

```text
Profile
  |
Vault Lookup
  |
Link available Track
  |
Resolve missing Track
```

Это позволяет переносить пользовательский профиль между устройствами.

---

# 45. Плейлисты

Пользователь может:

- создать;
- удалить;
- переименовать;
- редактировать;
- сортировать;
- менять порядок;
- добавлять Track;
- удалять Track;
- копировать;
- делиться.

---

# 46. Автоматические плейлисты

Предварительные категории:

```text
Энергичное
Веселое
Спокойное
Грустное
Фоновое
```

Activity:

```text
Workout
Cycling
Work
Coding
Driving
Sleep
Background
```

---

# 47. Автоматическое распределение новых Track

После загрузки Track анализируется.

На основе:

- embeddings;
- metadata;
- mood classification;

Track может автоматически попасть в один или несколько стандартных плейлистов.

Функция должна отключаться пользователем.

---

# 48. Smart Playlists

Поддержать динамические правила.

Например:

```text
Genre = Rock
AND
Rating >= 4
AND
LastPlayed > 30 days
```

Примеры:

- Recently Added;
- Often Played;
- Forgotten;
- New Favorites;
- Workout;
- Cycling.

---

# 49. Recommendation Engine

Основной подход:

> Embeddings-first.

Поведение пользователя не должно быть главным источником данных о вкусе.

Причина - действия вроде Skip или Seek могут происходить случайно и иметь внешний контекст.

При этом `embeddings-first` не означает `embeddings-only`. Целевой pipeline:

```text
Candidate generation
  -> hard filters
  -> relevance scoring
  -> diversity/freshness re-ranking
  -> queue construction
  -> explanation/debug record
```

Источники кандидатов:

- nearest neighbors нескольких taste clusters;
- похожие на последние явно выбранные Track;
- related artists/releases;
- forgotten favorites;
- new releases;
- exploration pool;
- controlled random pool.

Hard filters исключают недоступные, запрещенные пользователем, недавно повторенные и несовместимые с выбранным контекстом Track.

---

# 50. Audio Embeddings

Для Track вычисляется embedding:

```text
Audio
 |
 v
Embedding Model
 |
 v
Vector
```

Использование:

- similarity search;
- recommendations;
- clustering;
- discovery;
- mood classification.

Embedding является versioned derived data. Минимальные поля:

```text
embedding_id
track_id
model_id
model_version
weights_sha256
preprocessing_version
pooling_strategy
dimension
dtype
normalized
vector
created_at
```

Для одной Recording MAY одновременно храниться несколько embeddings. Обновление модели выполняется фоново, без удаления старого индекса до завершения миграции и проверки качества.

Предварительные кандидаты для benchmark:

- LAION CLAP - audio/text shared space и natural-language music search;
- MERT 95M/330M - музыкальные representation embeddings;
- Essentia Discogs-EffNet - similarity, style и downstream-теги.

Ни одна модель не фиксируется как production-default до сравнения качества, скорости, VRAM/RAM, лицензии и воспроизводимости на реальной коллекции.

---

# 51. Taste Profile

Профиль вкусов пользователя должен поддерживать несколько кластеров.

Например:

```text
Taste Profile
|
+-- Rock
+-- Metal
+-- Electronic
+-- Ambient
```

Не следует использовать единственный усредненный embedding как единственное представление предпочтений.

Допустимые методы:

- K-Means;
- HDBSCAN;
- другие алгоритмы кластеризации.

Профиль должен поддерживать контексты, которые пользователь может включать явно:

```text
General
Workout
Cycling
Work
Sleep
Party
```

Для playlist, session и отдельного прослушивания требуется функция `Exclude from Taste Profile`. Фоновая музыка, сон, музыка других людей и тестовое прослушивание не должны необратимо искажать общий профиль.

---

# 52. Explicit User Signals

Сильные пользовательские сигналы:

```text
Like
Dislike
```

Слабые:

```text
Skip
Seek
Listen duration
Repeat
```

Слабые сигналы:

- не используются в первой модели;

или

- имеют очень низкий вес.

Каждое событие прослушивания SHOULD содержать:

```text
context
origin: ORGANIC | RECOMMENDED | PLAYLIST | WAVE | SEARCH
recommendation_request_id
position_ms
played_ms
completion_ratio
explicit_feedback
excluded_from_taste
```

Разделение organic и recommendation-driven событий необходимо для уменьшения feedback loop и корректной offline-оценки.

---

# 53. My Wave recommendations

Пример смеси:

```text
40% close to taste clusters
20% neighboring similarity
20% exploration
10% forgotten
10% random discovery
```

Соотношения должны быть конфигурируемыми.

Проценты являются стартовой гипотезой, а не постоянным правилом. Re-ranker должен оптимизировать одновременно:

- relevance;
- artist/album diversity;
- freshness и repeat suppression;
- novelty;
- serendipity;
- плавность перехода;
- выбранную степень exploration.

Нужны два режима shuffle:

- `Pure Random` - воспроизводимый равномерный shuffle по seed;
- `Fresh Shuffle` - выбор наиболее свежей из нескольких случайных последовательностей с понижением недавно прослушанных Track.

Для оценки Recommendation Engine используются temporal split и минимум следующие метрики:

```text
Recall@K
NDCG@K
catalog coverage
intra-list diversity
novelty
repeat rate
Like rate
long-play rate
Skip rate
```

Оптимизация одной engagement-метрики без контроля разнообразия запрещена.

---

# 54. Exploration Mode

Нужно предусмотреть параметр:

```text
Familiar <--------> Experimental
```

Источники discovery:

- nearby embeddings;
- related artists;
- alternative genres;
- forgotten Track;
- new releases;
- random.

---

# 55. Использование LLM

LLM не требуется для основной рекомендательной системы.

В перспективе небольшая локальная модель может использоваться для natural-language запросов.

Например:

```text
"Что-нибудь бодрое, но не слишком агрессивное для вечерней поездки"
```

LLM разбирает запрос, а Track выбираются через:

- embeddings;
- metadata;
- rules.

LLM не получает прямой доступ к файлам, URL и административным операциям. Его результат трактуется как структурированный фильтр/intent и проходит validation. При недоступности LLM поиск и рекомендации продолжают работать.

Natural-language music search в первой реализации SHOULD по возможности использовать общий audio/text embedding space без обязательного запуска LLM.

---

# 56. Главная страница

Предварительные блоки:

```text
Continue Listening

Recently Added

New Releases

Recommended

My Wave

Forgotten Favorites

Playlists
```

---

# 57. Отслеживание новых релизов

Система определяет артистов из библиотеки пользователя.

Пользователь может:

```text
Follow Artist
```

Release Watcher проверяет появление новых релизов через metadata-источники.

Новый релиз появляется на Home.

Действия:

```text
Add
Find
Download
```

Автоматическое скачивание не обязательно.

---

# 58. Watchlist

Можно подписаться на исполнителя независимо от наличия его Track в библиотеке.

---

# 59. Sharing Track

Вместо обязательной отправки аудиофайла передается reference.

Например:

```text
Track metadata
Track ID
Fingerprint
Source references
```

Получатель выполняет локальный или Vault lookup.

---

# 60. Sharing Playlist

Плейлист может передаваться через:

- app link;
- QR;
- JSON;
- файл.

После открытия:

```text
Tracks: 37

Already local: 24
Available in Vault: 8
Missing: 5
```

Действие:

```text
Download missing
```

---

# 61. Wave / Room

Wave - режим синхронного прослушивания несколькими пользователями.

Создатель комнаты является Host.

Host управляет:

- Play;
- Pause;
- Previous;
- Next;
- Seek;
- Queue.

Вход:

- link;
- QR;
- Room Code.

---

# 62. Hybrid Wave Playback

Каждый клиент самостоятельно выбирает источник воспроизведения конкретного Track.

```text
               Track X
              /   |   \
             /    |    \
          User A User B User C
          LOCAL  STREAM LOCAL
```

Если у пользователя есть корректная локальная копия - используется она.

Если ее нет - используется Music Vault Stream.

---

# 63. Проверка доступности Track для Wave

Три уровня.

## При подключении к Room

Первичный inventory / availability check.

## Background preflight

Во время текущего Track проверяются следующие несколько Track.

Например:

```text
Track 15 - LOCAL
Track 16 - LOCAL
Track 17 - STREAM_REQUIRED
```

## Final check

Перед переключением:

- файл существует;
- файл доступен;
- Track соответствует fingerprint.

---

# 64. Wave Prefetch

Если будущего Track нет локально, Android может заранее скачать его.

Режимы:

```text
Off
Next Track
Next 3 Tracks
Aggressive on Wi-Fi
```

Если prefetch завершен до начала композиции:

```text
STREAM_REQUIRED
      |
      v
LOCAL
```

---

# 65. Wave Clock

Воспроизведение должно синхронизироваться по общей временной шкале.

Команда содержит:

```text
track_id
position_ms
start_at_server_time
```

Клиенты заранее:

- получают команду;
- буферизуют stream при необходимости;
- готовят local file;
- запускают воспроизведение в заданное время.

---

# 66. Drift Correction

Клиент периодически сравнивает:

```text
expected_position
actual_position
```

Стратегии:

- маленькая разница игнорируется;
- средняя корректируется небольшим изменением playback speed;
- большая корректируется seek.

Пороговые значения определяются тестами.

---

# 67. Source switching внутри Track

STREAM -> LOCAL или LOCAL -> STREAM во время текущей композиции не является обязательной функцией первой версии.

Предпочтительное правило:

> Источник фиксируется до окончания текущего Track.

---

# 68. Vault fallback для Wave

Если Track отсутствует во внешнем публичном доступе, но имеется в личном Music Vault, он может использоваться для:

- собственного воспроизведения;
- восстановления на устройстве;
- streaming доверенным пользователям в рамках разрешенного использования.

---

# 69. Party Mode

Будущее расширение Wave.

Участники могут:

- предлагать Track;
- добавлять Track в очередь;
- голосовать;
- менять порядок queue.

---

# 70. Backup

Два уровня.

## Profile Backup

- metadata;
- library;
- playlists;
- preferences;
- settings.

## Full Backup

Дополнительно:

- audio;
- fingerprints;
- embeddings;
- Vault metadata.

Поддерживаемые storage backend в перспективе:

- local disk;
- NAS;
- WebDAV;
- external backup storage.

Backup MUST различать:

- critical state: PostgreSQL, profile, playlists, source mappings, provenance, encryption/config references;
- primary blobs: оригинальные VaultObject;
- derived data: transcoding cache, thumbnails, embeddings и поисковые индексы.

Derived data MAY не копироваться, если гарантировано и проверено ее воспроизводимое восстановление.

Минимальные production-цели для домашнего сервера:

```text
Profile/DB RPO: <= 24 h
Profile/DB RTO: <= 4 h
Restore drill: не реже одного раза в квартал
```

Full Backup должен быть консистентным: database snapshot и manifest blob-объектов относятся к одной backup generation. Успешное создание архива без тестового restore не считается доказанным backup.

---

# 71. Restore

После установки на новом телефоне:

```text
Install AutPlay
      |
Connect AutPlay Server
      |
Restore Profile
      |
Vault Lookup
      |
Download required Track
      |
Search remaining Track
```

Пример отчета:

```text
Library:          3482
Vault available:  3105
Recoverable:       347
Unavailable:        30
```

---

# 72. Integrity Check

AutPlay должен уметь проверять:

- file exists;
- decode works;
- SHA-256;
- fingerprint consistency.
- orphan database references;
- unreferenced/quarantined blobs;
- missing embeddings/index entries;
- backup manifest consistency.

Пример:

```text
1246 Track OK
2 corrupted
1 missing
```

---

# 73. Self-healing Library

Если AudioVariant потерян:

```text
Track
 |
Vault
 |
Original Source
 |
Alternative Sources
```

Metadata Track не удаляется.

Пользовательская библиотека сохраняет композицию как логический объект.

Self-healing не должен автоматически заменять Recording похожей, но другой версией. Любой recovery candidate ниже auto-match threshold получает `REVIEW_REQUIRED`.

---

# 74. Автоматическое освобождение места

AutPlay может предлагать удаление локального AudioVariant, не удаляя Track из библиотеки.

Например:

```text
Remove Track not played for 12 months
```

Статус:

```text
AVAILABLE_LOCAL
      |
      v
AVAILABLE_VAULT
```

Позже:

```text
Download / Restore
```

Должны поддерживаться:

- `Pinned` - не удалять автоматически;
- per-user cache quota;
- minimum free-space watermark;
- Wi-Fi/charging constraints;
- preview before bulk eviction;
- запрет eviction последней известной копии без подтвержденного Vault/backup.

---

# 75. Поиск внутри библиотеки

Минимально:

- title;
- artist;
- album;
- genre;
- year;
- playlist.

Перспективно:

```text
artist:muse
genre:rock
year:>2020
rating:>=4
```

---

# 76. Fuzzy Search

Должны учитываться:

- typo;
- case;
- punctuation;
- transliteration.

Например:

```text
linin park
линкин парк
Linkin Park
```

должны приводить к одному релевантному результату.

---

# 77. Lyrics

Будущая функция.

Поддерживать:

- plain lyrics;
- synchronized lyrics.

Полученный текст может кэшироваться локально.

---

# 78. Статистика

Локально хранить:

- listening time;
- top tracks;
- top artists;
- genres;
- discoveries;
- added Track;
- skips.

В перспективе:

```text
AutPlay Year Review
```

---

# 79. Android stack

Предварительно:

```text
Kotlin
Jetpack Compose
Media3
Room
WorkManager
```

Финальный выбор производится после технического прототипа.

---

# 80. Android database

Предварительные сущности:

```text
Track
AudioVariant
Artist
Album
ReleaseGroup
Release
ReleaseTrack

LibraryEntry

Playlist
PlaylistEntry

Fingerprint
Embedding

SourceReference

DownloadTask
SyncEvent
SyncCursor
Tombstone

UserPreference
TasteCluster
ListeningEvent
RecommendationPack

WaveCache
```

Room schema MUST хранить ссылки на локальные file/content URI отдельно от каталожной сущности AudioVariant и поддерживать автоматические migration tests.

---

# 81. Server stack

Предварительный вариант:

```text
Python
FastAPI
SQLAlchemy
PostgreSQL
pgvector
WebSocket
FFmpeg / ffprobe
Chromaprint
```

Причина выбора Python - большое количество будущих задач обработки данных и ML.

Высоконагруженные компоненты при необходимости позднее могут быть реализованы отдельно.

Python dependency и environment workflow SHOULD выполняться через `uv` с lock-файлом. Версии runtime, контейнеров, FFmpeg и ML-моделей должны быть закреплены; production не использует плавающие теги `latest`.

Начальная очередь серверных jobs SHOULD использовать PostgreSQL с `FOR UPDATE SKIP LOCKED`, lease и heartbeat. Отдельный broker (Redis/RabbitMQ/NATS) добавляется только после подтвержденного bottleneck или требования, которое PostgreSQL-queue не закрывает.

Для векторного поиска default:

1. exact cosine search в pgvector на малом каталоге;
2. HNSW после benchmark recall/latency;
3. Spotify Voyager или отдельный vector service рассматривается только при доказанном ограничении pgvector.

---

# 82. Server database

Предварительные сущности:

```text
User
Device

Track
Artist
Album
ReleaseGroup
Release
ReleaseTrack

AudioVariant
Fingerprint
Embedding
EmbeddingModel

Source
SourceReference

VaultObject

UserLibraryEntry

Playlist
PlaylistEntry

TasteCluster

SyncEvent
SyncCursor
Tombstone
IdempotencyRecord

DownloadJob
IngestJob
ImportJob
ImportEntry
JobAttempt
JobLease

WaveRoom
WaveMember
WaveQueueEntry
```

---

# 83. Server components

```text
AutPlay Server
|
+-- API
+-- Authentication
+-- User Management
+-- Device Management
+-- Music Catalog
+-- Music Vault
+-- Search
+-- Source Adapters
+-- Download Queue
+-- Job Scheduler
+-- CPU Worker Pool
+-- GPU ML Worker
+-- Ingest Pipeline
+-- Fingerprint Index
+-- Embedding Index
+-- Recommendation Engine
+-- Sync Engine
+-- Offline Event Resolver
+-- Library Migration
+-- Release Watcher
+-- Wave Coordinator
+-- Streaming Service
+-- Backup
+-- Administration CLI / Minimal Admin UI
+-- Metrics / Logs / Audit
```

---

# 84. API groups

Все новые endpoint публикуются под versioned prefix, предварительно:

```text
/api/v1/auth
/api/v1/devices

/api/v1/library
/api/v1/tracks
/api/v1/artists
/api/v1/albums

/api/v1/playlists

/api/v1/search
/api/v1/downloads

/api/v1/vault
/api/v1/import
/api/v1/sync

/api/v1/recommendations
/api/v1/releases

/api/v1/wave
/api/v1/stream

/api/v1/profile
/api/v1/backup
/api/v1/jobs
/api/v1/admin

/health/live
/health/ready
/metrics
```

API requirements:

- OpenAPI является contract source of truth;
- cursor pagination для изменяемых списков;
- bounded page size;
- idempotency key для повторяемых command/upload endpoint;
- ETag / `If-Match` или resource version для optimistic concurrency;
- единый error envelope с stable machine-readable code;
- request/trace ID;
- backward compatibility policy минимум для текущей и предыдущей mobile API version;
- contract tests Android <-> Server.

Streaming endpoint MUST поддерживать HTTP Range, корректные `Content-Length`, `Content-Type`, `ETag`, `HEAD` и отмену запроса. Direct play предпочтителен. Transcoding включается по профилю клиента, а cache key включает source SHA-256, preset и encoder version.

---

# 85. Download Manager

Состояния:

```text
QUEUED
SEARCHING
RESOLVING
DOWNLOADING
VALIDATING
INGESTING

COMPLETED
FAILED
PAUSED
CANCELLED
RETRY_WAIT
```

Функции:

- pause;
- retry;
- cancel;
- parallel limit;
- Wi-Fi only;
- charging only;
- background jobs.

Для каждой задачи сохраняются progress bytes, expected size, checkpoint/resume capability, attempt history и final error code. Retry выполняется только для классифицированных transient errors.

---

# 86. Import Job

Состояния:

```text
PARSING
MATCHING
VAULT_LOOKUP
SEARCHING
DOWNLOADING
INGESTING

WAITING_SERVER
REVIEW_REQUIRED

COMPLETED
FAILED
PAUSED
CANCELLED
RETRY_WAIT
```

Job worker получает задачу по lease. Если heartbeat пропал, lease истекает и задача безопасно возвращается в очередь. Любое side effect действие должно быть идемпотентным.

---

# 87. Синхронизация

Не использовать модель:

```text
Client DB
   |
overwrite
   |
Server DB
```

Предпочтительно:

```text
Local changes
     |
Event Journal
     |
Incremental Sync
     |
Conflict Resolver
```

Синхронизация MUST использовать:

- transactional outbox на стороне записи;
- inbox/idempotency table на стороне применения;
- monotonic per-device sequence;
- server sync cursor/checkpoint;
- schema version событий;
- batch limits;
- snapshot bootstrap для нового устройства;
- compacting старого журнала только после подтвержденного checkpoint;
- явное обнаружение `DEVICE_RESET` / потерянного локального журнала.

---

# 88. Conflict Resolution

Отдельные правила должны существовать для:

- Likes;
- playlists;
- playlist order;
- metadata changes;
- Track deletion;
- LibraryEntry deletion;
- device offline edits.

Минимальная политика:

- Like/Dislike - последняя явная операция пользователя по server-normalized time, с сохранением event history;
- metadata - provider/raw metadata не перезаписывается пользовательской правкой; canonical override хранится отдельно;
- playlist metadata - optimistic concurrency;
- playlist entries/order - стабильный `entry_id` и sortable position key, а не перезапись всего массива;
- deletion - tombstone побеждает старые offline-события, но может быть отменен новой явной операцией;
- неразрешимый конфликт - `REVIEW_REQUIRED`, без тихой потери данных.

---

# 89. Удаление данных

Необходимо четко различать:

```text
Remove from playlist

Remove from user library

Delete local AudioVariant

Delete server AudioVariant

Delete Track globally
```

`Delete Track globally` является административной операцией с защитой от случайного выполнения.

По умолчанию удаления логические. Физическое удаление VaultObject выполняется асинхронно после grace period, проверки всех user/library/playlist/backup ссылок и записи audit event.

---

# 90. Authentication

Standalone может работать без аккаунта.

Server mode требует:

- User identity;
- Device identity;
- access token;
- refresh token;
- device revoke;
- logout all devices.

Production requirements:

- password hashing Argon2id, если используется пароль;
- короткоживущий access token;
- rotating refresh token, хранить только hash;
- device-bound session metadata;
- RBAC минимум `owner`, `admin`, `user`;
- object-level authorization на каждую библиотеку, playlist, job и stream;
- CSRF-защита cookie-based Web UI;
- секреты вне репозитория и обычных логов;
- audit log для входов, токенов и административных операций.

---

# 91. Privacy

По умолчанию:

```text
Telemetry: OFF
External analytics: OFF
Cloud account: NOT REQUIRED
Recommendation data: PRIVATE
```

История прослушивания, embeddings вкусов и статистика не должны уходить сторонним сервисам без явного разрешения.

---

# 92. Web UI

Отдельное desktop-приложение на первом этапе не требуется.

Полный пользовательский Web UI создается позднее. Однако с этапа Music Vault MUST существовать Administration CLI, а SHOULD - минимальная локальная Admin UI для диагностики и ручного разбора `AMBIGUOUS` / `REVIEW_REQUIRED`.

Функции:

- library management;
- Vault management;
- playlists;
- imports;
- download jobs;
- storage monitoring;
- users;
- ambiguous matches;
- source configuration;
- diagnostics.

---

# 93. Кроссплатформенность

На первом этапе не требуется писать полноценные клиенты для:

- Windows;
- Linux Desktop;
- macOS.

Архитектура:

```text
Android App
+
Cross-platform Server Core
+
Future Web UI
```

позволяет избежать лишней кроссплатформенной разработки.

При необходимости полноценные desktop-клиенты могут появиться позднее.

Матрица поддержки сервера:

- Linux x86_64 - основная production-платформа, Docker/Podman, NVIDIA GPU;
- Windows x86_64 - supported development/test и MAY native personal-server mode;
- macOS - supported development/test для CPU-path;
- ARM64 Linux - MAY после отдельной проверки FFmpeg/Chromaprint/ML dependencies.

Общий server core, domain logic, schema migrations и API tests MUST работать без CUDA. GPU profile является Linux/NVIDIA-ускорением, а не условием кроссплатформенности.

---

# 94. User Interface Android

Основные разделы:

## Home

- Continue Listening;
- Recently Added;
- New Releases;
- Recommendations;
- My Wave;
- Playlists.
- Offline Ready;
- Problems Requiring Attention.

## Search

Вкладки:

```text
Local
Vault
External
```

## Library

- Tracks;
- Artists;
- Albums;
- Playlists;
- Downloads.
- Pinned / Offline;
- Unavailable;
- Duplicates and Merge Review.

## Player

- playback;
- queue;
- Wave status.

## Track

- metadata;
- source;
- quality;
- local status;
- Vault status;
- restore/download.
- recording/release versions;
- metadata provenance and confidence;
- merge/split correction.

## Recommendations

- similar;
- clusters;
- discovery.

## Import

- folders;
- external library;
- profile.

## Vault

- status;
- sync;
- pending ingest.

## Settings

- storage;
- audio;
- sources;
- server;
- recommendations;
- Wave;
- privacy.
- cache quota and proactive offline pack;
- exclude contexts from Taste Profile;
- diagnostics export.

---

# 95. User-visible Sync Status

Обычный интерфейс не должен показывать внутреннюю сложность.

Например:

```text
Synced
```

или:

```text
14 Track waiting for sync
```

Отдельно доступен diagnostic screen.

---

# 96. Производительность

Проектировать минимум на:

```text
10 000 - 100 000 Track
```

в пользовательских metadata.

Серверный Catalog и Vault должны иметь возможность масштабироваться дальше.

Android UI не должен блокироваться во время:

- download;
- fingerprint;
- embedding;
- import;
- synchronization;
- integrity check.

Целевой нагрузочный профиль первой production-версии:

```text
Users: 1-5
Catalog: 100 000 Track
Playlist entries: 1 000 000
Concurrent streams: 5
Concurrent imports: 2
```

Начальные SLO на reference hardware и локальной сети:

- local Android search p95 <= 150 ms после построения индекса;
- server metadata search p95 <= 300 ms при 100 000 Track;
- открытие paginated library page p95 <= 500 ms;
- начало Vault direct stream p95 <= 2 s в LAN;
- API error rate <= 1% без учета явно некорректных запросов и недоступных external providers;
- ingest и import не увеличивают p95 streaming start более чем на 20%;
- приложение не загружает всю библиотеку или все обложки в память одновременно.

Remote streaming SLO определяется отдельно с учетом фактической полосы и latency. GPU throughput не фиксируется до benchmark Stage 0.

Для каждого SLO должны существовать воспроизводимый test dataset, команда benchmark и сохраненный отчет p50/p95/p99.

---

# 97. Background Tasks

В фоне выполняются:

- downloads;
- synchronization;
- fingerprints;
- embeddings;
- Vault reconciliation;
- integrity checks;
- Release Watcher;
- imports.

Android implementation должна учитывать ограничения фоновой работы ОС.

На Android:

- Media3 `DownloadService`/`DownloadManager` используется для управляемых offline media downloads;
- WorkManager - для гарантированно завершаемых отложенных metadata/sync jobs;
- тяжелый foreground transfer показывает notification и поддерживает cancel;
- constraints Wi-Fi, unmetered network, charging и battery level доступны пользователю;
- приложение тестируется в OS background-restricted mode и после process death.

На сервере обязательны отдельные очереди/приоритеты:

```text
P0 playback/stream control
P1 interactive API/search
P2 sync/download
P3 ingest/import
P4 ML enrichment/reindex/integrity
```

Низкоприоритетная GPU/ML-задача не должна вытеснять streaming, database backup или критический ingest.

---

# 98. Нефункциональные требования

AutPlay должен быть:

- offline-first;
- modular;
- extensible;
- crash-resistant;
- recoverable;
- idempotent;
- observable.
- secure by default;
- resource-bounded;
- backward-compatible;
- testable;
- reproducible.

Критические операции должны выдерживать:

- потерю сети;
- падение клиента;
- падение сервера;
- повторный запуск;
- повторную синхронизацию;
- повторный импорт.

Обязательная наблюдаемость:

- structured JSON logs;
- `request_id`, `trace_id`, `job_id`, `user_id` в обезличенном виде;
- metrics по API latency/error, queue depth/age, ingest, storage, stream, sync и GPU;
- liveness/readiness checks;
- audit log административных операций;
- diagnostic bundle с автоматическим удалением token, URL secrets и персональных путей.

Минимальная отказоустойчивость:

- low-disk watermark приостанавливает download/ingest до заполнения диска;
- ошибка GPU переводит ML job в CPU/deferred mode, но не останавливает API;
- external provider failure открывает circuit breaker;
- database migration имеет backup/rollback plan;
- derived index может быть перестроен из source-of-truth данных;
- конфигурация валидируется до запуска сервисов.

Безопасность проверяется по OWASP ASVS и OWASP API Security Top 10. Особое внимание: object-level authorization, SSRF в Source Adapter, parser/file-upload limits, archive/path traversal, command injection через FFmpeg arguments, rate limits и unrestricted resource consumption.

Untrusted upload/HTML/archive MUST обрабатываться с ограничениями размера, времени, памяти, глубины вложенности и числа записей. Source Adapter не получает доступ к loopback, metadata endpoints и внутренним сетям без явного allowlist.

---

# 99. Этап 0 - Technical Prototype

Проверить:

- Android Media3 playback;
- Room;
- filesystem;
- background download;
- один Source Adapter;
- fingerprint library;
- Android -> FastAPI;
- простой Vault prototype.
- Recording / ReleaseTrack / AudioVariant identity spike;
- проверку ложных merge на live/remix/remaster/radio edit;
- CAS atomic ingest и повтор после crash;
- PostgreSQL job queue с lease/heartbeat;
- CPU-only server path на Linux и Windows;
- обнаружение RTX 3060 12 GB и изолированный GPU container;
- benchmark CLAP, MERT и Essentia на выборке реальной библиотеки;
- pgvector exact vs HNSW benchmark;
- HTTP Range streaming;
- prototype sync с duplicate event delivery.

Stage 0 завершается короткими ADR и benchmark reports. Без подтверждения identity, resumability и playback path переход к массовому импорту и ML не допускается.

---

# 100. Этап 1 - Local Core MVP

Реализовать:

- Android app;
- Local Library;
- Player;
- folder import;
- Search;
- Source Adapter System;
- Download Manager;
- metadata;
- fingerprint;
- playlists;
- profile export/import.
- process-death queue restore;
- offline pin/cache quota;
- merge review для локальных дубликатов;
- crash-safe Room migrations.

---

# 101. Этап 2 - Music Vault

Реализовать:

- кроссплатформенное server core и Linux production deployment;
- Music Vault;
- server Catalog;
- AudioVariant;
- Canonical Variant;
- Ingest Pipeline;
- deduplication;
- Vault-first download;
- Android synchronization;
- Offline Journal;
- restore.
- immutable CAS, quarantine и GC;
- versioned API и OpenAPI contract tests;
- PostgreSQL job queue;
- minimal Administration CLI/UI;
- metrics, health checks и audit;
- backup/restore drill.

---

# 102. Этап 3 - Library Migration

Реализовать:

- HTML;
- CSV;
- JSON;
- M3U/M3U8;
- importer adapters;
- Vault lookup;
- fuzzy matching;
- import preview;
- Materialize;
- multi-service merge.
- versioned adapter manifests;
- golden fixtures;
- checkpoint/cursor resume;
- review UI для ambiguous matches;
- import audit report.

---

# 103. Этап 4 - Recommendation Engine

Реализовать:

- embedding generation;
- vector search;
- similar Track;
- clustering;
- Taste Profile;
- recommendations;
- mood playlists;
- exploration.
- RTX 3060 GPU worker с CPU/deferred fallback;
- model registry и versioned embeddings;
- candidate/filter/rank/re-rank pipeline;
- temporal offline evaluation;
- organic vs recommended event origin;
- Exclude from Taste Profile;
- offline Recommendation Pack;
- natural-language retrieval через audio/text embeddings.

---

# 104. Этап 5 - Release Watcher

Реализовать:

- Follow Artist;
- Watchlist;
- release checks;
- Home recommendations for new releases.

---

# 105. Этап 6 - Wave

Реализовать:

- Room;
- Host;
- WebSocket;
- synchronized clock;
- LOCAL playback;
- Vault Stream fallback;
- preflight;
- prefetch;
- drift correction.

---

# 106. Этап 7 - Social

Реализовать:

- Track sharing;
- playlist sharing;
- QR;
- app links;
- Party Mode;
- collaborative queue;
- voting.

---

# 107. Этап 8 - Web UI

Реализовать браузерный интерфейс управления сервером.

---

# 107A. Этап Production Hardening

Выполняется перед объявлением стабильной версии и частично параллельно этапам 1-4:

- threat model и OWASP verification;
- load/fault tests;
- migration/rollback tests;
- dependency и model license audit;
- container image scanning и SBOM;
- backup restore drill;
- alerting по disk, DB, queue, stream и GPU;
- release channels и rollback;
- operational runbook;
- data-retention и deletion verification.

---

# 108. Что не включать в первый MVP

Перенести на более поздние этапы:

- Wave;
- Party Mode;
- full Recommendation Engine;
- LLM;
- Web UI;
- native desktop clients;
- advanced release tracking;
- complex P2P;
- mid-track STREAM -> LOCAL switch.
- collaborative filtering и обучение больших transformer recommender;
- обязательную CUDA-зависимость;
- отдельный Redis/RabbitMQ без доказанной необходимости;
- HLS/adaptive streaming, если direct HTTP Range закрывает reference-сценарий;
- OpenSubsonic compatibility до стабилизации внутреннего API.

---

# 109. Критерии готовности MVP

MVP считается готовым, если пользователь может:

1. установить AutPlay;
2. выбрать каталог;
3. импортировать локальную музыку;
4. выполнить поиск через Source Adapter;
5. выбрать Track;
6. скачать файл;
7. получить metadata;
8. добавить Track в Library;
9. воспроизвести его;
10. создать Playlist;
11. перезапустить приложение без потери состояния;
12. экспортировать профиль;
13. импортировать профиль обратно.
14. продолжить воспроизведение после process death;
15. остаться работоспособным при недоступном сервере, GPU и external source;
16. не создать дубликат после повторной доставки одной download/import команды;
17. пройти автоматические migration и rollback tests на поддерживаемой версии БД.

---

# 110. Критерии готовности Music Vault

Vault считается готовым, если:

1. Android подключается к серверу;
2. Track ищется в Vault;
3. существующий Track отдается без внешнего скачивания;
4. новый файл проходит Ingest;
5. fingerprint предотвращает дубликаты;
6. offline Track попадает в Offline Journal;
7. после появления сервера Track автоматически попадает в Vault;
8. локально потерянный Track можно восстановить.
9. partial upload не появляется как готовый AudioVariant;
10. повторный ingest одного blob идемпотентен;
11. HTTP Range и отмена stream работают;
12. low-disk режим безопасно останавливает новые ingest jobs;
13. DB/profile backup восстановлен в чистом test environment;
14. удаление проходит tombstone, grace period, quarantine и reference recheck;
15. сервер работает при остановленном GPU worker и VPN.

---

# 111. Критерии готовности Library Migration

Функция готова, если:

1. пользователь загружает экспорт стороннего сервиса;
2. AutPlay извлекает Track;
3. восстанавливает Playlist;
4. показывает preview;
5. проверяет Vault;
6. не получает заново уже существующие Track;
7. materialize отсутствующие;
8. повторный импорт не создает дубликаты.
9. сохраняются порядок и повторяющиеся entries плейлиста;
10. job продолжается с checkpoint после падения;
11. ambiguous match не объединяется автоматически;
12. изменение входной schema определяется явно;
13. отчет содержит parser/adapter version и решения по каждой записи.

---

# 112. Критерии готовности Recommendation Engine

Функция готова, если:

1. embeddings успешно рассчитываются;
2. работает similarity search;
3. создаются Taste Clusters;
4. рекомендации строятся преимущественно на embeddings;
5. Like / Dislike корректируют профиль;
6. случайный Skip автоматически не считается Dislike.
7. embedding содержит полную model/preprocessing provenance;
8. старая и новая embedding-модели могут сосуществовать во время миграции;
9. recommendation event помечается origin/request ID;
10. работает Exclude from Taste Profile;
11. quality report содержит relevance, diversity, novelty и repeat rate;
12. остановка RTX 3060 не нарушает playback, search и sync;
13. offline Recommendation Pack формирует очередь только из доступных на устройстве Track.

---

# 113. Критерии готовности Wave

Wave считается готовым, если:

1. Host создает Room;
2. пользователь подключается;
3. Track запускается синхронно;
4. LOCAL используется при наличии Track;
5. Vault Stream используется при отсутствии;
6. будущие Track проверяются заранее;
7. prefetch может перевести будущий Track в LOCAL;
8. Play/Pause/Seek/Next синхронизированы;
9. drift автоматически корректируется.

---

# 114. Зафиксированные архитектурные решения

## 1. LOCAL-FIRST

Android автономен.

## 2. SERVER-OPTIONAL

Сервер расширяет функциональность, но не является обязательным.

## 3. MUSIC VAULT

Vault является предпочтительным источником серверного аудио.

## 4. TRACK != AUDIOVARIANT

Recording отделена от ReleaseTrack, AudioVariant и физического VaultObject.

## 5. FINGERPRINT-ASSISTED IDENTITY

Fingerprint является сильным, но не единственным сигналом. Merge учитывает duration, metadata, version markers, external IDs и confidence; пограничные решения обратимы и требуют review.

## 6. VAULT-FIRST DOWNLOAD

При доступном сервере сначала проверяется Vault.

## 7. EVERY ACQUISITION -> VAULT

Любой новый Track со временем предлагается Vault.

## 8. OFFLINE JOURNAL

Offline-действия не теряются.

## 9. LIBRARY MIGRATION FIRST

Существующая библиотека пользователя является полноценным источником данных AutPlay.

## 10. EMBEDDINGS-FIRST RECOMMENDATIONS

Рекомендации строятся преимущественно на содержимом музыки.

## 11. HYBRID WAVE

LOCAL является предпочтительным источником, STREAM - fallback.

## 12. OPEN DATA

Профиль пользователя должен быть переносимым и экспортируемым.

## 13. VPN INDEPENDENCE

VPN и AutPlay являются независимыми сервисами, даже если размещены на одной физической машине.

## 14. GPU OPTIONAL

RTX 3060 12 GB ускоряет ML enrichment, но API, Vault, streaming, sync, import core и playback не зависят от CUDA.

## 15. VERSIONED DERIVED DATA

Embeddings, индексы, tags, recommendations и transcoding cache содержат model/tool/preprocessing version и могут быть перестроены.

## 16. AT-LEAST-ONCE + IDEMPOTENCY

Sync и server jobs допускают повторную доставку; side effects дедуплицируются по устойчивым ключам.

## 17. IMMUTABLE VAULT BLOBS

VaultObject неизменяем и адресуется hash; удаление отделено от удаления пользовательской ссылки.

## 18. CROSS-PLATFORM CORE, LINUX PRODUCTION

Server core работает без CUDA на Linux/Windows/macOS, а основной production deployment и NVIDIA acceleration ориентированы на Linux.

## 19. MEASURE BEFORE SPLIT

PostgreSQL queue, pgvector и modular monolith являются default. Отдельный broker, vector DB или microservice добавляется только по измеренному bottleneck или границе отказа.

---

# 115. Основной сценарий получения Track

При доступном сервере:

```text
User Search
    |
    v
Vault Lookup
   / \
  /   \
YES   NO
 |     |
 |   External Search
 |     |
 |   Download
 |     |
 |   Ingest
 |     |
 +-----+
    |
    v
Music Vault
    |
    v
Android
```

---

# 116. Сценарий при недоступном сервере

```text
User Search
    |
External Source
    |
Android Download
    |
Local Ingest
    |
Library
    |
Offline Journal
    |
Server Returns
    |
Vault Lookup / Upload
    |
Music Vault
```

---

# 117. Сценарий массового импорта

```text
Spotify / Yandex / HTML / CSV / M3U
                |
                v
         Library Importer
                |
                v
        Metadata Normalize
                |
                v
            Vault Lookup
            /         \
           /           \
        MATCH        MISSING
          |             |
        Link       External Search
                        |
                     Ingest
                        |
                        v
                      Vault
                |
                v
         User Library
```

---

# 118. Сценарий Wave

```text
                AutPlay Server
                 Wave Clock
                     |
              +------+------+
              |             |
           User A         User B
              |             |
        Track Local?   Track Local?
           YES             NO
            |               |
          LOCAL         Vault Stream
```

---

# 119. Главная цель AutPlay

AutPlay должен создать устойчивую пользовательскую музыкальную библиотеку, в которой:

- пользователь контролирует Track;
- физические AudioVariant можно восстанавливать;
- данные не привязаны к одному музыкальному сервису;
- коллекция переносима;
- Vault сохраняет эталонные копии;
- Android продолжает работать без Vault;
- внешние источники взаимозаменяемы;
- сервер не является единой точкой отказа;
- VPN не является частью музыкальной системы;
- отказ стороннего сервиса не уничтожает библиотеку.

---

# 120. Следующая стадия проектирования

После окончательной проверки настоящего ТЗ необходимо подготовить:

1. System Architecture v1.
2. ER Diagram.
3. Android Room schema.
4. Server PostgreSQL schema.
5. Track identity specification.
6. Fingerprint strategy.
7. Vault storage specification.
8. Source Adapter API.
9. Library Import Adapter API.
10. Sync Protocol.
11. Conflict Resolution specification.
12. OpenAPI contract.
13. Wave Protocol.
14. Streaming Protocol.
15. Embedding model benchmark.
16. Recommendation algorithm prototype.
17. Security model.
18. Container deployment architecture.
19. MVP backlog.
20. Acceptance tests.
21. Recording / Release / ReleaseTrack identity ADR.
22. Event envelope, outbox/inbox и tombstone specification.
23. Job lease/checkpoint protocol.
24. GPU Worker and Model Registry specification.
25. Recommendation evaluation protocol.
26. Import Adapter manifest and fixture specification.
27. Threat Model and SSRF/file-processing security profile.
28. Backup manifest, RPO/RTO and Restore Runbook.
29. Observability and SLO specification.
30. Database migration and rollback policy.
31. Dependency/model license inventory and SBOM policy.

---

# 121. GPU-архитектура для NVIDIA RTX 3060 12 GB

## 121.1. Роль GPU

RTX 3060 12 GB является доступным ускорителем домашнего сервера и SHOULD использоваться для тяжелого пакетного inference. Она не является обязательной частью core-path.

На GPU выполняются:

- audio embedding extraction;
- segment embedding aggregation;
- zero-shot audio/text retrieval при выбранной модели;
- mood/activity/style tagging;
- batch re-embedding после смены модели;
- MAY - локальная небольшая LLM для разбора natural-language intent;
- MAY - экспериментальные модели переходов и рекомендаций.

На CPU остаются:

- SHA-256;
- Chromaprint/fingerprint;
- metadata parsing;
- decode validation;
- FFmpeg orchestration;
- database/API/sync;
- direct streaming;
- базовые рекомендации по уже рассчитанным vectors.

## 121.2. Изоляция

```text
autoplay-api              no GPU
autoplay-stream           no GPU
autoplay-worker-cpu       no GPU
autoplay-ml-gpu           RTX 3060 12 GB
autoplay-ml-cpu-fallback  optional
```

GPU доступна только `autoplay-ml-gpu` через NVIDIA Container Toolkit. Остальные containers не получают GPU device и продолжают работу при остановке драйвера, CUDA runtime или ML container.

## 121.3. Планировщик GPU

Первая версия использует один тяжелый GPU job одновременно. Конкретные batch size и число workers определяются benchmark, а не фиксируются заранее.

Приоритеты:

1. interactive semantic search, если результат нельзя получить из индекса;
2. embedding нового Track;
3. user-requested playlist analysis;
4. background bulk re-embedding;
5. research/experimental jobs.

Планировщик учитывает:

- свободную VRAM;
- текущий model residency;
- queue age;
- job priority;
- температуру/power state при наличии telemetry;
- лимит одновременного CPU decoding;
- disk/network pressure.

Out-of-memory классифицируется отдельно: worker уменьшает batch и повторяет ограниченное число раз. Бесконечный retry запрещен.

## 121.4. Model Registry

Каждая модель регистрируется:

```text
model_id
task
version
source
license
weights_sha256
runtime
precision
input_sample_rate
segment_duration
preprocessing_version
embedding_dimension
enabled
created_at
```

Загрузка произвольных weights по URL из job payload запрещена. Модели добавляются owner/admin через allowlisted registry и проходят hash/license validation.

## 121.5. Benchmark Gate

На репрезентативной выборке реальной коллекции сравниваются минимум:

- LAION CLAP;
- MERT 95M;
- MERT 330M, если помещается с безопасным VRAM reserve;
- Essentia Discogs-EffNet.

Измерения:

```text
tracks/hour
segments/second
peak VRAM
peak RAM
GPU utilization
decode overhead
embedding storage size
nearest-neighbor relevance
genre/mood retrieval quality
text-to-music quality
failure rate
CPU fallback time
```

Benchmark хранит dataset manifest, seed, software/model versions и raw results. Выбирается не одна «самая умная» модель, а минимальный набор моделей, дающий измеримую пользу без дублирования функций.

## 121.6. Graceful Degradation

При недоступной RTX 3060:

- новый Track доступен сразу после core ingest;
- ML status становится `PENDING_ACCELERATOR` или `CPU_FALLBACK`;
- существующий embedding index продолжает обслуживать запросы;
- exact metadata search и playback работают;
- очередь не теряет progress;
- пользователь видит причину задержки только на diagnostic/job screen.

---

# 122. Векторный поиск и Recommendation Serving

При каталоге до 100 000 Track PostgreSQL + pgvector является default, чтобы не вводить отдельную систему хранения раньше необходимости.

Порядок внедрения:

1. exact cosine search и baseline quality;
2. HNSW с измерением recall@K и p95/p99;
3. настройка filters/iterative scan или partial indexes;
4. только при подтвержденном bottleneck - benchmark Spotify Voyager или отдельного vector service.

ANN index является derived data. Source of truth - versioned embedding rows; индекс можно удалить и перестроить.

Запрос векторного поиска MUST фильтровать:

- model version;
- user-visible catalog/ACL;
- availability;
- explicit policy;
- block/dislike state;
- выбранный context.

Результаты приблизительного индекса проверяются точным cosine re-score на коротком candidate list до финального ранжирования.

---

# 123. Offline Recommendation Pack

Для бесшовной LOCAL-FIRST работы сервер периодически формирует компактный пакет:

```text
pack_id
user_id
created_at
expires_at
catalog_snapshot
model_version
candidate_track_ids[]
compact vectors/features
precomputed queue
ranking parameters
signature/checksum
```

Android хранит только пакет, относящийся к доступным или подготовленным для offline Track.

Источники offline-музыки:

- пользовательские pinned downloads;
- управляемый cache последних прослушиваний;
- proactive download pool;
- текущая предварительно рассчитанная очередь.

Proactive download:

- выключается пользователем;
- имеет явную storage quota;
- по умолчанию требует Wi-Fi/unmetered network и достаточный заряд;
- не вытесняет Pinned;
- объясняет, сколько места занято и зачем загружен Track;
- удаляется как cache, не как LibraryEntry.

При кратком обрыве сети используется precomputed queue. При длительном offline локальный lightweight ranker перестраивает очередь с учетом свежих Like/Dislike/Skip, но не пытается запускать тяжелую server model.

---

# 124. Source Adapter и Import Adapter production contract

Каждый adapter работает как изолированный plugin/module с versioned contract.

Обязательные свойства:

- capability declaration;
- strict input/output schema;
- bounded results и file sizes;
- pagination/cursor;
- timeout;
- exponential backoff + jitter;
- `Retry-After` support;
- circuit breaker;
- per-provider concurrency/rate limit;
- deterministic normalization;
- sanitized logs;
- health/last-success metrics;
- golden fixtures и regression tests.

API/OAuth adapter хранит token в server secret storage, использует минимальные read-only scopes и не включает token в SourceReference.

HTML/file adapter MUST защищаться от:

- path traversal;
- archive bomb;
- excessive nesting;
- huge rows/fields;
- malformed encodings;
- embedded scripts;
- remote resource auto-fetch;
- formula injection при последующем CSV export.

Spotify-specific adapter SHOULD поддерживать официальный account-data JSON и пользовательские CSV-экспорты. При Web API-доступе используются pagination, playlist snapshot/version, batch calls и обработка 429/`Retry-After`.

Yandex Music adapter SHOULD в первую очередь поддерживать стабильный пользовательский export/file format. Неформальный API MAY существовать как отключаемый экспериментальный adapter, но не считается надежным путем восстановления.

---

# 125. Streaming и Transcoding

## Direct Play

Если клиент поддерживает codec/container и пропускной способности достаточно, сервер отдает immutable VaultObject через Range requests без перекодирования.

## Transcoding

Transcoding выполняется отдельным bounded CPU worker. GPU transcoding не является задачей первой версии и не должен конкурировать с ML за RTX 3060.

Preset включает:

```text
target codec
bitrate/quality
sample rate policy
channel policy
normalization policy
encoder version
```

Кэшированный результат адресуется:

```text
source_sha256 + preset_hash + encoder_version
```

Незавершенный output хранится в staging и удаляется после timeout. Client disconnect отменяет ненужный live transcode, если результат не должен быть сохранен в cache.

HLS/adaptive bitrate MAY добавляться для нестабильного remote-доступа после измерения. Для MVP предпочтителен более простой HTTP Range path.

OpenSubsonic-compatible read-only API MAY появиться после стабилизации внутренней модели. Он дает экосистему готовых клиентов, но является compatibility layer и не определяет внутреннюю domain model AutPlay.

---

# 126. Multi-user isolation

Общие сущности:

- Recording/Release catalog;
- immutable VaultObject;
- технические AudioVariant;
- public metadata.

Пользовательские сущности:

- LibraryEntry;
- Like/Dislike/rating;
- play history;
- playlist и order;
- Taste Profile;
- recommendation events;
- cache/pin policy;
- private source references.

Физическая дедупликация blob не делает его автоматически доступным всем пользователям. Каждый stream/download проверяет authorization через пользовательскую LibraryEntry/share policy.

Удаление одним пользователем не удаляет общую Recording или blob, если остались другие ссылки. Глобальные merge/split/delete доступны только owner/admin и пишутся в audit.

---

# 127. Product-функции, добавленные после review

| Приоритет | Функция | Польза | Этап |
| --- | --- | --- | --- |
| P0 | Merge/Split Review | Исправляет опасные ошибки автоматической дедупликации | 1-3 |
| P0 | Storage Health Center | Показывает low disk, orphan, corruption, backup age и очередь | 2 |
| P0 | Job Center | Pause/retry/cancel/progress для import, ingest, download и ML | 2 |
| P0 | Saved Queue + Continue Listening | Восстановление после process death и смены устройства | 1-2 |
| P0 | Pinned vs Cache | Не дает автоочистке удалить важную музыку | 1-2 |
| P1 | Proactive Offline Pack | Бесшовная музыка и рекомендации без сети | 4 |
| P1 | Exclude from Taste Profile | Не искажает вкус сном, работой, гостями и тестами | 4 |
| P1 | Fresh Shuffle | Снижает ощущение повторов при сохранении случайности | 1/4 |
| P1 | Recommendation Explanations | «Похож на...», «новый релиз...», «давно не слушали» | 4 |
| P1 | OpenSubsonic Compatibility | Доступ к готовым desktop/mobile/TV-клиентам | после 2 |
| P1 | Import Mapping Memory | Повторно применяет подтвержденные пользователем match-решения | 3 |
| P2 | ListenBrainz/Last.fm scrobbling | Опциональная переносимость истории | после 4 |
| P2 | Public share links with expiry | Контролируемый обмен без постоянного доступа | 7 |
| P2 | Auto playlists by activity/BPM | Полезно для Cycling/Workout | 4 |
| P2 | Year Review | Локальная годовая статистика | после 4 |

P0 входит в соответствующий acceptance gate. P1/P2 не расширяют первый Local Core MVP, если явно не указано иное.

---

# 128. Deployment, Update и Rollback

Production deployment SHOULD использовать Compose/Podman Compose и отдельные persistent volumes:

```text
postgres-data
vault-data
cache-data
backup-staging
```

Требования:

- containers запускаются non-root, где это возможно;
- read-only root filesystem для stateless services;
- resource limits;
- pinned image digest/version;
- startup/readiness dependencies;
- secrets через files/secret store;
- config schema validation;
- no automatic destructive migration;
- backup перед потенциально необратимой migration;
- expand/contract DB migration для совместимости старого Android client;
- documented one-command rollback приложения;
- отдельно документированный rollback schema/data.

GPU deployment включается отдельным profile. Один и тот же release должен проходить CPU-only integration test.

---

# 129. Test Strategy

Обязательные уровни:

- unit tests domain logic;
- property-based tests normalization/idempotency/order;
- adapter golden/contract tests;
- Android Room migration tests;
- PostgreSQL migration and rollback tests;
- API schema/contract tests;
- end-to-end offline -> online sync tests;
- duplicate delivery tests;
- crash/fault injection между каждым шагом ingest/import;
- corrupted/truncated audio tests;
- CAS orphan/GC/quarantine tests;
- authorization tests на все user-owned resources;
- SSRF/upload/archive abuse tests;
- Range/transcoding/player compatibility tests;
- load and soak tests;
- GPU OOM/restart/CPU fallback tests;
- backup restore test в чистое окружение.

Критические state machine SHOULD проверяться model-based/property-based тестами, а не только happy path examples.

CI разделяется:

```text
fast: lint + unit + schema
integration: DB + API + adapters fixtures
android: unit + Room + Media3 smoke
e2e: server + Android/emulator
nightly: load + fault + large import
gpu: scheduled/on-demand on RTX 3060
```

---

# 130. Основания архитектурных решений и открытые реализации

Ниже перечислены инженерные источники, идеи из которых адаптированы под домашний local-first масштаб AutPlay. Они используются как ориентиры, а не как требование копировать внутреннюю архитектуру крупных сервисов.

## Spotify

- [Voyager: production nearest-neighbor library](https://engineering.atspotify.com/2023/10/introducing-voyager-spotifys-new-nearest-neighbor-search-library) - основание для ANN benchmark после pgvector, а не для преждевременного отдельного сервиса.
- [spotify/voyager](https://github.com/spotify/voyager) и [spotify/annoy](https://github.com/spotify/annoy) - открытые реализации поиска ближайших соседей; Annoy полезен как baseline, но его статический индекс не выбран default для часто меняющегося каталога.
- [Exclude from Your Taste Profile](https://engineering.atspotify.com/2023/10/exclude-from-your-taste-profile) - основание для контекстов и исключения сессий из Taste Profile.
- [Shuffle: Making Random Feel More Human](https://engineering.atspotify.com/2025/11/shuffle-making-random-feel-more-human) - основание для раздельных Pure Random и Fresh Shuffle.
- [Spotify Web API rate limits](https://developer.spotify.com/documentation/web-api/concepts/rate-limits) - основание для pagination, snapshot/version, batching и `Retry-After` в API adapters.

## Яндекс Музыка и Яндекс Research

- [Офлайн-рекомендации Моей волны](https://habr.com/ru/companies/yandex/articles/1010992/) - основание для proactive cache, precomputed queue и компактного Offline Recommendation Pack.
- [Режим «Незнакомое» и candidate/filter/rank pipeline](https://habr.com/ru/companies/yandex/articles/845680/) - основание для отдельного exploration intent, многостадийного ranking и метрик diversity/serendipity.
- [Yambda-5B dataset и benchmark code](https://huggingface.co/datasets/yandex/yambda) вместе с [paper](https://arxiv.org/abs/2505.22238) - основание для разделения implicit/explicit событий, temporal evaluation, baseline benchmarks и поля organic/recommendation origin. Пользовательские данные AutPlay при этом остаются локальными.

## Музыкальная идентичность и fingerprint

- [MusicBrainz Recording](https://musicbrainz.org/doc/recording), [Track](https://musicbrainz.org/doc/Track) и [Release](https://musicbrainz.org/doc/Release) - основание для разделения Recording, Release и ReleaseTrack.
- [AcoustID in MusicBrainz](https://musicbrainz.org/doc/AcoustID) - показывает, что fingerprint-to-recording связи могут требовать link/unlink correction.
- [acoustid/chromaprint](https://github.com/acoustid/chromaprint) - fingerprint предназначен для near-identical audio и не является универсальным единственным merge key.

## Self-hosted ecosystem

- [Navidrome](https://github.com/navidrome/navidrome) - ориентир по multi-user, large library, transcoding, ReplayGain, saved queue и cross-platform server.
- [OpenSubsonic](https://opensubsonic.netlify.app/) - кандидат compatibility API для готовых клиентов после стабилизации внутренней модели.

## Runtime и production

- [Android Media3 downloads](https://developer.android.com/media/media3/exoplayer/downloading-media) и [Android background optimization](https://developer.android.com/topic/performance/background-optimization) - основание для разделения media downloads и guaranteed deferred work.
- [pgvector](https://github.com/pgvector/pgvector) - exact/HNSW vector search в PostgreSQL.
- [PostgreSQL SKIP LOCKED](https://www.postgresql.org/docs/current/sql-select.html) - основание для начальной queue-like таблицы с несколькими workers.
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/) - изоляция GPU-enabled container.
- [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/) и [OWASP API Security Top 10](https://owasp.org/API-Security/editions/2023/en/0x11-t10/) - security verification baseline.

---

# 131. Итоговая граница первой стабильной версии

Первая стабильная версия AutPlay не обязана повторять весь функционал Spotify или Яндекс Музыки.

Она считается инженерно жизнеспособной, если обеспечивает:

1. надежный Android local player;
2. корректную domain identity без опасного автоматического merge;
3. crash-safe local import/download;
4. optional server с immutable Vault;
5. offline journal и идемпотентную sync;
6. восстановление profile и Vault из проверенного backup;
7. массовый resumable Library Migration;
8. работа core-path без GPU и VPN;
9. ускоренное RTX 3060 ML enrichment как отдельный слой;
10. измеримую наблюдаемость, безопасность и критерии приемки.

Wave, Social, LLM и полный Web UI развиваются после стабилизации этих оснований.

---

# 132. AutPlay Face

Следующая визуальная линия плеера — [AutPlay Face](AutPlay_Face_Product_Concept_v1.md): пара
выразительных глаз, которая передаёт характер текущей музыки, мягко реагирует на её текущую динамику
и вторично показывает состояния приложения.

Продуктовая семантика непрерывна и допускает смешанные состояния по направлениям calm/energetic,
positive/melancholic, soft/aggressive, light/dark, relaxed/tense и direct/atmospheric. Она описывает
музыку, а не психологическое состояние пользователя. Визуальная тема отделена от смысла, чтобы
несколько будущих designs одинаково интерпретировали один Track.

MVP требует одну законченную тему в реальном Now Playing, пять различимых mood-сцен, плавные
переходы, реакцию на текущую playback dynamics, idle и реакции play/pause/Like/Dislike-or-skip.
Face не заменяет artwork, controls, текстовые состояния или accessibility semantics и не зависит
для основной работоспособности от сервера, GPU или готового embedding.

Детальные технологии, rendering, модели, API, schema и persistence здесь намеренно не выбираются.
Их можно определить только в отдельно активированном implementation milestone после принятия
продуктовой границы.
