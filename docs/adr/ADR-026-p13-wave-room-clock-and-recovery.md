# ADR-026: Wave room, clock and recovery boundary

- Status: Accepted under the standing technical-decision authorization
- Date: 2026-08-17
- Phase: P13

## Context

P13 adds coordinated group playback over the green P08 playback/source-resolution and P09
authenticated device-session foundations. A Wave action is live, server-coordinated behavior; it
is not an offline library mutation and must not be inserted into the P09 Offline Journal. The
server must preserve a durable ordered room timeline even when WebSocket delivery is lost, while
each Android device continues to authorize and open its own LOCAL, completed download, or Vault
source. A room code is a locator, not a media grant.

The design inputs intentionally leave clock and drift thresholds to executable tests and defer a
public-Internet/TLS topology. P12 also remains blocked on external RTX/model evidence; Wave cannot
depend on GPU availability or claim that blocker complete.

## Decision

1. Wave remains inside the CPU-only modular monolith. A dedicated `wave` domain/application/
   persistence boundary owns room state, membership, a canonical Recording queue, per-device
   preflight, an ordered command log and bounded timing reports. PostgreSQL is the durable source
   of truth. P09 sync events, Redis, a broker, a Wave microservice and P2P audio are not used.
2. Alembic `0015_wave_runtime` and Room v10 are additive named migrations. PostgreSQL stores the
   shared canonical room state. Room stores only a profile/user/device-bound recovery projection;
   it never stores bearer credentials, private URLs, raw paths, device clock samples or byte
   progress. A downgrade refuses rather than destroy existing Wave rows.
3. An authenticated host creates a room with at most seven additional allowlisted users. An
   authenticated user/device joins only when both the normalized room code and allowlist match.
   The code uses ten unambiguous Crockford-style base32 characters (at least 50 random bits), is
   stable only for the room lifetime, is stored as a digest and is never sufficient authorization.
   v1 bounds a room to eight active members, 100 queue entries, three look-ahead entries and a
   six-hour absolute lifetime.
4. Membership is device-bound. The host may explicitly transfer control to an active member.
   Unexpected host loss freezes host commands for a 30-second grace period; recovery or a
   deterministic transactionally locked election emits an ordered host-transfer command. If no
   eligible member remains, playback pauses and the room becomes degraded/orphaned. Close and
   expiry are terminal and do not mutate any local library state.
5. Each accepted timeline/queue command locks the room, validates the room epoch, expected command
   sequence and expected queue version, then materializes state and appends exactly one command in
   the same transaction. A stable client command ID makes an exact retry return its original
   result; conflicting reuse fails. Lifecycle/maintenance transitions lock and consume the current
   sequence atomically because expiry/election cannot depend on a disconnected client cursor.
   Queue updates are host-only, bounded and increment the queue version, invalidating older
   preflight.
6. REST snapshots and sequence catch-up are authoritative. The authenticated WebSocket endpoint
   carries bounded live commands or invalidations and may disappear on API restart. A client
   applies only the next contiguous sequence. Duplicate/old messages are ignored; a gap, epoch
   mismatch or malformed message stops execution and requires a new REST snapshot. Socket
   authentication/presence is revalidated on a bounded lease, so device/session revocation closes
   live access without granting media access.
7. Join, background and final preflight reuse the P08 source order: readable LOCAL, completed
   Media3 download, normally owner-authorized Vault stream, otherwise unavailable. Only an active,
   present participant set captured for preparation gates a start; every captured member must
   report a fresh final READY result. Explicit unavailable, preparation timeout or unstable clock
   aborts the start to PAUSED. Late/disconnected devices never rewrite an already accepted start;
   they rejoin from a snapshot and catch up after preparing. The selected source is fixed until
   the current item ends.
8. Media3 remains the only playback/download executor and byte-progress truth. Wave may request at
   most the next three proactive downloads through the existing download-intent boundary, with at
   most two concurrent prefetches. WorkManager does not download audio. Android materializes only
   an accepted current Wave item into the P08 execution queue and never modifies library rows.
9. Clock estimation uses seven initial NTP-style exchanges, retains at most 20 samples and takes
   the median offset of the five lowest-RTT samples. A clock is eligible only when p95 RTT is at
   most 1,000 ms, uncertainty is at most 100 ms and the estimate is at most 60 seconds old. Start
   lead is `max(2000 ms, 3 * p95_rtt + 2 * max_uncertainty + 250 ms)`, capped at 8,000 ms; an
   estimate that cannot meet the cap fails closed. Media3 schedules against monotonic elapsed time,
   never device wall time.
10. Drift is sampled every two seconds. Absolute drift up to 80 ms is ignored. Drift from 80 to
    250 ms uses speed 0.98 or 1.02 for at most five seconds and returns to 1.0 only after two
    consecutive samples at or below 40 ms. Drift above 250 ms, or three persistent medium samples,
    triggers one seek with a ten-second hard-seek cooldown; speed direction cannot flip more often
    than every six seconds. Rebuffering, drift over 1,000 ms or clock uncertainty over 250 ms enters
    degraded recovery instead of oscillating corrections.
11. The declared trusted-local deterministic target is p95 command lag <=250 ms, p95 start skew
    <=150 ms and p95 absolute drift <=100 ms after ten seconds when p95 RTT <=250 ms and injected
    jitter <=100 ms. High-latency/unstable-clock fixtures must fail closed or degrade rather than
    claim the target. Metrics use only low-cardinality command/source/outcome labels and never room,
    user, device, Recording, code, token, URL, path or payload labels.

## Consequences

- Wave can recover after duplicate, delayed, reordered or lost WebSocket delivery without treating
  the socket as durable state.
- A member may listen only through sources already available to that member. Host ownership and a
  room code never confer Vault access.
- Server, provider or GPU failure cannot corrupt the normal local queue/library. Cached Wave state
  is display-only after process death until fresh authentication, snapshot and clock calibration.
- Party Mode voting/member queue edits, mid-track source switching, public Internet topology,
  multi-API-instance fanout and P14 production hardening remain out of scope.
- P12 remains visibly blocked on A-030 RTX evidence and is neither completed nor silently deferred
  by this decision.
