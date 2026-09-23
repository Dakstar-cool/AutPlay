# Sona native capture storage v1

Alembic `0063` reserves six owner-scoped relations. It does not start capture, a scheduler,
inference, or serving. Every row is tied to one persisted P11 request and owner. A bundle binds
the exact baseline hash and interaction watermark; its `universe_count` must match the complete
retained baseline snapshot, at most 5,000 tracks. The canonical bundle is bounded to 16 MiB and
expires exactly 180 days after its P11 request time. A universe above 1,024 records
`UNIVERSE_OVER_CAP`; it cannot be silently shortened to enter quality evidence.

The active lineage cursor survives consumption of a single target and advances a monotonic
registry generation until expiry or cancellation. Each target dispatch and work row uniquely
binds request, model, tokenizer, pipeline and execution-profile hashes. The work row also binds
the exact lineage hash, generation, lease and bounded attempt count. Attempts are append-only;
terminal evidence is insert-only and requires a `SUCCEEDED` work row. Work cannot be inserted
for an expired or over-cap bundle, or for a target whose exact tuple differs. Bundle, attempt
and evidence updates are rejected. Deleting a bundle for expiry, consent withdrawal or account
deletion cascades through its cursor, targets, work, attempts and evidence, leaving the P11
request and served items intact.
Initial cursor, dispatch and work states are checked on insert. Work claims advance exactly one
claim generation and attempt; every exit from `CLAIMED` requires the matching
immutable attempt row in the same transaction. Successful publication also
requires a live claim and terminal evidence, checked by a deferred database
constraint trigger. The SQL transition graph matches the pure lifecycle
contract: `PENDING` cannot jump directly to terminal ineligible, and
`RETRY_WAIT` cannot jump to exhausted without a new claim.
An unwired retention adapter deletes at most 100 expired roots per transaction using the database
clock and `FOR UPDATE SKIP LOCKED`; the FK cascade removes their derived rows. It leaves P11
requests and served items intact. Training-consent denial/withdrawal now deletes that owner's
capture roots under the existing account lock after the independent private intent is durable.
Post-restore replay and reconciliation remain future work.

Production capture remains disabled until independent consent authority, cutoff-bound temporal
event capture, complete mandatory-filtered candidate membership, pre-SQL canonical byte/hash
validation, transaction fault injection, restore fencing and bounded cleanup are implemented.
The schema is a fail-closed foundation, not native quality evidence or authorization to process
owner data.

The P11 SQL runtime now supports an optional native-capture writer within the same transaction
as baseline snapshot, request and items. An optional pre-snapshot gate can lock and verify the
current independent R1B training-consent grant inside that transaction. Only a public
`recommendations` request with a proven grant invokes the writer; its baseline retention is
extended to at least 180 days from the request time. An unconsented request keeps ordinary P11
retention. Gate and writer must be configured together. The writer receives the exact grant,
and the prepared bundle must carry its receipt hash and revision. It still must persist both
bundle and cursor before commit. A failed writer or
missing row rolls the P11 transaction back. The grant receipt hash binds the exact independent
operation, owner, actor tag, request hash, revision and change time. Production composition leaves
the gate and writer unset until the cutoff-bound event source and restore gate exist.

`SonaCaptureBundleV1` now validates the exact P11 request and retained baseline hash, owner and
cutoff-bound temporal snapshot/document/evidence hashes, 180-day input retention, independent
consent receipt shape, complete mandatory-filtered candidate membership and the P11 ranking hash
before SQL. The canonical bytes include every candidate Recording ID in stable order. A 1,025+
track baseline remains complete and records `UNIVERSE_OVER_CAP`.
The minimal checked-in fixture `tests/fixtures/ml/sona-capture-v1-minimal.json` freezes the
canonical bytes and SHA-256 with fixed owner/request/snapshot identities.
`SqlAlchemySonaCaptureWriter` requires the exact temporal snapshot row bound to the freshly
captured baseline and inserts bundle plus cursor in the caller's transaction. The temporal
repository accepts a caller-owned transaction for this path. A fault after all inserts proved
rollback of the P11 request, temporal snapshot, bundle and cursor together. PostgreSQL also
checks SHA-256 against the stored bytes of bundle, attempt and terminal evidence. This writer is
not wired to live capture: the same-transaction event source, independent consent verification,
restore reconciliation and cleanup scheduling are still required.

The existing startup training-consent restore guard now removes a restored owner's Sona capture
roots whenever the independent ledger cannot prove a current grant. It does so under the account
and training-run locks before reconciling an unmatched policy. A closed-database backup-clone
test restores a stale grant and bundle after a later withdrawal, then verifies that reconciliation
removes the bundle/cursor while preserving the P11 request. Broader Sona-specific quarantine,
receipt and post-backup deletion projection remain to be built.

An independently provisioned `FilesystemServingConsentLedger` now provides a separate R1C
serving-purpose intent history with its own file, key, identity/head/event MAC domains, fixed
`SONA_R1C_DESCENDANT_MODEL_SERVING_V1` purpose and monotonic owner/purpose generation. It is not a
capture-consent grant and is not wired to the API or a PostgreSQL projection yet. A future grant
will be usable only after its exact independent head and current PostgreSQL projection agree.
The optional runtime fields `SERVING_CONSENT_LEDGER_PATH`, `SERVING_CONSENT_LEDGER_KEY`, and
`SERVING_CONSENT_LEDGER_KEY_ID` must be supplied together and cannot reuse the training/privacy
file or key. After explicit configuration, the offline command
`python -m autplay.entrypoints.serving_consent_admin initialize-ledger` creates the file once;
`verify-head` authenticates the independent history. Neither command reconciles PostgreSQL or
enables R1C serving.
