# Track metadata release, 2026-09-16

## Delivered

AutPlay now reads embedded tags and covers, enriches descriptions through MusicBrainz
and Cover Art Archive, and uses configured AcoustID fingerprints when text lookup
cannot identify a recording. Album editions remain distinct: ambiguous results are
offered for review instead of being silently merged. The private AcoustID credential
is configured outside source code.

Fields include title, artist, album, album artist, edition release date, original release
date, recording date when supplied, label, disc and track numbers, and provider IDs.
Partial dates retain their precision. Each field retains its source; manual values
and explicit clears survive automatic enrichment. Audio is never retagged.

PostgreSQL migration 0032 adds metadata, immutable revision history and artwork.
Owner checks, optimistic revision checks, operation replay and job-generation fencing
protect updates. A separate CPU worker processes new/changed Vault audio and bounded
backfill batches, with provider pacing and bounded retries. Interactive requests have
priority over background work. New descriptions use the existing sync event stream.

Android Room 17 adds profile-scoped projections and artwork caching. Track details
show provenance and provide refresh, edit and candidate selection. Artwork is bounded,
SHA-256 checked and stored atomically. Local tags can arrive before or after server
sync. The current Media3 item receives metadata changes without replacing its audio
source, position or listening event. Explicitly cleared text cannot reappear from
embedded Media3 tags. MediaStore imports no longer incorrectly require SAF permission.

## Deployment and network

- Private server: `autplay-metadata:20260916-r2`, schema `0032_track_metadata`.
- Seven existing service containers were replaced in place, preserving runtime
  configuration; the separate metadata worker was added. API, mobile API, stream,
  CPU/music/metadata workers report healthy. Operator/admin containers remain running.
- Existing data volumes, identity/history and unrelated concurrent passkey/admin work
  were preserved. Deployment used an isolated metadata-only source staging area.
- Database backup and stopped prior containers remain in the private deployment area.
  Runtime containers retain `unless-stopped` restart policies.
- M52 received `0.3.7-metadata`, version code 11, through in-place installation.
- APK SHA-256: `d6515c552f96283ca3a834e169b1d8ac55719f5e4a1e427a0ceb61ec75a99c41`.
- Repeated Tailscale control-map timeouts stopped after forcing the control connection
  over HTTPS/443. The user persisted `TS_FORCE_NOISE_443=true` in the service drop-in.
  File transfer, phone API traffic and SSH remained available for over 20 minutes.
  No Tailscale logout, key reset or public service exposure was required.

## Validation

- 63 targeted server tests passed against a disposable real PostgreSQL instance:
  provenance/date/ambiguity, provider bounds and errors, owner HTTP authorization,
  private artwork, revision replay, stale job fencing, manual preservation, atomic
  edition/cover selection, job priority/retries, schema inventory, schema drift and
  clean migration up/down/up. [JUnit results](evidence/metadata-2026-09-16/server-metadata-results.xml).
- The isolated 0032 schema fingerprint was corrected independently of concurrent
  0033 source work, after real migration/drift/inventory validation. No check was disabled.
- New server runtime modules passed strict mypy and targeted Ruff checks.
- Android application and instrumentation APKs build with one Gradle worker;
  all 297 JVM tests pass.
- Real M52 Room 16-to-17 migration and profile/revision/explicit-clear projection
  tests pass without clearing production application data.
- Embedded-file acceptance preserves source and imported SHA-256, reads album,
  track number and exact `2001-04` date, decodes its cover, and exposes local playback.
- Real external acceptance reached REVIEW with three candidates for Carefree, chose
  Calming, reached READY and stored the release description and verified JPEG on M52.
  Live AcoustID lookup and MusicBrainz recording/release resolution were exercised.
- With Wi-Fi disabled and mobile data already disabled, after app force-stop/restart,
  external description/artwork checks pass. The offline UI displays the Calming cover;
  the cached Carefree track plays. Wi-Fi was restored afterwards.
  [Offline screenshot](evidence/metadata-2026-09-16/external-offline.png).
- The generated embedded track reaches NOT_FOUND while retaining its original tags
  and cover; its real detail UI shows the no-match explanation and provenance.
  [Track details](evidence/metadata-2026-09-16/embedded-detail.png).
- A real MediaSession regression changes metadata on the generated fixture, then
  explicitly clears title/artist/album. It verifies current metadata, retained cover,
  unchanged position/media ID/listening event and restores the original projection.
  [Device result](evidence/metadata-2026-09-16/current-player.txt).
- All 3,113 pre-existing phone track IDs and all 28 pre-existing listening-event IDs
  remain present. Additional events come from acceptance playback. Fixture hashes
  were checked directly; the older database snapshot had no stored local-audio hashes,
  so it is not evidence of a whole-library byte comparison.
  [Preservation checks](evidence/metadata-2026-09-16/preservation.txt).
- The obsolete malformed generated test fixture was removed from the active library
  through the ordinary library mutation. User audio files were not deleted.
- Bounded server and Android read-only reviews completed; actionable findings were
  fixed and affected checks repeated. No commit, push or unrelated deployment occurred.

## Ongoing behavior

Existing-library enrichment continues in the background. At the last server observation,
1,101 metadata rows existed: 39 READY, 110 REVIEW, 109 NOT_FOUND, 669 QUEUED and 174 RETRY;
265 artwork blobs were stored. M52 had synced 1,001 descriptions and cached 173 images.
These are progress snapshots, not a claim that every track has a catalog match or cover.

MusicBrainz intermittently returned busy/network responses. The worker retains retry
state and respects provider delays; an unavailable provider is not treated as a match.
Some tracks require an edition choice or manual correction, and catalog coverage is
inherently incomplete. Original local playback remains available independently.
