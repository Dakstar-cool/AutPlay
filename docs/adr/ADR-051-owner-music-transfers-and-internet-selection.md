# ADR-051: Owner music transfers and Internet selection

Status: implemented for the explicitly requested private deployment, 2026-09-16.

The owner requested phone music import, explicit upload/download controls, and five
Internet search results followed by explicit selection. Existing streaming remains
the default for Vault playback; user downloads use the durable Media3 download cache.

Phone import copies selected MediaStore audio into Music/AutPlay, verifies all bytes,
then commits the normal Room library/journal command. Originals are retained. Repeated
copies use profile-scoped SHA identities and restore the existing local entry. Pending
MediaStore recovery only touches rows created by this application.

PCM WAV is accepted alongside the existing AAC, FLAC, MP3 and Opus formats, using the
same bounded full-decode/probe/hash gates. This avoids rejecting valid phone recordings.
WAV acceptance does not extend PCM acceptance to unrelated containers such as AVI.

The private bearer API adds `/api/v1/music`. Internet lookup uses the pinned yt-dlp
YouTube adapter and preserves provider ranking, returning at most five playable-length
candidates. The query is sent externally only on explicit Search. Responses include
provider, title, uploader, duration and candidate ID. Search itself acquires no audio.
This is an additive provider boundary; existing discovery/import contracts are unchanged.
No cookies, account login, paywall or DRM bypass is introduced.

Search snapshots and selections belong to the authenticated owner. Immutable PostgreSQL
snapshots expire after 24 hours for new selections. Selection must name a snapshot
member. A leased PostgreSQL job downloads that exact source, uses normal chunked Vault
ingest and SHA verification, and publishes through the existing owner sync catalog.
Previously selected active/ready provider IDs reuse the owner's acquisition. Job fences
and live device checks protect publication. The worker is separate from Vault ingest
workers; no additional broker is introduced. Search is disabled unless explicitly enabled.

Phone uploads preserve the existing UserTrackRef and use an explicit CREATE_RECORDING
review when it is unresolved. No title-based or uncertain recording merge occurs.
Cross-recording byte collisions retain the existing Vault quarantine/review behavior.
They must not silently alias two owner refs or override their history/preferences.

Sync makes completed Vault tracks visible immediately after publication. Optional device
download is a separate durable WorkManager-to-Media3 action after that sync. An offline
claim requires complete cache bytes, not merely a successful streaming request.
