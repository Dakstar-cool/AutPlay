# Face v2 projection sync and download contract

This document freezes Revision 15 sections 7.4 and 8.1 before the server and
Android sync paths are implemented. The v2 schemas and pure lease verifiers do
not enable publication, sync, download or playback.

## Capability and ordering

The server must filter `FACE_PROJECTION` bootstrap rows and incremental
`FACE_PROJECTION_UPSERTED` / `FACE_PROJECTION_TOMBSTONED` events unless the client
negotiated `FACE_PROJECTION_V2`. An incapable client advances its cursor normally
without receiving an unknown Face event. Rollout is ordered: filtering server,
then capable Android apply path, then Face publication. Capability-negative
tests using a v1.0.0 client must pass before publication.

An upsert carries exactly `projection-envelope.schema.json`; a tombstone uses
`projection-tombstone.schema.json` and contains no timeline bytes. A newer
tombstone/generation wins over any delayed older upsert or download. An observed
activation, policy, redirect, source or license change makes the old projection
unselectable immediately. An unobserved remote revocation during offline use
is bounded by the signed lease. Sync applies the parent, complete sorted
artifact-policy children and a durable timeline download-work row in one Room
transaction with its cursor. WorkManager enqueue happens after commit and is
reconciled after process death or profile recovery.

## Authorized timeline fetch

`GET /api/v1/face/projections/{projection_id}` is authenticated for the exact
owner and server profile. Every request rechecks the live reference, sponsor,
policy, activation, canonical `VALID` AudioVariant, source SHA-256 and current
license decision/generation for every artifact. Authority loss rejects fetch and
renewal and requires a sync tombstone. The response is bounded canonical Face
v2 timeline bytes with content type
`application/vnd.autplay.face-timeline.v2+json`; `ETag` is the result hash.
Normal HTTP compression is allowed. Raw embeddings and source paths are absent.
No cross-profile or anonymous fetch is permitted.

The Android worker binds the request to its current profile/user and expected
projection generation. It rejects cross-origin redirects, excess compressed or
expanded size, unexpected content type, invalid canonical bytes, result hash or
identity mismatch, and a late profile/generation change. The verified cache
write and projection state change are atomic. Retries are bounded; playback is
never blocked by a fetch. A superseded projection is never selected as fallback.

## Lease and current authority

The signed envelope contains all required artifact roles and individually
bounded decisions. Both codecs recompute the complete required-set and
policy-list digests, minimum positive lease (capped at seven days), strictest
disposition and exact signed issue/expiry interval. The Android verifier uses
the pinned P-256 server identity and trusted effective time. These pure checks
do not prove the current source, policy, activation, redirect or license
generation; selection and fetch must check those separately. An offline lease
never renews itself.

On online acceptance, Android must persist signed server time, elapsedRealtime,
boot count and maximum seen wall time together per profile. Within one boot,
effective time advances as the maximum of current wall time, prior maximum,
and signed server time plus nonnegative elapsed time since acceptance. A boot
change, elapsed-time discontinuity, absent anchor, identity rotation or
unverifiable remaining lease selects neutral until online renewal. Selection
persists the advanced maximum. Wall-clock rollback cannot extend the lease.

The local Face projection remains unselectable without an exact Media3
`VerifiedPlaybackSource`: complete authorized download/cache key and index
generation, AudioVariant, source bytes/length/hash, decoded sample rate/count,
presentation-map digest and source-verification generation. Nullable legacy
metadata, fingerprints, partial cache spans and a live stream are insufficient.
The same Media3 download owns the audio bytes; Face stores proof metadata and
timeline bytes, never a second audio copy. On proof loss, profile change, seek,
source change or authority change, rendering becomes neutral without delaying
playback.

## Owner data

Owner export includes authorized projection metadata, canonical timeline
bytes, hashes, lineage, license notices and relevant tombstones. It excludes
raw embeddings, weights, internal jobs, shared-reference counts and other
owners' data. Profile replacement or account deletion first makes local rows
unselectable, then removes cache bytes.
