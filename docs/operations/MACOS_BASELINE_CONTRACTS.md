# macOS stage 0: baseline persistence and contracts

Inventory date: 2026-10-13. Baseline commit: `102380418421f87679732406f8052150e8226da8`.
Work branch: `codex/macos-inventory-20261013`. The checkout was clean at initial inspection.
This inventory describes checked-in Android/server sources, not a running server, deployed schema
or accepted Mac release. No Gradle, database migration, audio or packaging tests were run.

The [draft macOS plan](../design/explorations/AutPlay_macOS_Port_Plan_Draft_v1.md) supplies proposed
stages; the [feature matrix](MACOS_FEATURE_MATRIX.md) records proposed first-release allocation.
Frozen security decisions, contracts, code, build and CI are unchanged by this task.

## Source and toolchain identity

[settings.gradle.kts](../../settings.gradle.kts) includes only `:apps:android`; there is no
desktop/shared entry point at this baseline. [Version catalog](../../gradle/libs.versions.toml)
pins Kotlin `2.4.10`, AGP `9.1.0`, KSP `2.3.9`, Room 3 `3.0.1`, SQLite `2.7.0`, Media3 `1.10.1`,
WorkManager `2.11.2`, Compose BOM `2026.06.01` and JVM JCS helper `1.1`.
These are source pins, not evidence of Compose Multiplatform/native dependency compatibility.
No dependency upgrade or engine choice is made here.

## Actual Room schema and complete migration chain

[AutPlayDatabase.kt](../../apps/android/src/main/kotlin/app/autplay/data/local/AutPlayDatabase.kt)
declares **version 18**, `exportSchema = true` (lines 135–136), database name `autplay.db`,
`BundledSQLiteDriver`, WAL and IO query context. `open` registers every adjacent migration
`1 -> 2` through `17 -> 18` (line 181); it has no destructive fallback.
The actual exported [18.json](../../apps/android/schemas/app.autplay.data.local.AutPlayDatabase/18.json)
has Room `identityHash` `2b94182c1b55f3a206d0a03039145883` and file SHA-256
`616151ff315260a380a8857c8bcc5879a0378f1467b2fb8e3a28f0b09bfddca1`.
The [schema directory](../../apps/android/schemas/app.autplay.data.local.AutPlayDatabase)
contains historical versions 1–18. The historical
[Room design](../design/AutPlay_Android_Room_Schema_v1.md) does not override current source version.

| Step | Actual change / preservation boundary | Source |
| --- | --- | --- |
| Initial v1 | Catalog/library/preferences/playlists/local audio/download intent, queue/history, Journal/outbox, sync/search base | [1.json](../../apps/android/schemas/app.autplay.data.local.AutPlayDatabase/1.json) |
| 1 -> 2 | P08 profile attribution, active listening-session checkpoint fields, queue/history recommendation attribution and session positions | [Database][db] `MIGRATION_1_2` |
| 2 -> 3 | Profile-scoped sync runtime diagnostic table | [Database][db] `MIGRATION_2_3` |
| 3 -> 4 | Durable paginated bootstrap state | [Database][db] `MIGRATION_3_4` |
| 4 -> 5 | Tombstone/conflict tables rebuilt with profile-scoped keys and explicit `legacy-unscoped` sentinel; rows copied | [Database][db] `MIGRATION_4_5` |
| 5 -> 6 | Recommendation interaction fact table indexed by profile/type/time | [Database][db] `MIGRATION_5_6` |
| 6 -> 7 | Library, user track, preference, playlist/entries and listening tables rebuilt for profile scoping; local PKs/references preserved, server-ID uniqueness scoped | [Database][db] `MIGRATION_6_7` |
| 7 -> 8 | Local import job/entry, match decisions/candidates and review indexes | [Database][db] `MIGRATION_7_8` |
| 8 -> 9 | Owner-scoped offline recommendation pack fields and durable presentation idempotency | [Database][db] `MIGRATION_8_9` |
| 9 -> 10 | Wave room/preflight/queue projections; authority, tokens, URLs and clock samples not persisted here | [Database][db] `MIGRATION_9_10` |
| 10 -> 11 | Remote import metadata, Vault upload intent and recommendation response snapshot | [Database][db] `MIGRATION_10_11` |
| 11 -> 12 | Artist/credit/name/link/owner projections; no artist identity inferred from legacy names | [Database][db] `MIGRATION_11_12` |
| 12 -> 13 | Guest room/preflight/queue sanitized projections; guest authority remains process-local | [Database][db] `MIGRATION_12_13` |
| 13 -> 14 | Immutable device-local recommendation temporal delta, parent pack hash linkage | [Database][db] `MIGRATION_13_14` |
| 14 -> 15 | Durable session and active-listen taste-exclusion booleans in queue snapshot | [Database][db] `MIGRATION_14_15` |
| 15 -> 16 | Derived track search repair/FTS rebuild; affected synced `null` projections request authoritative bootstrap reset without guessing offline metadata or altering pending intent | [Search repair][search-migration] |
| 16 -> 17 | Track metadata projection and bounded-artwork cache metadata tables | [Metadata migration][metadata-migration] |
| 17 -> 18 | Portable account scope/records/listening aliases/per-source cursors/statistics; additive tables and indexes | [PortableAccountData.kt][portable-data] `PortableAccountDataMigration` |

Existing instrumentation evidence sources include
[database tests](../../apps/android/src/androidTest/kotlin/app/autplay/data/local/AutPlayDatabaseTest.kt),
[P08 migration tests](../../apps/android/src/androidTest/kotlin/app/autplay/data/local/P08RoomMigrationTest.kt),
[search repair tests](../../apps/android/src/androidTest/kotlin/app/autplay/data/local/SearchRepairMigrationTest.kt),
[metadata migration tests](../../apps/android/src/androidTest/kotlin/app/autplay/data/local/TrackMetadataMigrationTest.kt)
and [portable migration tests](../../apps/android/src/androidTest/kotlin/app/autplay/data/local/PortableAccountDataMigrationTest.kt).
Their existence is not a fresh passing result. A later shared-storage change needs real SQLite
migration/FTS/transaction proof and retained Android fixtures; it cannot copy a phone DB to Mac
and call that collection transfer.

## Android content URI constraint and media ownership

[LocalAudioStateEntity in Entities.kt][entities] checks the parsed source scheme is `content`
and authority is nonblank (lines 305–309). The contract is a real MediaStore/SAF content URI,
not an arbitrary URI-like string. [ContentUriInspector][uri] also validates scheme/authority
and Android permission/access. [PhoneMusicRepository][phone] and
[ContentTreeAudioScanner][scanner] use MediaStore/SAF APIs; [Daos.kt][daos] has availability SQL
that distinguishes persisted permission and `content://media/%`.

Desktop paths, `file://` URLs, security-scoped bookmarks and managed copies need a separate
platform locator/access adapter and reviewed storage boundary. Do not put a Mac path into
`content_uri`, invent a content URI, or transfer Android URI permissions. Track identity,
account ownership and local byte location remain distinct. Account metadata sync grants no
access to another device's files or a target server's Vault objects.

[ADR-018](../adr/ADR-018-standalone-outbox-and-journal-lineage.md) retains atomic local mutation
+ Journal/outbox facts and immutable lineage. [ADR-021](../adr/ADR-021-p08-media3-room-playback-download-ownership.md)
retains Android Media3 playback/download execution and Room persistence ownership;
WorkManager owns durable deferred work. [ADR-050](../adr/ADR-050-android-ui-functional-parity-state-ownership.md)
retains UI/state owners. Desktop ports must preserve these boundaries; this inventory does not
amend an ADR or select a new owner.

The scanner accepts `audio/*` or extensions `.mp3`, `.m4a`, `.aac`, `.flac`, `.ogg`, `.opus`,
`.wav` ([scanner][scanner], line 64). This is import recognition, not a guaranteed decoder matrix.
[PlaybackDataSources.kt](../../apps/android/src/main/kotlin/app/autplay/playback/PlaybackDataSources.kt)
uses Media3 sources/extractors. No tested desktop formats exist at this baseline. The draft's
MP3/AAC-M4A/FLAC/Opus/PCM-WAV matrix must pass with real audio and HTTP Range/seek/redirect/error
cases on the selected engine; Ogg codecs/containers need explicit evidence too.

## Contract/version inventory

Schema-directory `v1`, OpenAPI document version and an in-payload version are separate facts.
Portable recovery export version 2 is not enrollment v2 and does not broaden device platforms.

| Surface | Baseline version / exact source | Compatibility boundary |
| --- | --- | --- |
| Profile discovery/capability/enrollment/session lifecycle | [Profile pairing v1 schemas](../../contracts/profile-pairing/v1), [OpenAPI](../../contracts/openapi/v1/autplay-profile-pairing.openapi.json); enrollment `contract_version = v1`, `schema_version = 1` | Signed identity/capability payload, pinned origins, expected account/device/server, nonce and binding commit remain mandatory. |
| Device admission/social | [Admission request](../../contracts/social/v1/admission-request.schema.json), [social schemas](../../contracts/social/v1), [OpenAPI](../../contracts/openapi/v1/autplay-social.openapi.json) | Admission uses exact request members and `api_major`; directory v1 does not mean every request contains `contract_version`. Platform is Android only. |
| Self-device pairing | [v1 schemas](../../contracts/self-device-pairing/v1), [claim](../../contracts/self-device-pairing/v1/claim-request.schema.json), [OpenAPI](../../contracts/openapi/v1/autplay-self-device-pairing.openapi.json) | `contract_version = v1`, `schema_version = 1`; claim contains signed Android platform. SAS/source approval and recipient ownership must survive extension. |
| Portable account passport/root proofs | [Android crypto][portable-crypto], [server crypto][server-crypto], [server service][portable-server] | Passport `format = autplay-portable-account`, `version = 1`; P-256 identity, bounded exact JSON, PBKDF2-HMAC-SHA256 600,000 iterations and domain-separated proofs. No standalone passport JSON Schema is present in the portable-account schema directory. |
| Portable recovery file | [PortableAccountCrypto.kt][portable-crypto], export lines 128–129, import lines 152–156 | `format = autplay-portable-account-recovery`; current export **version 2**, import accepts **1 or 2**. Lineage document has version 1; do not lose predecessors/ever-enrolled state. |
| Non-secret settings transfer | [SettingsTransferCodec.kt](../../apps/android/src/main/kotlin/app/autplay/application/settings/SettingsTransferCodec.kt) | Exports `schema_version = 3`, imports versions 1–3; bounded to 64 KiB. Language/appearance/palette/download/prefetch/visual-transition preferences only, without credentials or local access grants. |
| Portable metadata records | [account-record.schema.json](../../contracts/portable-account/v1/account-record.schema.json), [Android codec](../../apps/android/src/main/kotlin/app/autplay/application/portableaccount/PortableAccountDataCodec.kt) | Directory v1; inner `document.version = 1`, record wrapper is `record_id/kind/document`. Kinds LISTENING/PREFERENCE/PLAYLIST/PLAYLIST_ENTRY/TASTE. No passport/credentials/media access grant. |
| Portable server readiness | [operations](PORTABLE_ACCOUNTS.md), [0066 migration](../../server/migrations/versions/0066_portable_local_account.py) | Metadata readiness expects `0066_portable_local_account`. `portable_accounts` and `self_device_pairing` are independently opt-in capabilities/flags. This is not evidence flags/migration are enabled on a deployed server. |
| Sync/Journal events | [push](../../contracts/events/v1/push-request.schema.json), [client event](../../contracts/events/v1/client-event.schema.json), [bootstrap](../../contracts/events/v1/bootstrap-request.schema.json), [sync OpenAPI](../../contracts/openapi/v1/autplay-sync.openapi.json) | `protocol_version = 1`; client event `schema_version = 1`, canonical payload max 262,144 bytes, fixed binding/sequence/idempotency. Keep old event hashes unchanged. |
| Server-bound account recovery/deletion | [recovery OpenAPI](../../contracts/openapi/v1/autplay-account-recovery.openapi.json), [deletion OpenAPI](../../contracts/openapi/v1/autplay-account-deletion.openapi.json), [Android recovery](../../apps/android/src/main/kotlin/app/autplay/application/accountrecovery/AccountRecoveryProtocol.kt) | OpenAPI info version `1.0.0`; wire `v1/1`. Separate recovery format `autplay-account-recovery`, version 1; it is not a portable passport recovery file. |
| Wave/guest/recommendations | [Wave OpenAPI](../../contracts/openapi/v1/autplay-wave.openapi.json), [guest document](../../contracts/social/v1/guest-document.schema.json), [recommendation schemas](../../contracts/recommendations/v1) | Existing v1 contracts and policy/pack versions remain independent; implementation presence does not put them in proposed first-release scope. |
| Consent/statistics/resource admission/public access | [consent OpenAPI](../../contracts/openapi/v1/autplay-training-consent.openapi.json), [statistics schemas](../../contracts/profile-statistics/v1), [resource admission](../../contracts/resource-admission/v1), [public access](../../contracts/public-access/v1) | Preserve exact command authority, owner scopes and confirmed privacy state; no scope/authority broadening in this inventory. |

[PORTABLE_ACCOUNTS.md](PORTABLE_ACCOUNTS.md) records command JSON bounds (16,384 bytes), data
POST bounds (524,288 bytes, 1–32 records), per-source cursors, 200,000 records/account and 64 MiB
serialized JSON. Runtime additionally validates canonical UUIDs, integer tokens, canonical
document size and listening alias membership; JSON Schema alone is insufficient proof.

## Hardcoded platform blockers

The broad Device table allows Android/Web/Other, but this does **not** make enrollment,
admission, self-pairing or portable enrollment compatible with Mac. Every layer below must be
included in later platform-contract review, preserving old Android requests and stored rows.

| Layer | Exact baseline source / useful location | Actual restriction |
| --- | --- | --- |
| Enrollment JSON Schema | [exchange request](../../contracts/profile-pairing/v1/enrollment-exchange-request.schema.json), line 21 | `platform` const `ANDROID`. |
| Enrollment HTTP | [profile_pairing_http.py](../../server/src/autplay/entrypoints/profile_pairing_http.py), line 44 | Validator pattern `^ANDROID$`. |
| Admission JSON/HTTP | [request schema](../../contracts/social/v1/admission-request.schema.json), line 21; [device_admission_http.py](../../server/src/autplay/entrypoints/device_admission_http.py), line 33 | `ANDROID` only at request/validator level. |
| Admission storage | [models/profile_pairing.py](../../server/src/autplay/adapters/postgresql/models/profile_pairing.py), line 198 | CHECK `platform = 'ANDROID'` (`device_admission_platform_check`). |
| Device storage enum/CHECK | [models/account.py](../../server/src/autplay/adapters/postgresql/models/account.py), lines 190–191 | CHECK `platform IN ('ANDROID', 'WEB', 'OTHER')` (`ck_device_platform`); no MACOS. |
| Profile pairing service creation/fallback | [application/profile_pairing.py](../../server/src/autplay/application/profile_pairing.py) | Hardcoded assignments at 696, 812, 1321, 1930; platform fallback at 1570. Review each operation's authority/propagation rather than globally replacing strings. |
| Portable enrollment | [application/portable_account.py][portable-server], line 469 | New `DeviceRow(platform="ANDROID")`; adding a client field alone cannot propagate MACOS. |
| Self-pairing contract/domain | [claim schema](../../contracts/self-device-pairing/v1/claim-request.schema.json), line 80; [domain/self_device_pairing.py](../../server/src/autplay/domain/self_device_pairing.py), line 111 | Signed claim platform is const/validated `ANDROID`. |
| Self-pairing storage adapter | [postgresql/self_device_pairing.py](../../server/src/autplay/adapters/postgresql/self_device_pairing.py), lines 91, 217 | Rejects non-Android source device and creates Android recipient. |
| Android enrollment/admission/protocols | [profile transport][profile-port], line 94; [Admission.kt](../../apps/android/src/main/kotlin/app/autplay/application/profilepairing/Admission.kt), line 184; [SelfPairingProtocol.kt](../../apps/android/src/main/kotlin/app/autplay/application/selfpairing/SelfPairingProtocol.kt), line 195; [AccountRecoveryProtocol.kt](../../apps/android/src/main/kotlin/app/autplay/application/accountrecovery/AccountRecoveryProtocol.kt), line 92 | Clients embed Android in enrollment/admission/self-pair/recovery device requests. These adapters cannot be reused unchanged for Mac. |
| Android device list | [profile transport][profile-port], line 133 | Filters active devices to `platform == ANDROID`; could hide new Mac devices even after server accepts them. |
| Server trusted device view | [trusted_devices.html](../../server/src/autplay/web/templates/trusted_devices.html), line 33 | Android gets label `Android`, other platform text falls through; future MACOS display/revoke requires verification, not assumed complete UI support. |

No extension/versioning strategy is selected here. Stage 4 needs an approved ADR, explicit
capability/version negotiation, non-destructive CHECK migration and platform propagation,
old Android/new server tests and clear old-server/Mac rejection. **Mac must not claim ANDROID**
to pass existing checks. Lists and persisted unknown values require forward-compatible handling.

## Signed canonical payload risks

1. **Platform changes signed bytes.** [Profile transport][profile-port] constructs enrollment
   fields, adds `signature_algorithm`, canonicalizes and hashes before signing. Server
   [profile pairing domain](../../server/src/autplay/domain/profile_pairing.py) uses RFC 8785
   canonical SHA-256 with contract-specific omitted fields. Adding MACOS or changing schema
   members requires versioned compatibility; do not rewrite stored Android requests/receipts.
2. **Different signature encodings are deliberate.** Profile envelopes require ES256-P1363
   (64-byte signature, DER conversion at JVM verification). Portable root proofs use P-256
   DER ECDSA over `autplay:portable-account:{purpose}:v1\n` plus canonical request bytes
   ([server crypto][server-crypto], lines 85–117). Reusing one generic signer with the wrong
   encoding/domain/hash recipe would break verification or authority separation.
3. **Self-pairing and recovery are independent domains.**
   [Self pairing domain](../../server/src/autplay/domain/self_device_pairing.py), lines 124–126,
   omits `request_sha256/device_signature_b64url`; domain-separated kind proofs and SAS bind
   exact request identity. Server-bound recovery and portable recovery cannot be interchanged.
4. **JCS/exact JSON is observable wire behavior.**
   [Portable crypto][portable-crypto] and [Android codec](../../apps/android/src/main/kotlin/app/autplay/application/portableaccount/PortableAccountDataCodec.kt)
   rely on canonical bytes. Preserve UTF-8, numbers versus strings, null versus absence,
   exact members, Unicode, base64/base64url rules and size limits. Root/document/request hash,
   key thumbprint and revision/lineage fixtures must match across Android/JVM/Mac/server.
5. **Journal events cannot be regenerated for desktop.**
   [P04ClientEventHasher](../../apps/android/src/main/kotlin/app/autplay/application/sync/P04ClientEventHasher.kt)
   includes fixed event/binding fields and canonical payload, not journal epoch in event hash.
   Desktop gets its own device ID and local DB; importing account metadata must preserve
   listening aliases/idempotency without editing existing event bytes or silently reassigning owners.

Later proof sources to retain/extend include
[Android portable crypto tests](../../apps/android/src/test/java/app/autplay/application/portableaccount/PortableAccountCryptoTest.kt),
[server portable crypto tests](../../server/tests/test_portable_account_crypto.py),
[record contract tests](../../server/tests/test_portable_record_contract.py),
[self-pair proof tests](../../server/tests/test_self_device_pairing_proof.py),
[client event hasher tests](../../apps/android/src/test/java/app/autplay/application/sync/P04ClientEventHasherTest.kt)
and [portable data convergence tests](../../apps/android/src/test/java/app/autplay/application/portableaccount/PortableAccountDataConvergenceTest.kt).
These are reference fixtures/test sources; none was executed during this inventory.

## Privacy and native release boundaries

[Privacy operations](PRIVACY_DELETE_EXPORT.md) and [portable operations](PORTABLE_ACCOUNTS.md)
separate ordinary owner data export, secure recovery-document export, account deletion ceremony
and physical purge. There is no ordinary full owner archive HTTP endpoint; the internal portable
owner export helper pages owner records/typed taste and omits passport/challenge/credential
material. It does not export media or authorize another owner's Vault bytes. Migration 0066
integrates portable purge and permits development downgrade only when its new tables are empty.
This task neither performs real-data deletion/export nor changes accepted privacy/security policy.

The draft makes Apple Silicon the initial target. A real **Mac ARM64** or appropriate macOS CI
is required for audio engine/native bridge, Keychain/device signatures, `.app`/`.dmg` runtime
packaging, signing/notarization/Gatekeeper and sleep/wake/media controls. No AVFoundation/LibVLC
choice, minimum OS version, working native package or x64 qualification is established here.
**Intel/x64 support is not claimed.** Independent x64 build and test evidence is required before
adding it. Server/GPU installation on Mac and Face remain outside the port.

## Inventory verification boundary

Documentation-only acceptance checks: Git baseline/branch identity, relative Markdown links and
anchors, tracked reference availability at the baseline, line references and `git diff --check`.
Only the two owned Markdown documents are committed. The local result note records exact commands,
outcome and commit SHA. No runtime or migration acceptance is implied by link checks.

[db]: ../../apps/android/src/main/kotlin/app/autplay/data/local/AutPlayDatabase.kt
[search-migration]: ../../apps/android/src/main/kotlin/app/autplay/data/local/SearchProjectionRepairMigration.kt
[metadata-migration]: ../../apps/android/src/main/kotlin/app/autplay/data/local/TrackMetadataMigration.kt
[portable-data]: ../../apps/android/src/main/kotlin/app/autplay/data/local/PortableAccountData.kt
[entities]: ../../apps/android/src/main/kotlin/app/autplay/data/local/entity/Entities.kt
[daos]: ../../apps/android/src/main/kotlin/app/autplay/data/local/dao/Daos.kt
[uri]: ../../apps/android/src/main/kotlin/app/autplay/application/importing/ContentUriInspector.kt
[phone]: ../../apps/android/src/main/kotlin/app/autplay/application/importing/PhoneMusicRepository.kt
[scanner]: ../../apps/android/src/main/kotlin/app/autplay/application/importing/ContentTreeAudioScanner.kt
[portable-crypto]: ../../apps/android/src/main/kotlin/app/autplay/application/portableaccount/PortableAccountCrypto.kt
[server-crypto]: ../../server/src/autplay/adapters/portable_account_crypto.py
[portable-server]: ../../server/src/autplay/application/portable_account.py
[profile-port]: ../../apps/android/src/main/kotlin/app/autplay/application/profilepairing/OkHttpProfilePairingPort.kt
