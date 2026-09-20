# Current training, checkpoint and publication authority: 2026-09-19

The live trainer now binds its complete verified inputs to the current registry,
rechecks consent at bounded batch boundaries, seals its exact checkpoint hash and
exports a candidate that requires atomic PostgreSQL publication before serving.
The later continuation composes this library behavior with retained preparation,
process ownership, filesystem inventory, cleanup and production serving gates. Production
consent enablement still requires the physical target acceptance listed below.

### Current authorization before payload reads (2026-09-19 continuation)

The dataset loader now separates bounded canonical manifest headers from tensor loading.
Plain owner-derived training binds the exact source/dataset/lineage tuple to current
authority before the first caller NPY is read, checks authority before every subsequent
tensor read, and rereads the canonical manifest envelope around those reads. Withdrawal
during loading therefore stops the next payload read. The header parser rejects duplicate
JSON keys, oversized manifests, unknown source kinds and noncanonical lineage data.

Standalone synthetic training admits only manifest identities emitted by the deterministic
trusted generator for its bounded recording counts 1–16 before caller payload access. The
full tensor identity is checked again after loading. Both the library and CLI reject a
caller dataset that merely changes its source labels or content and recomputes unsigned
hashes; the negative tests forbid payload reads as well as model allocation.

Quality training has a separate current loader. It validates the three signed manifest
identities, common source and lineage key, and the complete train/validation/test owner
union from headers, then acquires current authority before split tensors, calibration and
tokenizer payloads are used. Authority is rechecked throughout loading and is supplied to
the complete pre-publication reread. The historical approval loader remains available for
inspection; its result is not current training authority.

The final affected training batch after these changes contains **45 passing cases**:
authority, pipeline, signed quality approval and real PostgreSQL publication integration.
The preceding focused quality/authority run has **33 passes**. Ruff, formatting and strict
mypy pass on all seven changed training source/test files (plus the typed authority helper).
Independent read-only review closed the synthetic relabel finding and found no remaining
material issue in that pre-payload scope. The trusted-filesystem `is_symlink`/open race and
immutable-root/process composition remain outside this slice and are not claimed as closed.

### Durable measured training admission (migration 0057)

Migration **0057_training_execution** adds one immutable execution ticket per open
training run. The ticket binds the exact run, immutable-root inventory digest, admitted
input bytes and maximum output bytes. It consumes the same PostgreSQL global internal-I/O
slot as ingest, cleanup, provider maintenance and metadata work. Capacity stays charged
through consent withdrawal and server-identity rotation until the repository receives
exact `NOT_STARTED` evidence or matching child-process exit evidence.

New starts lock the run's server identity before the global admission lock, then lock the
complete participant authority set and run. SQL triggers use the same order. Workload
report v3 is required and must name the run's current server instance and epoch. Starts
and renewals use PostgreSQL time and grant at most five seconds; direct SQL cannot store
missing child/heartbeat/closure evidence, a future heartbeat or a caller-invented future
deadline. Identity rotation blocks start and renewal while still allowing an exact child
exit to release retained capacity. Downgrade refuses retained execution history or an
active v3 policy.

The final affected PostgreSQL group passed **62 cases** with one expected Linux-image-only
skip. All **five** migration close-gate cases passed. The refreshed training environments
contain the same `training_work.py` SHA-256 as the current server tree, and the complete
authority/pipeline/approval/publication group passed **45 cases** with seven existing Torch
export warnings. Ruff and formatting passed on 16 affected files; strict mypy passed on
nine source files. The disposable database harness left only its preserved fixture
database. Independent bounded review found no remaining material lock-order, race, SQL or
downgrade defect in this 0057 slice.

This migration proves durable admission accounting. It does not itself launch a retained
child, establish the immutable training-root inventory, or reconcile and clean that root.

## Implemented behavior

Both plain and quality owner-derived training require current authority, including
non-quality inputs. Source hash, dataset/bundle hash, lineage key and the complete
train/validation/test owner-token union must match the registered run. Calibration
membership remains bound to validation by the signed bundle loader. Admission binds
those inputs and transitions READY to RUNNING in one current transaction. Historical
0026 database policy and frozen reviewer approval cannot substitute for current consent.

The trainer checks before each optimizer batch, after training and before candidate
installation. Refusal propagates, prevents checkpoint return and removes its temporary
checkpoint directory. Synthetic standalone training accepts only the exact deterministic
generator output for recording counts 1–16. Changing labels or valid tensor content and
recomputing the unsigned hashes does not confer synthetic authority.

Checkpoint schema **4** carries compact run/server/epoch/input-binding provenance.
It contains neither raw owner IDs nor thousands of lineage tokens. Legacy schema 3
loads without inferred current authority. Migration **0056_training_checkpoint**
retains one immutable exact manifest digest per run. The live trainer seals it after
the final signed/current checks and before rename. Exact sealing is idempotent; a
different digest, update/delete or downgrade with retained seals is refused. Sealing
uses current identity, sorted account/policy and run locks under READ COMMITTED.
Publication and serving require the seal even when an optional compact receipt is absent.

`export_and_publish_sona_checkpoint` requires the exact seal before checkpoint/model
loading, verifies checkpoint weights and tokenizer dependencies, repeats signed/current
checks, installs an ONNX schema-2 candidate and commits its five exact dependency hashes
under current PostgreSQL locks. The separate serialized tokenizer artifact-manifest hash
is distinct from the existing logical tokenizer-fit hash; frozen signed field meanings
are unchanged. The installed graph/manifest/commit result must equal the trusted exporter
result before publication. Rehashed replacement bytes cannot become a published tuple.

A local COMMITTED marker proves file consistency only. A new consumer requires a current
publication port before artifact reads and verifies the exact compact receipt and all
five hashes before CUDA construction. Missing/unavailable/currently unapproved authority
fails closed. The production `--serve-sona-shadow` entry point now composes this read-only
PostgreSQL publication authority after deletion/consent restore guards and readiness,
before model loading. No automatic trust of legacy artifacts or test-only bypass was added.

Lost PostgreSQL commit replies recover through exact PUBLISHED operation/tuple replay,
including after ordinary withdrawal and without rereading historical training inputs.
Changed operation IDs are refused. Withdrawal before publication leaves inert installed
bytes. Existing candidates that are not PUBLISHED require controlled reconciliation;
they are neither republished from mutable sidecars nor removed without writer ownership.
Regrant never revives an invalidated run.

### Production train-and-publish composition

`autplay-sona-training controlled-train-publish` is now the owner-derived production
entry point. It requires canonical run/execution/publication UUIDs, the exact
account-to-consent-revision set, exclusive absolute input/output roots, a bounded lineage
key file and an admitted maximum output size. It selects the server identity bound to the
current measured internal-I/O policy, verifies the consent ledger and restore fences, reads
only the bounded manifest header before admission, then composes the retained process
coordinator with exact PostgreSQL publication.

The child has at most five seconds of renewed I/O authority. After sealing the checkpoint,
that same retained child loads and exports ONNX while its process-tree handle, renewal
watchdog and global admission slot are still live. The exporter builds and validates the
graph, manifest and commit marker in memory, reserves the bounded publication intent and
rejects the aggregate checkpoint-plus-candidate size before writing artifact bytes. It
installs the candidate only under `<execution>/publication/` and writes a final intent that
binds the execution, run, publication operation, immutable-root inventory, tokenizer path,
artifact name, checkpoint and candidate hashes.

Only after exact process-tree exit does the controller verify that complete file set and
its checkpoint-directory inode, commit the already-created five-hash publication tuple,
erase owner input, close the execution and release capacity. A crash or publication failure
therefore leaves no output outside durable checkpoint cleanup. A pre-commit transport
failure still closes the execution and clears owner input; the exact CLOSED retry verifies
the retained intent and publishes the same bytes without retraining or rereading deleted
historical input. Before any candidate reread, the exact-exit transition durably stores one
canonical exporter seal in PostgreSQL. The seal covers the execution/run/operation,
immutable-root inventory, paths, checkpoint digest, artifact/manifest/commit hashes and
the five-hash publication tuple. Both the first attempt and CLOSED replay rerun the exact
checkpoint inode, file-set and aggregate-bound verifier and require that independently
stored seal. Recomputed sidecars therefore cannot authorize changed artifact bytes. Exact
PUBLISHED replay follows the same path. Immediately before returning the child-owned tuple,
the exporter also compares the final reread artifact/provenance/five hashes to its saved
in-memory export result, so a replacement before seal creation is rejected as well.
Standalone `train-quality` remains refused because it has no retained production
composition.

The real disposable-PostgreSQL/CPU integration test executes that complete CLI flow with a
forced failure before the PostgreSQL publication commit, then repeats the same operation
after input deletion and finally replays the PUBLISHED result. Before the successful retry,
it proves that an extra retained file is rejected, then changes the ONNX artifact and
recomputes its manifest, commit marker and intent; the immutable PostgreSQL seal rejects
that fully self-consistent replacement. Restoring the exact four files permits replay. The
test verifies one PUBLISHED run, one CLOSED execution, zero retained internal capacity,
persisted seal/optimizer/device metadata and identical artifact/manifest hashes. The real
coordinator test also observes STOPPING plus charged capacity inside the pre-cleanup
publication window.

### Immutable retained publication seal (migration 0059)

Migration **0059_training_publication_seal** stores the child-produced canonical seal on
`ml.training_execution` only during the authorized PREPARED/RUNNING to STOPPING
transition. A separate trigger makes the value immutable afterward. Downgrade refuses to
discard any retained seal evidence. Empty adjacent downgrade/upgrade remains supported.

### Privacy deletion publication fence (migration 0058)

Migration **0058_training_privacy_fence** adds an immutable hash-only publication
revocation row keyed by run and deletion request. The restricted final privacy purge
inserts it atomically when the purged participant belongs to a PUBLISHED run. Both the
training registry and serving authority reject that run afterwards, including after a
database restore. Ordinary consent withdrawal still preserves serving for a completed
published model; only final privacy deletion installs this irreversible fence. Downgrade
refuses retained revocation evidence.

## Verification

- Trainer/source/approval batch: **26 passes**, 26.11 s, with four existing Torch ONNX
  exporter warnings. New legacy-v3 compatibility case: **one pass**, 5.50 s. This is
  **27 distinct cases**, not one 27-case run.
- Real disposable PostgreSQL + CPU training + actual ONNX export + consumer integration:
  **three passes**, 28.13 s, with three existing exporter warnings. The cases exercise
  lost commit reply, withdrawal before publication and replacement/rehashed files after
  exporter return. Each also rejects checkpoint-provenance transplantation before model
  allocation. These use synthetic owner-shaped tensors, not deployment owner data or CUDA.
- Registry/policy/metadata batch: 36 passes and one outdated unsealed second-run fixture.
  The fixture was given an explicit seal. The affected final three-case group passed
  **three cases**, 21.98 s: publication operation conflict, exact immutable seal/downgrade
  and new empty 0056→0055→0056 cycle. Together these cover **39 distinct passing cases**,
  not one final 39-case run.
- Clean full-chain lifecycle, linear predecessor inventory, live Alembic drift and PUBLIC
  privilege checks: **four passes**, 17.20 s. Previously verified adjacent cycles are
  reused; only the newly affected cycle/lifecycle was required again.
- Optional worker artifact/settings/source/startup checks: **14 passes**, 3.76 s, with
  one unavailable Windows symbolic-link case. Nine artifact/transport cases are included
  in this batch, rather than counted twice.
- Ruff passed on 13 server/worker and nine training Python files. Strict mypy passed on
  five server, nine training and three worker files. Formatting and `git diff --check`
  passed. Bounded independent read-only review is closed; reviewers did not run tests.
- Retained-child publication regression batch before the immutable-seal delta: **25
  passes**, 94.76 s. It covers
  controlled execution and recovery, real PostgreSQL publication races, exact CLOSED and
  PUBLISHED replay, real ONNX runtime output, and a checkpoint-that-fits/candidate-that-does-
  not-fit bound rejected before the artifact directory is created. Ruff and strict mypy
  pass on the six changed production modules and two changed test modules.
- Final immutable-seal training group: **26 passes**, 107.23 s. It includes the complete
  retained execution/real PostgreSQL publication/ONNX bound group plus a direct adversarial
  replacement of graph, manifest, commit and intent immediately before the final child
  reread. The independently saved exporter result rejects that tuple and removes it.
  Migration/training execution/metadata checks pass **46 cases**, 99.36 s, including every
  adjacent downgrade/upgrade.
- Follow-up independent read-only review: **APPROVED**. It verified the final exporter
  reread comparison, immutable database seal and CLOSED inode/file-set/bound replay; both
  prior P1/P2 findings are closed and no new P0-P2 finding was reported.

Current migration head after the later local-bridge closure: **0060_local_bridge_authority**.
Migration **0059_training_publication_seal** remains the training seal revision. Inventory: **170 tables,
1921 columns, 156 explicit indexes**. Mapping fingerprint:
`f3f1f8db5c44bb46d1f8d07a6df85618b6b4af04fa568d2933696f327b893215`.
The training dev environment adds the server's existing pinned httpx2 for disposable
fixtures; source paths keep checks on the current repository rather than a cached wheel.
No GPU runtime or Torch dependency was added to the CPU server.

Branch/HEAD remain `codex/readme-current-state` /
`860511aae9f1fcdb98b7b9b944f97ef27929fb8b`; extensive adjacent working changes are preserved.
Current PostgreSQL checks use guarded `autplay_p02_*` databases on loopback 3399. No production
data, credentials, migration, commit, push or deployment was used.

## Subsequent composition and remaining acceptance

The retained coordinator now composes historical preparation through a separate connection,
current authority, a canonical immutable-root inventory, the global admission ticket,
bounded cancellation, exact process-tree exit, bounded output verification and durable
cleanup. It detects renamed input/output inodes, verifies retained output again after an
external recovery race and never releases capacity from a receipt or elapsed TTL. The
recovery CLI drains unfinished cleanup without reading owner tensors. Publication serving
uses the current database tuple described above. Independent review of that continuation
closed after its external-recovery race finding was fixed.

The independent consent restore fence is described in
[the restore record](ADMIN_TRAINING_RESTORE_2026_09_19.md). Restored open process
reservations now have an explicit trusted offline closure path with real host-empty proof;
see [offline execution drain](ADMIN_OFFLINE_EXECUTION_DRAIN_2026_09_19.md). Neither path was
run against production data or a deployment.

Physical Windows Hello, A55 and private-network exclusion, plus joint byte/worker
measurements on the target system, remain open. This record alone does not close the full
Admin/account goal.
