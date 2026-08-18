# ADR-021: P08 Media3 and Room Playback/Download Ownership

- Status: Accepted
- Date: 2026-08-16
- Decision owner: standing in-scope technical-decision authorization

## Context

P08 must preserve a queue entry's complete immutable recommendation attribution and the identity of
one logical listening session across process death. Room v1 retained only
`recommendation_request_id`, which cannot preserve rank, source, surface, recording ID and local/
server impression IDs required by P04. Finalizing against mutable current settings could also
retarget a session after an account/profile switch.

The Room design also described a completed Media3 download as local audio, while the physical
`local_audio_state.content_uri` contract requires a real `content://` URI. Media3 requires its
download cache and `DownloadIndex` to remain the execution/progress authority; inventing a fake
content URI would create a second, stale representation.

## Decision

1. Add the non-destructive Room `1 -> 2` migration. Queue entries and listening events retain the
   canonical attribution JSON. The active queue checkpoint retains the stable listening-event ID,
   positions, accumulated monotonic played time and the immutable user/device/profile binding
   captured at session start.
2. A queue entry ID is the Media3 `mediaId`. Duplicate track refs remain distinct, and no URL,
   token or content URI is persisted in queue/session state.
3. Media3 `DownloadManager`, `DownloadIndex` and the download cache are the only download execution,
   byte-progress and cached-byte truth. Room stores durable intent, storage class, coarse state and
   stable failure code only. Completed downloads are read through the Media3 cache, not projected as
   fake `local_audio_state` rows.
4. Use physically separate caches: `NoOpCacheEvictor` for policy-managed downloads and bounded LRU
   for opportunistic stream bytes. Admission uses an explicit managed quota/free-space reserve;
   automatic eviction may remove stream/proactive cache only, never pinned or user downloads.
5. Pin Media3 `1.10.1`. Playback uses an exported `MediaSessionService` for Android system controls,
   but accepts only the app package or controllers Media3 classifies as trusted. App-only explicit
   service commands additionally carry a process-local capability.
6. Persist a playback checkpoint every 15 seconds while playing and on pause, seek, error,
   shuffle/repeat change and orderly teardown. Queue replacement finalizes the prior logical session
   against its original snapshot and captured owner.

## Consequences

- Room schema v1 remains an immutable migration fixture; schema v2 has the exact normalized SHA-256
  recorded in `VERSIONS.md` and a real API 26 migration test.
- Authenticated Vault URLs and bearer/refresh tokens remain runtime-only. Stable synthetic
  `autplay-vault://profile/audio-variants/variant` references are authorized at each open.
- A crash loses at most the bounded checkpoint interval, not the queue identity, attribution,
  owner, shuffle/repeat mode or stable listening-event ID.
- P09 sync and server interaction projection are unchanged and have not started.

## Rejected alternatives

- Persist a resolved/signed HTTP URL or token in Room/`DownloadRequest`: stale and sensitive.
- Mirror Media3 byte progress in Room: duplicate truth and callback race risk.
- Wrap download-cache spans in a fabricated `content://` provider: bypasses `DownloadIndex` and
  corrupts cache ownership.
- Use WorkManager for media downloads: violates the Media3 execution boundary.
- Reconstruct attribution or owner from track/time/current settings at finalization: breaks causal
  identity and can cross account boundaries.
- Use one shared cache for streams and durable downloads: incompatible retention guarantees.
