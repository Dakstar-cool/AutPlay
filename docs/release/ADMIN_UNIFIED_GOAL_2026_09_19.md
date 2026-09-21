# Unified Admin/account goal status: 2026-09-19

The operator confirmed that the intended target phone is the Samsung Galaxy A55 (`SM-A556E`);
earlier M55 wording was a naming error. The target schema and active documentation use A55, while
all M52 results remain explicitly supplemental.

This is the single acceptance index for the seven ADR-053 criteria. Local implementation is
complete to the physical/deployment boundary. The goal remains active because target evidence
cannot be replaced by emulators, virtual credentials, synthetic measurements or invented data.

| Criterion | Local result | Remaining target proof |
| --- | --- | --- |
| 1. Four Admin sections and browser WebAuthn | Implemented and covered by PostgreSQL/HTTP/browser protocol tests, EN/RU rendering, narrow/desktop and virtual-authenticator cases. | Gate A: canonical private HTTPS origin, real Windows Hello and A55 platform credentials, keyboard/mobile views and third-device network denial. |
| 2. QR self-service for every ACTIVE role | Implemented with initiating account/device/session and new-key binding, comparison confirmation, replay/expiry checks and unchanged recovery generation. A joined physical A55 run completed separate OWNER, ADMIN and USER ceremonies through the production Android runtime, OkHttp and Keystore into FastAPI/PostgreSQL. For OWNER the harness discarded the first successful exchange reply after commit, retained the encrypted pending journal across a real process stop, then replayed the exact exchange and materialized the binding in fresh instrumentation. All three Android binding commits and server exchanges passed while the recovery authority stayed unchanged. | Gate B still binds equivalent ceremonies and receipts to the exact reviewed target server identity. |
| 3. One-use TXT/manual recovery | Implemented with atomic generation consumption, code rotation, revocation of prior devices, application/browser sessions, trust, passkeys and pending admission, then one new binding. Lost replies remain recoverable after ordinary receipt cleanup only with the exact committed refresh secret, request, key and live result binding. A joined physical A55 run deliberately lost the successful commit reply, recovered it through the outcome route, rotated generation 1 to 2 and left one active V2 binding after revoking all seeded prior authority. A separate 1/1 physical run selected a synthetic recovery document in the real external system picker and verified its exact bytes through the production bounded reader. | Gate B still requires the combined recovery receipt and equivalent ceremonies to be bound to the exact target server identity. |
| 4. Suspension, cancellation, purge and restored-backup protection | Immediate suspension, explicit pre-deadline cancellation, symmetric refresh-bound lost-cancel outcome, last-ACTIVE-OWNER serialization, final purge, independent deletion ledger/startup fence and trusted offline execution drain are implemented. A joined physical A55 run additionally proved source detach, immediate `DELETION_PENDING` with zero active authority, a 30-day request, explicit cancellation after a deliberately lost success reply, recovery-code rotation and one replacement V2 binding with matching independent-ledger cancellation evidence. | Gate D on an operator-approved isolated real backup and target storage/process policy. |
| 5. Explicit shared-training consent | Default-private monotonic consent, current checks before payload/start/batch/publication, withdrawal invalidation/input cleanup, independent restore ledger/fence, retained process/storage cleanup and current completed-model publication serving are implemented. Final privacy deletion irreversibly revokes serving for affected PUBLISHED runs while ordinary withdrawal preserves completed models. A joined physical A55 run additionally proved encrypted Android grant replay after a deliberately lost success reply, withdrawal, final revision 2 and matching independent-ledger evidence through the production runtime/transport. | Gate D proves the independent ledgers and current publication tuple on the restored target. |
| 6. Quotas and global admission | Editable defaults 5 devices/2 playback/2 transfer, per-account/global CAS, six audio paths and eleven internal report-v3 paths are enforced. I/O authority is database-clock bounded to five seconds; capacity remains through STOPPING/crash until exact process-tree exit and cleanup. A joined physical A55 proof filled the default five-device allocation and showed that the production Android recipient received `account_device_limit_reached` on both the initial exchange and exact retry across process death without changing existing device, session or recovery authority; EN/RU dark-theme captures are hash-bound. | Gate C creates reviewed real reports from the worst permitted joint workload on the intended server; applying those reports remains a separate operator action. |
| 7. End-to-end acceptance | Current PostgreSQL/server/Android/browser races, replay, rollback, revocation, quota, crash, EN/RU, applicable static/build gates and bounded independent reviews are recorded in the linked evidence. The power-loss audit found Git intact and PostgreSQL WAL recovery healthy. | Gates A-D in the [target runbook](ADMIN_TARGET_ACCEPTANCE_2026_09_19.md). |

## Current local boundary

Migration head is `0060_local_bridge_authority`: 170 tables, 1921 columns and 156 explicit
indexes; mapping fingerprint
`f3f1f8db5c44bb46d1f8d07a6df85618b6b4af04fa568d2933696f327b893215`. Branch/HEAD remain
`codex/readme-current-state` / `860511aae9f1fcdb98b7b9b944f97ef27929fb8b`; the extensive
working tree and adjacent user changes are preserved. No commit, push, deployment, production
migration, live-network change, real credential registration or measured-budget application
occurred.

Migration `0060` closes the legacy acquisition-bridge byte path. Its explicitly provisioned,
deterministic owner device now derives a schema-bounded `LOCAL_BRIDGE` authority with no user
session or bearer token. Every upload chunk uses transfer admission, a retained Vault child,
post-write authority revalidation and exact process-exit cleanup. Real PostgreSQL/filesystem replay,
the adjacent guarded downgrade, schema drift and the surrounding resource/upload regressions pass.

The clean target A55 now carries the reviewed production-ID build `app.autplay` version code 11 /
`0.3.7-metadata`. Its APK SHA-256 is
`4efa69ee06fca167226ad6fea4e0404f688d8611f06920f86a0f31922513e276`; no prior production or QA
package existed on the phone. Five joined physical runs passed for role-complete self-pairing,
one-use recovery, deletion/cancellation, consent grant/withdrawal and the sixth-device quota
refusal. The role and quota cases include real `force-stop`, confirmed process exit and fresh
instrumentation; all runs used fresh PostgreSQL databases migrated through `0059`, persisted no
credential, left the production package unchanged and removed reverse mappings and disposable
Docker resources. The closed 15-file private evidence set is under
`.autplay-codex/evidence/a55-20260919/`. `scripts/admin_a55_evidence_audit.py` pulls the installed
base APK back from the A55, verifies its full byte hash and signer, validates all six manifests and
five report semantics, rejects raw-serial or unmanifested files, and records the remaining gate
limitations. Its aggregate report SHA-256 is
`2de0c97418c776ccd1bf4ed428ddc8ea2ef21fcaa37eafaf346cad7b0c9c6a36`.
The physical system-picker proof is under
`.autplay-codex/evidence/a55-system-picker-20260919/`; its report SHA-256 is
`20c6952565b6983da777b527ef78c977652e695aeaa3379afbfba1fd61928b3b`.
It records `com.google.android.documentsui`, an external `content://` result, exact 54-byte
document digest verification through the production bounded reader, unchanged production-package
metadata and complete cleanup.
The reviewed install, APK and audit are copied into the closed historical partial acceptance bundle
`.autplay-codex/evidence/admin-target-acceptance-a55-20260919/`; its `PENDING` manifest for `0059` has
SHA-256 `68d16202a7570288bf45f8e2e3bf8119cc08c621f2cf99d7c4a498b8c2a491d5` and records
`a55_reviewed_install` as `PASS`; the picker evidence remains attached to the `PENDING` manual
recovery control without promoting disposable-server runs to target-server proof. It is not valid
for the new `0060` target identity and remains preserved as historical evidence.

The preceding `0059` remote candidate was qualified without touching its live project. The deterministic
candidate archive SHA-256 is
`29d1b3dbe01f4b8968f5ea081859eaa2ecade07598a14d2a4ce2ba2a0f55183a`; its target-built image is
`59607176a34fb999825840f8b1c15373404ae315d884bb7d9dff54d0d9a921f6`. Fresh `0059`, separate
`0032 → 0059`, and full disposable production-Compose checks all pass on `II`. The Compose check
proved both independent ledgers, healthy API/mobile/stream/worker, worker UID/GID 999, zero
effective capabilities and cgroup2 containment. Its before/after evidence shows the live image
`c6518996ff1b0cdfaa4df24dcabe8a574b3019fefe2c31ebae8bcc08e09adb58`, live migration
`0032_track_metadata` and production container set were unchanged. Synthetic one-slot resource
values were confined to the disposable smoke and do not satisfy Gate C.

The current `0060` candidate has now repeated that complete qualification. Its deterministic
438-file archive SHA-256 is
`504cf8310f3f197db11ea856cf4200703215cd366647ccb306bb60baa2194aed`, source-tree SHA-256 is
`51a22090184fdbd9f5da921a58217f4e7429fddb10d1ec78935e078eaea9abda`, and the target-built image is
`813f13bd270f96ba719716e4834e59cbccd4f78a5ea8eb23d67037523572c65d`. Clean `0060`, separate
`0032 → 0060`, and complete disposable production-Compose checks pass locally and on `II`. The
remote report hashes are `8a6f2a596934853bd84764ec454a87b552d426ea1a8bade966f5356a1da1da6c`,
`3fe06db61f7ea6eff51f3a5141eda3433caf879664551761a03af13aeba54744` and
`b2461600e1b9d08d4f5ccc1db8f3ef2127dbd882662ca5d18da4cff6723e4367`. Before/after evidence again
binds the unchanged live `0032` image, migration and container set. The new closed 28-artifact
acceptance bundle is `.autplay-codex/evidence/admin-target-acceptance-a55-20260920/`; its valid
`PENDING` manifest SHA-256 is
`b20346a8a4fb2e5876710e6d4925fe90e8f74b4771d173d7dc45ae63dd6b6466`. Deployment and Gates A-D
remain separate operator-reviewed steps.

The final read-only integrity pass still reproduces the candidate archive SHA-256 above and the
438-file source-tree SHA-256
`51a22090184fdbd9f5da921a58217f4e7429fddb10d1ec78935e078eaea9abda`. On `II`, production remains
at `0032_track_metadata`, image
`c6518996ff1b0cdfaa4df24dcabe8a574b3019fefe2c31ebae8bcc08e09adb58` and container-set SHA-256
`70b8f44d79f9e29b0d12837aef5c3123ffdb6abd7568bba28dffa5288acdd170`; all five core services are
healthy. The prepared candidate image and archive also retain their recorded hashes, and no
candidate disposable Compose project remains on the host.

The canonical server-only gate passes 253 contract/release tests and 2220 server tests, with 43
documented Windows/Linux platform skips. Ruff passes 650 files and strict mypy passes 586 source
files. The only failed intermediate full run exposed a stale PostgreSQL statistics snapshot in a
lock-observer test; refreshing `pg_stat_activity` snapshots preserved the exact-lock assertion and
the subsequent 15-case module and complete canonical rerun passed.

The remaining canonical project gates pass as well: GPU has 41 passing tests and two documented
Windows symlink skips; Sona training has 87 passing tests; local acquisition has 231 passing tests
and four platform skips; Android has 376/376 unit tests plus `lintDebug`, `assembleDebug`,
`assembleTrustedLan` and release/R8. The first Android pass exposed an obsolete owner-port test
fixture that treated internal deletion/recovery journal reads as active-profile reads. The fixture
now returns an empty result for those separate slots; the focused test and complete Android gate
pass. The current local debug APK SHA-256 is
`a45993b1b337dc6e4328687aef3e15e1c671a78c0055d80abfc23ad3aa214e96`; Gate B remains bound to the
separately reviewed physical-A55 artifact and its exact-target rerun.

The restored-process continuation is in
[offline execution drain](ADMIN_OFFLINE_EXECUTION_DRAIN_2026_09_19.md). Independent review
found one Linux cgroup-root validation defect; the pinned-descriptor `cgroup2` fix passed the
Linux regression and follow-up review returned `APPROVED` with no other P0-P2 finding. The
full `restore-drain` command also completed one real disposable-PostgreSQL recovery from
persisted absent PID through filesystem cleanup, `CLOSED` and zero retained capacity. The
connected Galaxy M52 supplied supplemental side-by-side QA evidence: current RU screens,
secure-window refusal on Profile, **13/13** focused recovery/deletion/training-consent tests and
the complete connected suite (**234 total, 230 passed, 4 expected skipped, 0 failures/errors**)
passed. The full run also found and closed a stale `/devices/bind` WorkManager test-fixture gap;
the two affected lifecycle cases pass on the physical phone. The separately invoked joined M5B
case then passed **1/1** through physical Android OkHttp/Keystore, FastAPI and a disposable
PostgreSQL 18.4/pgvector database at migration `0059`; durable state confirmed two devices, one
revoked session and three security-audit events. Its loopback-only one-shot handoff, side-by-side
package checks, serial redaction and complete container/network cleanup are recorded in the
private hashed evidence bundle. A second joined Android/server proof then exercised QR
self-pairing for three separate ACTIVE accounts, one per role. OWNER, ADMIN and USER each completed
the production recipient runtime, real comparison/account confirmation, Android Keystore binding,
FastAPI exchange and PostgreSQL commit. For OWNER the wrapper discarded the first successful
exchange reply after the commit. The encrypted pending journal survived `am force-stop`, `pidof`
confirmed process exit, and fresh instrumentation replayed the exact exchange and materialized the
binding; ADMIN and USER remained ordinary control pairings. The 53.994-second run made four
exchange attempts for three exchanged ceremonies, six active devices and twelve self-pairing audit
events; every recovery credential retained its original verifier at generation 1 and the
recovery-operation count stayed zero. Its one-shot QR handoffs exposed only the loopback URL to
instrumentation, the QA binding was cleared after every role, and the production package metadata
remained unchanged. The private report and manifest are `SELF_PAIRING_ROLES_ANDROID_E2E.json` and
`SELF_PAIRING_ROLES_ANDROID_E2E.sha256.json`; report hash
`84321e7599527431ea623c7c56e8ccb998ee569ab43535e19d8c7ebbec8f46e9` was revalidated and the bundle
contains no raw serial. A third joined proof exercised manual one-use account recovery on
the M52 through the production recipient runtime, OkHttp and Android Keystore into FastAPI and a
disposable PostgreSQL 18.4/pgvector database at `0059`. The harness dropped the successful commit
reply once; Android resolved the exact outcome once, rotated recovery generation 1 to 2, revoked
two prior devices and application sessions plus one browser session, passkey and trusted key, and
left one active V2 device/session binding. The 44.363-second run created exactly one recovery
operation and audit event. Its one-shot loopback handoff exposed no account ID, recovery code,
refresh secret or bearer in Gradle arguments or the report. The production package remained
version `0.3.7-metadata`, version code 11 with its original update timestamp; ADB reverse and all
disposable Docker resources were removed. The private report is
`ACCOUNT_RECOVERY_ANDROID_E2E.json`, SHA-256
`a1be672b2d9f0174aa25a804b1cfb771ef92ac2ee8706691a6b31e9557cff514`, with its adjacent manifest;
the bundle contains no raw serial. The recovery run also validated the TCP-specific PostgreSQL
healthcheck that prevents Compose from accepting the official image's socket-only temporary
initialization server. A fourth joined M52 proof exercised account deletion and explicit
cancellation through the production Android source/recipient runtimes, OkHttp, M5 authority and
Android Keystore into FastAPI, PostgreSQL `0059` and the independent deletion ledger. Before the
request reply, the server snapshot was already `DELETION_PENDING`, authority generation 2, with
zero active devices and sessions. The request retained an exact 30-day cancellation window. The
harness then dropped the successful cancellation reply; Android recovered the exact outcome,
rotated the recovery credential from generation 1 to 2 and created one replacement V2 binding.
Final state was `ACTIVE` at authority generation 3, with the original device/session revoked,
request state `CANCELLED` revision 2 and two ACTIVE OWNER accounts. The 56.387-second run preserved
the production package and removed QA state, reverse mapping and disposable resources. Its private
report is `ACCOUNT_DELETION_ANDROID_E2E.json`, SHA-256
`3dce84fdab3fd5df6bce9e60341dade181c3f9154a6ccb2109ef5abfb4ba0170`, with its adjacent manifest;
the bundle contains no raw serial or credential. A fifth joined M52 proof exercised explicit shared-training consent through
the production Android runtime, OkHttp, M5 session authority and Android Keystore against FastAPI,
PostgreSQL `0059` and a separate encrypted SQLite consent ledger. The first successful grant reply
was deliberately replaced by `503`; the encrypted Android journal retained the exact intent, a
fresh runtime replayed it, and the server returned its immutable revision-1 `GRANTED` receipt.
Android then withdrew consent at revision 2. PostgreSQL and the independent ledger both ended at
`WITHDRAWN` revision 2 with exactly two operations; the V2 device/session remained active and
unrelated authority was unchanged. The 48.704-second run made three PUT calls for two committed
operations, cleared the encrypted pending journal, preserved production package metadata, and
removed the one-shot handoff, reverse mapping and disposable Docker resources. The private report
is `TRAINING_CONSENT_ANDROID_E2E.json`, SHA-256
`66ff69e41f5b5e66a838c6e9b563cbf89ad9f1f2929127ef98f2667af9377574`, with its adjacent manifest;
the bundle contains no raw serial or credential. A sixth joined M52 proof filled one OWNER account
to the default five active devices and sessions, then ran the production Android self-pairing
recipient through two exchange attempts. Both returned the allowlisted
`account_device_limit_reached` code; the encrypted pending ceremony remained retryable, the
ceremony remained `APPROVED` revision 3, all five existing devices and sessions stayed active,
and recovery generation 1 and its verifier remained unchanged with zero recovery operations.
The run exposed and fixed the Android transport's parsing of the standard nested `error.code`
envelope. The 59.520-second proof split the two exchange attempts across separate instrumentation
processes: after the first refusal the harness issued `force-stop`, confirmed no QA PID remained,
then a fresh process recovered the encrypted pending journal and repeated the exact exchange. It
also rendered the pending refusal in RU and EN on the M52;
both reviewed dark-theme captures show the localized device-limit message and retry action without
offering Close while the encrypted request remains pending. It preserved production package
metadata and removed QA state, reverse mapping and disposable resources. Its private report is
`SELF_PAIRING_DEVICE_QUOTA_ANDROID_E2E.json`, SHA-256
`9749801191db02019fc8fc1be63b5ec469bf8166309b4df76d26963d7607a29c`, with its adjacent
three-file manifest; the bundle contains no raw serial or credential. The two separately orchestrated playback process stages also pass
on the M52: the harness verified the QA package identities, real process exit after `force-stop`
and queue/order/position/mode restoration from Room in a fresh process. A separate physical P14
joined run passed through two file-backed Room databases, current `OkHttpSyncTransport`, FastAPI
and disposable PostgreSQL at `0059`: the server committed during a simulated lost ACK, accepted the
same immutable event ID/hash exactly once on retry and projected one row to the second device. Its
durable counts are inbox 1, sync event 1, user projection 1 and device cursors 2; the one-shot
loopback handoff persisted neither bearer nor account identity. The current test APK also passed
the complete `SyncCoordinatorAcceptanceTest` class **23/23** without joined arguments. The
production-ID app's version and last-update timestamp remained unchanged after all physical
harnesses. A final read-only audit revalidated all ten canonical M52 reports and manifests, bound
all 44 private files, confirmed their shared device digest and absence of the connected raw serial,
matched five recorded production-package fingerprints to the currently installed package, and
confirmed no ADB reverse entry, QA process or disposable harness Docker resource. Its aggregate
report `M52_EVIDENCE_AUDIT.json` has SHA-256
`6ec0ea322d70df43a025c4364dc978b0f6606c92fae22c0da578a0f47d87a439`; the refreshed audit rejects
boolean aliases for integer schema versions and numeric counts. Three retained empty
screenshot attempts are explicitly classified as non-evidence placeholders. M52 remains
explicitly excluded as a substitute for A55 acceptance.

The final local closure adds purpose-bound `/recovery/outcome` and
`/deletion/cancel/outcome`, the `0058_training_privacy_fence` tombstone, immutable
`0059_training_publication_seal` evidence, and the production
`controlled-train-publish` composition. Focused real PostgreSQL/Android/CPU checks cover
receipt-cleanup lost replies, irreversible published-model deletion fencing, retained
capacity through publication and exact replay after owner-input cleanup. The independent
review for these final deltas is recorded after its findings are closed.

## Evidence index

- [Current training/execution/publication authority](ADMIN_TRAINING_EXECUTION_2026_09_19.md)
- [Independent consent restore fence](ADMIN_TRAINING_RESTORE_2026_09_19.md)
- [Deletion and backup-protection behavior](ADMIN_ACCOUNT_DELETION_2026_09_18.md)
- [Recovery implementation and verification](ADMIN_ACCOUNT_RECOVERY_2026_09_18.md)
- [Android recovery verification](ADMIN_ANDROID_RECOVERY_VERIFICATION_2026_09_18.md)
- [Android deletion verification](ADMIN_ANDROID_DELETION_VERIFICATION_2026_09_18.md)
- [Measured resource contract](../design/AutPlay_Resource_Measurement_Report_v1.md)
- [Current continuation record](ADMIN_CONTINUATION_2026_09_18.md)

Only the remaining physical/operator controls in Gates A-D remain. Their required targets, inputs, negative checks and
evidence fields are frozen in the target runbook so user participation can begin without another
implementation-planning round. The local `scripts/admin_target_acceptance.py` utility scaffolds
the private bundle and rejects missing controls, unreviewed or changed artifacts, incomplete
target identity, passing gates outside migration `0060`, a non-production Gate B Android build,
v1/v3 measurement mismatches and false PASS aggregation. Gate C's structured reports are checked
as soon as that gate claims PASS. Gate D likewise requires strict target-bound restored-state and
live-PID-abort JSON reports, including equal database state digests around the required atomic
refusal. Each gate's strict operator sign-off binds the exact required
target fields plus every other control's status, path, capture time and artifact hash, and cannot
predate the evidence. A read-only `inspect` command reports the exact missing target fields,
incomplete controls and artifact count after running the same integrity checks; the utility does
not contact or configure a target. Its complete root release suite contains **75 tests**. The
combined release and seven joined-harness safety modules pass **100/100**;
Android debug lint, Ruff and strict mypy for the new
self-pairing, recovery, deletion, consent, quota and Compose-readiness coverage are green.
