# ADR-042: A1C scheduled discovery and opt-in automatic import

- Status: Accepted for A1C implementation
- Date: 2026-08-26
- Scope: Post-MVP A1C only
- Decision owner: explicit user activation plus standing in-scope technical authorization

## Context

A1B safely implements manual Jamendo discovery and authorized Vault-first acquisition. A1A already
reserves the independent `SCHEDULED` and `AUTO_IMPORT` policy values, immutable policy revisions,
cadence/checkpoint semantics and rechecks at every irreversible boundary. Runtime support is absent.

Scheduling without a second operator gate could make installing a client ID unexpectedly start
external traffic. Reusing mutable candidate fields as policy/checkpoint truth would lose audit and
make revocation races unsafe. Treating the current top-25 popularity call as release discovery would
also repeat an unstable chart rather than use a deterministic bounded provider page.

Jamendo's official v3 `/tracks` method accepts exact `artist_id`, `offset`, `limit` and stable
`releasedate_desc id_desc` ordering, with the existing `audiodownload_allowed` and license
evidence. This is sufficient for a bounded two-page scheduled scan without a new provider or rights
decision: https://developer.jamendo.com/v3.0/tracks

## Decision

1. Keep `MANUAL_ONLY + REVIEW_REQUIRED` as the persisted default and add an independent operator
   setting `discovery_automation_enabled`, default false. The client ID alone never activates work.
2. Store a current owner/canonical-artist policy projection plus immutable revisions. Bind one exact
   Jamendo provider artist mapping and reuse the accepted provider/adapter/market/right boundaries.
3. Use a fixed 24-hour v1 cadence. Poll at most every five minutes, claim 20 due policies, permit one
   run per due slot/revision, scan two pages of 25 and auto-select at most 10 new candidates per run
   and 50 per owner in any rolling 24-hour window.
4. Persist run/page/checkpoint truth in the `discovery` context. Jobs carry only owner and opaque
   policy/run IDs. Existing PostgreSQL jobs own lease, retry, heartbeat, cancellation and fencing.
5. Require exact confirmation code
   `AUTO_IMPORT_ADDS_AUTHORIZED_TRACKS_WITHOUT_PER_TRACK_REVIEW_V1`. Policy CAS
   uses owner, operation UUID and application-computed canonical request hash. Exact replay returns
   the stored revision; divergent replay conflicts.
6. A stale policy may finish a bounded metadata response but cannot checkpoint as current,
   auto-select, acquire, publish Vault bytes or materialize owner state. Downgrade/disable cancels
   queued automatic work but never manual A1B work or previously READY media.
7. Keep manual and automatic source authorizations purpose-scoped and record immutable acquisition
   attempts. Automatic selection converges on the A1B candidate/acquisition/Vault path, but
   cancelling one automatic attempt cannot revoke manual authority or require a terminal attempt to
   be reopened. It does not create a second identity, Vault or library implementation and cannot
   bypass F-016.
8. Preserve zero-impression, no-global-broadcast and owner-isolation rules. A READY automatic track
   is ordinary owner-authorized library media under existing recommendation filters.

## Consequences

Automation is explicit at both operator and owner levels, bounded and locally reversible for future
work. Immutable revisions and run/page rows make policy races and crash recovery testable. The
fixed cadence is intentionally conservative; changing it or adding another provider requires a
later reviewed revision, not an unversioned configuration tweak.

The implementation adds PostgreSQL and server/Admin Web surface but no Android schema, broker,
microservice, live credential, public deployment, Room capability or recommendation-serving change.

## Rejected alternatives

- Client ID implies automation: surprising external I/O and unsafe deployment default.
- Mutable policy row without revision history: cannot prove stale-job revocation or exact replay.
- Scheduler payload contains provider cursor/metadata: leaks capability/evidence into Jobs.
- Unbounded offset crawl or popularity-only top-25 polling: poor checkpoint semantics and
  uncontrollable load.
- Auto-import through a separate ingest path: duplicates security and breaks A1A readiness.
- Disabling policy deletes acquired music: destructive and contrary to accepted ownership rules.
