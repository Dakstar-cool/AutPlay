# Admin/account target acceptance runbook: 2026-09-19

This records completed target evidence and the remaining physical acceptance for ADR-053 after
local implementation. A completed partial target run does not close a gate whose other required
controls remain pending. Do not substitute the connected Galaxy M52, an emulator, a virtual
WebAuthn authenticator or synthetic throughput fixtures for the named targets.

The operator corrected the phone name before target execution: the intended target is the
Samsung Galaxy **A55**, and ADB identifies the connected clean device as `SM-A556E`. Earlier M55
wording was a naming error and has been replaced in the acceptance schema, controls, UI copy and
active documentation. No M52 result is reclassified as A55 evidence.

## Physical A55 Android evidence (partial Gate B)

The clean target was confirmed as `SM-A556E`, Android 16, with security patch `2026-08-05`.
Neither `app.autplay` nor `app.autplay.qa` existed before the reviewed production-ID install.
The installed build is `app.autplay` version code 11 / `0.3.7-metadata`; reviewed APK SHA-256 is
`4efa69ee06fca167226ad6fea4e0404f688d8611f06920f86a0f31922513e276` and signer-certificate
SHA-256 is `be0db5b668b832ff8e48f043120bec7b2ead80138267235214583361c8d02380`.
The raw serial and build fingerprint are omitted; their SHA-256 values are
`37aa14db887ecb331b723f0661e22dc12df22b5c663adce8351ae612304ce0a4` and
`20cef80cb52160d1a3436ef6a9c9851ff508cb8abf0f2618a9868461c780d107`.

Five joined physical runs then passed through the production Android runtimes, OkHttp and
Keystore into FastAPI and a fresh disposable PostgreSQL 18.4/pgvector database migrated through
`0059`: role-complete OWNER/ADMIN/USER self-pairing with a lost successful reply and real process
death; one-use recovery with a lost commit reply; deletion plus explicit cancellation with a lost
cancel reply; consent grant replay and withdrawal; and the sixth-device quota refusal across real
process death. Their report SHA-256 values are, respectively,
`b72635330c4e8b966faa6f83ef56347538597c8e7ae300da86e58a476bf6b93b`,
`d6b2e53867fa82ce1b43c6a5ecef4c5504938613be999b89b660dd16e8f45111`,
`6c5b6c3de8f5b0e3df5070cab8193db8dfced33f43c5ca27cb2ae95cde8d4631`,
`22b2e25aa9f7bca36688e7399e77f813a4c0783c261b05dee72d1224b31242be` and
`2c8a9cde0c4d8da43b8b4718f47b6c7110fa324ba80d7bcf84688cdfaa0e0ccc`.
The quota run also produced reviewed EN/RU dark-theme screenshots bound by its three-file
manifest.

The read-only `scripts/admin_a55_evidence_audit.py` closure pulled the currently installed base
APK back from the phone, matched its complete byte hash and signer to the reviewed artifact,
revalidated all six manifests and five joined reports, bound the closed 15-file private inventory,
and confirmed one device digest, no raw serial, unchanged production-package metadata, zero ADB
reverse mappings, no QA process and no disposable harness Docker resource. The aggregate report
and manifest are `A55_EVIDENCE_AUDIT.json` and `A55_EVIDENCE_AUDIT.sha256.json` under
`.autplay-codex/evidence/a55-20260919/`; report SHA-256 is
`2de0c97418c776ccd1bf4ed428ddc8ea2ef21fcaa37eafaf346cad7b0c9c6a36`.

A separate physical instrumented test then opened the production
`ActivityResultContracts.OpenDocument` contract on the A55. The external
`com.google.android.documentsui` activity displayed a 54-byte synthetic, non-credential recovery
document, returned an external `content://` provider result and fed the selected bytes through the
same bounded production reader used by account recovery. The selected SHA-256 matched exactly and
the run completed **1/1** in 27.748 seconds. The reviewed screenshot, UI hierarchy, complete
instrumentation transcript and report are under
`.autplay-codex/evidence/a55-system-picker-20260919/`; report SHA-256 is
`20c6952565b6983da777b527ef78c977652e695aeaa3379afbfba1fd61928b3b`.

The reviewed install, APK and aggregate audit are also closed into the historical `0059`
`.autplay-codex/evidence/admin-target-acceptance-a55-20260919/`. Its valid `PENDING` manifest has
SHA-256 `68d16202a7570288bf45f8e2e3bf8119cc08c621f2cf99d7c4a498b8c2a491d5`;
`a55_reviewed_install` is `PASS`, and the picker artifacts are attached to the still-`PENDING`
`txt_manual_recovery` control. Migration `0060_local_bridge_authority` now supersedes that target
identity, so this preserved bundle cannot pass current validation. Gate B requires the Android
ceremonies and database receipts to be rerun against the exact deployed `0060` target before its
remaining controls can be reviewed as passing.

The current preparatory bundle is
`.autplay-codex/evidence/admin-target-acceptance-a55-20260920/`. It binds the reviewed A55/APK and
system-picker bytes, current origin/RP/TLS/network descriptors, the deterministic `0060` candidate,
local and remote migration/Compose reports, and the final read-only target snapshot. Validation
reports 28 artifacts / 26,886,516 bytes with all Gates A-D correctly `PENDING`; manifest SHA-256 is
`b20346a8a4fb2e5876710e6d4925fe90e8f74b4771d173d7dc45ae63dd6b6466`. The bundle deliberately does
not promote the earlier disposable-server Android ceremonies to target-server PASS evidence.

This is real A55 evidence, but Gate B remains pending until the same reviewed target server
identity is bound to the ceremonies and recovery receipts. Gate A still requires real Windows
Hello and A55 platform passkeys plus private-network exclusion; Gates C and D still require the
target-host measurement and isolated real-backup rehearsal.

## Target server and current candidate preflight (not deployment)

The intended server is already running on the operator's remote Ubuntu host `II`. Private Admin is
available at `https://ii-rtx3060.tailafc9b3.ts.net`, and private API/stream routing remains on the
same tailnet host at port 8443. The purchased names `api.autplay.win` and `stream.autplay.win`
resolve to the intended public address, while public TCP 443 and the PA3 edge remain deliberately
inactive. The live project stayed healthy on image
`c6518996ff1b0cdfaa4df24dcabe8a574b3019fefe2c31ebae8bcc08e09adb58` and migration
`0032_track_metadata` throughout these checks.

The current `0060` candidate is a deterministic 438-file source archive with SHA-256
`504cf8310f3f197db11ea856cf4200703215cd366647ccb306bb60baa2194aed` and source-tree SHA-256
`51a22090184fdbd9f5da921a58217f4e7429fddb10d1ec78935e078eaea9abda`. The target-built image is
`813f13bd270f96ba719716e4834e59cbccd4f78a5ea8eb23d67037523572c65d`. A fresh disposable database
reached `0060_local_bridge_authority` with 170 mapped tables, 1921 columns and 156 explicit indexes;
a separate database passed `0032 → 0060`. Their remote report hashes are
`8a6f2a596934853bd84764ec454a87b552d426ea1a8bade966f5356a1da1da6c` and
`3fe06db61f7ea6eff51f3a5141eda3433caf879664551761a03af13aeba54744`.

The complete current production Compose model also passed locally and on `II` with disposable
secrets, volumes, database and a non-overlapping private network. PostgreSQL, API, mobile API,
read-only stream and worker were healthy; both independent ledgers existed; the worker ran as
UID/GID 999 with `CapEff=0` inside cgroup2. The current remote Compose report SHA-256 is
`b2461600e1b9d08d4f5ccc1db8f3ef2127dbd882662ca5d18da4cff6723e4367`; the equivalent local report
SHA-256 is `1d607e41a6f88e4d0db6dd8014e5454b7e471754f7b6bfd0d8d6046de38aac4e`.

The preceding `0059` candidate was transferred as a deterministic 437-file source archive, SHA-256
`29d1b3dbe01f4b8968f5ea081859eaa2ecade07598a14d2a4ce2ba2a0f55183a`, and built on the target as
image `59607176a34fb999825840f8b1c15373404ae315d884bb7d9dff54d0d9a921f6`. A fresh disposable
database reached `0059` with the expected 170 mapped tables, 1921 columns and 156 explicit indexes;
a separate disposable database passed the exact `0032 → 0059` migration. Their report hashes are
`9750d341b69d162ce6eddd2d5772ea2e21c803775db4889ecf8f2177199a3800` and
`c83615decdd55669ca7c201760498c4ffc18032992d93df404321bed076ec801`.
These hashes remain historical evidence; the current hashes above supersede them for deployment
review.

The preceding `0059` production Compose model also passed on that Ubuntu host with disposable secrets,
volumes, database and a non-overlapping private network. PostgreSQL, API, mobile API, read-only
stream and worker were all healthy; both independent ledgers existed; the worker ran as UID/GID
999 with `CapEff=0` inside cgroup2. Docker's Ubuntu AppArmor profile required a worker-only
`apparmor=unconfined` bootstrap setting for the private cgroup2 mount. Seccomp, read-only rootfs,
`no-new-privileges`, the private cgroup namespace and the explicit bootstrap capability list stayed
active, and the process dropped every capability before executing worker code. The remote report
hash is `3b3ee99574324717dcc0b7717103a8c6119a25f456fb53c5117d15a5b2d5dd57`; the equivalent local
report hash is `f3a61adf12940980b9aebf6c4354c2e797c58e9dd69579520a196521e44a660f`.

The remote smoke used explicitly synthetic one-slot policy values only inside its disposable
database. It therefore proves packaging, startup order and Linux containment, but it is not Gate C
capacity evidence. Before/after digests show the live container set, image and migration head were
unchanged, and cleanup left no disposable container, volume or network. No production migration,
image switch, public-edge activation, credential registration or live policy application occurred.

## Supplemental M52 evidence (not a gate pass)

The connected Galaxy M52 was used only for non-substitutive device evidence. The isolated
`app.autplay.qa` debug APK (version 0.3.0, SHA-256
`d7eaf371df409c82c8db88132bf3a0945721c84bc1fffbe074d49aadee1989f2`) was installed
beside the existing production-ID app on SM-M526B, Android 13, 2025-05-01 security patch;
the raw ADB serial is omitted and its SHA-256 is
`3c3bbd67d1dbf47e1da56599151568a31cb39f76ae37f994bb3ca947b940ac53`.

Standard ADB/Gradle evidence shows the RU home/settings/training-consent content on the
physical display. The profile window retains `FLAG_SECURE`: Android correctly refused
screenshot bytes while UI hierarchy exposed only non-secret invitation/statistics labels.
The three focused recovery/deletion/training-consent instrumented classes first completed
**13/13 tests, 0 skipped, 0 failed** on the M52. A subsequent complete side-by-side connected run
completed **234 tests: 230 passed, 4 expected skipped, 0 failures/errors** in 271.626 device-test
seconds; Gradle reported **BUILD SUCCESSFUL** in 5m03s. The run includes the physical Android
Keystore, QR/self-pairing and secure-window UI, Room, HTTP, recovery/deletion/consent and real
WorkManager lifecycle surfaces. Skips are limited to the two-stage playback process harness, the
joined real-server pairing E2E and the emulator-only root Wi-Fi transport toggle.

The joined M5B class was then executed separately through the hardened side-by-side harness. It
completed **1/1 test** in a 47.049-second end-to-end run from physical Android OkHttp/Keystore
through FastAPI to a script-owned disposable PostgreSQL 18.4/pgvector database migrated through
`0059`, then verified enrollment, device binding, sync and revocation in durable server state.
The final counts were two devices, one revoked session and three security-audit events. The
invitation was consumed once through a loopback-only `adb reverse` handoff; neither the invitation
nor bearer credentials entered Gradle arguments or the report. The harness verified the
`app.autplay.qa` and `app.autplay.qa.test` package identities before installation, retained the
existing production-ID app and removed the reverse mapping, containers, volumes and network.

A dedicated role-complete joined proof then ran three independent QR ceremonies on the physical
M52. Separate ACTIVE OWNER, ADMIN and USER accounts each used the production Android recipient
runtime, OkHttp, Android Keystore and ordinary binding committer against FastAPI and a disposable
PostgreSQL 18.4/pgvector database at migration `0059`. All three binding commits and exchanges
passed in 53.994 seconds. For OWNER the harness discarded the first successful exchange reply after
the server committed it. Android retained the encrypted pending exchange journal across a real
`am force-stop`; `pidof` confirmed process exit, then fresh instrumentation replayed the exact
exchange and materialized the binding. ADMIN and USER remained ordinary controls. The run made four
exchange HTTP attempts for three durable `EXCHANGED` ceremonies, two active devices per role and
twelve self-pairing audit events. Each account's pre-seeded recovery credential
remained at generation 1 with the exact original verifier, and
`account.account_recovery_operation` remained empty. The QR payload was consumed once per role
through the loopback wrapper; only the loopback URL entered instrumentation arguments. QA state
was cleared between roles, the production package remained version 0.3.7-metadata/version code 11
with the same last-update timestamp, and all reverse mappings and disposable Docker resources were
removed. The sanitized private report is `SELF_PAIRING_ROLES_ANDROID_E2E.json`, SHA-256
`84321e7599527431ea623c7c56e8ccb998ee569ab43535e19d8c7ebbec8f46e9`, with its adjacent manifest.

A dedicated joined recovery proof then exercised the production Android manual-recovery runtime,
OkHttp and Keystore on the M52 against FastAPI and disposable PostgreSQL 18.4/pgvector at `0059`.
The wrapper dropped the first successful commit reply after the database transaction; Android made
one outcome call and recovered the committed result. Recovery generation advanced from 1 to 2,
two prior devices and application sessions were revoked together with one browser session,
passkey and trusted key, and exactly one new V2 device/session binding remained active. The
44.363-second run recorded one recovery operation and one audit event. Its loopback-only one-shot
handoff did not place the account ID, recovery code, refresh secret or bearer in Gradle arguments
or evidence. The production package remained version 0.3.7-metadata/version code 11 with the same
last-update timestamp; the reverse mapping and disposable Docker resources were removed. The
sanitized private report is `ACCOUNT_RECOVERY_ANDROID_E2E.json`, SHA-256
`a1be672b2d9f0174aa25a804b1cfb771ef92ac2ee8706691a6b31e9557cff514`, with its adjacent manifest.
This M52 run remains supplemental; the current A55 system-picker proof and the still-missing exact
target-server recovery receipt are tracked separately under Gate B.

A joined deletion/cancellation proof exercised the production Android deletion source and
cancellation recipient runtimes, OkHttp, M5 authority and Android Keystore against FastAPI,
PostgreSQL `0059` and the independent deletion ledger. The server snapshot taken before returning
the deletion reply was already `DELETION_PENDING`, authority generation 2, with zero active devices
or application sessions. The request had an exact 30-day cancellation window. The harness then
dropped the successful cancellation reply; Android resolved the exact outcome, rotated recovery
generation 1 to 2, restored the account at authority generation 3 and left one new V2 binding.
The original device/session remained revoked, the request ended `CANCELLED` revision 2, the
independent ledger recorded the cancellation, and two ACTIVE OWNER accounts remained. The
56.387-second run left the production package unchanged and removed QA state, reverse mapping and
disposable resources. The sanitized private report is `ACCOUNT_DELETION_ANDROID_E2E.json`, SHA-256
`3dce84fdab3fd5df6bce9e60341dade181c3f9154a6ccb2109ef5abfb4ba0170`, with its adjacent manifest.
Final purge and restored-backup exclusion remain Gate D work on the approved target backup.

A separate joined consent proof then exercised the production Android consent runtime, OkHttp,
M5 session authority and Android Keystore against FastAPI, PostgreSQL `0059` and the independent
encrypted SQLite consent ledger. The server committed the initial `GRANTED` decision while the
harness replaced its successful reply with `503`. Android retained the exact intent in its
encrypted journal; a fresh runtime replayed it and received the immutable revision-1 receipt,
then committed `WITHDRAWN` at revision 2. PostgreSQL and the independent ledger both ended at
`WITHDRAWN` revision 2 with exactly two operations, while the V2 device/session remained active.
The 48.704-second run made three consent PUTs, cleared the pending journal, left the production
package unchanged, and removed the one-shot handoff, reverse mapping and disposable resources.
The sanitized private report is `TRAINING_CONSENT_ANDROID_E2E.json`, SHA-256
`66ff69e41f5b5e66a838c6e9b563cbf89ad9f1f2929127ef98f2667af9377574`, with its adjacent manifest.
This supplements the Android lifecycle proof; restored-backup fencing and completed-model serving
remain Gate D work on the target backup and server.

A joined device-quota proof then filled an OWNER account to the default five active devices and
sessions before starting a sixth physical M52 self-pairing recipient. The production recipient
runtime made the initial exchange and one exact retry; both reached FastAPI/PostgreSQL and returned
the allowlisted `account_device_limit_reached` code. The encrypted pending ceremony remained
retryable, the server ceremony remained `APPROVED` revision 3, all five existing devices and
sessions stayed active, and recovery generation 1 and its verifier remained unchanged with zero
recovery operations. The run exposed and fixed the Android transport's handling of the standard
nested `error.code` envelope. The 59.520-second run split the exchanges across separate
instrumentation processes: after the first refusal the harness issued `force-stop`, confirmed the
QA PID was absent, then a fresh process recovered the encrypted pending journal and repeated the
exact exchange. It also rendered the pending refusal on the M52 in RU and EN. Both visually reviewed dark-theme captures show the localized limit message and
retry action, with no Close action while the encrypted request remains pending. The run left the
production package unchanged and removed QA state, reverse mapping and disposable resources. The sanitized private report is
`SELF_PAIRING_DEVICE_QUOTA_ANDROID_E2E.json`, SHA-256
`9749801191db02019fc8fc1be63b5ec469bf8166309b4df76d26963d7607a29c`, with its adjacent
three-file manifest. This confirms the physical refusal path but does not replace Gate C's joint target
resource measurement or Gate B's A55 message check.

The two playback process stages skipped by the ordinary suite were also run separately on the
physical M52. The hardened harness permitted a physical target only in side-by-side QA mode,
verified both APK package identities, seeded a playing three-item queue, persisted edited order,
position, repeat/shuffle and listening state, proved the QA process exited after `force-stop`, and
restored the same queue and a position above ten seconds in a fresh process. Both stages passed;
the receipt records `physical_qa_side_by_side`, the two reviewed APK hashes and the serial digest.

A separate hardened P14 joined run completed **1/1 test** in 30.545 seconds through two
independent file-backed Room databases, production `OkHttpSyncTransport`, FastAPI and disposable
PostgreSQL 18.4 at migration `0059`. It closed/reopened Room after the offline transaction,
committed the event while deliberately losing the first ACK, retried the same immutable event
ID/hash as duplicate `APPLIED`, and projected it exactly once on the second device. Durable counts
were one device-event inbox row, one sync event, one user-track projection and two device cursors.
The harness passes only a loopback handoff URL to instrumentation; account IDs and both disposable
bearers are consumed once from memory and never enter Gradle arguments or the report. During this
run the old test fixture's unscoped credential store was corrected to mirror real profile-scoped
Keystore behavior, including the independent deletion-journal slot.
The resulting current test APK then passed the complete `SyncCoordinatorAcceptanceTest` class
without joined arguments: **23/23 passed, 0 failed/skipped**, covering the ordinary local/fake path
as well as the now-current fixture format.

The first full attempt exposed a stale lifecycle fixture: production sync now performs
`/devices/bind` before push/pull, while its MockWebServer still returned a pull document for that
route. The fixture now verifies and echoes the exact non-secret binding; both affected physical
WorkManager scenarios pass in isolation and in the complete run. The unsafe `su 0 svc wifi`
scenario now uses a declared emulator assumption instead of trying to alter a physical phone.
Private screenshots/hierarchies and the sanitized full summary/hash manifest are under
`.autplay-codex/evidence/m52-20260919/`; the generated connected-test report is under
`apps/android/build/reports/androidTests/connected/debug/`. Gradle removed the QA package after
testing, so the same reviewed APK was reinstalled; `app.autplay.qa` and the untouched
production-ID `app.autplay` still coexist. The joined report and its SHA-256 manifest are
`M5B_PROFILE_PAIRING_E2E.json` and `M5B_PROFILE_PAIRING_E2E.sha256.json` in the same private
evidence directory; the report contains the serial digest and no raw device serial or credential.
The process-death receipt, sanitized stage outputs and their manifest are under
`l1-process-death/`; they also contain no raw serial. After all physical harnesses, the production
package remains version 0.3.7-metadata, version code 11, with its last-update timestamp unchanged.
The P14 report and manifest are `P14_ANDROID_SERVER_E2E.json` and
`P14_ANDROID_SERVER_E2E.sha256.json`; the report binds the QA APK hashes, uses only the serial
digest and contains no credential. The focused 23-test receipt, instrumentation output and hashes
are under `sync-coordinator-current/`. The role-complete self-pairing run used QA APK hash
`41f6d7f291e12a5a5292a64c9b5985d472fbb3485f93fa72b7b9601ba8cc866a` and test APK hash
`23b41f5bfa0ee3d56a262493e3203f0286e6bd71fcf3f3de6f9ccb1432d3aba7`. The quota proof used
the updated QA APK hash `41f6d7f291e12a5a5292a64c9b5985d472fbb3485f93fa72b7b9601ba8cc866a`
and test APK hash `2638bd8410b9b317529edce71af1ba529a69cf17c6c7b3a5ce6a6bde6135bbca`.

The read-only `scripts/admin_m52_evidence_audit.py` closure then revalidated all ten canonical
M52 reports and their ten heterogeneous SHA-256 manifests without replaying a physical test. It
bound a 44-file, 1,573,431-byte private inventory; confirmed one device digest throughout; scanned
every file for the connected raw serial; compared all five recorded production-package
fingerprints with the currently installed package; and confirmed zero ADB reverse entries, no QA
process and no disposable harness Docker resource. Three retained zero-byte captures from failed
early screenshot attempts are named explicitly as non-evidence placeholders; any other empty file
is rejected. The aggregate report and manifest are `M52_EVIDENCE_AUDIT.json` and
`M52_EVIDENCE_AUDIT.sha256.json`; report SHA-256
`6ec0ea322d70df43a025c4364dc978b0f6606c92fae22c0da578a0f47d87a439`.
The refreshed audit also rejects JSON booleans masquerading as integer schema versions or numeric
test counts; it passed against the unchanged ten-report M52 set after that type hardening.

This M52 evidence exercises real disposable server receipts but does not exercise the exact target
server and satisfies neither Gate A nor Gate B. The exact target server and the remaining
A55/operator controls stay mandatory below.

## Required inputs and authority

The operator supplies the intended server instance, canonical private HTTPS Admin origin/RP ID,
the actual Samsung Galaxy A55, the target Windows laptop with Windows Hello, and the selected
device-level private-network policy. Changing live network admission, registering real passkeys,
using real credentials, restoring a real backup and applying measured budgets each require the
operator's explicit authorization for that prepared target.

Record source/HEAD, server build and migration, Android APK application ID/version/signing digest,
Windows build, browser version, A55 model/serial hash and Android build, network-policy revision,
server identity/epoch, hardware/storage/database configuration digest, and UTC start/end. Evidence
must omit cookies, recovery codes, private keys, bearer tokens and raw device identifiers.

The operator deferred the real pre-deployment backup until a dedicated NAS is available. The
target-bound helper is therefore staged at
`/srv/autplay/operator/admin-target-backup/admin_target_nas_backup.py` on `II` (SHA-256
`c3067cb8ee9b431ce865e3cdcefdf90a92ae6057b76a1adbe93448622f83278f`) but must not be executed
against local production storage. After the NAS is mounted on `II`, first run its read-only
preflight; `--execute` is the explicit mutation boundary and `--leave-stopped` is reserved for the
immediately following Gate D/deployment window:

```bash
python3 /srv/autplay/operator/admin-target-backup/admin_target_nas_backup.py \
  --destination-root /mnt/nas/autplay-backups
python3 /srv/autplay/operator/admin-target-backup/admin_target_nas_backup.py \
  --destination-root /mnt/nas/autplay-backups --execute
```

The helper rejects the root filesystem, symlinks, insufficient free space, baseline/candidate hash
drift, an active public edge, and unhealthy core services. It leaves the independent music-download
shards running, quiesces only stateful production consumers, writes an `.incomplete` generation,
verifies every file by SHA-256 readback, and publishes the generation atomically only after a
completion record exists. Unless a successful run explicitly uses `--leave-stopped`, it restores
the preserved runtime even when backup creation fails.

Create the private, ignored evidence bundle before touching a target:

```powershell
uv run --project server --frozen python scripts/admin_target_acceptance.py scaffold `
  --output-directory .autplay-codex/evidence/admin-target-acceptance-<UTC> `
  --branch codex/readme-current-state `
  --head 860511aae9f1fcdb98b7b9b944f97ef27929fb8b `
  --dirty-worktree
```

The generated manifest contains the exact controls below but starts as `PENDING`. Keep every
artifact inside that directory, record only its relative path, SHA-256 and UTC capture time, and
set `redaction_reviewed` only after manual review. The validator rejects missing controls,
unlisted files, symlinks, changed hashes, evidence outside the acceptance window, incomplete
target identity, any passing gate outside migration `0060_local_bridge_authority`, a Gate B
build other than `app.autplay` 11 / `0.3.7-metadata`, resource-report versions below v1/v3 and a
false aggregate `PASS`. Gate C reports are parsed as soon as Gate C is marked `PASS`, including
while other gates remain pending:

```powershell
uv run --project server --frozen python scripts/admin_target_acceptance.py validate `
  --bundle .autplay-codex/evidence/admin-target-acceptance-<UTC>
```

At any point, use the read-only inspection command to obtain the exact missing target fields,
incomplete controls and current artifact count for every gate. It runs the same integrity and
semantic validation first and does not modify the bundle:

```powershell
uv run --project server --frozen python scripts/admin_target_acceptance.py inspect `
  --bundle .autplay-codex/evidence/admin-target-acceptance-<UTC>
```

Exit `0` means all four declared gates are complete and internally consistent, exit `1` means a
valid but still pending/failed rehearsal, and exit `2` means invalid evidence. This is a closure
and integrity check: it cannot decide whether a screenshot, transcript or operator assertion is
truthful. Operator review remains mandatory.

Each passing gate's `operator_signoff` control contains exactly one strict JSON object with
`schema_version`, `gate`, `status`, `reviewer`, `reviewed_at`, `targets_sha256` and
`evidence_sha256`. `status` is `PASS`; the reviewer identifier uses only safe non-secret identity
characters. `targets_sha256` hashes the compact key-sorted JSON subset of target fields required by
that gate. `evidence_sha256` hashes the compact key-sorted JSON of every other control's status and
evidence descriptors. The validator requires review time inside the target window, no earlier than
the latest reviewed artifact and no later than the sign-off artifact's capture time. Changing a
target field, control status, evidence path, capture time or artifact hash invalidates the sign-off;
the sign-off file cannot bind itself.

Gate D additionally parses exactly one JSON artifact from each of `restored_state_assertions` and
`live_pid_abort` whenever Gate D claims `PASS`, even if another gate is still pending. Both use
`schema_version: 1`, `status: PASS`, and repeat the exact manifest values for `migration_head`,
`server_identity_sha256`, `environment_sha256` and `backup_sha256`. The restored-state report must
assert an isolated environment, stopped supervisors, both independent ledger guards, the offline
drain, all four bounded cleanup routes, readiness only after the guards, absence of finally deleted
accounts, refusal of withdrawn inputs, serving of the current completed publication tuple, and no
capacity release without host-empty evidence. It must set `production_direct_targeted` and
`open_execution_capacity_released_without_host_empty` to `false`.

The live-PID report records `platform` as `windows` or `linux`, exact
`error_code: offline_process_still_running`, a SHA-256 process identity, and SHA-256 snapshots of
the relevant database state before and after the attempt. It requires a live observed process, an
aborted transaction, equal before/after state hashes and `production_direct_targeted: false`.
Unknown, missing, duplicate or wrongly typed fields are rejected. These structured assertions make
the claimed outcomes machine-checkable; the underlying transcripts and operator review remain the
proof that the observations are truthful.

Every target `*_sha256` is the lowercase SHA-256 of the exact operator-reviewed record bytes;
`environment_sha256` must equal both measurement reports. The one structured exception is
`server_identity_sha256`: hash the ASCII bytes of the compact, key-sorted JSON object
`{"identity_epoch":N,"server_instance_id":"canonical-uuid"}`. This binds Gate C to both the
persisted server UUID and its epoch without placing either raw value in the acceptance manifest.
The current canonical server-only gate passes **253/253 contract/release tests** and **2220 server
tests**, with 43 documented platform skips. Ruff passes 650 files and strict mypy passes 586 source
files. The target-acceptance validator accepts the new closed 28-artifact bundle as `PENDING` and
its inspection lists the exact incomplete controls and missing Gate C/D target digests.

The accompanying project gates also pass: GPU **41 passed / 2 Windows symlink skips**, Sona
training **87 passed**, local acquisition **231 passed / 4 platform skips**, and Android **376/376**
unit tests plus `lintDebug`, `assembleDebug`, `assembleTrustedLan` and release/R8. The current local
debug APK has SHA-256
`a45993b1b337dc6e4328687aef3e15e1c671a78c0055d80abfc23ad3aa214e96`. It is a local build-gate
artifact; the Gate B install/evidence remains the separately audited A55 APK recorded above and
still requires the exact `0060` target lifecycle run.

A final read-only target check reproduces production migration `0032_track_metadata`, image
`c6518996ff1b0cdfaa4df24dcabe8a574b3019fefe2c31ebae8bcc08e09adb58` and container-set SHA-256
`70b8f44d79f9e29b0d12837aef5c3123ffdb6abd7568bba28dffa5288acdd170`, with all five core services
healthy. The candidate archive remains
`504cf8310f3f197db11ea856cf4200703215cd366647ccb306bb60baa2194aed`; the same 438 current source
files reproduce source-tree SHA-256
`51a22090184fdbd9f5da921a58217f4e7429fddb10d1ec78935e078eaea9abda`.

## Gate A: private network and real WebAuthn

1. Confirm the canonical private HTTPS origin and RP ID from both allowed targets. Verify the
   certificate chain and that no public or alternate origin reaches Admin Web.
2. From an unlisted third device on the same tailnet/private network, prove that TCP/HTTPS access
   is denied before browser authentication. Tailnet membership alone is not the allow rule.
3. On the Windows laptop, register a real platform credential and complete login with Windows
   Hello user verification. Exercise cancel, wrong origin/RP, replayed challenge, revoked
   credential and post-revocation session refusal.
4. On the A55, register a separate real platform credential and complete independent login.
   Exercise the same cancellation/revocation boundary and the narrow/keyboard EN/RU Admin views.
5. Prove that a synchronized passkey on a device outside the network allowlist still cannot reach
   Admin Web. Preserve only credential public metadata and sanitized operation/audit IDs.

Pass requires the four Admin sections in EN/RU at desktop and A55 width, keyboard navigation on
the laptop, user-verifying ceremonies on both targets, exact revocation, and network denial for
the third device. Virtual-authenticator test evidence remains useful protocol evidence but cannot
satisfy this gate.

## Gate B: Android lifecycle on the A55

The reviewed production-ID APK was installed on a clean A55, and the joined physical runs already
exercise self-service QR pairing for every ACTIVE role, encrypted pending state across process
death, recovery after a simulated lost reply, deletion request and explicit cancellation, consent
grant/withdrawal, and quota refusal messages. To close this gate, bind equivalent ceremonies and
independent server/database receipts to the same reviewed target server identity. The real A55
system picker and production bounded reader are already proven with a synthetic document; the
server-bound run must preserve their result with the target recovery receipt. Recovery must consume one code, rotate
it, revoke all prior devices/application sessions/browser sessions/trust/passkeys, and leave
exactly one new binding. Pairing must preserve the recovery generation.

Use standard ADB/Gradle control only after `adb devices -l` shows the exact A55 and the operator
has selected its serial. Artemis is not required and should be used only if the operator
explicitly requests it. Store screenshots/traces in a private evidence directory and review them
for secrets before sharing. Audio quality, durable server state and backup behavior need their
own instrumentation; screenshots do not prove them.

## Gate C: joint resource measurement

Run one steady-state combined workload on the intended server and its real storage/database/
network configuration. It must sustain the proposed audio ceilings while exercising all six
audio paths and the worst permitted simultaneous mix of all eleven internal v3 paths:

```text
PLAYBACK_CURRENT_NEXT, RANGE_SEEK, DOWNLOAD, UPLOAD,
INTERNET_ACQUISITION, A1_ACQUISITION,
INGEST_ANALYSIS_PUBLICATION, FINALIZED_STAGING_CLEANUP,
PROVIDER_STAGING_CLEANUP, PROVIDER_SCRATCH_RETIREMENT,
ORPHAN_RETIREMENT, ORPHAN_MISSING_CHECK, VAULT_INVENTORY,
DEVICE_UPLOAD_CLEANUP, METADATA_ENRICHMENT,
TRAINING_DATASET_CHECKPOINT, TRAINING_ROOT_CLEANUP
```

Capture the exact recipe/workload digest, environment digest, operation outcomes, useful byte
rates, CPU/disk/network peaks, database and queue p95, maximum queue depth, failures and timeouts.
Produce the bounded resource report v1 and joint internal report v3 defined in
`docs/design/AutPlay_Resource_Measurement_Report_v1.md`. Review their exact byte hashes before
running the initialize/apply commands. Do not infer the ceilings from launched clients,
process-local concurrency or a synthetic unit-test fixture. Applying reports to a live database
is a separate operator action after review.

## Gate D: isolated restored-backup rehearsal

Restore an operator-approved backup into an isolated network and storage namespace with all
normal supervisors stopped. Verify the independently retained deletion and consent ledgers, then
run the trusted offline execution drain with the exclusive training roots. Drain ordinary Vault,
upload, CPU/ingest and metadata cleanup; run both restore guards; only then start readiness and
acceptors. Prove that finally deleted accounts cannot return, withdrawn training inputs cannot
authorize unfinished work, completed published models still serve when their current publication
tuple is valid, and no restored open execution frees capacity without real host-empty evidence.

Pass requires preserved command output, ledger/backup identity digests, the strict
`restored_state_assertions` report described above and the strict `live_pid_abort` report from a
negative rehearsal where a deliberately live persisted PID causes the whole offline-drain
transaction to abort. The rehearsal must never target production directly.

## Sign-off

The unified Admin/account goal can close only when all four gates have dated PASS evidence tied
to the exact target identities and reviewed builds. Any configuration or identity change that
invalidates the recorded environment requires only the affected physical gate to be repeated.
