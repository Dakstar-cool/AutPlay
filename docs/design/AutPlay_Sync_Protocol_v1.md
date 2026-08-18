# AutPlay Sync Protocol v1

**Status:** FROZEN FOR IMPLEMENTATION BY P04

**Contract version:** `1.0.0`

**HTTP base:** `/api/v1`

**Normative schemas:** `contracts/events/v1/`

**Normative HTTP description:** `contracts/openapi/v1/autplay-sync.openapi.json`

**Normative examples:** `tests/fixtures/sync/v1/`

## 1. Scope and language

This document freezes the language-neutral boundary between an Android client and an optional
personal AutPlay server. It defines transport data, ordering, retry, conflict, tombstone,
bootstrap and compatibility behavior. It does not implement either sync engine.

`MUST`, `MUST NOT`, `SHOULD` and `MAY` are normative. JSON member names and string values are
case-sensitive. UUID values use the canonical hyphenated representation. All integer counters
are non-negative JSON integers and MUST be handled as signed 64-bit values by implementations.

The protocol inherits the local-first invariant: a local mutation and its Offline Journal event
commit in one Room transaction without a synchronous server call. Transport is at-least-once.

## 2. Trust, identity and binding

Every operation except initial authentication uses the P03 bearer session. The server derives the
authoritative `user_id` and `device_id` from the revalidated session and compares them with the
body or query binding. A mismatch returns the non-disclosing `BINDING_MISMATCH`; UUID possession,
hash knowledge and a matching `server_profile_id` never grant access.

`server_profile_id` is an Android-local UUID identifying one configured personal-server profile.
It is echoed by the server for wrong-profile protection but is not an authorization principal.
It maps to the Room `sync_cursor.server_profile_id` proposal and needs no PostgreSQL column.
`user_id` maps to `account.user_account.user_id`; `device_id` maps to `account.device.device_id`.

`protocol_version` is the integer `1` on every request. `journal_epoch` is a client-generated UUID
for one durable device Journal lineage. It remains stable across app/process restarts and changes
only after an explicit device/Journal reset. P05 owns its Room cursor/sequence persistence. P09
must add it to the PostgreSQL device sync binding/cursor (or an equivalent durable owner/device
lineage record) before implementing reset detection; this is an accepted future migration
proposal, not a P04 migration.

`POST /api/v1/devices/bind` idempotently confirms the authenticated device metadata and returns
the binding. It MUST NOT bootstrap trust for an unauthenticated device. Device enrollment and
session issuance remain the P03 authentication boundary. The server stores the accepted name,
platform, app version and last-seen state in `account.device`; Android stores the binding in the
server profile and sync cursor.

Revoked devices receive `DEVICE_REVOKED` and MUST stop automatic retry until the user signs in or
rebinds through an authorized flow.

## 3. Client event and deterministic identity

A client event contains:

| Field | Meaning | Persistence mapping |
| --- | --- | --- |
| `event_id` | Client-generated immutable UUID; primary duplicate identity | Room `offline_journal_event.event_id`; PostgreSQL `sync.device_event_inbox.event_id` |
| `idempotency_key` | Immutable client key, normally the canonical string form of `event_id` | Accepted P05 `offline_journal_event.idempotency_key` field; PostgreSQL `sync.idempotency_record.idempotency_key` |
| `user_id` | Claimed owner, checked against the session | PostgreSQL inbox `user_id`; Room active server-profile owner |
| `device_id` | Origin device, checked against the session | Room Journal/cursor; PostgreSQL inbox `device_id` |
| `server_profile_id` | Local wrong-profile guard | Room cursor/server-profile proposal; echoed only on the server |
| `device_sequence` | Per-device monotonic sequence allocated transactionally, starting at 1 | Room Journal/sequence allocator; PostgreSQL inbox `device_sequence` and cursor `last_acked_device_sequence` |
| `event_type` | Versioned operation discriminator | Both event tables `event_type`; unknown strings remain parseable |
| `schema_version` | Payload schema major, `1` for this contract | Both event tables `schema_version` |
| `aggregate_type` | Stable aggregate discriminator | Room Journal; PostgreSQL inbox canonical aggregate type |
| `aggregate_local_id` | Immutable Android aggregate UUID | Room Journal and local projection; echoed in ACK only |
| `aggregate_server_id` | Known canonical server UUID or explicit JSON `null` | Room Journal/projection binding; converted to canonical PostgreSQL `aggregate_id` |
| `base_server_row_version` | Last observed server row version, or `null` for create/commutative operation | Room Journal; checked against aggregate `row_version`; not added to the P02 inbox |
| `occurred_at` | Client clock metadata | Room Journal and PostgreSQL inbox `occurred_at`; never an ordering or idempotency key |
| `payload` | Event-specific JSON object | Room Journal and PostgreSQL inbox `payload` |
| `request_hash` | Lowercase hex SHA-256 of the canonical hash input | Room Journal and PostgreSQL inbox `request_hash` bytea |

The hash input is the complete client-event JSON object with `request_hash` omitted. It is encoded
with RFC 8785 JSON Canonicalization Scheme and hashed as UTF-8 bytes. A retry MUST preserve every
hashed member, including an explicit `aggregate_server_id: null`; it MUST NOT rewrite an older
pending event after a later ACK binds the aggregate.

The server recomputes the hash before processing. A repeated `event_id` or
`(device_id, device_sequence)` with the same hash returns the stored terminal ACK as `DUPLICATE`
without a second domain change. Reuse with a different hash is terminal `REJECTED` with
`EVENT_HASH_MISMATCH` or `DEVICE_SEQUENCE_REUSE`. These errors disclose no stored payload.

The event idempotency scope is server-derived as
`sync-event:{user_id}:{device_id}:{journal_epoch}` and is never supplied as a free-form scope by
the client. Inside that scope, a repeated `idempotency_key` with the same event identity and hash
returns the stored result; the same key with a different event identity/hash is terminal
`REJECTED/IDEMPOTENCY_KEY_REUSE`. Integrity precedence is event ID, device sequence, then
idempotency key. The bounded ACK result is stored in `sync.idempotency_record.response_reference`;
a database uniqueness error is never exposed as protocol behavior.

The RFC 8785 canonical `payload` bytes MUST be at most 262,144 bytes. A push body
MUST be at most 8,388,608 bytes and contain 1-100 events. These byte limits are semantic checks
over the received UTF-8/canonical representation, not JSON Schema `maxLength` approximations.

## 4. Aggregate identity and supported v1 operations

P00-D006 Variant A is normative. Android `aggregate_local_id` never changes. PostgreSQL
`sync.device_event_inbox.aggregate_id`, `sync.sync_event.aggregate_id` and
`sync.tombstone.aggregate_id` always contain the effective canonical server ID.

The initial v1 aggregate policy is:

| Aggregate type | Authority | Allowed client intent |
| --- | --- | --- |
| `USER_TRACK_REF` | Client-creatable | `USER_TRACK_REF_CREATED`, `USER_TRACK_REF_PATCHED`, `USER_TRACK_PREFERENCE_SET`, `AGGREGATE_DELETED` |
| `LIBRARY_ENTRY` | Client-creatable | `LIBRARY_ENTRY_UPSERTED`, `AGGREGATE_DELETED` |
| `USER_TRACK_PREFERENCE` | Client-creatable; identity follows its `USER_TRACK_REF` | `USER_TRACK_PREFERENCE_SET` |
| `PLAYLIST` | Client-creatable | `PLAYLIST_CREATED`, `PLAYLIST_METADATA_PATCHED`, `AGGREGATE_DELETED` |
| `PLAYLIST_ENTRY` | Client-creatable | `PLAYLIST_ENTRY_UPSERTED`, `PLAYLIST_ENTRY_MOVED`, `AGGREGATE_DELETED` |
| `LISTENING_EVENT` | Client-creatable append-only fact | `LISTENING_EVENT_RECORDED` |
| `USER_INTERACTION_EVENT` | Client-creatable append-only fact | `RECOMMENDATION_IMPRESSION_RECORDED`, `RECOMMENDATION_FEEDBACK_RECORDED` |
| `RECORDING`, `RELEASE`, `RELEASE_TRACK`, `ARTIST`, `VAULT_OBJECT`, `AUDIO_VARIANT`, `MATCH_DECISION`, `JOB` | Server-authoritative | No create with a null server ID; supported future mutations require a known authorized server ID |

An unknown `event_type` or `aggregate_type` is structurally parseable but is terminally rejected as
`UNSUPPORTED_EVENT_TYPE` or `UNSUPPORTED_AGGREGATE_TYPE`. This permits old clients to preserve and
surface future strings without pretending to apply them.

### 4.1 Specialized user-interaction events

Every event first validates against `client-event.schema.json`. A known event type then validates
against the specialized schema named by the generic schema's dispatch annotation. An unknown event
type remains structurally parseable and receives terminal `UNSUPPORTED_EVENT_TYPE`; generic envelope
acceptance never authorizes or applies an unknown semantic operation.

`LISTENING_EVENT_RECORDED`, `RECOMMENDATION_IMPRESSION_RECORDED` and
`RECOMMENDATION_FEEDBACK_RECORDED` validate through `user-interaction-event.schema.json` and its
payload schemas. These append-only facts require `base_server_row_version: null` and
`event_id == aggregate_local_id`. JSON Schema validates the null constraint; P09 enforces ID equality
as `EVENT_AGGREGATE_ID_MISMATCH`. The adopted/effective aggregate ID is the canonical interaction ID,
so Room, Offline Journal, sync inbox and server projection share one stable idempotency identity.

A recommendation attribution identifies `recommendation_request_id`, canonical `recording_id`, the
generated `source_rank`, source and surface. Candidate-pool provenance and component/model versions
remain on the immutable recommendation request/item record; they are not copied into the interaction
wire payload. Actual presentation adds `presentation_id` and `display_position`. Online delivery,
candidate generation or offline-pack download alone is not an impression. An impression is recorded
once when an item first becomes presented in the active visible viewport for one presentation;
recomposition/rebinding reuses the same event ID, while a later presentation may record a new event.

The client persists a durable presentation-to-impression mapping before journaling the event. Its
owner/profile-scoped key is `(presentation_id, recommendation_request_id, source_rank)` and stores
the recording, display position and stable interaction event ID. P09 enforces the corresponding
owner-scoped semantic uniqueness. A different event ID for an already recorded key is terminal
`IMPRESSION_ALREADY_RECORDED`, does not create a second interaction projection and does not replace
the original. This complements, rather than redefines, transport duplicate semantics.

Direct `SELECTED`/`DISMISSED` UI feedback references the impression local ID and nullable acknowledged
server ID. Logical playback outcome remains `LISTENING_EVENT_RECORDED`; like/dislike remains
`USER_TRACK_PREFERENCE_SET`; playlist add/remove remains the owning playlist-entry event. Those
domain events may carry the same optional attribution and project one canonical interaction fact in
P09. They MUST NOT also emit a duplicate generic feedback event.

Recommendation request, generated item rank/recording, offline pack, impression and authenticated
user must share ownership. Missing and cross-user attribution both return non-disclosing
`ATTRIBUTION_NOT_FOUND`; mismatched item or recording uses `ATTRIBUTION_ITEM_MISMATCH` or
`ATTRIBUTION_RECORDING_MISMATCH`. Causality is never inferred from track identity or clock proximity.
A same-device feedback/listening event may reference an earlier contiguous pre-ACK impression by its
local ID and explicit null server ID; the server commits/classifies the earlier event first.

Interaction payloads are bounded, additive and covered by the unchanged RFC 8785 event hash. They
MUST NOT contain tokens, credentials, private URLs, raw filesystem paths, raw search queries, raw
model features or personal debug text. Unknown bounded strings/additive members are preserved for
compatibility but are not executed as known semantics.

Logical listening wire values map without translation to the existing PostgreSQL contract:
`event_origin` uses `ORGANIC`, `RECOMMENDED`, `PLAYLIST`, `SEARCH`, or `WAVE`; `context` uses
`GENERAL`, `WORKOUT`, `CYCLING`, `WORK`, `SLEEP`, or `PARTY`; duration maps to
`track_duration_ms`. `RECOMMENDED` requires non-null recommendation attribution. A future unknown
origin/context remains structurally readable but receives `UNSUPPORTED_ENUM_VALUE` before the v1
projection; it is never silently remapped.

For the first create of a client-creatable aggregate with `aggregate_server_id: null`, the server
proposes `aggregate_local_id` as the canonical ID, checks ownership and global availability, and
adopts it atomically with the domain mutation and inbox row. A later contiguous pre-ACK event from
the same device may also carry null: the server resolves it to that already adopted canonical ID
only when a durable earlier create inbox lineage proves the same user, device, aggregate type and
local ID. It then authorizes and applies the follow-up normally; the existing owned canonical ID is
not an availability collision. Missing lineage, cross-type or cross-owner state is rejected
without disclosure. For a known server ID the server resolves any authorized redirect before
checking the row version. A server-authoritative null ID is rejected as `SERVER_ID_REQUIRED`; an
unavailable proposed ID in the authenticated user's own namespace uses
`AGGREGATE_ID_UNAVAILABLE`; unauthorized/cross-owner targets use non-disclosing
`AGGREGATE_NOT_FOUND`.

The ACK always echoes `aggregate_local_id`. `APPLIED` and `CONFLICT` return a resolved non-null
`aggregate_server_id`; `DUPLICATE` returns the same canonical correlation required by its
`original_outcome`. A duplicate of an original `REJECTED` result may keep the server ID null and
must replay the stored safe error. Android binds a returned server ID and changes Journal state in
one Room transaction without rekeying local rows or foreign keys.

## 5. Push ordering and ACK semantics

`POST /api/v1/sync/push` accepts a bounded event array. Duplicate/integrity lookup happens before
new-sequence eligibility. This is required when a server commit succeeded but its ACK was lost.
An exact terminal event below the next expected sequence returns `DUPLICATE` even though its
sequence is already acknowledged; a mismatched replay is rejected by the integrity rules in
section 3.

After that lookup, array sequences MUST be strictly ascending and contiguous. The array MAY start
below the next expected sequence only with a contiguous exact-duplicate replay prefix; the first
new event MUST equal the next expected sequence. A reordered batch returns a per-event
`REJECTED/BATCH_SEQUENCE_NOT_ASCENDING`, with retryable true, no domain mutation and no checkpoint
advance. It is not silently sorted.

The next eligible new sequence is `last_acked_device_sequence + 1`. After any replay prefix, a
batch that starts above it or omits an intermediate value yields a per-event
`REJECTED/DEVICE_SEQUENCE_GAP`, with retryable true, no new domain mutation and no checkpoint
advance; the client must resend the missing event first. A duplicate-only batch leaves the
checkpoint unchanged and still returns the stored ACK correlation. Once the batch sequence shape
is valid, a terminal semantic rejection for an eligible new sequence is itself acknowledged and
advances the contiguous device checkpoint so one bad event cannot block the device forever.
`acknowledged_through_device_sequence` is the highest contiguous durably classified sequence,
whether `APPLIED`, `DUPLICATE`, `CONFLICT` or terminal `REJECTED`.

Each eligible event is processed in a server transaction: reserve/check inbox and sequence,
authorize owner and aggregate, lock/check the aggregate, apply or classify, append any canonical
`sync.sync_event`, update inbox and cursor, and commit. A batch may therefore contain mixed ACKs;
an event failure does not roll back previously committed events. This is deliberate partial
acceptance, not a partially applied single event.

ACK outcomes are disjoint:

| Outcome | Meaning | Local handling |
| --- | --- | --- |
| `APPLIED` | Domain intent committed once and canonical event/checkpoint committed | Atomically bind ID/version and archive the Journal event |
| `DUPLICATE` | Exact hash was already terminal; stored result is replayed, with no second mutation | Apply the returned binding/version exactly like the original terminal ACK |
| `CONFLICT` | Event was understood and authorized but not applied because concurrent state needs explicit policy/user action | Archive or block event per `resolution_state`; persist `sync_conflict`; never silently overwrite |
| `REJECTED` | Malformed, unsupported, unauthorized, impossible or sequence-invalid intent was not applied | Retry only when `error.retryable` is true; otherwise expose safe action/state |

Schema-level outcome requirements are mandatory: `APPLIED` includes a non-null canonical ID and
`server_row_version`; `CONFLICT` includes a non-null canonical ID and the conflict
taxonomy/resolution object; `REJECTED` includes an error with `code`, safe `message`, `retryable`
and `request_id`; and `DUPLICATE` includes `original_outcome` plus the stored fields required for
that original outcome. An ACK missing its outcome-specific object is invalid and MUST NOT advance
Android Journal state. The per-ACK `error.request_id` is the correlation ID for the request that
produced the stored classification; exact duplicate replay preserves it.

`base_server_row_version` is mandatory for destructive or overwrite-like updates and delete, except a
create or explicitly commutative preference operation. A mismatch never uses client time as a
tiebreaker. The server applies the conflict rules below.

## 6. Conflict taxonomy

`conflict_kind` is one of:

- `STALE_VERSION`: both sides changed non-destructively and no deterministic merge applies;
- `EDIT_VS_DELETE`: a local edit targets a tombstoned aggregate;
- `DELETE_VS_EDIT`: a delete was based on an older live version;
- `ORDER_COLLISION`: playlist position cannot be resolved without a deterministic rebase;
- `ID_COLLISION`: an adopted/local ID cannot safely bind;
- `REDIRECT_COLLISION`: alias and canonical rows both exist locally;
- `POLICY_REVIEW`: a known value or operation requires user confirmation.

Resolution states are `AUTO_MERGED`, `LOCAL_WON_EXPLICIT`, `SERVER_WON_EXPLICIT` and
`REVIEW_REQUIRED`. Only `AUTO_MERGED` is non-visible. Like/dislike is a normalized explicit
operation with server history; metadata from raw/provider sources does not overwrite user-entered
metadata; playlist metadata uses optimistic versioning; playlist entries retain stable IDs and
fractional position keys. A tombstone defeats an older offline edit, while a later explicit restore
may undo it. Destructive conflicts MUST NOT use silent last-write-wins.

Conflict snapshots are bounded, allowlisted JSON projections. They MUST NOT contain access/refresh
tokens, authorization headers, private URLs, filesystem paths, credentials or unbounded raw
payloads.

The same recursive safe-object rule applies to every client/server event payload, bootstrap
snapshot, error detail and conflict projection. Property names that represent tokens,
authorization, passwords/credentials, private/base URLs or filesystem/raw paths are schema-invalid
at any depth. Event-specific implementations MAY narrow payloads further; they MUST NOT weaken this
minimum privacy schema.

## 7. Pull, cursor and transaction boundary

`GET /api/v1/sync/pull` accepts an opaque `cursor` and `limit` from 1-500. A cursor is a signed or
authenticated implementation token bound to user, device/profile lineage, protocol version and a
server checkpoint. Its bytes and claims are not a public database-offset contract and clients MUST
not parse or synthesize it.

Each returned `server_event` has an explicit monotonic `server_sequence` for deterministic order
and diagnostics. Events are strictly ascending and belong to the authenticated user. A page gives
`from_cursor`, `next_cursor`, `has_more` and a complete bounded event list.

Android applies every event, tombstone, redirect and conflict in the page and advances the Room
cursor to `next_cursor` in one transaction. If any apply step or process dies, the transaction
rolls back and the old cursor remains; replay is idempotent. PostgreSQL changes the device's
`last_pulled_server_sequence` only after the page checkpoint is acknowledged by the next valid
request/status transition. Empty pages may still rotate an opaque cursor.

An unknown additive member or persisted enum string is preserved. An unsupported pulled event or
payload schema version is stored in the P05 proposed applied/deferred server-event record, changes
Sync Status to `UPGRADE_REQUIRED`, rolls back the page projection transaction and MUST NOT advance
the cursor. P05 owns this durable dedupe/deferred-event seam; no P04 code is implemented here.

An invalid, expired, pruned or wrong-lineage cursor returns `CURSOR_INVALID` with
`bootstrap_required: true`, never a guessed numeric offset.

## 8. Tombstones and redirect events

Delete writes the domain soft-delete state, a `sync.tombstone`, and an `AGGREGATE_TOMBSTONED`
server event in one transaction. The tombstone identifies only canonical `aggregate_server_id`,
the deleting event, deletion time and `retain_until`. Retention MUST cover the maximum supported
offline/ACK window and MUST NOT be compacted while an active device checkpoint can still need it.
The exact operational duration is a server policy, not a client clock decision.

Android maps a tombstone by server ID, retains its immutable local ID, records a local tombstone
and commits the cursor atomically. A pre-binding offline delete retains both local ID and nullable
server ID in the Journal; after an adopted create is known, the ACK/pull binds the same row and the
delete resolves to the canonical ID.

`AGGREGATE_REDIRECTED` contains alias and canonical server IDs. With only an alias local row,
Android records the redirect/canonical binding without changing its local ID. When both
`L1 -> S1` and `L2 -> S2` exist and the server redirects `S1 -> S2`, Android MUST NOT violate the
unique server-ID constraint or rewrite local foreign keys. P00-D006-R1 therefore accepts this P05
initial Room schema proposal:

```text
aggregate_redirect(
  server_profile_id, aggregate_type,
  alias_local_id, alias_server_id,
  canonical_local_id, canonical_server_id,
  created_by_server_sequence, created_at_ms
)
```

The primary key is `(server_profile_id, aggregate_type, alias_local_id)`; unique constraints reject
two canonical targets for one alias. The row retains both local IDs and original server bindings.
Reads and new mutations resolve to the canonical local row; existing foreign keys and immutable
pending Journal events stay unchanged. Redirect, projection/tombstone/conflict correlation and
cursor commit in one Room transaction. Implementations reject cycles, cross-type, cross-profile
and unauthorized redirects. This is an accepted P05 initial-schema proposal, not a PostgreSQL
migration or P04 engine implementation.

## 9. Bootstrap and reset

`POST /api/v1/sync/bootstrap` starts or continues a bounded snapshot. The request includes the
binding, reason, opaque continuation token when any, and `pending_local_event_count` only; pending
event bodies remain in the Journal. The response contains a stable opaque `snapshot_id`, snapshot
items, redirects, tombstones, `next_page_token`, `has_more`, the final `snapshot_cursor`, and the
mandatory directive `PRESERVE_REBASE_RETRY`.

Android MUST NOT clear or rewrite pending Journal events during bootstrap. Each page is applied in
one Room transaction, but the live sync cursor changes only when the final page, local adopted-ID
reconciliation and bootstrap state commit. The client then rebases/retries pending intent with its
original event IDs and hashes; a stale base version may become a visible conflict.

For each snapshot aggregate Android resolves in this order:

1. existing row with the same unique server ID;
2. unbound row whose local ID equals the incoming canonical server ID, but only when aggregate
   type, active profile/user and durable client-create Journal lineage prove this device proposed
   the adopted ID;
3. otherwise create a new stable local ID and bind it.

Step 2, snapshot application and bootstrap progress are atomic. If the equal local ID lacks proof,
the client records `ID_COLLISION/REVIEW_REQUIRED` and does not bind or silently duplicate it. This
closes the committed-create/lost-ACK window. Redirect collision uses the store in section 8.

A device reset creates a new device identity and sequence lineage; old pending events are not
silently reassigned. Recovery requires explicit user-visible import/rebase policy.
`DEVICE_RESET_REQUIRED` and cursor expiry both require bootstrap.

P09 MUST back a multi-page bootstrap with a durable stable snapshot/session record unless it proves
an equivalently durable repeatable snapshot mechanism that survives request/process boundaries.
That future store is owner/device/journal-epoch scoped and retains snapshot ID, cutover high-water,
page state and expiry; its exact physical shape belongs to P09. This is an accepted migration
proposal required by the v1 consistency contract, not a PostgreSQL change in P04.

## 10. Status, retry and errors

`GET /api/v1/sync/status` exposes a simple state plus bounded diagnostics: binding, state,
contiguous device checkpoint, pending/conflict counts, bootstrap requirement, last successful
sync metadata and a safe last error. It contains no token, private URL, raw payload or server path.

Stable error codes include:

`AUTH_REQUIRED`, `DEVICE_REVOKED`, `BINDING_MISMATCH`, `INVALID_REQUEST`,
`UNSUPPORTED_PROTOCOL_VERSION`, `UNSUPPORTED_SCHEMA_VERSION`, `UNSUPPORTED_EVENT_TYPE`,
`UNSUPPORTED_AGGREGATE_TYPE`, `UNSUPPORTED_ENUM_VALUE`, `EVENT_HASH_MISMATCH`,
`DEVICE_SEQUENCE_REUSE`, `IDEMPOTENCY_KEY_REUSE`,
`BATCH_SEQUENCE_NOT_ASCENDING`, `DEVICE_SEQUENCE_GAP`, `DEVICE_RESET_REQUIRED`,
`SERVER_ID_REQUIRED`, `AGGREGATE_ID_UNAVAILABLE`,
`AGGREGATE_NOT_FOUND`, `PAYLOAD_TOO_LARGE`, `BATCH_TOO_LARGE`, `CURSOR_INVALID`,
`BOOTSTRAP_REQUIRED`, `RATE_LIMITED`, and `INTERNAL_ERROR`.

Specialized interaction validation also uses `EVENT_AGGREGATE_ID_MISMATCH`,
`ATTRIBUTION_NOT_FOUND`, `ATTRIBUTION_ITEM_MISMATCH`, `ATTRIBUTION_RECORDING_MISMATCH` and
`IMPRESSION_ALREADY_RECORDED`.

Every error has `code`, safe `message`, `retryable`, and `request_id`; optional `retry_after_ms` is
bounded. HTTP retries use exponential backoff with jitter and server guidance. Semantic terminal
errors are not retried unchanged. Transport failure after commit is treated as unknown outcome and
retried with the identical event/hash.

## 11. Compatibility policy

The server supports the current and immediately previous mobile API major. Within `/api/v1`:

- senders MAY add optional object members; receivers MUST ignore and preserve them where their
  persistence boundary promises round-trip safety;
- known enum fields use explicit fallback/unknown handling; unknown event and aggregate strings
  are parseable and get stable semantic rejection when not executable;
- a receiver MUST reject an unsupported `schema_version` before domain mutation;
- required-member removal, meaning change, type narrowing, enum removal, hash-input change,
  status reinterpretation or cursor-lineage change is breaking;
- a breaking change requires `/api/v2` or an explicit version adapter, new immutable schemas and
  golden vectors, dual-version contract tests, rollout/rollback notes and an updated compatibility
  window;
- schema files under `events/v1` are immutable after release except clarifying descriptions that
  do not change validation. A correction that changes accepted instances creates a new schema
  version and vector set.

Next-version procedure: open an ADR describing compatibility/security/storage impact; add rather
than replace versioned schemas/OpenAPI/vectors; prove current and previous clients against both
server adapters; define migration and downgrade behavior; update the version register and phase
handoff; only then deprecate the previous version. Unknown values must survive during a rolling
upgrade, and no version transition may compact unacknowledged Journal events or tombstones.

## 12. Complete storage-impact ledger

All wire fields map to existing storage, transient derivation, or these explicit owning-phase
persistence proposals:

- Existing Room design: local/server projection IDs, Offline Journal event/hash/sequence/base
  version/payload/state, `sync_cursor`, `tombstone`, `sync_conflict` and snapshot state.
- Existing PostgreSQL schema: `account.user_account`, `account.device`,
  `sync.device_event_inbox`, `sync.sync_event`, `sync.device_sync_cursor`, `sync.tombstone`,
  `sync.idempotency_record`, and domain aggregate `row_version` columns.
- P05 proposal already present in the Room design: `sync_cursor.server_profile_id`, bootstrap
  snapshot/page state and safe conflict snapshots; add `offline_journal_event.idempotency_key`,
  `sync_cursor.journal_epoch`, an opaque cursor value and durable applied/deferred server-event
  dedupe. These fields are accepted for P05 initial schema, so no Room migration is needed in P04.
- P05 proposal accepted by P00-D006-R1 and specified in section 8: general
  `aggregate_redirect` store.
- P09 PostgreSQL proposals: durable `journal_epoch` on the device sync lineage, and a durable
  bootstrap snapshot/session store unless an equally durable stable-snapshot mechanism is proven.
- P07/P08 Room proposal: retain bounded recommendation attribution on owning preference, playlist,
  queue and logical-listening rows/events so event ID/hash and causality survive restart.
- P11 Room proposal: durable owner/profile-scoped presentation-to-impression mapping so
  recomposition/process restart reuses one event ID.
- P09 PostgreSQL proposal: append-only canonical `library.user_interaction_event` projection for
  impressions and analytical/training attribution, including presentation/display fields and an
  owner-scoped semantic uniqueness constraint. Existing `library.listening_event` remains the
  logical playback-session aggregate and preference rows remain mutable domain truth.
- Transient/derived only: HTTP `request_id`, retry hint, page counts, `has_more`, display status,
  echoed binding and opaque cursor/token encodings. They require no persistence migration.

The complete non-event-family mapping is:

| Wire family/fields | Server source or persistence | Android source or persistence |
| --- | --- | --- |
| Binding `protocol_version`, `user_id`, `device_id`, `server_profile_id`, `journal_epoch`, metadata and supported versions | Protocol/version and supported-version lists are transient; owner/device and metadata map to `account.user_account`/`account.device`; epoch uses the P09 device-lineage proposal | Server profile and epoch use the accepted P05 `sync_cursor` additions; metadata comes from the authenticated device/profile configuration |
| Push request binding and `events` | Binding is checked against the P03 session; events map as listed in section 3 | Binding comes from `sync_cursor`; events come from `offline_journal_event` |
| Push `acknowledged_through_device_sequence` and ACK identity/outcome/error/version/redirect/conflict | Checkpoint maps to `sync.device_sync_cursor`; durable classification maps to inbox plus `sync.idempotency_record.response_reference`; canonical event/domain row supplies frozen version; redirect/conflict is a bounded response projection | One transaction updates Journal state, cursor, local/server binding, row version, redirect and `sync_conflict` |
| Pull `from_cursor`, `next_cursor`, `has_more`, `events`, `server_time` | Cursor encoding and envelope metadata are transient; checkpoint maps to `device_sync_cursor`; event identity/order/body maps to `sync.sync_event`; row version/operation is frozen in its JSON payload; delete metadata maps to `sync.tombstone` | Accepted P05 opaque cursor and applied/deferred-event store; projections, tombstones, redirects and conflicts commit with the cursor |
| Bootstrap snapshot/page/high-water/cursor, aggregates, tombstones, redirects and directive | P09 stable snapshot/session proposal; aggregate source tables; `sync.tombstone`; canonical redirect events; directive/envelope metadata are transient | Existing bootstrap state plus accepted P05 cursor/epoch/deferred-event/redirect seams; pending Journal rows remain immutable |
| Status state/checkpoints/counts/times/error | Derived from device cursor, inbox/idempotency/conflict/bootstrap state and safe transient diagnostics | Derived from Journal/cursor/conflict/bootstrap state; last safe error maps to Journal/cursor diagnostics |
| Error code/message/retry/request/bootstrap fields | Transient allowlisted HTTP result; durable per-event code is in inbox/idempotency response reference | Per-event safe code/state maps to Journal/conflict/status; request IDs and retry hints need not persist |
| Specialized listening/impression/feedback attribution | P09 atomically maps the validated event into existing listening/request/item rows and the proposed append-only canonical interaction projection; ownership/rank/recording checks are application constraints | P07/P08 persist the owning domain event and one immutable Journal envelope with stable local impression references; no duplicate generic feedback event |

P04 requires no PostgreSQL migration. Any implementation that needs a different persistent field
MUST stop and propose it in its owning phase rather than silently changing this contract.
