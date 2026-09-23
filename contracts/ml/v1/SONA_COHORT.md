# Sona R1C owner-cohort and breaker control

This is the disabled administrative boundary for Revision 15 sections 7.5 and
10.4. An activated model starts with an empty owner allowlist. New owners and
unlisted owners receive P11. Model readiness alone never enrolls a recipient.

`POST /admin/ml/sona/cohorts/{owner_user_id}/operations` accepts the exact
[`sona-cohort-operation-command.schema.json`](sona-cohort-operation-command.schema.json)
body, bounded to 4 KiB before JSON parsing. The path identifies the affected
owner; the body cannot substitute another owner. The server supplies actor,
current role/session, action time and resulting generations. Only a live
`OWNER` or `ADMIN` Web session with CSRF and a fresh one-time operation-bound
WebAuthn assertion may mutate a cohort. The assertion binds path owner,
action, canonical command hash, operation ID, current session, expected
activation/cohort/breaker generations and a five-minute expiry. A retry with
the same actor, operation ID and command hash returns the committed receipt;
reuse with different content conflicts.

`ADD_RECIPIENT` requires an unexpired signed R1C approval, current activation,
published model and execution profile, every current artifact-license
generation, independent serving-consent head and PostgreSQL projection, and
participant ancestry free of withdrawal/deletion. It cannot confer serving
consent. `REMOVE_RECIPIENT` and `KILL_COHORT` immediately advance cohort
generation and route subsequent requests to P11. `ENTER_HALF_OPEN` requires an
existing `OPEN` breaker and its exact expected breaker generation; it cannot
force `CLOSED`, skip probes or turn a killed/ineligible owner back on. A failed
precondition leaves all generations unchanged. The mutation and append-only
audit/breaker receipt commit together under the owner/cohort authority lock.

Breaker states are exactly `CLOSED`, `OPEN` and `HALF_OPEN`. In `CLOSED`, a
rolling five-minute window with at least 50 eligible attempts opens on more
than 5% error/timeout/authority fallback; Sona p95 above 300 ms or 1.25 times
paired P11 for three consecutive one-minute buckets; any identity, output,
owner or mandatory-filter violation; GPU authority loss; or Admin kill. Two
consecutive qualifying daily windows of at least 100 served decisions with
diversity/repeat/HHI drift also open it. `OPEN` serves P11 for at least 15
minutes, with repeated-failure cooldown doubling to at most one hour.
`HALF_OPEN` admits exactly ten sequential eligible probes while everyone else
receives P11. Ten valid probes with no safety/authority failure and passing
latency close it; any failure reopens it. Insufficient traffic leaves it
half-open. Manual reset enters only `HALF_OPEN`.

Every breaker transition appends a receipt and increments breaker and cohort
generations atomically. A threshold crossed by the current request permits
only a P11 serving decision for that request. A change after inference fences
any Sona final commit that has not reacquired and revalidated the new tokens.
Breakers, buckets and receipts remain owner-scoped for export, deletion and
retention. No route or serving change is implemented by this contract.
