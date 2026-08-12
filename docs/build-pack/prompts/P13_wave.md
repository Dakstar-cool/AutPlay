# P13 - Wave Group Playback

Выполни только phase P13. Следуй common protocol and read `HANDOFF_P12.md` plus P08/P09 handoffs.

## Цель

Реализовать Hybrid Wave group playback: host-controlled shared queue/timeline, media availability preflight, local/Vault source selection, synchronized start and bounded degraded behavior.

## Inputs

- Product specification Wave/Room sections
- System Architecture Wave, playback and WebSocket boundaries
- P08 playback/source resolver
- P09 sync/device sessions
- P11/P12 recommendation queues where available

## Scope

1. WaveRoom lifecycle: create, join, leave, close, expiry.
2. Host/member authorization and stable room code policy.
3. Versioned shared queue and command sequence.
4. Per-device preflight: local readable, downloaded, Vault streamable or unavailable.
5. Bounded prefetch before synchronized start.
6. Server-time/round-trip clock estimation and scheduled start.
7. Commands: play, pause, seek, skip, queue update and host transfer if approved by spec.
8. WebSocket live command/invalidation channel with REST snapshot recovery.
9. Drift measurement/correction policy that avoids audible oscillation.
10. Disconnect/rejoin, late join, host loss and degraded-mode UX.
11. Metrics: command lag, start skew, drift, buffer/rejoin failures.

## Constraints

- Audio bytes are not relayed peer-to-peer.
- Each device uses its normal LOCAL/Vault source authorization.
- WebSocket is not durable source of truth; snapshot/sequence recovery required.
- Room code is not sufficient authorization for private media.
- Do not assume VPN; transport is normal authenticated network path.
- If public Internet topology/TLS is not configured, test in trusted local environment and document boundary.

## Required tests

- two and multiple device/emulator sessions;
- differing local availability but same canonical Recording;
- preflight unavailable item;
- delayed/duplicated/reordered command;
- device clock skew and high latency;
- host disconnect/transfer/room close;
- WebSocket reconnect and snapshot catch-up;
- token revoke/member authorization;
- server/GPU/provider degradation;
- measured start skew/drift against declared target.

## Acceptance

Wave starts a shared item only after preflight policy, preserves ordered commands through reconnect and reports measured timing. Failure never corrupts local queue/library.

Create `HANDOFF_P13.md`, update A-032/A-033 and stop.
