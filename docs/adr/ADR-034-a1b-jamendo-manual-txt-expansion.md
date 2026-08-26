# ADR-034: A1B Jamendo manual TXT collection expansion

- Status: Accepted; runtime milestone PASS on 2026-08-26
- Date: 2026-08-23
- Scope: Post-MVP A1B manual discovery/import only
- Decision owner: explicit user activation and confirmation of the bounded manual scheme

## Context

The accepted A1A contract separates provider discovery from authorized playable acquisition and
forbids a download or generic job result from claiming Vault readiness. The user selected Jamendo,
confirmed personal-use downloads, required a one-second request interval and requested a manual
Admin Web expansion of an owner-uploaded TXT collection.

Imported artist strings are not identities. A large download inside an HTTP request would also be
non-resumable and could leave untracked bytes. Reusing Android upload authority for a Web request
would collapse intentionally distinct principals.

## Decision

1. Use Jamendo API v3 behind `autplay.jamendo.manual` 1.0.0. Discovery and acquisition capabilities
   stay independent; each byte request requires a fresh `audiodownload_allowed=true` result, a
   validated CC license URL and an allowlisted Jamendo download origin.
2. Keep the client ID in an operator secret file. Never put credentials or raw download URLs in
   PostgreSQL, jobs, Web HTML, logs, diagnostics or exports.
3. Parse TXT through the existing bounded import application service. Aggregate artist display
   names only within the authenticated owner's import job and sort by collection track count.
4. Make expansion two-step and manual: select 1-20 imported names, show the exact Jamendo artist ID
   mapping and popularity plan, then require a second explicit Start action. Names never become
   provider or Catalog keys.
5. Select `ceil(total / 2)` tracks by Jamendo `popularity_total`, capped at 25 per artist and 200 per
   operation. Enforce at least one second between provider requests and bounded time/body limits.
6. Persist the operation and candidates before acquisition. CPU jobs carry only owner plus opaque
   row IDs and use the existing PostgreSQL lease/fence/retry machinery.
7. Do not manufacture an Android device principal for Web. Provider bytes enter a dedicated
   server-acquisition staging lineage and then the canonical immutable Vault ingest. `READY` is set
   only with the complete A1A identity/Vault/library/sync/analysis transaction.
8. A preview has no side effects beyond bounded provider metadata reads and creates no impression.
   A per-track failure is visible and does not roll back tracks that independently reached READY.
9. Keep failed/raw discovery evidence at most 30 days; keep minimized provider/track/license
   provenance with READY owner lineage until authorized deletion/minimization.
10. Keep Android local-first: local search precedes Vault, Vault precedes optional external
    discovery, Add materializes to Vault first, and device Download remains Media3-owned.

## Consequences

The Admin Web can safely plan large additions without equating a text name with identity or holding
one long network request open. The implementation needs additive discovery persistence and a
server-acquisition ingest seam; the existing device upload contract remains unchanged. A1C
scheduled/automatic discovery remains inactive.

## Rejected alternatives

- Hitmo scraping or timing intended to conceal automation: incompatible with the authorized-source
  boundary.
- Download all files synchronously from the Web request: no durable checkpoint, cancellation or
  crash recovery.
- Persist Jamendo download URLs: unnecessary capability leakage and stale authorization.
- Treat the first name match as identity: unsafe ambiguity and false association.
- Reuse P10 rows as acquisition jobs or represent staged bytes as READY: wrong ownership/state.
- Create a fake Android device for Web: collapses principal and audit boundaries.
