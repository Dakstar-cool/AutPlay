# ADR-028: Stable Artist identity projection for Frontend M4

- Status: Accepted under explicit user authorization
- Date: 2026-08-20
- Milestone: Frontend M4 prerequisite; not P15

## Context

Frontend M4 requires Artist browse/detail and Recording/Release links, but Room v11 contains only
display and normalized artist text. The canonical server model already owns
`catalog.artist.artist_id` as a UUID primary key and represents a compound credit through
`catalog.artist_credit` plus ordered `catalog.artist_credit_name` members. That identity is lost at
the public sync boundary. Creating a name hash, reusing a Recording/Release ID, or selecting an
external provider ID would contradict the false-merge and server-authority invariants.

Some legacy and imported catalog rows intentionally contain an `artist_credit` with no canonical
member rows. Their text is useful display evidence but does not prove an Artist identity. The
contract must preserve that unresolved state while remaining compatible with Android clients that
predate this projection.

## Decision

1. Identity Catalog remains the authoritative owner. `catalog.artist.artist_id` is the only Artist
   ID, uses the existing UUID format, survives rename, and is never derived from a name, artwork,
   URL, Recording, Release, or external identifier. No second Artist identity is introduced.
2. Artist and catalog credit data are shared server catalog data. Public delivery is nevertheless
   owner-scoped: an authenticated user receives only the Artist closure reachable from that
   user's live `library.user_track_ref.recording_id` relations. Android materializes the same data
   separately for every `server_profile_id`; possession of any UUID grants no access.
3. The additive public sync-v1 `catalog_projection_version=1` capability publishes four typed
   projections: `ARTIST`, `ARTIST_CREDIT`, `RECORDING_ARTIST_CREDIT`, and
   `RELEASE_ARTIST_CREDIT`, through `CATALOG_ARTIST_UPSERTED`,
   `CATALOG_ARTIST_CREDIT_UPSERTED`, `CATALOG_RECORDING_CREDIT_LINK_UPSERTED`, and
   `CATALOG_RELEASE_CREDIT_LINK_UPSERTED`. Artist-credit
   members preserve `position`, `credited_name`, `join_phrase`, and the raw `role` string. The
   existing `USER_TRACK_REF` snapshot gains an optional canonical `recording_id`.
4. Bootstrap materializes a repeatable, bounded owner closure and orders dependencies before
   links. Incremental catalog mutations append owner-scoped snapshot events to the existing
   `sync.sync_event` stream in the same PostgreSQL transaction as the authoritative change. Event
   IDs include the canonical payload digest, while owner/device cursor binding, source row
   versions, and ordered server sequences make retry and restart idempotent. Recording/Release
   link payloads page the complete owner proof in at most 100 `owner_recording_ids` per event. A
   canonical `owner_scope_id`, zero-based page number, and page count let Room replace and assemble
   the proof atomically; incomplete scopes remain hidden. Room normalizes the members and
   intersects them with live profile UTRs, so release ownership does not depend on a Release
   projection that sync v1 does not publish and no payload or local string grows without bound.
   Catalog events are not accepted as Android-created mutations.
5. Capability gating is mandatory. A client that does not request
   `catalog_projection_version=1` receives the pre-existing bootstrap and pull projection set;
   its opaque cursor advances across filtered catalog events. Unknown
   additive strings remain preserved by the existing compatibility seams; they are never mapped
   to a known semantic value.
6. Room v12 is an additive, non-destructive migration. It adds profile-scoped Artist, credit,
   ordered credit-member, typed catalog-subject/credit, and normalized owner-proof projections for
   Recording and Release.
   It does not replace or
   reinterpret `RecordingProjectionEntity`, `ReleaseTrack`, `UserTrackRef`, library, playlist,
   queue, playback, history, download, import, or pending Journal state. No destructive fallback
   is permitted.
7. Room keys include `server_profile_id` plus the canonical server UUID. Credits can be unresolved:
   an empty member set remains unresolved and cannot appear in Artist browse results. Repeated
   delivery replaces one credit's bounded member set in a Room transaction so order and roles
   cannot be partially observed. The authoritative cross-platform limit is 1,000 ordered members
   per credit and PostgreSQL enforces it at the table boundary.
8. The application boundary exposes typed `ArtistId` and `ArtistCreditId` values plus bounded
   browse/detail and Recording/Release credit DTOs. Compose receives neither Room entities nor DAO,
   HTTP, SQL, or sync types. This prerequisite does not implement an Artist screen or navigation.
9. There is no automatic legacy backfill. A future backfill may only attach an existing or newly
   adjudicated canonical Artist when authoritative evidence proves the relation. External IDs stay
   versioned evidence and ambiguous same-name rows remain unresolved.
10. PostgreSQL migration `0016_artist_id_sync_contract` adds the reverse partial indexes needed
    for owner-closure lookup, the 1,000-member invariant, and the parent credit-version invariant
    for ordered member changes, touching both old and new parents when a member moves. It does not
    merge, delete, or rewrite existing catalog data. The production import-review writer and the
    dedicated catalog mutation service call the publisher before their owning transaction commits.
    Publisher events use bounded PostgreSQL bulk inserts with atomic conflict handling; fan-out
    never performs one query per UTR or per projected aggregate. Credit roles are bounded raw
    values rather than a closed database enum, and bounded join phrases prevent oversized legacy
    rows from escaping through bootstrap or pull. Every server-generated catalog payload is
    RFC8785-canonicalized and checked against sync v1's 262,144-byte ceiling before persistence;
    an oversized valid aggregate fails its owning transaction with `PAYLOAD_TOO_LARGE` rather than
    emitting an out-of-contract event.

## Consequences

- Two Artists may share every display field and still remain distinct because all joins use their
  UUIDs. Rename updates presentation without rekeying Room or application state.
- One Artist may participate in multiple ordered credits across recordings and releases without
  duplication or name grouping.
- Existing Android clients remain usable because catalog projection delivery is capability-gated;
  upgraded clients perform an idempotent bootstrap before consuming incremental catalog events.
- Direct out-of-contract SQL catalog edits do not fabricate public events. Authoritative Artist
  mutations must use the Identity Catalog application transaction/publisher seam.
- Artist UI, the remaining M4 surfaces/qualification, M5A, external-provider selection, and any
  uncertain identity adjudication remain outside this prerequisite.
