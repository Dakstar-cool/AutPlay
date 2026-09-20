# Training registry foundation: 2026-09-18

Superseded current inventory/execution slice:
[live trainer and checkpoint/publication authority](ADMIN_TRAINING_EXECUTION_2026_09_19.md).
The 0055 inventory and evidence below describe this preceding foundation.

The current-authority registry is implemented and independently reviewed. It is a
control-plane foundation, not composed preparation/training/serving or physical
cleanup acceptance. Production consent enablement remains prohibited. Account-policy
and Android evidence is in [the preceding record](ADMIN_TRAINING_CONSENT_2026_09_18.md).

## Behavior

Migration **0055_training_work** registers an immutable source/server identity/epoch,
lineage key ID and exact participant consent revisions. Owner tags use the existing
Sona HMAC over raw UUID bytes. Participant membership is restricted to its registration
transaction, with a deferred exact-count guard; later insertion/update cannot change it.
Preparation, readiness, start and publication share stable account/policy/run locks.

Every accepted consent revision, including a redundant GRANTED decision, terminally
invalidates affected unfinished work and queues a durable cleanup claim in the same
transaction. Account suspension follows the same path. Regrant/cancellation cannot
revive INVALIDATED work. Published runs retain serving authority but queue removal of
their training inputs. Exact publication replay reports the same immutable artifact,
manifest, checkpoint and tokenizer hash tuple even after ordinary withdrawal.
Changed publication or reused operation identity is rejected. Serving authority
checks current server identity/epoch and matching lineage-key scope, with file/hash
verification still required by the eventual consumer.

These mutations require READ COMMITTED in application and PostgreSQL. Snapshot
isolation can miss newly registered work despite locking unchanged account rows;
both consent and account-status inverse races now reject instead of committing a
partial invalidation. Read-side `require_granted` retains its stale-RR serialization
guard. No locks span file I/O, training or ONNX conversion.

Cleanup COMPLETE cannot be inserted or updated yet. It requires the next actual
training-object/writer cleanup protocol. Final account purge refuses participant
removal while that proof is absent, preserving the account and preventing a false
erasure claim. The frozen 0053/0054 purge definitions are extended through an explicit
participant DELETE, with protected immutable evidence. Downgrade takes exclusive
account/consent/work locks and refuses any registered evidence.

Consent revisions are bounded by exact JSON/JCS integers, **2^53−1**. The terminal
value is reserved for a private decision, so the last permissible grant remains
revocable. Android rejects impossible terminal grants and refuses terminal mutations
before writing a journal. See [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785.html)
for the numeric representation boundary.

## Verified scope

- Final registry/policy/metadata batch: **35 passes**, 30.97 s (16 registry, 13 policy,
  six metadata). It covers exact/changed operation replay, absent consent, membership
  sealing/deferred rollback, all three unfinished phases, redundant grant, publication
  versus withdrawal, both inverse-RR races, stale publication, current identity/key
  scope, final-purge blocking, impossible cleanup completion and terminal consent.
- The migration/schema batch passed 26 cases with one stale index-count assertion;
  that assertion was updated to the new inventory. The affected final group passed
  **four cases**, 19.55 s: index inventory, live Alembic drift, PUBLIC privileges and
  incomplete-registration rollback. The unchanged 54 adjacent cycles were verified
  in the previous record; the new 0055→0054→0055 cycle passed in the registry batch.
- Last-OWNER stale-RR regression passed once (12.21 s). The affected downgrade race
  remains covered in the final 35-case batch; its barrier recognizes the newly earlier
  account lock and still proves retained decision/downgrade refusal.
- Ruff/quiet formatting passed on **20 affected Python files**; strict mypy passed
  on six newly affected registry/policy/model/test files. Earlier eight-file API typing
  evidence is reused for unchanged inputs. Root wire-contract and seven HTTP cases
  passed after the numeric-bound changes.
- Final affected Android consent batch: **16 JVM passes** (15 runtime, one transport)
  and both APK builds. Combined with the unchanged 27 pairing/five credential cases,
  this is **48 distinct cases**, not an all-at-once 48-case run. Three unchanged UI
  cases/screenshot evidence remain valid for their input states. Final separate lint
  passed: 0 errors, 0 warnings, one pre-existing hint, 4m47s.
- Bounded read-only review is closed; reviewers did not run tests. Implementing agent
  ran checks in disposable databases, without production data or credentials.

Inventory at this slice: **167 tables, 1878 columns, 155 explicit indexes**. Fingerprint:
`e5d6e0d77bf9446258cf6289bf92528f21f09e0b683c38e3e784fb799beb9e6b`.
Branch/HEAD unchanged: `codex/readme-current-state`,
`860511aae9f1fcdb98b7b9b944f97ef27929fb8b`. Existing extensive working changes are
preserved. `git diff --check` passed. No commit, push, deploy or production migration.

## Next composed block

The registry performs no file I/O and issues no process permit. Preparation must
verify all train/validation/test/calibration contributors and use separate historical
and current database connections. Training must check run invalidation at bounded
batch boundaries. Durable candidate hashes must become PUBLISHED atomically in this
registry; consumers must require that exact tuple. Training-root object inventory,
actual writer-exit evidence, controlled cleanup and independent consent restore
reconciliation remain required. Local COMMITTED markers alone are not this authority.
