# AutPlay Wave Protocol v1

**Status:** P13 executable baseline  
**Decision:** [ADR-026](../adr/ADR-026-p13-wave-room-clock-and-recovery.md)  
**Transport boundary:** authenticated trusted-local test path; public Internet/TLS topology deferred

## 1. Purpose and ownership

Wave synchronizes a host-controlled canonical Recording queue and playback timeline across
authenticated AutPlay devices. PostgreSQL owns room membership, queue versions and ordered
commands. REST owns mutations and recovery snapshots. WebSocket is a disposable live notification
channel. Android Media3 owns playback and download execution on each device.

Wave does not:

- relay audio between peers;
- grant access to another user's Vault object;
- use the P09 Offline Journal as a live room log;
- require GPU enrichment or an external provider;
- switch source during the current item;
- implement Party Mode voting or member queue edits.

## 2. Limits

| Item | Wave v1 limit |
| --- | ---: |
| Active members | 8 devices |
| Queue entries | 100 |
| Preflight look-ahead | current + next 3 |
| Concurrent proactive prefetch | 2 |
| Absolute room lifetime | 6 hours |
| Host-loss grace | 30 seconds |
| Presence freshness | 30 seconds |
| Command document | 16 KiB |
| REST snapshot | 256 KiB |
| WebSocket envelope | 32 KiB |

## 3. Identity and authorization

A room is identified by an opaque UUID and one normalized ten-character code using the alphabet
`0123456789ABCDEFGHJKMNPQRSTVWXYZ`. The server stores only its SHA-256 digest. A collision is retried
with a strict bound.

The code is a locator. Join requires all of:

1. a current access token;
2. an active user, device and session;
3. a room invitation for the authenticated user, materialized as membership for the caller's
   authenticated device at join;
4. the matching normalized code.

All room operations re-check the authenticated user/device membership. Media resolution separately
re-checks the requesting user's normal P06 Vault authorization. Outsiders receive a masked not-found
response; a valid code never discloses membership, queue or media availability.

## 4. Durable state

A room has a stable epoch, status, host user/device, queue version, latest command sequence,
playback state, current queue entry, base position/effective server time and expiry. Members are
device-bound and have explicit role/status/presence. Queue entries identify canonical Recording,
not a local URI or borrowed Vault grant.

Every accepted timeline/queue command:

1. locks the room row;
2. validates epoch, expected sequence and expected queue version;
3. validates the actor and command-specific policy;
4. appends one typed command at the next sequence;
5. materializes the new room/queue state;
6. commits both changes atomically;
7. publishes an optional WebSocket notification only after commit.

Lifecycle mutations (leave, close, explicit/automatic transfer and expiry) also lock the room and
append exactly one command atomically with their materialized state, but use the locked current
sequence rather than accepting a stale client timeline body. This keeps maintenance transitions
idempotent-by-state and prevents expiry/election from depending on a disconnected client cursor.

A client command ID and request SHA-256 define retry identity. Exact replay returns the stored
result. Reuse with different bytes is a conflict. Commands are never accepted out of sequence.

## 5. Lifecycle and host policy

Room status transitions are:

```text
OPEN -> ORPHANED -> OPEN
OPEN -> CLOSED
OPEN -> EXPIRED
ORPHANED -> CLOSED
ORPHANED -> EXPIRED
```

The host may explicitly transfer control to an active member. Unexpected host absence starts a
30-second grace period and freezes new host commands. If the host does not return, the server uses
one room-row transaction to select the earliest eligible present member and append `HOST_TRANSFER`.
If no eligible member exists, playback is paused and the room becomes `ORPHANED`. Close and expiry
are terminal. Leaving or losing a room never deletes or rewrites a device library row.

## 6. Queue and command types

The shared queue preserves duplicate Recording entries through distinct queue-entry UUIDs. Host
queue replacement/update is bounded, increments `queue_version` and invalidates every older
preflight. Party Mode member writes are not accepted.

Wave v1 durable command types are:

- `QUEUE_UPDATE`;
- `PLAY` / `RESUME`;
- `PAUSE`;
- `SEEK`;
- `SKIP`;
- `TRANSFER`;
- `START_ABORTED`;
- `CLOSE` / `EXPIRE`.

The timeline expected position is the stored base position while paused, and otherwise the base
position plus elapsed server time since its effective time.

## 7. Availability and start gate

Each device preflights the current and next three entries in this order:

1. readable local content whose final identity/readability check passes;
2. a completed Media3 download;
3. a freshly authorized and prepared Vault stream;
4. unavailable.

A report is bound to room epoch, device, queue version and queue entry and has a short expiry. The
current item is `READY` only after final verification and Media3 preparation. Vault readiness also
requires an opened authorized source and at least three seconds of buffered media.

Starting captures the active/present participant set. Every captured device must have a fresh
current-version final-ready result and an eligible clock. An explicit unavailable result, timeout
or unstable clock appends `START_ABORTED` and leaves the timeline paused. A device disconnected
before capture may rejoin later; it cannot rewrite an already accepted start.

Prefetch delegates to the existing Media3 DownloadService/DownloadManager. Modes are `OFF`,
`NEXT`, `NEXT_3` and `AGGRESSIVE_WIFI`, but all are capped at the next three entries and two active
downloads. Room never stores byte progress.

## 8. Clock and scheduled execution

Clock exchange records client monotonic `t0/t3` and server epoch receive/send `s1/s2`:

```text
network_rtt = (t3 - t0) - (s2 - s1)
offset = ((s1 - t0) + (s2 - t3)) / 2
```

Android takes seven initial samples, retains 20 and estimates from the median of the five lowest
RTT samples. Eligibility requires p95 RTT <=1,000 ms, uncertainty <=100 ms and age <=60 seconds.
The scheduled lead is:

```text
max(2000 ms, 3 * p95_rtt + 2 * max_uncertainty + 250 ms)
```

The value is capped at 8,000 ms; a start that cannot fit the cap fails closed. Android converts the
server instant to monotonic elapsed time and never schedules from device wall clock.

## 9. Drift correction

Every two seconds the client compares expected and actual position:

| Absolute drift | Action |
| --- | --- |
| <=80 ms | Ignore |
| 80-250 ms | Speed 0.98/1.02 for at most 5 seconds |
| >250 ms or three persistent medium samples | One seek |
| >1,000 ms, rebuffer, or uncertainty >250 ms | Degraded reprepare/catch-up |

Speed returns to 1.0 after two samples <=40 ms. Hard seeks have a ten-second cooldown and speed
direction cannot flip inside six seconds. This prevents audible correction oscillation. The source
remains pinned until the entry ends.

## 10. Snapshot and WebSocket recovery

Recovery order is always:

1. authenticate and fetch a REST snapshot;
2. atomically apply room/queue/self-preflight state in Room;
3. open WebSocket with the applied epoch and `after_sequence`;
4. apply only `sequence == last_sequence + 1`;
5. ignore older duplicates;
6. close live execution and fetch a new snapshot on a gap, epoch mismatch or malformed event.

The server sends `hello`, `event`, `snapshot_required` and `ping` envelopes. WebSocket never performs
a durable mutation and never replays from process memory. API restart, socket loss or multi-second
delay is therefore recoverable from PostgreSQL. Authentication/presence is revalidated on a bounded
lease; a revoked session closes live access.

## 11. Declared evidence target

On the named deterministic multi-device fixture with p95 RTT <=250 ms and injected jitter <=100 ms:

- p95 command lag <=250 ms;
- p95 start skew <=150 ms;
- p95 absolute drift <=100 ms after ten seconds;
- no hard seek more often than once per ten seconds;
- no speed-direction change more often than once per six seconds.

The high-latency/clock-unstable fixture must abort or enter degraded recovery and must not be counted
as meeting the target. Metrics and logs use only bounded command/source/outcome labels; room code,
user/device/Recording IDs, tokens, URLs, paths and command payloads are excluded.
