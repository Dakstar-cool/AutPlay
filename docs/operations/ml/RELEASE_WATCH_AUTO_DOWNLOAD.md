# M11 staged auto-download consent

This backend stores an independent owner-wide watch auto-download choice, but
the operator gate in migration `0067_release_watch_auto_download` starts **off**.
While it is off, the bearer API rejects an enable request with HTTP 409 and
stores no advance consent. Monitoring, the Home feed, review-required scan
policies, and manual A1B acquisition continue to work. Following an artist or
refreshing favorites never enqueues a download.

## Android and API contract

The routes exist only when the existing `DISCOVERY_AUTOMATION_ENABLED` API
switch registers `/api/v1/release-watch`.

- `GET /api/v1/release-watch/auto-download` returns
  `{contract_version:"release-watch-v1",schema_version:1,enabled:false,revision:0,execution_enabled:false,consequence_confirmation:"AUTO_IMPORT_ADDS_AUTHORIZED_TRACKS_WITHOUT_PER_TRACK_REVIEW_V1"}`
  for a new owner. The revision is zero until the first effective mutation.
- `PUT /api/v1/release-watch/auto-download` accepts
  `{enabled:boolean,expected_revision:integer,consequence_confirmation?:string}`.
  Enabling requires the exact confirmation code above and an enabled operator
  gate. Disabling requires the confirmation field to be absent. A stale revision
  returns HTTP 409. A repeated identical command at the current revision is a
  no-op. Invalid confirmation returns HTTP 422. Responses use private no-store
  headers and return the same fields as GET.
- Each item in `GET /api/v1/release-watch/artists` adds `auto_download` with
  `watch_policy_id`, `provider_artist_id`, `policy_revision`,
  `policy_import_mode`, `effective_opt_in`, `execution_enabled`, and `reason`.
  The fixed reasons are `WATCH_POLICY_PENDING`, `ARTIST_REVIEW_REQUIRED`,
  `OWNER_CONSENT_OFF`, `STAGED_GATE_OFF`, and `READY`. The policy and provider
  fields are null until a watch-origin A1C policy exists. Existing A1C
  `SET_ARTIST_POLICY` with its own consequence confirmation changes a watched
  artist from `REVIEW_REQUIRED` to `AUTO_IMPORT`; zero-history manual follows
  can make that owner-confirmed change once scheduled reconciliation creates
  their safe-default watch policy.

`effective_opt_in` requires current owner consent and a current watch-origin
`AUTO_IMPORT` policy. `execution_enabled` additionally requires the separate
operator gate. Neither field represents a right to skip source rights, identity,
lease, quota, or owner-library checks. The operator gate is stored as a
single PostgreSQL row shared by API and worker. Each off/on transition bumps its
revision in a trigger, invalidating prior consent. A new visible owner PUT is
required after a later on transition.

## Authority and revocation

An A1C page may select a watch-origin candidate automatically only after the
gate, matching-revision owner consent, effective follow or current inferred
favorite, no exclusion, active artist, and exact provider artist binding pass in
the same short transaction. The 0067 gate does not add or change the existing
queue priority. The queue-load review must decide a fair priority and complete
its proof before any operator enables the gate.

`PostgresBulkDiscoveryRepository._require_current_attempt` repeats those checks
for watch-origin `AUTOMATIC` lineage. The existing claim, pre-provider-I/O,
durable receipt, Vault ingest, and owner-library publication paths call this
method. Revocation of consent or follow therefore fences a queued or in-flight
attempt at the next authority checkpoint. A transaction that already passed a
checkpoint commits before a concurrent revocation; subsequent checkpoints
observe the revoked state.

For these checks, the lock order is gate `FOR SHARE`, consent `FOR SHARE`, then
owner `FOR SHARE` before watch/identity reads. Consent PUT takes gate `FOR SHARE`,
owner `FOR SHARE`, a per-owner advisory lock, then consent `FOR UPDATE`; the two
owner share locks are compatible. Follow/unfollow takes owner `FOR UPDATE` before changing the watch
choice. The scheduled bridge takes owner `FOR SHARE` before creating a policy.
Provider and queue locks remain under their existing A1C/resource admission
order. A race test forces consent and unfollow revocation to wait behind a Vault
authority check, then proves the next check fails.

Owner consent revisions are append-only with a PostgreSQL mutation trigger.
Account purge is its only authorized delete path and removes both current state
and revision history. The default-off gate is independent of owner data.

## Verification and remaining gate

The focused PostgreSQL tests cover default-off state, zero-history watch policy
elevation, confirmation and CAS, owner isolation, operator off/on invalidation,
no auto-acquisition under the off gate, queued attempt fencing at claim/Vault
boundaries, concurrent consent/unfollow revocation, immutable history, and purge.
Alembic metadata drift, PUBLIC privileges, full upgrade/downgrade, and the
adjacent revision chain are checked against disposable PostgreSQL.

This remains a staged backend. The synthetic queue-load review in
`M11_PRIORITY_ACQUISITION_REVIEW.md` identifies a bounded fair-priority option,
but production admission, byte/backlog limits, multi-worker load and starvation
proof remain open. No operator should enable the gate before those checks pass.
Android shows the execution state from GET, blocks enabling while the gate is
off, and keeps disabling available.
