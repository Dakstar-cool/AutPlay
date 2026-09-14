# ADR-050: Android UI functional parity state ownership

Status: Accepted

Date: 2026-09-10

Owners: Android product UI, playback, persistence and Wave boundaries

## Context

Several Android routes exposed only counts or a subset of lifecycle actions although their
application contracts already returned richer state. Filling the screens directly from Compose
would create competing authorities, lose durable identity across recreation, and risk optimistic
success for remote or Media3-owned work.

## Decision

Compose remains a pure state renderer and intent source. Each parity surface uses its existing
owner or a bounded typed application projection:

1. Vault Search resolves remote recording identity against profile-scoped Room `UserTrackRef`
   rows. Only resolvable rows may create an attributed durable Search queue; unresolved rows remain
   visible and honestly unavailable. Query and binding generations reject late responses.
2. Taste exclusion is two independent flags on the durable queue snapshot. Playback service
   commands mutate them under the playback state mutex; final event exclusion is their boolean OR.
   Track Like/Dislike state remains independent.
3. Import pause/resume/cancel is submitted once through `LocalImportReviewRepository`; Room Flow is
   the displayed authority and cancellation requires confirmation.
4. Wave host-transfer targets come from the authoritative server snapshot. The server revalidates
   freshness, membership and revocation at mutation time, and Android never changes role before a
   post-command snapshot.
5. History uses bounded keyset pagination over immutable listening-event identity. Downloads expose
   bounded Room intent state but leave byte progress and execution to Media3.
6. Server Features retains its diagnostic purpose: it renders bounded rows, sections, reasons and
   replay identity without becoming a second Library or claiming to refresh Home.

All remote state is profile/binding scoped, errors use stable safe presentation, and no token,
private URL, raw path or unrestricted payload enters UI state.

## Consequences

- Activity recreation and process recovery reconstruct user-visible state from Room or the current
  authoritative snapshot instead of `remember` state.
- Duplicate history listens and queue entries remain distinct; new screens do not invent identity.
- A remote row can be useful without being falsely playable.
- Download presentation remains less granular than Media3 byte progress by design.
- Devices that have never received older listening events cannot synthesize their original event
  times during a fresh bootstrap; this parity change presents the bounded local/synced projection
  that exists and does not alter the sync bootstrap protocol.

## Evidence

Room v15 and its named migration are documented in
`docs/design/AutPlay_Android_Room_Schema_v1.md`. The additive Wave snapshot contract is documented in
`docs/design/AutPlay_Wave_Protocol_v1.md` and frozen in
`contracts/openapi/v1/autplay-wave.openapi.json`. Unit, Room migration/repository and Compose tests
cover the state/action matrices and are summarized in the Android UI parity release evidence.
