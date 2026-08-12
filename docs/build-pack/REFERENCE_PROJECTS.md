# Reference Projects and Transferable Patterns

Цель раздела - дать Codex проверенные ориентиры, а не предложить скопировать чужую архитектуру целиком.

## License rule

По умолчанию использовать только идеи, публичные protocols и собственную реализацию. Перед копированием code/snippet проверить текущую лицензию конкретного файла и compatibility с лицензией AutPlay. GPL/AGPL code не копировать в AutPlay без отдельного осознанного решения.

## 1. Navidrome

Sources:

- [Repository](https://github.com/navidrome/navidrome)
- [Documentation](https://www.navidrome.org/docs/)
- License shown by repository: GPL-3.0

Полезные patterns:

- large-library scan как resumable/indexing process;
- filesystem change monitoring не заменяет periodic reconciliation;
- multi-user play counts/favorites/playlists отделены от shared media library;
- direct streaming and transcoding treated as different paths;
- compatibility API isolated from internal domain model.

Для AutPlay:

- заимствовать test scenarios для rescan, rename, duplicate tags и unavailable files;
- не копировать internal code из-за лицензии и другой domain model;
- не принимать filesystem tags как canonical global Recording без identity evidence.

## 2. beets

Sources:

- [Repository](https://github.com/beetbox/beets)
- [Autotagger matching options](https://beets.readthedocs.io/en/stable/reference/config.html#autotagger-matching-options)
- License shown by repository: MIT

Полезные patterns:

- matching через weighted distance, а не один identifier;
- separate strong/medium recommendation thresholds;
- gap между первым и вторым candidate;
- downgrade recommendation при missing/unmatched tracks;
- import preview and manual decision path.

Для AutPlay:

- сохранить evidence per feature, winning margin и reason codes;
- calibrate thresholds на собственном labeled dataset;
- не переносить numerical defaults beets как доказанные thresholds AutPlay.

## 3. MusicBrainz Picard, AcoustID and Chromaprint

Sources:

- [Picard fingerprint explanation](https://picard-docs.musicbrainz.org/en/latest/tutorials/acoustid.html)
- [MusicBrainz Recording](https://musicbrainz.org/doc/Recording)
- [MusicBrainz Track](https://musicbrainz.org/doc/Track)
- [Chromaprint repository](https://github.com/acoustid/chromaprint)

Полезные patterns:

- Recording отличается от track position on release;
- одна recording может иметь несколько slightly different fingerprints;
- fingerprint lookup может ничего не найти или вернуть не связанную с Recording сущность;
- fingerprint association можно исправлять/unlink, поэтому она не вечная identity truth;
- fingerprint batches retry as a unit without pretending partial success.

Для AutPlay:

- version algorithm, preprocessing and fingerprint payload;
- сохранять raw metadata and provenance after match;
- auto-match требует metadata/fingerprint agreement and no hard conflict;
- remaster/live/remix/edit markers участвуют как blocking evidence.

## 4. AndroidX Media3

Sources:

- [Downloading media](https://developer.android.com/media/media3/exoplayer/downloading-media)
- [Media3 repository and demos](https://github.com/androidx/media)

Полезные patterns:

- `DownloadService` wraps a singleton `DownloadManager`;
- `DownloadManager` owns scheduling and state transitions;
- `DownloadIndex` persists download state;
- WorkManager/Platform scheduler restarts work when requirements are met;
- download cache should not use normal streaming eviction rules.

Для AutPlay:

- Room stores download intent and reconciliation, not second progress truth;
- process death and duplicate callbacks must be idempotent;
- foreground service/notification behavior tested on target Android versions;
- local content URI and server AudioVariant remain separate references.

## 5. Jellyfin

Sources:

- [Transcoding modes](https://jellyfin.org/docs/general/post-install/transcoding/)
- [Repository](https://github.com/jellyfin/jellyfin)

Полезные patterns:

- explicit playback decision ladder: Direct Play -> Remux/Direct Stream -> Transcode;
- client capability drives transformation;
- dashboard/telemetry exposes actual playback mode;
- transcoding has much higher resource cost and separate failure modes.

Для AutPlay:

- V1 audio path: local file -> direct Vault Range stream -> optional transcode;
- record reason when direct play is rejected;
- no silent transcoding in API process;
- RTX 3060 ML allocation must not be assumed available for transcode.

## 6. Music Assistant

Sources:

- [Developer documentation](https://developers.music-assistant.io/)
- [Server repository](https://github.com/music-assistant/server)
- License shown by repository: Apache-2.0

Полезные patterns:

- separate music, metadata, player and plugin provider roles;
- provider declares supported capabilities;
- interactive credentials/setup separated from runtime options;
- stream-details discovery separated from byte streaming;
- provider manifest contains metadata and dependency information.

Для AutPlay:

- Source Adapter port должен объявлять capabilities and limits;
- metadata provider не переписывает source truth silently;
- provider failures/timeouts/circuit state do not affect core readiness;
- credentials never enter SourceReference or logs.

## 7. pgvector

Source:

- [Official repository README](https://github.com/pgvector/pgvector/blob/master/README.md)

Полезные patterns:

- exact nearest-neighbor search is default and gives perfect recall;
- approximate index trades recall for speed;
- HNSW has memory/build tradeoffs;
- filters and multitenancy can reduce approximate recall;
- exact re-ranking can validate approximate candidates.

Для AutPlay:

- до 100k Recording начинать с exact cosine plus relational filters;
- HNSW only after recorded latency/recall benchmark;
- version/dimension/model filters mandatory;
- model migration writes parallel embeddings, never overwrite in place.

## 8. What not to copy

- Navidrome/Jellyfin server-first assumptions into Android standalone mode.
- A provider framework before one real adapter exists.
- beets thresholds without AutPlay dataset.
- fingerprint/ISRC/provider IDs as unique Recording keys.
- Media3 download progress into a duplicate Room state machine.
- HNSW merely because pgvector supports it.
- GPL code without explicit licensing decision.

## 9. Required reference usage in phases

| Phase | References |
| --- | --- |
| P06 | Navidrome, Jellyfin |
| P08 | Media3, Jellyfin |
| P10 | beets, MusicBrainz/Picard/Chromaprint, Music Assistant |
| P11-P12 | pgvector |

Codex должен cite specific adopted pattern in ADR/test plan, но реализация остается самостоятельной.
