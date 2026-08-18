# AutPlay Android frontend M2 server-surfaces handoff

## Outcome

`PASS` for the bounded post-P14 frontend M2 server-surfaces milestone on 2026-08-18. This is not P15,
does not change PostgreSQL or public REST contracts, and does not broaden the P14 release boundary.

## Delivered scope

- A shared bounded authenticated Android HTTP seam with one refresh retry and stable redacted errors.
- Separate non-secret API and stream service origins. The legacy single-origin DataStore value migrates
  to both new origins; settings export includes neither origin nor credentials.
- Explicit online server-library snapshot/search/history/playlists. Results stay ephemeral and are not
  merged into the P09 sync-owned Room library.
- Remote import start/report/cancel/resume and evidence-safe review, with durable job projections and
  bounded WorkManager polling across process death. The UI never offers blind candidate `ACCEPT`.
- Durable resumable Vault upload intents. WorkManager receives only an intent ID, computes size and
  SHA-256, uses a stable idempotency key, reconciles the server offset, sends 1 MiB chunks, completes,
  polls terminal state, and supports cancellation. No bytes, token, endpoint or raw path is persisted.
- Online recommendation home/serve/exact replay/algorithmic replay with bounded response snapshot
  metadata and SHA-256. Cached offline packs remain the default Home source.
- Contract-backed logout-current, logout-all and revoke-current-known-device, plus a separately named
  immediate local disconnect. Local library and non-secret endpoint settings survive credential clear.
- A side-by-side QA application ID for physical qualification without uninstalling or clearing the
  user's existing signed application.

## Explicitly not delivered

- No password change, public login, registration, device enumeration or invented device-list API.
- No blind import candidate acceptance: the existing report contract does not expose enough candidate
  identity/evidence to satisfy the unresolved-first/F-015 boundary.
- No server library page is persisted as canonical local library state and no Compose code performs
  networking directly.
- No production signing, publication, push, deployment, VPN or public TLS/domain selection.
- A real Vault byte upload followed by Range playback was not executed because the physical test
  profile had no readable local audio item already associated with a server `recording_id`. The UI
  fails closed with a waiting-for-sync/selection message; transport, worker and device gating are tested.

## Main changes

- `apps/android/src/main/kotlin/app/autplay/application/server/`: authenticated transports,
  application repository, and durable state repository.
- `apps/android/src/main/kotlin/app/autplay/ui/ServerFeaturesScreen.kt` and `MainActivity.kt`: server
  snapshot, import, upload, recommendation and session surfaces.
- `apps/android/src/main/kotlin/app/autplay/work/`: Vault upload, remote-import polling and offline-pack
  refresh workers.
- `NonSecretSettingsStore.kt`, settings transfer, credential store and playback source resolution:
  two-origin migration and redacted lifecycle handling.
- `AutPlayDatabase.kt`, entities and DAO: additive Room schema v11.
- `scripts/provision-local-phone.ps1`: two-origin, app-ID-selectable, fail-closed local provisioning.

## Persistence and contract notes

- Room advances from v10 to v11 through named `MIGRATION_10_11` and adds only
  `remote_import_job_projection`, `vault_upload_intent`, and `recommendation_response_snapshot`.
- Exported schema: `apps/android/schemas/app.autplay.data.local.AutPlayDatabase/11.json`, identity
  hash `75bc40117d2dba9e17c1216ea4492854`.
- PostgreSQL remains at Alembic `0015_wave_runtime`; no server migration or public API change exists.
- New DataStore origins are `api_service_base_url` and `stream_service_base_url`; legacy
  `server_base_url` is read as both and then rewritten. A stream origin without an API origin is invalid.
- Server import resolver state is nullable because valid pending/rejected report rows return JSON null.

## Verification

- Host Android gate:
  `gradlew -Pautplay.qaSideBySide=true :apps:android:testDebugUnitTest :apps:android:lintDebug
  :apps:android:assembleDebug :apps:android:assembleDebugAndroidTest` — `BUILD SUCCESSFUL`.
- Physical Samsung SM-A556E:
  `gradlew -Pautplay.qaSideBySide=true :apps:android:connectedDebugAndroidTest` — 89 tests,
  0 failures, 0 skipped, `BUILD SUCCESSFUL`.
- Final combined physical/lint rerun after review fixes:
  `gradlew -Pautplay.qaSideBySide=true :apps:android:lintDebug
  :apps:android:connectedDebugAndroidTest` — 89 tests, 0 failures, 0 skipped, `BUILD SUCCESSFUL`.
- Physical lifecycle evidence includes cold start, navigation, process restart, Room v10-v11
  preservation, service-process behavior, offline/server-unavailable states and absence of unsafe
  import `Accept` actions.
- Live local FastAPI/PostgreSQL/worker/stream smoke on ports 8787/8788: API ready, stream live,
  empty owner-scoped library snapshot, online recommendation serve, and a three-row import fixture.
  The import reached 3/3 terminal rows (`PENDING` 2, `REJECTED` 1) and the durable reference/report
  remained visible after application reinstall/restart.
- `git diff --check` reports no whitespace errors (only existing line-ending conversion warnings).
- Independent read-only re-review after fixing Vault terminal polling, bounded import scheduling and
  import cursor pagination reports zero Critical, High or Medium findings.

## Operational note

The existing production-ID app was preserved. During qualification, replaying a copied stale refresh
credential correctly triggered server-side device-session revocation. Provisioning now never replays a
copied refresh credential: it requires a fresh access token and fails closed when that token is near
expiry. A one-off local-admin recovery revoked the affected device sessions and issued a new development
session to the side-by-side `AutPlay QA` build. The older differently signed app therefore remains
installed but its previous server session may require a separately supported re-provisioning path.

After the final connected gate cleared the QA test profile, the app was provisioned again against the
original `autplay-local` runtime on LAN ports 18787/18788 while preserving the existing journal epoch.
Provisioning returned `PASS`, preserved first-install identity, removed its app-private input and started
the app; both API and stream ports were reachable directly from the phone. Windows currently excludes
8787/8788 from bind, so those default host ports were not used for the final manual-test runtime.

## Risks and next prerequisite

- Run the real Vault upload -> ingest -> authorized Range playback path once an eligible local track is
  synced to a server Recording; do not fabricate the association for a smoke test.
- Add a candidate-evidence read contract before any future candidate-specific `ACCEPT` UI.
- Multi-pane tablet composition, public/WAN topology and production account recovery remain separate
  decisions and must not be inferred from this milestone.

## Git state

M1, M2, local provisioning support and README assets are prepared as one post-P14 checkpoint.
Publication, deployment and production signing remain outside the checkpoint.
