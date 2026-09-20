# ADR-052: Owner track metadata enrichment

Status: accepted for implementation, 2026-09-16.

Track description is separate from canonical recording identity. Embedded tags,
MusicBrainz and Cover Art Archive enrich an owner-reachable UserTrackRef overlay;
they never merge recordings, rewrite audio bytes, or change listening history.
MusicBrainz recording/release IDs are evidence until a separate identity review.

Each field retains source, observation time and manual lock. Partial dates keep
their precision; original release, edition release and recording date are distinct.
Unknown dates stay unknown. Automatic matching requires conservative text/duration
agreement and an unambiguous edition. The owner can select an immutable candidate
snapshot or edit fields; explicit clears remain locked during later refreshes.

PostgreSQL stores bounded current projections, immutable revision/operation receipts
and content-addressed normalized JPEG artwork. Commands require owner access and
expected revision; retries replay the operation receipt. Worker commits require a
live job fence and the current generation. A periodic bounded sweep covers both
new imports and preexisting music, including older bridge processes.

Sync adds optional metadata_v1 to existing UserTrackRef events and bootstrap.
Old clients ignore the field; no unsupported event can stop their cursor. Incoming
client events cannot forge this server-owned projection. Android stores metadata
and verified art per server profile and existing local track ID, independently of
the audio cache, and applies updates transactionally with its search projection.

Providers use HTTPS host allowlists, bounded JSON/image responses and timeouts.
MusicBrainz requests are globally paced by a PostgreSQL advisory lock. Artwork is
decoded locally with bounded CPU tools before publication and served only through
an authenticated owner-ref endpoint. AcoustID is optional, uses a file-only operator
application key, and sends a fingerprint/duration rather than audio bytes.

Migration 0032 is additive and refuses downgrade while evidence exists. Android
Room17 must preserve existing IDs, files, journals and profile bindings. Deployment
requires database backup, real PostgreSQL tests, bounded review and phone acceptance.
