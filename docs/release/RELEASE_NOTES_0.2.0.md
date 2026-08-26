# AutPlay 0.2.0 local candidate release notes

## Status

AutPlay 0.2.0 is a locally built, minified Android candidate, not a published production release.
It extends the RC1 local-first base with Web-approved device admission, friends, optional profile
statistics sharing, complete manual playlists and a user-controlled ordinary queue. CI is omitted
by explicit user direction; no GitHub Release, registry push, deployment or production signing is
performed.

## Included since 0.1.0 RC1

- Web-approved exact-key Android admission and recovery without plaintext credential persistence.
- Accepted friends, privacy-bounded coarse presence and fast invitations into the current Wave.
- Offline owner profile statistics plus a default-private server policy. Sharing can be enabled in
  profile settings and is readable only by an accepted, unblocked friend.
- Bounded manual playlist create/open/rename/delete and exact duplicate-preserving entry edits.
- Durable ordinary-queue play-next/add-end/remove/reorder/clear plus previous/next. Media3 refresh
  preserves current entry, position, play intent, shuffle/repeat and process-death restoration.
- A redesigned repository README reflecting the current product and evidence boundary.

## Verification

- Android host lint, JVM unit, debug and minified release/R8 gate: PASS.
- Samsung SM-M526B/API 33 focused playlist/queue/persistence gate: 10/10 PASS.
- Guarded two-stage PID/force-stop/process-death restoration: PASS.
- Complete QA side-by-side Samsung connected gate: 160 tests, zero failures, three expected skips.
- S2 server baseline: 111 root tests and 600 server tests on real PostgreSQL 18.4/pgvector 0.8.6.

The exact local APK filename and SHA-256 are recorded in `AUTPLAY_0_2_0_BUILD.json` after the final
release build.

## Deliberately not claimed

- The APK is unsigned for production and is not distributed through a store or GitHub Release.
- Friend-visible statistics are not Internet-public. Collaborative playlists and cross-device
  active-queue sync are not included.
- Public Internet/TLS topology, multi-instance Wave fanout, production backup target and account
  registration/legal policy remain operator decisions.
- P12 real RTX/model evidence remains deferred with approval; no GPU model is active or required.
