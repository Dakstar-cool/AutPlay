# Admin resource HTTP boundary: 2026-09-16

This is local implementation evidence for the approved resource I/O stop amendment.
It is not full Admin/account acceptance or permission to enable production enforcement.

## Implemented

- `ResourceIoFastAPI` places the bound scope outside all FastAPI error middleware.
  The coordinator binds its deadline before the first admission RPC.
- Request frames have one serialized raw receive owner. After body EOF that owner
  watches disconnect during stream open/read and upload worker waits, under ASGI
  2.0 and 2.4. Before upload admission the h11 connection-lost event detects
  disconnect without receiving body bytes or emitting `100 Continue`.
- Expected early errors may send one bounded diagnostic within the atomically
  frozen remainder of the last authorized monotonic expiry. This never renews
  worker/read authority. Expiry, disconnect and caller cancellation are final.
- Both executable entrypoints select the pinned `ResourceIoH11Protocol`. The
  protocol fences the exact request cycle before aborting its transport, so
  Uvicorn 0.51.0 cannot send fallback500 after application termination. Missing
  transport support rejects process-backed admission before its first RPC.
- A completed cycle cannot abort or resume the connection's shared flow control
  after another keepalive request takes ownership.
- Unexpected child protocol/EOF failures before stream headers map to the stable
  retryable `vault_stream_unavailable` 503. Readiness now requires migration 0036.
- Process stop/sealing preserves an existing diagnostic deadline but never releases
  durable execution charge. Upload locks remain owned until the worker and exact
  process settle; cancelled HTTP waiters cannot roll back a surviving writer early.

## Evidence

The initial combined changed-scope runs passed:

- 58 runtime/API checks: deadlines, ASGI scope, real TCP, adjacent stream/upload,
  API startup and readiness.
- 30 real PostgreSQL/process checks: coordinator, exact process, upload transaction,
  stream/upload HTTP and actual child disconnects.
- The final receive/frozen-error race and completed-cycle flow-control fixes passed
  their 24 scope/TCP tests and targeted keepalive regression.
- 23 follow-up PostgreSQL cases passed after the final narrow fixes, covering
  launch failures, actual child disconnect, upload receipt/rollback and early errors.
- All 19 affected source/test files passed strict mypy and Ruff; formatting passed
  after normalizing readiness.py line endings.

Tests use disposable PostgreSQL 18.4 on loopback port 4142 in the owned
`autplay-admin-20260916` Compose project. They launch real isolated Vault children.
Synthetic ASGI transport support exists only in test helpers. TCP tests run the
actual pinned Uvicorn h11 protocol. Deliberate `flow.pause_writing()` exercises its
real backpressure wait deterministically.

A bounded independent read-only review closed the five checkpoint findings plus
four related defects found during implementation (child snapshot loss, terminal
receive/freeze race, old-cycle abort and old-cycle flow-control mutation).

## Remaining boundaries

Application deadline/transport abort cannot retract bytes already accepted by the
kernel or remote filesystem. Process capacity stays charged until exact exit is
confirmed, as approved in the stop amendment. Dedicated production control pools,
all provider/worker/client admission paths, measured deployment budgets, account
recovery/deletion/consent and target laptop/A55/private-network acceptance remain
open under the complete plan. No feature activation, commit, push or deployment
was performed by this continuation.
