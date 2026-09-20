# Shared-training consent v1

Implements the account-policy/client boundary of ADR-053(7). Execution, cleanup,
publication and restore enforcement are required before production enablement.
The older signed `REVOKE_DATASET_AND_DESCENDANT_ARTIFACTS_V1` policy stays immutable
and separate. Consent does not control personal statistics or recommendations.

## Current account authority

Authenticated caller-only `GET`/`PUT /privacy/shared-training` use current ACTIVE V2
account/device/session authority. Account, device and session locks precede the
database clock and actor recheck. All success/error/validation responses are private,
no-store, vary on Authorization and carry no-cache Pragma. Commands are strict JSON,
at most 1024 UTF-8 bytes, without duplicates, unknown fields or nonfinite numbers.

The command pins `account_id`, random `operation_id`, `expected_revision`,
`decision` (GRANTED/DENIED/WITHDRAWN) and `policy_version=1`. Absent policy is UNKNOWN
revision zero and participation off. First refusal persists DENIED. Withdrawal after
a grant is WITHDRAWN. Every new decision increments the retained revision; grant
requires exact current revision, while stale private decisions may only restrict use.
Other devices read the same saved account decision without asking again.

Revisions use the exact JSON/JCS integer range through 2^53−1. That terminal value
allows only DENIED/WITHDRAWN; clients do not create mutations/journals at that value.

Exact receipts bind account, actor device and canonical request SHA-256. Changed
replay is rejected. Exact replay returns both its applied decision/revision and the
**current** account policy; an old grant receipt cannot restore a withdrawn switch.
PostgreSQL guards retain monotonic policy and immutable operation evidence, with
deletion only through protected final purge. Migration 0054 explicitly extends the
frozen purge inventory without rewriting 0053. Downgrade locks both tables before
checking emptiness and refuses retained evidence.

`TrainingConsentService.require_granted` locks current account and policy through
the authority commit and checks exact revision. Stale REPEATABLE READ snapshots fail
closed. Pipeline composition must call this against a separate current-authority
database; consent in an isolated historical 0026 restore is never current authority.

The language-neutral wire schema is
`contracts/openapi/v1/autplay-training-consent.openapi.json`. Current policy revision
may exceed the revision applied by a replayed receipt.

## Android decision journal

The signed profile capability controls availability. The first UNKNOWN policy offers
equally sized Allow/Refuse actions after a server read; dismissal retains participation
off. Settings offer explicit withdrawal and describe that completed model weights
remain until normal replacement. Confirmation is published only from a validated
server result. Offline/error text never claims a newly accepted decision.

One encrypted journal per server profile/account/API origin stores the exact operation
before submission. Derived reserved UUIDv8 slots cannot be application session
profiles. Exact retry is allowed only for the same captured binding. A new binding
to the same account reads current policy without automatically replaying an old
binding's intent, and may explicitly supersede it with a fresh WITHDRAWN operation.
Different accounts retain independent intents. Generation/full-document checks under
the binding gate fence delayed success, error and loading publication. Cancellation
propagates; storage/network failure preserves unresolved intent.

Only the purpose-specific `consent_revision_conflict` can establish nonacceptance
of a stale grant: the server checks its exact receipt first under account/operation
locks. The client then requires refresh and another explicit grant. Other failures
retain the exact journal.

## Required execution and restore authority

Register the complete verified participant set for train, validation, test and teacher
calibration before owner-data preparation. Pin current consent revisions, server
identity, lineage HMAC key ID, source generation and dataset hashes. Preparation,
start, bounded training progress and publication revalidate current authority.
Withdrawal/deletion suspension invalidate unfinished affected runs and queue durable
training-root cleanup. Regrant never revives an invalidated run. Without row-level
owner mapping, retire/rebuild the whole affected bundle rather than deleting only
membership metadata. Personal inputs remain available for personal recommendations.

Filesystem checkpoint/ONNX/COMMITTED markers are candidates, not publication
authority. PostgreSQL must atomically record the exact artifact dependency hashes
after revalidation under stable account/run locks. Serving requires that publication
tuple, not current participant consent, so withdrawal keeps already completed models.
Cleanup preserves those model dependencies, remains authorized after revocation and
waits for exact writer exit. Capacity stays charged until actual exit acknowledgement.

A PostgreSQL restore alone can resurrect an old grant/run. The independent all-decision
ledger now retains validated intents before PostgreSQL commit. New-work authority
requires the exact latest owner GRANTED intent, immutable PG receipt and current policy,
including actor/request/revision/original-time bindings; repeated revision numbers in
alternate restore branches are insufficient. Evidence is read after account/policy locks.
Missing/corrupt history fails closed. Exact pending retry also pins the original previous
policy hash; superseded pending operations report attention, not historical nonacceptance.

Startup reconciliation reuses an existing PRIVATE barrier or durably records a system
barrier before ordinary +1 PG privatization/invalidation. It never creates a user receipt.
Unmatched uncommitted grants may require another explicit choice after restart. Full run
unions are locked in sorted order before per-owner callbacks, including already-private
and terminal-policy cleanup. Latest grants reserve future private journal-event capacity.
Published lookup/exact replay retain completed-model behavior without live consent proof.
The journal/key must be provisioned and retained outside PG restore generations; signed
chains do not detect rollback of the entire authentic independent file. See
[restore implementation and verified limits](../release/ADMIN_TRAINING_RESTORE_2026_09_19.md).
The separate final-account-deletion ledger does not cover ordinary consent withdrawal.
Migration 0055 implements immutable registration/publication and terminal invalidation
with pending cleanup claims, under READ COMMITTED. Snapshot-isolated consent and
account-status invalidation reject in PostgreSQL; they never silently miss new work.
Actual cleanup completion remains forbidden until retained object/writer proof exists.
The live trainer now binds exact current inputs, checks each bounded batch and seals its
schema-4 checkpoint digest in immutable migration 0056 evidence before installation.
Controlled library export requires that exact seal, then atomic publication of all five
dependency hashes. Serving resolution requires the current exact publication/receipt;
local markers, legacy metadata, relabelled synthetic inputs and transplanted receipts
cannot substitute. Existing unpublished candidates require retained-writer reconciliation.
Verified scope and the still missing process/preparation/cleanup composition are
in [the execution record](../release/ADMIN_TRAINING_EXECUTION_2026_09_19.md).
API settings currently reject enabling this feature in PRODUCTION; other profiles
default it off. The release record distinguishes verified policy/UI from these gaps.
