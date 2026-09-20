# Exact deletion nonacceptance and retained early evidence: 2026-09-19

An expired original deletion operation that never reached acceptance can now be
closed using its original signed bytes and recovery code. Android validates the
exact durable negative before clearing the identical encrypted journal. A restored
old PostgreSQL backup or ambiguous commit cannot fabricate nonacceptance. This
closes one local loss path within the single seven-criterion Admin/account goal;
the full goal and production/device acceptance remain open.

## Independent decision and restore authority

The existing separately retained privacy SQLite file now contains another signed,
append-only purpose-specific request chain, head and immutable coverage identity.
ATTEMPTED is durable before PostgreSQL authority revocation/acceptance commit. It
binds the owner HMAC tag, globally unique operation, canonical request hash and
digest of the complete immutable private receipt, including identity, original
key/code/authority generations and acceptance/deadline. No raw owner/key/code or
personal payload is exported. A failed PG commit leaves a conservative barrier;
only exact fresh retry with identical private facts can recover its original time.

Cancellation preserves those facts and appends its exact operation/hash/time before
PG commit. Rollback ambiguity blocks receipt, preview, purge and startup until exact
retry; it never authorizes final deletion. Each uncompleted cancellation reserves
its future journal position before unrelated records can consume capacity. Request
and cancellation UUIDs cannot be reused across purposes or owners. Existing final
PREPARED/COMPLETED history remains separate and verified on every public operation.

Startup validates current PG requests plus independent requests, including entire
owners missing from an old restore. Identity/global admission and per-owner locks
hold across fresh evidence reads; owner scans use 256-row keyset pages. Final-purge
exemption needs both independent COMPLETED and the exact current immutable PG
purge receipt after the complete current owner scan proves absence. Original row
counts/times may differ in a reapplied restore; absence alone cannot create a receipt
or an external completion. Unknown PREPARED without a SQL receipt remains incomplete.

`POST /deletion/request-resolve` requires the exact original P-256 signature and
private code header, with no bearer/cookie authority. Positive accepted receipt is
returned first. A strictly expired, never-ATTEMPTED, covered original can receive
SEALED, which permanently vetoes later acceptance. Its eight-field NOT_ACCEPTED
response binds account/operation/request hash and resolution time. The original
proof can replay after code/identity rotation or later account purge; changed code
or bytes cannot. This provides no new session or binding. Missing/corrupt evidence,
pre-cutover requests and attempted PG ambiguity remain private attention errors.

Android detaches only the captured binding, queries the historical receipt first,
then resolves expired REQUEST_PENDING originals with unchanged retained bytes.
RECORDED positive receipts are never downgraded to a negative. Full Instant
precision and exact schema/hash bindings are checked. A negative may clear only the
identical full journal under the existing shared binding gate, followed by durable
empty readback. Storage failure/replacement races stay unresolved. Old credentials
are never restored. EN/RU text explains nonacceptance and initialization.

## Offline first provisioning and cutover

The operator must stop **all** old accepting processes and drain their open accepting
transactions before `privacy_admin initialize-ledger`. The command checks migration
0056 readiness and samples PostgreSQL `clock_timestamp()` as immutable C. C must
upper-bound the last possible old acceptance. Host clock skew cannot supply C.
Requests at/before C+120 seconds cannot receive a negative. Deletion readiness stays
false through C+240 seconds to include the permitted 120-second client timestamp lag;
Android retains its binding and journal-free ready state during initialization.

The command refuses existing files. Legacy files without the new signed identity/
request chain and untracked historical PG requests fail closed; no automatic upgrade,
adoption, backfill, earlier-C override or history reset exists. A preserving legacy
upgrade needs separate offline operator work. Never discard old evidence to initialize
fresh history. Recover the original independently retained file/key and keep services
offline if history is lost. Full authentic file rollback together with PG remains
outside signed-chain protection. No deployment was provisioned in this work.

## Verified evidence

- Real disposable PG deletion/request/HTTP/purge group: **56 passes in 90.97 s**,
  plus one failed current-schema CLI case. It exposed stale runtime readiness head
  0053. Updating readiness to actual 0056 fixed that case: **one pass in 13.87 s**.
  These cover **57 distinct passing cases**, not one final 57-case invocation.
  Actual closed PG clones, deferred acceptance/cancellation commit failures, races,
  entirely absent restored owners, cutover boundaries and exact replay are covered.
- Ledger integrity/capacity/concurrency and HTTP startup group: **29 passes in
  11.89 s**. Root JSON schema/proof vectors: **two passes in 0.88 s**.
- Current readiness plus mapping inventory: **10 passes in 1.13 s**. Full current
  clean upgrade/downgrade/upgrade: **one pass in 8.06 s**; its gate now also requires
  runtime readiness to agree with the actual Alembic head.
- Android affected JVM group: **18 passes**, zero failures/errors, with strict
  dependency verification, one Gradle worker and unchanged standard 2 GiB heap.
  Final current-code lint and both APKs built successfully in **7m 12s** with the
  same strict verification/one worker/2 GiB heap: **0 errors, 0 warnings, one existing
  Compose hint**. No larger heap or disabled gate was needed.
- GPU startup/artifact/transport group: **11 passes in 5.18 s**. Broader current-source
  GPU entrypoint/startup strict mypy: **two files passed**. Server affected typing and
  Ruff/format checks passed on **17 affected server files**; strict mypy passed on
  those **17 files** after the final code changes.

The adjacent sync typing errors found during consent work are now fixed with typed
SQL predicates, payloads, explicit nullable narrowing and generic row return type.
Real PG catalog publication also exposed repeated track-metadata reads: one scoped
join now loads those facts for the owner/ref batch. Original UUID/payload semantics
and the existing 12-query bound are retained. Artist/sync group: **29 passes in
23.17 s**. Bounded read-only review found no issues in this slice.

Both GPU/training environments reinstalled only the frozen local non-editable
autplay-server package. Ten installed modules, including the new request-evidence
module and readiness, match current source SHA-256 in each environment without test
source-path overrides. No pins/locks or optional CPU/GPU boundary were changed.

Independent review is clean after fixes to coverage, cancellation/restore ambiguity,
final completion proof and PG clock cutover. Reviewers read code/tests and did not
run checks. Debugging used official [pytest configuration/fixtures](https://docs.pytest.org/en/9.0.x/reference/customize.html)
for separate PG/unit groups with explicit `-c server/pyproject.toml`, and the existing
canonical UTC helper after fixture timestamps failed the Z-only wire contract.
Repeated path guesses were replaced by exact [ripgrep inventory/glob discovery](https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md)
and literal path checks. Windows console-script trampoline failure was avoided with
`uv run ... python -m ...`, without environment resets or dependency changes.

## Current state and remaining acceptance

The subsequent training-execution continuation introduced **0059_training_publication_seal**.
The later local-bridge closure advances the current migration to
**0060_local_bridge_authority**: 170 tables, 1921 columns, 156 explicit indexes, fingerprint
`f3f1f8db5c44bb46d1f8d07a6df85618b6b4af04fa568d2933696f327b893215`.
Branch/HEAD remain `codex/readme-current-state` /
`860511aae9f1fcdb98b7b9b944f97ef27929fb8b`. Extensive adjacent dirty work is preserved.
Owned disposable PG checks use the discovered loopback port 3399 and guarded
`autplay_p02_*` databases. No commit/push/deployment/production migration, real
credentials or real account deletion occurred.

Controlled owner preparation, training byte/process admission, training-root immutable
inventory/cleanup, serving composition, historical restore closure/drain, remaining
byte paths and real Windows Hello/A55/private-network/joint measurements still need
completion. Neither a request proof nor checkpoint seal attests process exit or
releases capacity. Production training stays disabled and the unified goal stays active.
