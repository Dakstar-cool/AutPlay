# AutPlay unified ML delivery plan: Semantic Face and Sona/Sona-Lite

**Status:** REVISION_15_REVIEW_APPROVED_AWAITING_USER_SIGNOFF  
**Repository baseline:** `D:\AutPlayProd\AutPlay`, annotated tag `v1.0.0`, commit `9ac8778b6a2a24141bcf37af01fa22ed8600eacb`  
**Planning artifact location:** outside the Git repository so that the `v1.0.0` checkout remains unchanged  
**Implementation authorization:** NOT GRANTED  
**Publish/deploy authorization:** NOT GRANTED

## 1. Objective and release policy

Deliver one coherent, evidence-driven ML line for AutPlay with two deliberately separate products:

1. **Semantic Face**: an optional server-side audio analysis pipeline that produces an immutable,
   versioned musical-character timeline for one exact `Recording`/`AudioVariant`; Android downloads
   an authorized compact projection, samples it from local playback time, and renders a continuous
   Resonance Lens without network access in the playback path.
2. **Sona/Sona-Lite**: a server-side recommendation model that first runs in shadow against the
   immutable P11 baseline, earns a quality approval from native chronological evidence, and only
   then serves a bounded owner allowlist with automatic P11 fallback and explicit rollback.

The user selected the strict release sequence: **full Semantic Face is a production gate before
PA3/production launch**. The existing neutral PCM-reactive Face remains the runtime fallback but is
not sufficient to declare Phase 2 complete. Sona native evidence capture begins early, but Sona
quality and activation do not block the Semantic Face implementation sequence unless a shared
component fails.

No code, schema, branch, worktree, model download, publication, or deployment begins until this
plan passes adversarial review and the user explicitly approves the reviewed plan. After approval,
create a new branch and managed worktree directly from `v1.0.0`; never develop ML work in the
release checkout or merge it implicitly into the release branch.

## 2. Confirmed decisions

1. Keep `v1.0.0` immutable and use a separate branch/worktree.
2. Semantic Face remains a production gate before PA3/production.
3. Target a distributable **non-commercial/self-hosted** product. NC models are allowed only after
   exact artifact-level review. Model weights are installed separately and are not embedded in the
   APK, Git repository, or base server/GPU image.
4. Face and Sona own independent model lineages, quality approvals, activation epochs, and
   rollbacks. The same encoder may be reused only after passing both independent gates.
5. Store canonical Face timelines in PostgreSQL `BYTEA` with separately indexed metadata; no
   filesystem sidecar in the first release.
6. Restore `0026` is dry-run/rehearsal input only and permanently `quality_eligible=false`. Final
   Sona R1B PASS requires new native snapshots and mature outcomes.
7. Sona R1C starts with an explicit one-owner allowlist. New and unlisted owners remain on P11.
8. Sona may train one server-local model from several explicitly consenting owners. There is no
   cross-server dataset aggregation. Consent remains visible and auditable. Consent withdrawal
   stops future/unfinished training and cleans inputs; final account deletion revokes publication
   of any model whose ancestry includes that owner and falls back to P11 until retraining.
9. Build shared foundations and native Sona evidence capture first, then prioritize Semantic Face
   end to end while chronological Sona evidence accumulates.
10. Face workers and Sona inference run as separate processes under one GPU admission authority.
    Sona has serving priority; Face cooperatively yields or reduces its batch.
11. Face model selection is a bounded bake-off, not an open-ended research program.
12. Android sync carries Face projection availability/binding/tombstones; a separate authorized
    endpoint downloads the canonical timeline before playback. Playback never waits for network.
13. The selected "full Semantic Face" production gate requires all six initial axes. A limited-axis
    result may support further evaluation but cannot pass M7/Production Readiness Phase 2 without a
    later explicit product-scope decision from the user.
14. R1B training/evaluation consent and R1C cross-owner serving use are separate purposes. R1C is
    forbidden until every training participant in the exact model ancestry has explicitly granted
    the serving-use purpose and a separate signed R1C approval exists.
15. Initial R1C is restricted to the exact stored `recommendations` surface, `GENERAL` context, online requests,
    and owners whose complete mandatory-filtered candidate universe is at most 1,024 tracks. Home,
    offline packs, other contexts, and larger candidate universes remain on P11.
16. Every serving-contract-v2 request has one exact lower-case non-null stored surface and one committed
    decision regardless of insert path. Legacy null surfaces remain possible only on explicitly version-1
    rows created before cutover.
17. Android ML-consent step-up enrollment is a two-key, attested bootstrap: the current paired M5 key and
    the new per-use authenticated key both prove the same server nonce and binding. Unsupported devices use
    participant WebAuthn or fail closed; an access token alone can never enroll a credential.
18. A Face tuple is activatable only when every required artifact permits a positive offline lease. Its
    lease is the minimum of every artifact-specific reviewed lag and seven days, and its derived-output
    disposition is the strictest decision in the tuple.
19. Canonical Face timeline publication and owner projection fan-out are separate bounded operations.
    Publication never locks or writes an unbounded sponsor population in one transaction.
20. R1C serving decisions retain an immutable inference-evidence content hash, while the purgeable FK is
    held only in a separate lifecycle association excluded from decision identity.
21. Attribution arriving after its 180-day served-truth horizon is terminally rejected as nonretryable
    `ATTRIBUTION_EXPIRED`; it is never rebound to P11. Wire-contract-v1 `source` and `surface` remain
    required, bounded client presentation labels, distinct from server-derived serving provenance and
    request surface; safe extra pipeline/contract fields are ignored and never authoritative.
22. Face quality uses only operator-owned or explicitly licensed fixture audio independent of user
    accounts. Qualification approval remains valid only while its exact source and annotation evidence
    remains retained and authorized under the lifecycle in section 6.3.

## 3. Current baseline and factual gaps

### 3.1 Semantic Face

Already implemented:

- Face Local v2 is integrated into real Now Playing through
  `apps/android/src/main/kotlin/app/autplay/ui/face/ResonanceLensFace.kt` and
  `PlaybackPlayerSurfaces.kt`.
- `PlaybackAudioContourRuntime` provides bounded local PCM energy/contour. It is not pitch,
  timbre, mood, or musical-character inference.
- Lifecycle observation, reduced motion, stable accessibility semantics, bounded app reactions,
  and neutral fallback exist.
- Face Contract v1 exists in `contracts/face/v1`, Python domain/codec, and Kotlin domain/codec.
  Timeline identity binds source, preprocessing, embedding model, interpreter, and timebase;
  `FaceProjectionBinding` additionally binds result hash, activation epoch, profile, and user.
  Playback generation is a local runtime fence and is not part of the wire binding. Projection ID,
  policy generation, authorization expiry, and execution profile are not yet represented.
- `FaceTimelineSampler` performs bound local interpolation but is only used by tests.
- Two Essentia model pairs have deterministic CPU technical evidence on real authorized samples.

Not implemented or not qualified:

- no production Face model or semantic interpreter approval;
- no durable production Face Timeline target (the planned target is `ml.face-timeline/v2`), job
  handler, writer, retry policy, or publication fence;
- no PostgreSQL timeline/interpreter/reference/activation/backfill schema;
- no API, sync event, download transport, Room cache, cache reconciliation, or process-death path;
- no production renderer adapter from `FaceSemanticState(t)` to the Resonance Lens rig;
- no completed deletion/export/retention/last-reference/GC implementation;
- no real RTX 3060 throughput, VRAM, queue, OOM, or quality evidence;
- incomplete Android release matrix for rotation/fold, 200% font, hands-on TalkBack,
  color-vision/monochrome, process death, frame timing, and battery.

### 3.2 Sona/Sona-Lite

Already implemented:

- bounded Sona domain/request/output contracts;
- a three-layer GRU reference model, learned residual Semantic IDs, ranking heads, distillation,
  deterministic tensor/checkpoint formats, ONNX export, CUDA-only quality benchmark, and artifact
  publication tooling;
- `SonaShadowService`, `SonaShadowCoordinator`, exact/algorithmic replay, immutable PostgreSQL
  evidence, P11 postprocessing, and fail-closed reason codes;
- native temporal evidence/profile/snapshot schemas, training-consent and controlled-training
  authority, signed source/dataset/evaluation/artifact approvals, privacy revocation fences, and
  server-side paired evaluation thresholds;
- loopback Sona ONNX/CUDA inference mode and synthetic end-to-end evidence.

Not implemented or not qualified:

- production composition does not instantiate `SonaShadowService` or `SonaShadowCoordinator`;
- ordinary recommendation serving does not schedule shadow work after persisted P11 truth;
- Compose starts `ml-gpu` as the ordinary enrichment worker, not a separately supervised Sona
  inference service;
- current evidence is synthetic and permanently `quality_eligible=false`;
- no authorized native quality bundle with complete candidate sets, sufficient chronological span,
  mature outcomes, and one approved embedding model;
- no R1C serving policy, owner allowlist, online circuit breaker, activation receipt, or operational
  rollback path.

### 3.3 Shared GPU/model foundation

ADR-025 already provides an isolated `gpu/` project, read-only Vault/model-cache mounts, exact
artifact and preprocessing hashes, approved model registry, deterministic audio preprocessing,
durable embedding jobs, append-only activation evidence, CUDA provider checks, batch reduction on
OOM, and CPU independence. This plan extends those patterns; it does not move ML libraries into the
CPU server image.

## 4. Non-negotiable invariants

1. Android playback, CPU APIs, sync, P11, and the neutral Face work with no GPU/model service.
2. No network, database, decompression, or model inference occurs on the Android render thread or
   synchronously in the playback-control path.
3. A Face timeline is musical-content analysis. It never represents listener emotion, health,
   psychology, identity, or other sensitive state.
4. Face artifacts are scoped to an exact source lineage. Sona evidence and training participation
   are owner scoped. Neither scope may be silently widened.
5. Missing, stale, unauthorized, corrupt, incompatible, or low-confidence Face input produces the
   neutral/local fallback. It never blocks playback.
6. Missing, stale, unauthorized, corrupt, incompatible, timed-out, or unhealthy Sona input returns
   P11. It never returns a partial Sona ranking.
7. `FaceSemanticState` is the renderer boundary. Raw embeddings and model tensor layouts never
   cross into Android or UI code.
8. P11 remains the baseline and fallback authority. Sona does not mutate P11 request/ranking truth.
9. All model, interpreter, tokenizer, pipeline, dataset, approval, and activation identities are
   immutable and content addressed. Changes create successors, never in-place mutation.
10. PostgreSQL is authoritative for metadata, work state, ownership, activation, and canonical Face
    timeline bytes. Vault remains authoritative for source audio.
11. Migrations are additive/forward-only and Room never uses destructive fallback.
12. Model weights remain outside Git and release packages and are admitted only by exact manifest,
    byte size, SHA-256, source revision, license decision, runtime, preprocessing, and task.
13. No result derived during a disabled policy generation may publish after disable, deletion,
    consent revocation, last-reference loss, or activation supersession.
14. Public recommendation callers never select Sona, shadow mode, a model, or an activation. A
    server-owned router is the sole R1C authority; non-P11 public pipeline selection is rejected.
15. A Sona result is not served until its actual served items and serving decision are committed.
    Exact replay returns persisted served truth, never a newly recomputed substitute.
16. Training publication validity checked only at process startup is insufficient for R1C. Serving
    rechecks activation, consent-purpose, publication, deletion, and revocation generations before
    inference, after inference, and immediately before the commit that makes a result returnable.
17. After the audited recommendation-serving cutover, every persisted recommendation request on
    every surface has one immutable committed serving decision and the exact served items, even when
    the answer is P11. Absence of a decision is valid only for explicitly marked legacy v1 rows.
18. Android may render a semantic Face only for the exact verified bytes, duration, timebase, and
    AudioVariant named by the active projection. An unknown or changing playback source is neutral.
19. The serving-contract-v2 activation commit is the linearization point: a database transaction that
    creates a recommendation request holds the shared cutover lock until commit, while activation
    holds the exclusive form. No version-1 request can commit after activation commits.
20. A current artifact-license denial or revocation is live authority, not merely future admission:
    it fences work and selection, tombstones affected Face projections, and bounds offline exposure by
    the independently reviewed license-specific lease.
21. Contract-v2 request identity includes one of exactly `recommendations | home | offline_pack`; SQL
    null/three-valued logic, application-role direct inserts, or an unknown surface cannot bypass the
    deferred decision/served-item invariant.
22. Qualification material is not borrowed from an account/library authorization. A fixture source,
    annotation withdrawal, license loss, retention expiry, or deletion invalidates every dependent Face
    quality approval and activation before the evidence becomes unavailable.

## 5. Shared components and strict product boundaries

### 5.1 Shared components

Build or extend the following shared components once:

1. **ML artifact and license authority**
   - add an immutable generic `ml.artifact` content table and separate immutable license-decision
     relation; do not force tokenizers/interpreters/models into `ml.embedding_model`;
   - retain `ml.embedding_model` as an embedding-specific typed subtype referencing the generic
     artifact and add typed relations for Face interpreter/combined export, Sona model/tokenizer,
     and optional calibration artifact;
   - record exact license identifier/text hash, use restrictions, redistribution decision,
    modification decision, attribution payload, reviewer, review time, review reference, decision
    sequence, superseded sequence, effective generation, reviewed maximum offline revocation lag, and
    derived-output disposition;
   - maintain one transactionally derived current decision per artifact. Its states are
     `LEGACY_UNREVIEWED | APPROVED | DENIED | REVOKED`; only current `APPROVED` is selectable;
   - every activation freezes a sorted complete required-artifact set and its digest. For Face this
     includes encoder weights, interpreter/combined export, calibration (when separate), and any
     executable preprocessing/probe/codec artifact named by the semantic key; no required artifact may
     be omitted because it is small or bundled with another release;
   - the Face artifact-list codec is frozen as `FACE_ARTIFACT_POLICY_LIST_V1`. Allowed ASCII roles are
     `ENCODER_WEIGHTS`, `INTERPRETER_EXPORT`, `CALIBRATION`, `PREPROCESSING_EXECUTABLE`, `DECODER_PROBE`,
     and `TIMELINE_CODEC`. The signed activation manifest carries the required cardinality for every role:
     encoder and interpreter are nonzero; an optional role is zero only when the semantic key proves it is
     not a separate artifact. Multiple artifacts in one role are allowed only up to that cardinality; an
     exact `(role,artifact_sha256)` duplicate is forbidden, while one content hash may occupy two roles
     only when both manifest entries explicitly name it. Unknown roles or a cardinality mismatch reject
     activation/projection;
   - both list codecs sort by role's raw ASCII bytes, then raw 32-byte artifact SHA-256. The required-set
     document is JCS of `{"v":1,"entries":[{"role":...,"artifact_sha256":<lowercase 64 hex>},...]}` and
     hashes UTF-8 bytes under `autplay.face.required-artifact-set.v1\0`. The policy document is JCS of
     `{"v":1,"entries":[{"role":...,"artifact_sha256":...,"decision_sequence":...,
     "decision_generation":...,"max_offline_revocation_lag_ms":...,"disposition":...},...]}` and hashes
     UTF-8 bytes under `autplay.face.artifact-policy-list.v1\0`. Disposition is exactly
     `DELETE_AFTER_LEASE | RETAIN_NON_DISTRIBUTABLE`; all integers are non-negative JSON integers bounded
     by `2^53-1`. Python and Kotlin must match checked-in empty/minimal/multi-role/same-hash-two-role golden
     byte vectors and digests; locale-sensitive sorting, duplicate keys, alternate hex case, or unbounded
     numbers are rejected before signature verification;
   - derive one tuple policy transactionally: `offline_lease_ms = min(604800000,
     every required current decision.max_offline_revocation_lag_ms)`. Every input must be strictly
     positive; a zero lag makes the whole tuple ineligible for activation rather than "online-only".
     `derived_output_disposition` uses the strict order `DELETE_AFTER_LEASE` stricter than
     `RETAIN_NON_DISTRIBUTABLE`; one delete decision makes the whole dependent output delete-after-lease;
   - reject activation when any required artifact lacks current approval, and fence Face
     claim/publication plus Sona readiness/final commit when a successor denial/revocation becomes
     current. Existing v1.0.0 CPU behavior may continue through its legacy code path but does not
     thereby become approval for a new Face or Sona activation.
2. **Evidence and approval envelopes**
   - canonical JSON, bounded sizes, content hashes, exact source/artifact/config ancestry;
   - immutable signed approvals with the existing deployment-pinned reviewer trust anchor;
   - separate technical, quality, privacy, and activation approvals.
3. **GPU admission authority**
   - PostgreSQL-backed lease/generation fencing by device UUID;
   - reservation includes process kind, model identity, requested VRAM budget, priority, lease,
     heartbeat, and cancellation generation;
   - Sona serving acquires its resident reservation before constructing a CUDA session and has
     priority over new Face batches;
   - Face holds a reservation for the entire lifetime of its CUDA session, checks cancellation
     between bounded batches, and unloads/recreates the session or terminates on cancellation;
   - both processes fail closed on lease heartbeat/database-authority loss. PostgreSQL coordinates
     ownership; NVML measurement plus process exit/session unload proves actual VRAM release. There
     is no claim of driver-level hard preemption.
4. **Derived-data lifecycle hooks**
   - owner authorization, account deletion, consent revocation, last-reference loss, export,
     retention, backup/restore, tombstone, supersession, and reconciliation share established
     transaction/fence patterns but retain product-specific policies.
5. **Operational vocabulary**
   - bounded reason codes, metrics cardinality, queue and latency histograms, active lineage,
     fallback counters, integrity errors, and audit receipts.

### 5.2 Boundaries that must not be crossed

| Concern | Face | Sona |
|---|---|---|
| Primary scope | Recording/AudioVariant/source lineage | Owner history/request/candidate set |
| Output | Musical-character timeline | Ranked candidates and generated Semantic ID |
| Android | Downloads and renders projection | Receives ordinary recommendation response only |
| Sharing | Canonical timeline may be reused by currently authorized references to the same exact source | Evidence and participation remain owner scoped |
| Activation | Embedding + interpreter + timeline schema | Tokenizer + model + pipeline + cohort |
| Rollback | Select prior compatible timeline lineage | Return owner/cohort to P11 |
| Privacy | Never infers listener state | Uses interaction history under explicit training consent |

Face output is not a user-preference feature and is not fed into Sona in this plan. A shared encoder
is permitted only as an implementation reuse: Face and Sona still approve it independently and may
activate different versions.

## 6. Models, datasets, and licensing

### 6.1 Distribution policy

AutPlay is planned as a distributable non-commercial/self-hosted product. The operator installs
approved model weights separately. The installer/import command must:

1. accept only a local regular file below the configured model-import root;
2. reject symlinks, URLs, path traversal, unexpected size/format, pickle-bearing artifacts unless
   the reviewed conversion process consumes them in isolation, and all hash mismatches;
3. copy or convert into a content-addressed cache using staged write, checksum, `fsync`, and atomic
   rename;
4. persist the immutable manifest and license review before the artifact becomes selectable;
5. never download weights implicitly during API, worker, or Android operation.

The current official sources leave a conflict for Essentia models: the model catalog says
CC BY-NC-SA 4.0 while the licensing page says CC BY-NC-ND 4.0. Essentia runtime is AGPLv3 for the
non-commercial route. Therefore Essentia weights may be used in isolated evaluation, but production
activation requires one recorded resolution: exact artifact license text and hash plus a reviewer
decision that the planned unmodified server-side use and distribution mechanism comply. If the
conflict remains unresolved, those weights fail the license gate.

### 6.2 Bounded Face model bake-off

Freeze the bake-off protocol before development comparison. Compare only on development/calibration
data:

1. existing Musicnn-DEAM and EffNet-MTG-Jamendo frozen baselines;
2. LAION-CLAP music checkpoint, with explicit checkpoint/training-provenance review despite CC0
   repository/checkpoint labels;
3. YAMNet as a compact general-audio control, not an assumed production winner;
4. MERT-v1-95M only if the first three fail semantic gates and its CC BY-NC terms are accepted.

Each candidate must produce a safe, pickle-free ONNX artifact or an equally isolated reviewed
runtime. The production GPU process consumes only the reviewed artifact format; it never executes
remote/custom model code. A separately versioned `SemanticInterpreterRelease` maps embeddings and
segment scores to the six initial axes:

- positive/melancholic;
- calm/energetic;
- soft/aggressive;
- light/dark;
- relaxed/tense;
- direct/atmospheric.

Candidate selection is deterministic and recorded before final qualification. Development uses the
same segment rubric and exact metric implementation as qualification. For each axis `a`, define:

- `coverage_a = emitted_non_abstaining_segments / all_preregistered_segments`, floor `0.90`;
- Spearman `rho_a` over every segment, floor `0.45`;
- directional balanced accuracy `ba_a`, floor `0.65`, with at least 20 reference cases at each pole;
- `ece_a` using the ten bins below, ceiling `0.10`; and
- valid annotation reliability with Krippendorff alpha at least `0.50`.

An abstention is included as value `0` and confidence `0` for Spearman/ECE, counts as an error for a
directional case, and does not increment coverage. An undefined/non-finite metric, insufficient pole
count, invalid annotation axis, or coverage below the floor makes that axis fail. After computing in
binary64 with the pinned library/runtime, canonical decision values are decimal-quantized to six
places using round-half-even. Define normalized margins
`(coverage_a-.90)/.10`, `(rho_a-.45)/.55`, `(ba_a-.65)/.35`, and
`(.10-ece_a)/.10`; the axis margin is their minimum and the candidate score is the minimum axis
margin across all six axes. A full candidate has six non-negative axis margins. Select the largest
candidate score, then the lower arithmetic mean of the six ECE values, then higher target-GPU tracks
per hour, then lexicographically smaller manifest SHA-256. All comparisons use the canonical
six-place values. A limited research candidate uses the same score over its passing axes and the
same tie order, requires the stated four-axis rule, and can never touch the final set or pass M7.
Confidence intervals on development data are diagnostic, not a substitute for the final gate.

The interpreter owns normalization, clipping policy, calibrated confidence, abstention, stable
rounding/quantization, temporal smoothing, event extraction, and compatibility with encoder and
preprocessing versions. Failed axes abstain; they are not emitted as zero.

Bake-off selection outcomes are `SELECT_FULL_CANDIDATE`, `SELECT_LIMITED_CANDIDATE`, or
`NO_DEVELOPMENT_CANDIDATE`. `SELECT_LIMITED_CANDIDATE` requires at least four passing axes, including at least one of
positive/melancholic or direct/atmospheric; every disabled axis must always abstain. It is an
evaluation/canary outcome only and **does not** satisfy M7 or the selected full-Face production gate.
Only a six-axis `SELECT_FULL_CANDIDATE` may be frozen for final qualification. Final outcomes are
`APPROVE` or `NO_MODEL_PASS`; a limited selection, no development candidate, or final failure leaves
production blocked unless the user later changes the product scope explicitly.

### 6.3 Face datasets and annotation

Create three content-addressed collections. Audio stays outside Git and outside every participant
account/library/Vault authorization. Only operator-owned recordings or recordings covered by an exact,
written qualification license may enter these collections. The license manifest binds the source SHA-256,
permitted analysis/annotation/derived-evidence uses, retention deadline, territory if any, reviewer, text
hash, and withdrawal/termination terms. A user's ordinary upload, Face opt-in, or library reference is never
qualification consent.

The fixture lifecycle is executable:

- smoke audio expires 30 days after its recorded pilot completion; development/calibration audio and raw
  annotations expire 180 days after the bake-off selection receipt; both are deleted within seven days of
  expiry unless a documented legal hold blocks deletion;
- the sealed final corpus, its raw ratings, and decoder inputs are retained only while a dependent signed
  quality approval is current or its immediately prior activation remains inside the 30-day rollback window.
  Every approval has `expires_at <= signed_at + 365 days`; activation/readiness rejects an expired approval.
  After the later of approval expiry/supersession and its rollback deadline, source and rater-level material
  is deleted within seven days;
- early fixture-license termination, operator deletion, integrity quarantine, or inability to retrieve the
  exact bytes first increments a fixture-authority generation, invalidates dependent candidate/approval
  readiness and active Face selection to neutral, then purges within seven days where legally permitted.
  An approval never survives deletion of its raw source or required raw annotations; a successor requires a
  newly sealed corpus and requalification;
- fixture audio and the rater-identity map are absent from PostgreSQL and ordinary product backups. An
  encrypted operator fixture store is backed up separately with the same expiry; an independent deletion
  tombstone ledger is replayed before restore leaves quarantine. The operator evidence export contains
  manifests, licenses, reports, hashes, retention/hold state, and deletion receipts. Participant account
  export/deletion cannot expose or retain fixtures because they contain no account/library identifiers;
- raters grant explicit `FACE_QUALIFICATION_ANNOTATION_V1` purpose consent/contract before annotation.
  AutPlay stores only a random study pseudonym; the operator keeps the identity map separately. Access,
  export, correction, or withdrawal is fulfilled through that map. Withdrawal stops new rating, deletes the
  rater's raw rows within seven days, recomputes the sealed report, and invalidates any approval whose report
  hash, minimum raters, pole counts, reliability, or other gate would change. Consent records and
  non-personal deletion receipts retain no rating payload after expiry.

The collections are:

1. **Smoke pilot:** 10-20 operator-owned/licensed recordings for decoder/preprocessing/runtime failures.
   It cannot decide quality.
2. **Development/calibration:** at least 30 and preferably 50 representative operator-owned/licensed recordings,
   stratified across genre, production style, energy, instrumentation, duration, and recording
   quality. It may tune the interpreter and confidence policy and is the only collection used to
   compare candidates. The bake-off selects and signs exactly one encoder/interpreter/preprocessing/
   execution-profile candidate before final qualification is unsealed.
3. **Final qualification:** at least 15 additional operator-owned/licensed recordings, disjoint by Recording and, where
   feasible, artist and release. It is sealed before thresholds and the single selected candidate
   manifest are signed. Exactly that candidate is evaluated once. A failure yields `NO_MODEL_PASS`;
   evaluating a successor requires a newly collected/sealed qualification set, not choosing the best
   result from repeated candidates on this set.

For final qualification, pre-register twelve non-overlapping ten-second observation segments per
track, distributed across the full duration, plus independently marked transition times. With the
minimum 15 final-qualification tracks this yields at least 180 segment observations. Collect at least three
independent ratings per segment and track summary. Raters score the six continuous musical axes,
confidence/uncertainty, and meaningful section transitions; they are instructed to rate perceived
musical character, not their own mood.
Store only bounded annotations, study pseudonyms, rubric version, exact fixture-license authority/generation,
retention deadline, and hashes in the evidence boundary described above.
Do not use DEAM or MTG-Jamendo as independent evidence for a head trained on that same dataset.

DEAM and MTG-Jamendo remain non-commercial research/calibration references. Song Describer may be
used as an external caption-alignment evaluation set under its CC BY-SA terms, never as a substitute
for temporal labels or the sealed operator-owned/licensed final-qualification set.

### 6.4 Face semantic acceptance gates

Freeze the following exact protocol and its implementation hash before final-qualification unsealing:

- reverse-code each bipolar axis to `[-1,1]`; the per-segment reference is the median of at least
  three valid raters, and the model observation is the median model value over that exact segment;
- require Krippendorff's alpha (ordinal distance) at least 0.50 per axis. A lower value invalidates
  that axis dataset and requires new annotation; it is not a model failure that may be tuned away;
- compute per-axis Spearman over segments. The point estimate must be at least 0.45 and its
  Bonferroni simultaneous one-sided lower bound must exceed 0.20;
- directional cases have reference median at most `-0.20` or at least `+0.20`; predictions use
  thresholds `-0.15/+0.15`, and abstentions/wrong-neutral predictions count as errors. Require at
  least 20 cases in each pole, balanced accuracy at least 0.65, and its Bonferroni simultaneous
  one-sided lower bound above 0.55;
- ECE uses ten fixed confidence bins `[0,.1),...,[.9,1]`; correctness means correct directional pole
  for directional cases and absolute error at most 0.20 otherwise. Empty bins contribute zero
  weight. Require ECE at most 0.10 and its Bonferroni simultaneous one-sided upper bound at most 0.15;
- transition matching is one-to-one maximum-cardinality matching inside `+/-3,000 ms`; unmatched
  labels/predictions are FN/FP. Require micro F1 at least 0.60 and track-bootstrap lower bound above
  0.50. Qualification requires at least 60 reference transitions across at least ten tracks; zero or
  insufficient positives makes the gate `INSUFFICIENT_REFERENCE_TRANSITIONS`, never a vacuous PASS;
- blinded timeline-vs-time-shuffled preference uses one vote per rater/track. Require at least 65%
  preference and a one-sided lower bound above 50%;
- all scripts, bins, seeds, segment IDs, exclusions, and bootstrap outputs enter the signed report.

The confidence construction is executable rather than an implementation choice:

1. There are three separately preregistered six-hypothesis families: Spearman lower bounds with
   `H0: rho_a <= .20`, balanced-accuracy lower bounds with `H0: ba_a <= .55`, and ECE upper bounds
   with `H0: ece_a >= .15`. Each family uses Bonferroni one-sided `alpha_axis=.05/6`; no data-driven
   hypothesis ordering occurs. Transition F1 and blinded preference are two distinct singleton
   families at one-sided alpha `.05`.
2. Generate exactly 10,000 track-block resamples. Sample the qualification track IDs with
   replacement to the original track count; a sampled track contributes all its fixed segments,
   ratings, and transition labels. Duplicate draws are distinct blocks. Never drop or redraw a
   resample. An undefined lower-bound replicate takes the metric's worst value; an undefined
   upper-bound ECE replicate is `1.0`.
3. Derive a separate PCG64 seed for each metric family from the first unsigned big-endian 64 bits of
   `SHA-256("autplay.face.final-bootstrap.v1\\0" || qualification_manifest_sha256 || family_name)`;
   pin NumPy and the metric implementation hashes in the qualification manifest.
4. Use the empirical inverse-CDF/type-1 quantile: for sorted `B=10000` values, quantile `p` is
   element `max(1,ceil(p*B))` in one-based indexing. Lower bounds use `p=alpha_axis` (or `.05` for a
   singleton); upper bounds use `p=1-alpha_axis`. Threshold equality follows the operators stated
   above and is covered by golden fixtures.

The required floors also include:

- no license, provenance, integrity, finite-value, or source-lineage failure;
- deterministic repeated canonical result on the same artifact/execution profile after the
  interpreter's declared stable quantization;
- invalid/non-music/corrupt/too-short inputs abstain or fail closed with zero false high-confidence
  semantic publication in the qualification corpus;
- per-axis non-abstaining coverage is at least 0.90; final metrics apply the same abstention mapping
  as development and any undefined/non-finite point estimate fails that axis;
- all six axes meet every final gate. Limited-axis status exists only in development research and is
  never evaluated on this sealed final set.

If the sample is too small for the stated confidence calculation, the gate remains open rather than
relaxing thresholds.

### 6.5 Sona data sources

1. Restore `0026` is materialized only in an isolated restore with explicit owner authorization.
   It is tagged `RECONSTRUCTED_FROM_0026_SYNC_TRUTH_V1`, never represented as an original R1A
   snapshot, and all outputs remain `quality_eligible=false` and `activation_allowed=false`.
2. Native capture begins before Face implementation completes. It records the already-authoritative
   P11 request, complete candidate membership, normalized temporal evidence, snapshot/profile
   identity, immutable P11 ranking, and later causal outcomes.
3. Quality work begins only when preflight proves exactly one approved embedding model, complete
   mandatory-filtered candidate sets, all required labels/outcomes, sufficient chronological span,
   owner authorization/consent, bounded examples, and train/validation/test splits with two seven-day
   embargo gaps and no label timestamp touching the next split.
4. Dataset artifacts contain owner HMAC lineage tokens, never raw UUIDs. Raw request/recording IDs
   stay in the controlled materialization boundary and are absent from portable manifests.
5. Ordinary P11 snapshots keep their existing 30-day retention. Owners who explicitly enable R1B
   quality capture receive a visible 180-day source-bundle retention: baseline snapshot, complete
   candidate set, temporal snapshot/events, causal outcomes, capture/work rows, and exact hashes
   share one expiry. Withdrawal stops collection and removes retained training inputs under the
   existing cleanup fence; it never extends expiry. Signed approvals and aggregate audit receipts
   may remain, without owner payloads, while required for an active/rollback artifact.
6. Native readiness requires at least 300 fully bound requests over at least 28 chronological days,
   with non-empty 60/20/20 time splits after both seven-day embargoes, at least 180/60/60 examples in
   train/validation/test, and at least 30 labeled test cases for every required signal. These are
   minimum admission counts, not permission to tune on test data.

## 7. Target server design for Semantic Face

### 7.1 PostgreSQL schema

The implementation branch starts at Alembic head `0060`. Reserve and apply the following ordered,
forward-only migration map; if a concurrent branch consumes a number, preserve the order and rename
without collapsing responsibilities:

1. `0061_ml_artifact_authority`: immutable `ml.artifact` keyed by content SHA-256, immutable
    manifests, append-only `ml.artifact_license_decision`, and one current-decision projection with
    monotonic generation; add embedding/Face/Sona typed subtype FKs without weakening existing
    `ml.embedding_model` constraints. Upgrade derives content rows from the existing exact
    `weights_sha256`, byte size, manifest hash, and manifest, but creates only
    `LEGACY_UNREVIEWED` license decisions because v1.0.0 has no license-text hash or structured use,
    redistribution, modification, and attribution decisions. It also adds a dedicated non-null
    monotonic `account.device.device_key_generation`: migration backfills `1` for a row with a current
    `public_key` and `0` for a never-bound row, while new device rows default to `0`. A restricted trigger/
    function is the only key mutation path: first binding is exactly `0 -> 1`; replacement and revocation
    are exactly `old + 1` under the row lock (a revoked row may therefore have null `public_key` and a
    positive generation). SQL checks require generation `>= 0`, generation `0 => public_key IS NULL`, and
    `public_key IS NOT NULL => generation >= 1`; transition tests reject direct jumps, reuse, decrement,
    key change without increment, or first binding from a nonzero unbound state. The migration also installs the
    shared bounded operation-step-up challenge/receipt authority before any ML admin activation endpoint
    exists. General device `row_version` and session generation never substitute for this key generation.
2. `0062_gpu_admission_authority`: per-device current generation/lease plus append-only admission and
   release receipts; unique active holder per `(device_uuid,reservation_kind)` and bounded expiry,
   heartbeat, priority, requested/measured VRAM columns.
3. `0063_sona_native_capture_and_restore_fences`: immutable request source bundle, exact temporal/
   candidate/P11 hashes, source dispatch, retryable lineage work, attempts, evidence, retention,
   owner export inventory, post-backup deletion/withdrawal projection, quarantine/reconciliation
   receipts, and indexes by owner/expiry/state/lease. These fences exist before any new owner payload
   is retained.
4. `0064_face_artifact_activation`: Face interpreter release, qualification-set/approval authority,
    execution profile, activation chain, owner policy history/current projection.
5. `0065_face_timeline_work`: owner-scoped sponsorship intent, typed/coalesced target, work/attempt,
    canonical timeline v2, bounded projection-fan-out root, immutable per-sponsor projection intents,
    projection reference/lease, rollback-retention reference, backfill, redirect reconciliation/outbox,
    tombstones, Face privacy/restore fences, and lifecycle indexes.
6. `0066_face_sync_projection`: capability-safe Face aggregate/event/bootstrap projection and exact
   payload constraints; old clients receive neither incremental Face events nor Face bootstrap rows.
7. `0067_sona_r1c_serving`: dormant purpose-specific consent projection, approvals, activation chain,
    owner cohort projection, breaker state/receipts, immutable R1C inference evidence/candidates,
    serving decision, a purgeable decision/evidence lifecycle association, served items, purgeable v2
    request-replay payload/baseline truth, typed purgeable v2 attribution associations, owner-scoped
    serving lifecycle/expiry tombstone, an audited
    serving-contract cutover, attribution compatibility/expiry, export, and restore/quarantine gates.

Every migration has fresh-install, upgrade-from-0060, concurrent-race, privilege, downgrade-refusal,
and backup/restore tests. M2 may not capture native Sona data until 0061-0063 are present; Face
publication cannot start before 0064-0066; R1C cannot start before 0067 and its independent serving-
consent ledger is provisioned and reconciled.

The 0061 upgrade is deliberately non-activating. It preserves every existing embedding row and the
ordinary CPU/P11 path, verifies that the derived artifact identity matches the existing immutable
hashes, and emits an audit report for rows that cannot be derived exactly. No
`LEGACY_UNREVIEWED` row satisfies a new approval. An OWNER/ADMIN reviewer must append an exact manual
decision before Face/Sona selection. A later `DENIED` or `REVOKED` decision increments the current
license generation; Face claim/publication and Sona readiness/final commit bind and recheck that
generation, so an older `APPROVED` receipt cannot remain effective.

Add bounded tables/types equivalent to:

1. `ml.face_semantic_interpreter`
   - immutable interpreter ID/version, compatible embedding model/task, artifact and manifest hashes,
     axis schema/version, preprocessing hash, calibration evidence, license review, runtime revision,
     status, created/reviewed timestamps;
   - unique immutable version and no in-place update after review.
2. `ml.face_timeline`
   - semantic key (unique), result hash, exact Recording/AudioVariant/source hash, decoded sample
     identity and `SourcePresentationMapV1`,
      embedding model, interpreter, preprocessing, execution-profile digest, canonical `BYTEA`, byte
     size, keyframe/event counts, status, producing job/attempt, created/retained timestamps;
   - maximum 1 MiB enforced before SQL and with a database check; result hash is over exact canonical
     bytes; one semantic key cannot accept two different hashes.
3. `ml.face_timeline_activation`
   - append-only activation/rollback chain with sequence, target/previous encoder, interpreter,
     preprocessing, execution profile, Face v2 schema/codec, exact current artifact-license decision
     sequences/generations, evidence hashes, actor, action, timestamp, and monotonic epoch.
4. `ml.face_projection_reference`
   - current authorized owner/reference to an immutable timeline plus policy generation, activation
      and redirect generations, signed `issued_at_ms`/`authorized_until_ms`, server instance/identity
      epoch/thumbprint/key ID/signature, tombstone/supersession, retention basis, last-authorized
      timestamp;
   - no owner can create authority for a Recording/AudioVariant it cannot currently access.
5. `ml.face_analysis_policy`
   - self-chosen desired enabled state, independent admin safety-inhibition state, effective
     generation, selected lineage, actor, and reason; append-only history plus one current projection.
     An admin inhibition can only make effective state false and cannot manufacture owner consent.
6. `ml.face_backfill_operation`
   - owner, fixed eligibility watermark, request hash, policy generation, cursor, bounded batch size,
     counts by terminal reason, state, cancellation, and timestamps.
7. Extend the durable enrichment job target or add a typed Face job target so
   `ml.face-timeline/v2` has exact domain identity and cannot accept arbitrary URLs/paths.
8. `ml.face_analysis_sponsor`
   - immutable `face_sponsor_id` UUID identity plus owner/reference/policy/lineage, exact
     `source_generation`, AudioVariant and source SHA-256, predecessor/successor sponsor IDs,
     enabled/withdrawn/superseded generations, and retention authority; canonical work may be
     coalesced, but owner authorization and source history never are.
9. `ml.face_rollback_retention`
    - successor/prior activation, exact prior tuple, source/timeline/artifact references, fixed
      `retain_until`, live-reference condition, override reason, and GC/reconciliation state; one
      successor can retain only its immediate predecessor for at most 30 days.
10. `ml.face_projection_fanout` and `ml.face_projection_intent`
    - one immutable fan-out root per `(face_timeline_id,work_authority_generation)` with stable sponsor
      cursor, fixed batch limit `100`, counts, state, lease, and restart receipt; one immutable intent per
      `(fanout_id,face_sponsor_id,sponsor_generation)` binding the exact timeline, owner/reference,
      policy/activation/redirect/license generations and terminal `APPLIED | CANCELLED_*` reason;
    - fan-out/intents grant no authority by themselves. Intent discovery and projection materialization
      each recheck the individual sponsor and exact current authority under the sponsor lock.
11. `ml.face_qualification_set` and `ml.face_qualification_approval`
    - metadata only: immutable manifest/report hashes, collection kind, fixture-license and rater-consent
      authority digests/generations, source/segment/rater counts, fixed `retain_until`, current state,
      deletion/withdrawal generation, and separately signed approval with `expires_at`; raw audio,
      identity mapping, and raw annotations remain in their bounded external evidence stores;
    - activation references the exact approval and authority generation. A privileged reconciler applies
      independent fixture/rater tombstone-ledger facts before restore exposure; expiry, source deletion,
      license/consent withdrawal, count/report change, or hash failure transactionally makes readiness
      false and appends a deactivation/tombstone outbox before external evidence is purged.

Required keys and relations are not left to implementation guesswork:

- `ml.artifact(artifact_sha256)` is the generic immutable content identity;
- each reviewed typed release has a UUID PK and unique `(role,key,version,manifest_sha256)` plus an
  FK to `ml.artifact`; license decisions key `(artifact_sha256,decision_sequence)` and explicitly
  supersede the prior sequence. `ml.artifact_license_current` contains the exact current sequence,
  state, and generation and is updated in the same transaction as the append-only decision;
- `ml.face_timeline` is unique only on the complete Face v2 semantic key, which includes source hash,
  decoded-sample identity, `SourcePresentationMapV1` digest, decoder/probe identity, Face schema/codec,
  execution profile, encoder, interpreter, preprocessing, and calibration identities. The abbreviated
  `(recording_id,audio_variant_id,execution_profile_sha256,embedding_release_id,
  interpreter_release_id,preprocessing_sha256)` tuple is a non-unique lookup index; presentation-map,
  probe, schema, codec, or calibration successors may legitimately share it;
- projection references key `face_projection_id`, FK exact timeline/policy/activation, and unique one
  live projection per `(owner_user_id,user_track_ref_id,policy_generation)`;
- Face sponsorship has immutable `face_sponsor_id` as PK and a partial unique index allowing exactly
  one live, non-superseded sponsor per `(owner_user_id,user_track_ref_id,policy_generation,
  lineage_sha256)`. Each row additionally binds source generation, AudioVariant, and source SHA-256;
  replacement locks and supersedes the live row before inserting a successor with reciprocal
  predecessor/successor links. A coalesced Face work row keys exact `(recording_id,audio_variant_id,
  source_sha256,lineage_sha256)` and has at most one live lease while its locked live-sponsor set is
  non-empty. Sona work keys
  `(recommendation_request_id,model_manifest_sha256,tokenizer_sha256,pipeline_manifest_sha256,
  execution_profile_sha256)`;
- Sona served decisions key `recommendation_request_id`; served items key
  `(recommendation_request_id,source_rank)` and are unique by recording within the request;
- all owner-readable indexes begin with owner/profile scope; expiry and claim indexes are partial on
  live states; every hash column has a 32-byte check and every JSON/BYTEA body a pre-SQL and SQL bound.

The 0063 capture relations are `ml.sona_capture_bundle` (one per P11 request, owner FK, exact
baseline/temporal/candidate/ranking hashes, cutoff/watermark, canonical bounded document, 180-day
expiry), `ml.sona_capture_lineage_cursor` (one per captured request, active discovery through bundle
expiry and last-seen registry generation), `ml.sona_capture_target_dispatch` (one row per exact
request/model/tokenizer/pipeline/execution-profile target), `ml.sona_shadow_work` (exact lineage plus
execution-profile uniqueness and lease/state),
`ml.sona_shadow_attempt` (append-only bounded attempts), and `ml.sona_shadow_evidence` (immutable
terminal document/hash). The capture bundle preserves the complete P11 universe up to its existing
5,000-track bound; lineage work is eligible only at `1..1024`. A larger universe records an
ineligibility reason and is never truncated into quality evidence.

The 0067 serving relations are:

- a PostgreSQL purpose-specific serving-consent projection keyed by owner tag/purpose/revision. Its
  authority is the separately provisioned `FilesystemServingConsentLedger`, not the restored database;
- `ml.sona_serving_approval` keyed by signed approval hash and exact
  run/publication/artifact/purpose/execution-profile ancestry; `ml.sona_serving_activation` keyed by
  monotonic epoch and the same profile; and
  `ml.sona_serving_cohort(owner_user_id)` with activation epoch, state, generation, and reason;
- `ml.sona_breaker_state(cohort_id)` with state, generation, cooldown, failure streak, window counters,
  and append-only transition receipts. Because a cohort identifies an owner in the initial canary, this
  state, every counter bucket, and every receipt are explicitly owner-scoped personal data rather than
  anonymous telemetry;
- `ml.sona_r1c_inference_evidence(recommendation_request_id)` plus
  `ml.sona_r1c_inference_candidate(recommendation_request_id,candidate_rank)` rows unique by recording,
  binding
  the complete candidate-set hash, model/tokenizer/pipeline/artifacts, approval hash/expiry, serving-
  consent digest/generation, activation/revocation/cohort/breaker generations, execution profile,
  request/raw-output/postprocessed-ranking hashes, actual ranked Sona output, and fixed
  `candidate_evidence_expires_at=request.created_at+30 days`. This is separate
  from R1B `ml.sona_shadow_evidence`, whose no-serving semantics remain unchanged;
- `ml.recommendation_serving_decision(recommendation_request_id)` with P11 ranking hash, optional Sona
  R1C inference-evidence content hash (null only when Sona never ran), actual pipeline/artifact/all
  authority generations, fallback reason, exact state, non-negative `p11_item_count`,
  `p11_items_sha256`, `served_item_count`,
  `served_items_sha256`, canonical decision hash covering both count/digest pairs, and fixed
  `served_truth_expires_at=request.created_at+180 days`. The immutable decision has no purgeable
  evidence FK and is never updated during evidence cleanup. For contract v2 the P11 ranking hash is
  exactly `p11_items_sha256`, not a second independently encoded digest;
- `ml.recommendation_serving_evidence_link(recommendation_request_id,evidence_id)` as the only
  relational pointer from a decision to detailed R1C evidence. It is excluded from the canonical
  decision hash, has one row only when the stored evidence hash equals the decision hash, references
  the decision with `ON DELETE CASCADE` and evidence with `ON DELETE RESTRICT`, and is deleted by the
  privileged lifecycle worker before the 30-day evidence delete. Application roles cannot insert,
  update, or delete links directly; no trigger is permitted to mutate the decision;
- `ml.recommendation_served_item(recommendation_request_id,source_rank)` containing the actual item,
  fixed-scale `score_units_e8`, score kind, reasons/contributions plus their database-derived subdocument
  hashes, nullable original P11 rank, `served_item_sha256`, and uniqueness on recording. The
  compatibility wire name `source_rank` now means the committed 1-based served/display rank; for P11
  it equals the baseline rank, while Sona origin is explicit metadata. Existing `recommendation_item`
  remains immutable P11 baseline truth only through the request's 180-day replay lifetime; 0067 adds
  nullable-for-v1 `score_units_e8`, canonical subdocument hashes, and `baseline_item_sha256`, all required
  for a version-2 parent;
- `ml.recommendation_attribution_association(association_id)` is the only durable version-2 causal edge
  from a base event/output to its request and, for item-level events, served rank. It stores owner, request,
  nullable served rank/recording,
   separately named server `serving_provenance`/`request_surface` and bounded client
   `presentation_source`/`presentation_surface` with explicit wire-contract version, association kind,
   optional presentation identity, fixed
  `attribution_expires_at=request.created_at+180 days`, and exactly one nullable typed target ID for
  `library.listening_event`, `library.user_interaction_event`, `ml.recommendation_temporal_event`, or
  `ml.offline_recommendation_pack`; a check maps each enumerated kind to exactly its allowed target and
  requires `presentation_id` for `IMPRESSION|FEEDBACK` only. Item-level kinds require non-null served rank
  and recording. `OFFLINE_PACK` is explicitly request-level: rank, recording, and presentation are null,
  while `pack_served_item_count`, `pack_served_items_sha256`, decision hash, and pack-payload SHA-256 are
  non-null and must equal the request's committed complete served set plus the pack row. Partial unique
  indexes enforce one applicable
  edge per target. Composite FKs bind `(owner_user_id,recommendation_request_id)` to the request,
  `(recommendation_request_id,served_rank,recording_id)` to a unique served-item key for item-level kinds, and
  `(owner_user_id,target_id[,recording_id])` to owner-qualified unique keys on every typed target. Every
  typed target FK and the request FK use `ON DELETE RESTRICT`, never `CASCADE` or `SET NULL`; a target or
  request cannot disappear while its v2 association exists.
   Insert/update is accepted only when a deferred trigger also proves the target's event type/origin,
   recording, profile/device owner, and expiry match. Server provenance and request surface must match
   the committed serving decision/item and request; client presentation labels are never compared with
   those server fields or used as serving authority. A feedback association contains the
  referenced impression association ID and must match its owner, presentation, request, rank, recording,
   and server provenance/request surface through a composite FK plus deferred semantic check. Its client
   presentation labels may differ from the impression labels and do not establish causal identity.
   Cross-owner/cross-rank/wrong-kind
  rows are therefore invalid even to the application SQL role. Association rows are immutable after
  commit and deletable only by the content-bound lifecycle procedure. A partial unique constraint for
  `kind=IMPRESSION` covers `(owner_user_id,presentation_id,recommendation_request_id,served_rank)`; the
  impression write takes the matching domain-separated advisory lock, looks up that association first,
  and idempotently returns its target only when the canonical request hash matches, otherwise conflicts.
  The unique constraint is the final cross-device race authority, so two different interaction IDs cannot
  represent the same presentation/request/rank. For `OFFLINE_PACK`, the deferred trigger recomputes the
  request-level served count/digest from persisted items and requires the canonical pack envelope to carry
  the same decision hash/count/digest before the payload hash and association commit; no arbitrary single
  rank represents a multi-item pack. For a v2 base event the legacy
  request/rank columns are null and its durable JSON contains no attribution object/request ID. Revised
   base relations carry noncausal `attribution_contract_version`: `1` requires the legacy inline pointer,
   while `2` requires all legacy request/rank and feedback `impression_interaction_id` columns null;
   impression linkage lives only in the typed association. A deferred trigger on insertion (or a transition into a
  recommendation-origin event) requires the typed association in the same transaction for version 2;
  ordinary later noncausal updates do not recreate an expired edge. Its authorized expiry deletion
  preserves the listening/interaction/taste event. An offline pack is causal content rather than history
  and is deleted by its own expiry no later than the request boundary;

Migration 0067 makes association integrity reciprocal at the target boundary. Association creation locks
its typed target row `FOR UPDATE` before validating it and inserting the edge, so a concurrent target
update/delete cannot slip between validation and commit. Restricted `BEFORE UPDATE|DELETE` guards and
deferred constraint triggers run on each of `library.listening_event`,
`library.user_interaction_event`, `ml.recommendation_temporal_event`, and
`ml.offline_recommendation_pack`. While a v2 association is present, they reject deletion and any change
 to a field used by the association's owner, kind, served recording, server provenance/request surface,
 client presentation labels, presentation,
feedback/impression, expiry, or content checks; the deferred trigger rechecks the target and association
after every target/association change in the transaction. In particular:

- listening events freeze their ID, owner, device, track/recording, `event_origin`, context,
  `attribution_contract_version`, and the required-null legacy request field. Only unrelated play
  progress, duration, completion, explicit feedback, or taste-exclusion updates may proceed under their
  existing product rules, and those updates cannot carry a causal pointer in JSON or another column;
- interaction events freeze ID, owner/device, event type, recording, presentation,
  `attribution_contract_version`, the required-null legacy request/rank/impression IDs, and their
  sanitized payload;
- temporal events freeze ID, owner/profile/device, source-event identity/type, signal/derivation/origin,
  recording, evidence document/hash, `attribution_contract_version`, and required-null legacy causal
  columns. Any separate normalized taste materialization is written to its own versioned row;
- v2 offline packs freeze every identity, authority, encoding, payload, `payload_sha256`, and expiry
  column after insertion. Their insert guard verifies `SHA-256(payload)=payload_sha256` and the exact
  request-level decision/count/set digest; the application role has no direct `UPDATE|DELETE` privilege
  on v2 packs. Existing v1 packs retain their old behavior until their own expiry.

The typed target FKs and feedback-to-impression association FK use `ON DELETE RESTRICT`, never cascade,
even for normal account/event maintenance. `app_private.purge_recommendation_attribution_v2` is the only
path that can delete a live association or v2 pack. It takes the same per-request transaction lock,
verifies `SERVING_TRUTH_EXPIRED | PACK_EXPIRED | OWNER_EVENT_ERASURE | CONSENT_WITHDRAWAL |
ACCOUNT_DELETION`, and writes one idempotent content-bound receipt for each removed edge and target.
For an owner-requested impression erasure, the procedure first writes an owner-scoped impression-erasure
tombstone that rejects concurrent or later feedback links, then locks and enumerates every dependent
feedback association/interaction and its sync/temporal materialization under the request lock. It removes
feedback associations first, then dependent feedback sync/temporal rows and interaction targets; only
then does it remove the impression association, sync row, and interaction target. Every descendant is
included in the receipt set; all steps commit or roll back together. A feedback-only erasure removes its
own association/materializations/target and retains its impression. Version-2 base feedback rows have a
required-null legacy `impression_interaction_id`; a deferred guard forbids writing one even via direct
SQL, so no base-row causal reference survives edge expiry. Grandfathered version-1 feedback that still
uses the live inline `impression_interaction_id` is inventoried under the same owner/request lock and
deleted before a requested version-1 impression erasure; a deferred owner-qualified check forbids any
remaining v1 feedback from pointing to the erased target. Ordinary 180-day expiry instead removes v2
feedback associations before impression associations but preserves their independent noncausal base
interaction rows; grandfathered v1 rows retain their original base references and targets until their
separate privacy migration or owner erasure. Pack expiry removes the pack payload after its edge. Consent withdrawal/account
deletion use the same child-before-parent graph order over all affected requests. A privileged target
update that would change a guarded field still fails. Only unrelated noncausal updates remain available
through normal roles. Post-commit target mutation/delete, dependency-graph erasure, and concurrent
association-create-vs-target-update/delete tests must prove no edge can silently change or vanish.

Owner-requested event erasure also has a sync projection contract. In the same database transaction as
each interaction removal, the lifecycle procedure deletes the original content-bearing `sync.sync_event`
row but appends a new owner-scoped `RECOMMENDATION_INTERACTION_ERASED` event (schema version 2,
`operation=DELETE`, `aggregate_type=USER_INTERACTION_EVENT`, `aggregate_server_id` equal to the erased
interaction ID) and its minimal tombstone. It contains no event payload, recommendation IDs, or reason
details; the erasure event ID is distinct from the erased event ID. Publishing it and the erasure receipts
is atomic, retry-idempotent, and ordered after any earlier UPSERT. Pull and bootstrap both include this
tombstone for the authenticated owner. The Android sync contract must negotiate erasure-v2 capability
before affected server writes/cutover: in `SyncEngine.applyProjection`, route this DELETE before the
recommendation-UPSERT/fact-insert branch; Room `SyncDao` deletes
`recommendation_interaction_fact` by `(server_profile_id, erased_event_id)` and records a local tombstone
in the same transaction as its cursor advancement. The tombstone prevents a replayed older UPSERT from
recreating the fact. Also clear any local materialization/cache derived from that fact and fence any
queued re-upload by target event ID; the server erasure tombstone rejects that re-upload. An unsupported
client must defer the version-2 event and halt its cursor with `UPGRADE_REQUIRED`, never acknowledge or
skip the deletion. Rollout requires a minimum capable Android version; on reconnect a stale or old-epoch
device must upgrade, wipe the affected profile's interaction facts, and bootstrap before it can sync.
 Server erasure tombstones remain until every non-revoked owner device acknowledges them; at the 400-day
 privacy limit, any still-lagging device's journal epoch is invalidated and an owner/device erasure-floor
 sequence retained instead of per-event content, so a later pull cannot bypass the required local wipe.
 The bootstrap wire is explicitly typed, not inferred from `aggregate_type`: migration adds nullable-for-
 legacy `sync.tombstone.projection_event_type` and `projection_schema_version` with a check that both are
 set to `RECOMMENDATION_INTERACTION_ERASED`/`2` for interaction erasures and both null for generic legacy
 deletes. `_bootstrap_projections` freezes these fields in each snapshot item and `/sync/bootstrap`
 emits them alongside `tombstone_id`, `aggregate_type`, `aggregate_server_id`, `retain_until`, and
 `operation=DELETE`. The capable `OkHttpSyncTransport.bootstrap` maps that exact typed record to
 `RemoteEvent(RECOMMENDATION_INTERACTION_ERASED, schemaVersion=2, operation=DELETE)`, preserving the
 erased interaction ID as `aggregateServerId`; generic tombstones still map to `AGGREGATE_DELETED` v1.
 Unknown/malformed typed tombstones fail closed before any bootstrap cursor advances. The server refuses
 both bootstrap and pull for a profile lacking the negotiated `RECOMMENDATION_ERASURE_V2` capability while
 an unacknowledged owner erasure tombstone exists, returning `UPGRADE_REQUIRED`; it does not hand a typed
 tombstone to an old transport that would silently coerce it to generic v1. A version-2 event in pull and
 the corresponding typed bootstrap tombstone must invoke the same Room deletion function, including when
 the fact was persisted before bootstrap began. Android adds `RECOMMENDATION_ERASURE_V2` to both the
 `/sync/pull` query capabilities and every `/sync/bootstrap` body; the server freezes it for a bootstrap
 snapshot and rejects a continuation that drops it.

The same erasure transaction redacts every grandfathered v1 `sync.device_event_inbox` row for each
erased interaction: replace original `payload` with `{}`, set new `apply_status=ERASED`, and replace
`terminal_ack` plus the matching `IdempotencyRecordRow.response_reference` with a minimal nonretryable
`EVENT_ERASED` acknowledgement. Retain only owner/device/event ID, sequence, idempotency key, original
validated `request_hash`, and disposition through the duplicate-detection horizon; no recommendation
object or recording/presentation/pack ID survives in an inbox/ack copy. The new v2 inbox insert path is
hash-only from the start. A retried old push with the same event ID/hash or idempotency key returns that
terminal erasure outcome, never recreates its interaction/sync/attribution rows; a different hash is a
conflict. Inbox/ack/request-hash remnants remain owner-scoped personal data in export, retention,
consent/account deletion, backup-restore replay, and audit. Legacy scrubbing and new deletion sync event
commit atomically; failed transactions expose neither a half-redacted inbox nor a missing erasure event.
 Account deletion clears the local profile on next authenticated launch/sign-out; no server can wipe a
powered-off device immediately, and this exposure is disclosed rather than counted as a completed remote
wipe. Two-device pull, process-death, replay, reconnect, legacy-client, bootstrap, and delayed-ack tests
must show deletion wins and no synced copy silently persists after a capable device reconnects.
- `ml.recommendation_request_replay_payload(recommendation_request_id)` contains the bounded canonical
  request document/features, non-null resolved `effective_limit`, endpoint maximum, snapshot/content
  identities needed for exact or algorithmic replay, their hashes, and fixed
  `replay_expires_at=request.created_at+180 days`. Exactly one replay row is mandatory for every
  version-2 request in the same transaction. For a version-2 request the legacy
  base-row `request_document` is `NULL`, `request_features` is the canonical empty object, and the legacy
  snapshot FK columns are `NULL`; personal replay material exists only in this purgeable relation and
  in the equally 180-day `recommendation_item` baseline rows;
- `ml.offline_pack_binding_receipt(pack_id)` is an immutable, owner-scoped, non-payload binding record
  created atomically with each version-2 offline pack and its request-level association, validated
  against the already committed decision under the request lock. It has a globally unique pack UUID,
  `owner_user_id`, `device_id`,
  `recommendation_request_id`, serving-contract version, committed decision hash, complete served-item
  count/set digest, `pack_payload_sha256`, pack creation/content-expiry timestamps, and
  `attribution_expires_at=request.created_at+180 days`. Owner/request and owner/device composite FKs,
  a request-level decision/set deferred check, an insert-only guard, and a payload-hash equality check
  against the live pack make the receipt content-bound. There is no pack payload or item list in this
  receipt. Pack content/association may expire after seven days, but only the restricted lifecycle
  procedure can delete the receipt at the request's 180-day boundary or earlier on owner erasure,
  consent withdrawal, or account deletion; it removes the receipt before the request FK. The receipt is
  included in owner export, access audit, backup/restore purge, and the same privacy deletion ledger;
- `ml.recommendation_attribution_expiry_tombstone(owner_user_id,recommendation_request_id)` is not an FK
  child of the request and contains only contract version, expiry/reason, and request/decision/served-set
  content hashes. It is owner-scoped personal data, supports only the authenticated owner's terminal
  `ATTRIBUTION_EXPIRED` response, expires at request time plus 400 days, participates in export, and is
  deleted immediately on account deletion; and
- singleton `ml.recommendation_serving_cutover` plus non-null
  `recommendation_request.serving_contract_version`. Migration labels every pre-cutover request
  version `1`. Stored surface literals are exactly the existing lower-case values
  `recommendations`, `home`, and `offline_pack`; upper-case names elsewhere in this plan are prose.
  Preserve the legacy nullable column for version-1 rows, but add a validated database check exactly
  `serving_contract_version = 1 OR (serving_contract_version = 2 AND surface IS NOT NULL AND surface IN
  ('recommendations','home','offline_pack'))`; versions outside `1|2` are rejected separately.
  The restricted `BEFORE INSERT` trigger, which application roles cannot bypass or override, first
  takes the transaction-scoped shared advisory lock for the serving-contract domain, reads the
  cutover singleton under that lock, and assigns version `1` or `2`. The lock is retained through the
  request transaction commit. A deferred constraint trigger is selected solely by
  `serving_contract_version=2`, not by surface equality, and therefore runs for every v2 row including
  malformed direct inserts. It requires the non-null exact surface plus exactly one
  `COMMITTED_P11|COMMITTED_SONA` decision and a complete served-item set. The deferred trigger
  requires exactly one replay row, independently recomputes every baseline/served item hash and both
  ordered set hashes, verifies the decision's P11 ranking hash against the baseline set, requires
  `p11_item_count` to equal exactly ranks `1..p11_item_count` in `recommendation_item`, and requires
  `0 <= served_item_count <= min(effective_limit, endpoint_max)`, exactly the ranks
  `1..served_item_count`, no duplicate recording, equality with the stored count/digest, and the fixed
  empty-set digest when count is zero. Missing suffix rows, extra rows, reordered ranks, or tampering in
  any item field therefore abort the transaction even when the decision row itself is present.
  This is a reciprocal invariant, not an insert-only parent check: deferred constraint triggers fire on
  `INSERT|UPDATE|DELETE` of the request, replay row, decision, every baseline item, and every served item,
  enqueue the affected request ID once per transaction, and recompute the whole graph at constraint time.
  If the parent still exists, exact v2 completeness is mandatory. If the parent is absent, the transaction
  is valid only when the restricted lifecycle procedure also wrote a matching content-bound purge receipt
  (`EXPIRED | ACCOUNT_DELETION | CONSENT_WITHDRAWAL`) and no replay/decision/item/association child remains.
  Immediate immutable-row guards and revoked application-role `UPDATE|DELETE` privileges prevent direct
  mutation; normal creation/`PREPARING -> COMMITTED` and lifecycle deletion are available only through
  audited security-definer procedures with fixed `search_path`. Thus no child can be removed after an
  otherwise valid commit merely because the parent row was untouched.
- before cutover, the OWNER/ADMIN command proves every healthy serving instance has a fresh,
  authenticated capability heartbeat for `SERVING_DECISION_V2`, the minimum binary/schema version,
  and no incompatible instance. It then takes the exclusive transaction-scoped form of the same
  advisory lock, rechecks the fleet, records cutover ID/generation/time, activates version `2`, and
  commits. That commit is the linearization point: earlier shared-lock transactions finish before it;
  later inserts observe v2. A missing/stale instance aborts cutover, and a legacy instance that later
  attempts an insert is rejected by trigger/constraint rather than creating v1 truth. Only rows
  committed before the cutover activation may use baseline-only legacy attribution/replay.
  Migration 0067 explicitly grandfathers pre-cutover contract-v1 rows: their existing inline/FK causal
  graph and current product retention remain unchanged and are not claimed to satisfy the new v2 180-day
  deletion contract. They never receive a fabricated v2 decision/tombstone hash. The migration records
  their count/oldest timestamp as a privacy-review exception; accepting or separately migrating that
  finite legacy population is a prerequisite to enabling cutover, while every post-cutover row uses only
  the v2 association path.

Serving completeness uses `SERVING_HASH_LP_V1`, not RFC 8785 or PostgreSQL `jsonb::text`. Its byte codec
is frozen and implemented once in `app_private` plus the Python server: a domain is ASCII plus NUL,
followed by an unsigned 32-bit big-endian field count; each field is one byte `0x00` for null or
`0x01 || u32be(length) || bytes` for present. UUID is its 16 network-order bytes; signed integers are
8-byte two's-complement big-endian; booleans are `00|01`; hashes are raw 32 bytes; bounded enum/text is
exact UTF-8. Every v2 score is an exact signed `score_units_e8 BIGINT`, with a database check that any
compatibility `NUMERIC` equals `score_units_e8 / 100000000`; no binary float participates in persistence,
hashing, comparison, or response serialization.

Bounded nested item fields use `CANONICAL_JSON_LP_V1`: tags distinguish null/false/true/int64/UTF-8
string/array/object; every child is length-prefixed; arrays preserve order; object keys are unique,
schema-declared ASCII and sorted by raw bytes under `COLLATE "C"`; string values must already be NFC;
numbers must be int64 (fractional values are represented by schema-named fixed-point integers). Depth,
member count, string bytes, and total bytes are SQL-checked. An immutable PostgreSQL function recursively
encodes the stored JSONB and computes each subdocument hash, so an application cannot assert a hash that
does not match its document. Duplicate input keys, fractional JSON numbers, non-NFC strings, unknown
members, and out-of-bound documents are rejected before storage.

The baseline item field order is `[request_uuid,rank,recording_uuid,score_units_e8,
candidate_sources_hash,explanation_code,availability_snapshot_hash,contributions_hash,
reason_codes_hash,item_provenance_hash]`. The served item order is
`[request_uuid,source_rank,recording_uuid,score_units_e8,score_kind,attribution_source,
reasons_hash,contributions_hash,original_p11_rank,origin]`; the referenced arrays/objects use the nested
codec above. Baseline and served item hashes use those exact LP field lists under
`autplay.recommendation-p11-item.v2\0` and
`autplay.recommendation-served-item.v2\0`. Each ordered set hashes LP fields `[count, rank_1,
item_hash_1, ...]` under `autplay.recommendation-p11-set.v2\0` or
`autplay.recommendation-served-set.v2\0`; count zero therefore has one unique specified digest. The
decision hash uses the same codec and covers both count/digest pairs. Checked-in PostgreSQL/Python golden
vectors cover nulls, int64 limits, NFC/non-NFC, nested maps in different input order, empty/max sets,
fixed-point conversion and single-bit mutations; cutover is blocked unless every implementation
produces identical bytes and SHA-256 values.

`SCORE_E8_V1` freezes the live-float boundaries without silently changing P11 membership or ordering.
Each P11 generator first rejects non-finite/out-of-bound raw binary64 scores, then preserves the current
`_BaseGenerator._batch()` preselection exactly: sort/truncate by `(-raw_binary64,
recording_id.hex)` at the generator budget before any quantization. Only the selected contributions pass
through the existing `round(raw, 8)` and then
`Decimal(str(rounded_value)).quantize(Decimal('0.00000001'), ROUND_HALF_EVEN)`; negative zero becomes zero
and multiplication by `100000000` yields `score_units_e8`. The current P11 weighted scorer reconstructs
the same rounded contribution float solely for its frozen internal arithmetic, applies its existing
`round(total, 8)`, and immediately converts that final score to E8 before the final pool sort, diversity,
or seeded tie-break. Thus binary64 exists only inside those two versioned legacy P11 calculation stages;
it never enters persistence, hashing, replay identity, or response serialization. New Sona/external scores
reject non-finite values and convert directly through the same round/Decimal E8 function before their
ranking.

The accepted range is `[-92233720368.54775808,92233720368.54775807]`. Final ranking compares E8 integers
descending, then uses the existing seeded recording-ID tie-break; persistence, contribution documents,
replay, and response JSON derive only from the integers. The response writer emits the exact base-10 JSON
number `score_units_e8 / 100000000` with at most eight fractional digits and never round-trips it through
binary float. Golden vectors cover halfway cases, `nextafter` neighbors, positive/negative bounds,
negative zero, NaN/infinities, ties that collapse after quantization, parity with the current fixture
ranking, and raw-float near-ties straddling every generator-budget cutoff to prove selected membership is
unchanged.

State machines are exact:

- Face/Sona lineage work: `PENDING -> CLAIMED -> SUCCEEDED | RETRY_WAIT | TERMINAL_INELIGIBLE |
  TERMINAL_FAILED | RETRY_EXHAUSTED | CANCELLED | SUPERSEDED`. Stable policy/source/consent/retention
  failures are `TERMINAL_INELIGIBLE`; corrupt output, integrity/contract violation, unsupported
  runtime, and batch-one OOM are `TERMINAL_FAILED`; transient GPU busy/timeout/process or database
  loss enters `RETRY_WAIT`; expired `CLAIMED` also enters `RETRY_WAIT`; retry-budget exhaustion alone
  becomes `RETRY_EXHAUSTED`. Only `RETRY_WAIT -> PENDING` is reopenable.
- Sona capture discovery cursor: `ACTIVE -> EXPIRED | CANCELLED`; it remains `ACTIVE` until the
  bundle expires and advances a monotonic registry cursor, so a later model or execution-profile
  successor is discoverable. Each target dispatch independently moves `WAITING -> READY -> CONSUMED`
  or `TERMINAL_INELIGIBLE | EXPIRED | CANCELLED`; `CONSUMED` is terminal only for the exact
  `(request_id,lineage_sha256,execution_profile_digest)` target. The scanner locks active cursors,
  expands all newly eligible registry generations idempotently, and creates exact lineage work.
- Android Face download work: `PENDING -> RUNNING -> READY | RETRY_WAIT | TERMINAL_FAILED | CANCELLED |
  SUPERSEDED`; only `RETRY_WAIT -> PENDING` retries, and any projection/generation replacement moves
  nonterminal old work to `SUPERSEDED` before new work is inserted.
- Backfill: `PREPARED -> RUNNING <-> PAUSED -> STOPPING -> COMPLETED | CANCELLED | FAILED`.
- Activations are append-only actions `ACTIVATE | ROLLBACK | DEACTIVATE`; current state is a derived
  projection fenced by the newest monotonic epoch.
- Publication occurs only from a currently leased `CLAIMED` generation and atomically writes the
  immutable result plus `SUCCEEDED`; terminal states never reopen.
- A recommendation serving decision is transaction-local `PREPARING` and becomes exactly one of
  `COMMITTED_P11` or `COMMITTED_SONA` with its immutable served items before commit. No committed
  decision changes pipeline/items; a retry must idempotently confirm identical truth or conflict.
  After cutover, ineligible/unlisted owners, Home, offline packs, every Sona fallback, and ordinary
  P11 all use `COMMITTED_P11`; decisionless rows are never created under contract version 2.

Use PostgreSQL `BYTEA`, not JSONB, so canonical bytes and result hash remain identical. Store only
queryable lineage/lifecycle fields as columns. Validate real database growth and TOAST behavior
before qualification; a future sidecar requires a new reviewed schema and is not an implicit
optimization.

### 7.2 Job and publication workflow

1. Define the only eligible source as the current `RecordingCanonicalVariantRow` joined to an
   `AudioVariantRow` whose validation is exactly `VALID`, `deleted_at IS NULL`, immutable Vault object
   exists, and source SHA-256, decoded sample identity, and presentation map match.
   Metadata/fingerprint similarity never qualifies.
2. Freeze this positive trigger matrix. Valid canonical AudioVariant finalization creates sponsors
   for eligible enabled references; a new/resolved/admitted `UserTrackRef` created after the owner's
   enable watermark creates a sponsor even when the variant already exists; canonical-variant
   selection/replacement creates the successor; explicit backfill materializes pre-watermark rows.
   Re-enable sets a new watermark and never silently sponsors older library rows.
3. Freeze the negative/supersession matrix. Reference removal, library de-admission, owner disable,
    canonical replacement, validation change away from `VALID`, quarantine/deletion of a variant or
    Vault object, Recording deletion/redirect/rematch, policy/activation supersession, any required
    artifact-license decision changing away from current `APPROVED`, and account deletion withdraw or
    supersede the affected sponsor, tombstone its projection, and enqueue a successor only when a
    different exact eligible source now exists. Source replacement always inserts a new immutable
    sponsor: sequences A -> B -> A retain three linked rows and never collide with a prior key.
4. The mutation that can touch one aggregate writes its sponsor/outbox fact transactionally. Fan-out
   transitions use a durable bounded outbox. Every sponsor mutation acquires the domain advisory lock
   for the exact source/lineage and locks the existing coalesced work row before changing the live
   sponsor set, so claim/publication cannot observe a half-applied authorization transition.
5. The dispatcher coalesces live sponsors onto one idempotent `ml.face-timeline/v2` job keyed by exact
   source identity, embedding model, interpreter/schema, preprocessing, and execution profile.
   Job/attempt/activation do not enter the semantic identity; the Face v2 identity does.
6. Claim locks and rechecks the current canonical valid source, exact source fields, current license
   generations, active lineage/artifacts, GPU admission, and at least one individually authorized
   live sponsor before source I/O.
7. GPU worker reads the authorized source through the existing bounded read-only Vault path, performs
   deterministic decode/resample/segmentation, encoder inference, and interpreter conversion.
8. The interpreter validates bounds, applies stable quantization, reduces keyframes/events, encodes
   canonical bytes, and computes semantic/result hashes.
9. Canonical publication opens a short transaction, takes the same source/work lock, locks activation,
   and rechecks the current canonical valid source, work-authority generation, tuple license policy,
   plus existence of at least one live sponsor with a bounded `LIMIT 1` authority query. It never locks
   or enumerates the complete sponsor population. It inserts or idempotently confirms the timeline,
   completes the canonical work, and creates one durable `face_projection_fanout` root. A different
   result for an existing complete semantic key is a conflict and never overwrites.
10. A resumable fan-out scanner leases roots and walks live sponsor IDs in stable UUID order, at most
    100 per transaction using `FOR UPDATE SKIP LOCKED`. For each row it takes the individual sponsor
    lock, rechecks source/policy/activation/redirect/license and exact timeline compatibility, and
    inserts an immutable per-sponsor projection intent or a terminal cancellation receipt. A separate
    materializer consumes at most 100 intents per transaction, repeats the same individual authority
    check, and idempotently creates/tombstones one projection. Cursor and counters advance in the same
    transaction; crash before commit repeats safely, crash after commit resumes after the stored cursor.
    New sponsors arriving after the root's high-water mark create their own direct intent transactionally.
    Fan-out completion requires a second no-gap scan through that fixed high-water mark; it never means
    all historical sponsors were held in one transaction.
11. Job claims/publication/intents bind immutable sponsor ID and exact source/work-authority generation,
    not merely owner/reference/policy/lineage. A late A worker or intent cannot publish through successor
    B or a later A; redirect reversal and source restoration require a new sponsor and generation. One
    owner's disable/deletion/rematch cancels only that sponsor intent/projection and cannot cancel work
    authorized by another owner. Model rollback or sponsor count reaching zero before canonical publish
    fences it; after publish it leaves the immutable timeline unselected and GC-eligible. Late workers
    cannot regain authority. Retry is bounded and reason-coded; unsupported runtime/model/interpreter/
    integrity failures are terminal, while transient GPU loss may retry within policy.
12. A `RecordingRedirect` catalog change-set does not synchronously fan out all owners. In the same
    catalog-change transaction it increments a redirect generation and writes a durable Face redirect
    outbox row. A bounded, resumable reconciler walks affected references by stable cursor, resolves
    the complete redirect chain (maximum 16 hops, cycle/overflow fail closed), detaches old sponsors,
    tombstones old projections, and creates successor sponsorship only after exact target authority
    exists. Publication locks/rechecks the current redirect generation and chain before commit.
    Reversal/supersession writes a new generation/outbox and is reconciled the same way; old and target
    Recording identities cannot publish under a stale generation.
13. Face Contract v2 is a new canonical timeline/identity contract, not a modified v1 decoder. It adds
    `execution_profile_sha256` to timeline identity. The digest covers runtime,
     provider, precision, driver/device compatibility class, deterministic-kernel policy, and numeric
     quantization. It has new JSON schemas, Python/Kotlin domain and codecs, cross-language golden
     fixtures, `autplay.face.semantic-key.v2\0` and `autplay.face.timeline-result.v2\0` hash domains,
     and content type. A different execution profile creates a different semantic key. Contract v1
     remains immutable historical/integration evidence and is never emitted as a production v2 result.
14. Face v2 freezes `SourcePresentationMapV1` into the timeline identity and projection. Server decode
    records encoded-source hash, decoded sample rate/count, decoder/probe implementation and version,
    leading/trailing trim and encoder-delay/padding observations, and a bounded ordered set of edit-list
    presentation segments. Each segment binds half-open presentation microseconds to its first decoded
    sample; within a segment `sample = source_start_sample + floor((position_us -
    presentation_start_us) * sample_rate / 1_000_000)`. Keyframes/events use integer decoded-sample
    indices (`DECODED_SAMPLE_INDEX_V2`), never container milliseconds. Segment gaps, overflow,
    negative/out-of-range positions, an unrecognized edit, or incompatible decoder/probe identity are
    neutral. The canonical mapping bytes/digest, source hash, rate, and count are signed and hashed with
    the projection/timeline. Cross-language golden fixtures cover trimmed AAC/MP4 edit lists, MP3
    encoder delay/padding, VBR, exact segment boundaries, seeks, and end-of-source clamping.

### 7.3 Analysis controls and backfill

- `Deep musical-character analysis` defaults off.
- Disable prevents new enqueue/claim/retry/publication. A bounded grace may allow only exact jobs
  already running under the current fence to finish computation, but publication still rechecks the
  new generation and normally rejects them. Completed timelines remain immutable but unselected.
- Re-enable creates successor admission only for work that was already eligible/admitted. It does
  not reinterpret tracks finalized while off as implicit backfill.
- Existing-library analysis is a separate owner-scoped operation with fixed watermark, bounded
  batches, pause/resume/cancel, truthful counts, and no unbounded transaction or queue storm.
- Re-analysis under a successor model/interpreter is another explicit backfill; current compatible
  timelines are skipped.

### 7.4 API and sync

Add versioned CPU-only endpoints and sync projections:

1. Admin status/control: current desired/effective policy generation, selected lineage, GPU/model
   readiness, queue counts/reasons, activation history, and backfill operations.
2. Add capability `FACE_PROJECTION_V2`. Only capable clients receive bootstrap rows or incremental
   `FACE_PROJECTION_UPSERTED` / `FACE_PROJECTION_TOMBSTONED` events for aggregate
   `FACE_PROJECTION`; incapable clients advance normally without seeing them. The upsert payload is
   the Face projection envelope v2: projection ID, exact v2 timeline identity, result hash, byte size,
   activation epoch, policy generation, redirect generation, and a canonically sorted `artifact_policy`
   list containing every required artifact's SHA-256, role, current license decision sequence/generation,
   signed `max_offline_revocation_lag_ms`, and derived-output disposition. It also carries
   `required_artifact_set_sha256` over the frozen artifact identities and
   `artifact_policy_list_sha256` over the complete canonical policy entries, the tuple-derived lease and
   strictest disposition, `SourcePresentationMapV1` digest,
   server profile/user binding,
   `issued_at_ms`, `authorized_until_ms`, server instance/identity epoch/thumbprint/key ID, state, and
   P1363 signature over canonical domain `autplay.face.projection-lease.v2\0 || JCS(envelope without
   signature)`. Tombstones identify the projection and superseding generation without timeline bytes.
3. Freeze `GET /api/v1/face/projections/{projection_id}` as the first download route. It
    rechecks the authenticated owner, current reference, sponsor/policy/activation, and exact current
    canonical `VALID` AudioVariant/source hash plus every current artifact-license decision/generation
    on every request, supports `ETag` equal to result hash,
   returns bounded canonical v2 bytes as
   `application/vnd.autplay.face-timeline.v2+json` with normal HTTP compression, and returns no raw
   embedding or source path.
4. Authorization or license-generation loss rejects fetch and renewal, returns not-found/forbidden
   according to existing resource policy, and emits a tombstone via sync. There is no anonymous or
   cross-profile fetch.
5. Owner export includes currently authorized Face projection metadata and canonical timeline bytes,
   their hashes/lineage/license notices, and tombstones relevant to the owner. It excludes raw
   embeddings, model weights, internal job details, shared-reference counts, and other owners' data.
6. Rollout order is mandatory: capability-filtering server first, then Android advertising and
   applying `FACE_PROJECTION_V2`, then Face activation/publication. Contract tests prove a v1.0.0
   client never receives an unknown Face event and never enters `UPGRADE_REQUIRED` because of Face.

### 7.5 Control-plane actors and operations

Use the existing account roles exactly; do not infer authority from possession of an ID.

| Operation | Authorized actor | Frozen contract |
|---|---|---|
| Enable/disable Face analysis | Authenticated `OWNER`, `ADMIN`, or `USER`, for their own `user_id` only | `PUT /api/v1/ml/face-analysis-policy`; target is the principal, not a body field; body has `operation_id`, `expected_generation`, `enabled`, and `scope=NEW_UPLOADS` |
| Start/pause/resume/cancel Face backfill | Same affected authenticated participant only | `/api/v1/ml/face-backfills`; binds operation ID, current policy generation, fixed eligibility watermark, and expected operation revision |
| Grant/deny/withdraw Sona descendant-model serving use | The affected authenticated training participant only, regardless of role | `PUT /api/v1/ml/sona-serving-consent`; no target-user field; grant consumes the operation-bound Android step-up challenge below within five minutes, while deny/withdraw requires a valid current session and is never delayed by reauthentication |
| Safety-disable another participant's Face | `OWNER` or `ADMIN` | Web Admin writes a separate inhibition; it cannot set that participant's desired state to enabled |
| Import/review/revoke artifact license; approve/activate/rollback/deactivate Face or Sona | `OWNER` or `ADMIN` | Trusted local `autplay ml artifact ...` command for byte import; state changes through authenticated `/admin/ml/...` POST operations with current Web session, CSRF, a one-time operation-bound WebAuthn assertion expiring within five minutes, operation ID, and expected generation |
| Add/remove an R1C serving recipient, kill a cohort, enter breaker half-open | `OWNER` or `ADMIN` | `/admin/ml/sona/cohorts/...`; cannot bypass participant serving consent, approval expiry, license, publication, or deletion fences; reset can enter `HALF_OPEN` only |
| Read own Face/Sona consent/export state | Any authenticated role for self | Ordinary scoped API; no cross-owner counts or participant identities |

Android bearer endpoints are not browser-cookie endpoints and therefore do not use CSRF; Web Admin
mutations use the existing session-bound CSRF mechanism. Every mutation canonicalizes and hashes the
request, uses `(actor_user_id, operation_id)` for idempotency, returns the prior receipt only for the
same hash, conflicts on reuse with another payload, compares the expected generation/revision under
row/advisory lock, and appends an audit receipt containing actor role, self/administrative scope,
before/after generation, reason, and result. Consent intent is fsynced to its independent ledger
before the database projection. Authorization-negative tests cover cross-user IDs and enumeration,
`USER` access to admin operations, an `ADMIN` attempting to grant for another participant, stale
generations, changed-payload replay, missing/invalid CSRF, stale reauthentication, disabled/deleted
actors, and concurrent grant/withdraw or activate/kill.

Fresh authentication is an operation-bound proof, not JWT/session age. Migration 0061 adds a bounded
`ml.control_step_up_challenge`/receipt authority containing a random 32-byte nonce, challenge ID,
actor, session/device credential, purpose/action, caller-chosen operation ID, canonical request hash,
expected generation, issue/expiry (maximum five minutes), consumed time, and one-time result. Raw
signatures are not retained after verification; expired/consumed challenges are purged after 24
hours, while the ordinary mutation audit receipt retains only challenge/result hashes.

- Android step-up credential registration is a distinct authenticated ceremony, never a side effect of
  a consent grant. `POST /api/v1/ml/step-up/credentials/challenges` requires the current access token and
  returns a random 32-byte server nonce/challenge ID bound to actor user, active session, exact
  `DeviceRow.device_id`, current paired M5 public-key thumbprint and dedicated
  `DeviceRow.device_key_generation`, server instance/identity,
  purpose `ML_CONSENT_STEP_UP_REGISTER_V1`, and five-minute expiry. The client generates a new P-256
  `ML_CONSENT_STEP_UP_V1` key using that nonce as the Android attestation challenge, then sends SPKI,
  complete bounded certificate chain, authenticator declaration, an existing-M5 ES256-P1363 signature,
  and a new-key ES256-P1363 signature over the same canonical registration hash. That hash includes the
  server nonce, all bindings above, new SPKI/thumbprint, attestation-chain hash, OS/API branch, operation
  ID, issue/expiry, and domain `autplay.ml.step-up-register.v1\0`. Bearer possession without both proofs
  is rejected.
- The server validates off-device: exact nonce and leaf public-key equality, chain signatures and trusted
  Android hardware-attestation root, current revocation status, `TrustedEnvironment|StrongBox` security
  level, verified-boot/device-lock state, app package/signing-certificate identity, EC signing purpose,
  SHA-256 digest, and the required auth-per-use authorization. It then locks/rechecks the active
  user/device/session, M5 thumbprint, and exact `device_key_generation` before atomically consuming the
  challenge and storing only the scoped public key, attestation summary/hash, M5 parent generation, and
  credential generation. Paired-key replacement or revocation locks the device row and atomically
  increments `device_key_generation` in the same transaction that changes key authority; session
  generation and general row version are never accepted substitutes. Registration challenges,
  credentials, and every descendant operation challenge bind the exact parent generation, so a mismatch
  fails before signature verification/consumption and no pre-rotation descendant can commit afterward.
  Attestation absence, software security level, unknown root, stale revocation data, unsupported auth
  properties, substitution, or either invalid proof fails closed.
- The API matrix is frozen. On API 30+, create the key with `setUserAuthenticationRequired(true)` and
  `setUserAuthenticationParameters(0, AUTH_BIOMETRIC_STRONG | AUTH_DEVICE_CREDENTIAL)`; sign only through
  `BiometricPrompt`/`CryptoObject`. On API 26-29, use
  `setUserAuthenticationValidityDurationSeconds(-1)` and a per-use strong-biometric `CryptoObject`;
  combined device-credential authorization is not accepted on this branch. If the required strong
  biometric, secure lock, hardware attestation, or compatible authenticator is unavailable, the Android
  registration/grant remains unavailable. The affected participant may instead use a separate
  self-service WebAuthn user-verification assertion for the same operation-bound consent purpose only
  when that WebAuthn credential was already enrolled through an independently approved high-assurance
  ceremony; this flow may not enroll a first WebAuthn credential from the bearer token. Without such a
  credential, the operation fails closed. There is no bearer-only, recent-login, software-key, or
  time-window fallback.
- Once registered, Android grant calls `POST /api/v1/ml/step-up/challenges` and signs the domain-separated
  operation challenge with that credential after per-use user verification. Verification and one-time
  challenge consumption occur atomically with the self-only consent mutation. Device revoke, account
  disable/deletion, M5 paired-key rotation/revocation, screen-lock removal, OS-reported key invalidation
  (including the API 26-29 biometric-enrollment case), or explicit step-up rotation increments the
  credential generation and revokes every
  descendant challenge before any later grant can commit. Recovery requires the full two-key registration
  again or participant WebAuthn.
- Web Admin begins and completes a separate WebAuthn assertion with user verification required. Its
  challenge is bound to the current admin session, exact action, request hash, operation ID, expected
  generation, and relying-party/origin; the resulting receipt is consumed in the same transaction as
  the admin mutation and does not create or refresh a general login session.
- JWT `iat`, access-token refresh, cookie/session creation time, and a recent unrelated login are
  explicitly invalid step-up evidence. Tests cover challenge replay, changed body or operation,
  cross-session/device/actor use, expiry, stolen bearer, old-M5/new-key substitution, attestation-chain/
  challenge/property failure, API 26-29 and 30+ branches, unsupported authenticator/WebAuthn fallback,
  device/account/M5/step-up rotation and revocation, missing user verification, concurrent consumption,
  and successful retry returning only the already-committed idempotent receipt.

## 8. Android projection, cache, and rendering

### 8.1 Room and transport

From the current Room schema version 17, add Room v18 with a non-destructive migration and schema
export for:

- `face_projection`: projection/binding identity, profile/user, Recording, exact AudioVariant ID,
  source SHA-256, decoded duration/sample count/rate, `SourcePresentationMapV1` digest, v2 identity,
  model/interpreter/preprocessing/execution-profile digests, activation/policy/redirect and artifact-
  license generations, required-artifact-set and policy-list digests, tuple disposition, result hash,
  signed issued/expiry times, server instance/identity
  epoch/thumbprint/key ID/signature, state, and timestamps;
- `face_projection_artifact_policy`: one canonically ordered child row per required artifact with
  projection ID, sort index, artifact SHA-256, role, decision sequence/generation,
  `max_offline_revocation_lag_ms`, and disposition. The parent/list and download-work update is one Room
  transaction; no projection is selectable if a child is missing, duplicated, out of order, or differs
  from the signed list digest;
- `face_timeline_cache`: result hash, canonical bytes, byte size, last verified/accessed, and
  eviction eligibility, referenced atomically by the projection;
- mandatory `face_timeline_download_work`: projection/result/profile identity, expected generation,
  state, attempt/backoff, lease, last reason, and timestamps. It is separate from Media3 media
  downloads; and
- `face_playback_source_proof`: local track reference, exact AudioVariant, source SHA-256, decoded
  sample count/rate, `SourcePresentationMapV1` digest, Media3 download ID and cache key, exact
  `DownloadIndex` revision/generation, complete cached byte extent/length, verification generation,
  verified timestamp, and state. It stores proof metadata only, never a second audio copy. Nullable
  legacy `localSha256` or `serverAudioVariantId` alone is never a proof.

Sync applies an upsert/tombstone and its `face_timeline_download_work` row in the same Room
transaction. After commit it enqueues unique WorkManager work keyed by profile/projection/result;
startup, profile recovery, and periodic reconciliation enqueue any nonterminal row that lost its OS
work request. A background authenticated fetch:

1. verifies current profile/user and expected identity before request;
2. enforces response size/content type, decodes with the new strict `FaceContractV2Codec`, verifies result hash and
   projection binding, then writes cache and projection atomically;
3. rejects redirects to other origins, unexpected compression expansion, stale generations, and
   late profile/playback bindings;
4. never falls back to a superseded projection; an existing projection remains usable only while it
   is still the current exact activation/policy/redirect/source binding and its signed lease is live;
5. uses bounded retries and never wakes playback to fetch.

The projection envelope carries the sorted complete `artifact_policy` entries described above,
the frozen `required_artifact_set_sha256`, `artifact_policy_list_sha256` computed over the canonical
policy-entry bytes, tuple disposition, and the server-derived authorization lease. The lease
is `min(604800000, lag_1, ..., lag_n)` over every required current artifact decision, measured from signed
`issued_at_ms`; every lag must be strictly positive. A zero/missing lag blocks tuple activation and no
projection or lease is issued. Android recomputes the list digest, minimum lease, and strictest disposition
from the individually signed list values and rejects any duplicate/unknown role, missing required role,
digest mismatch, or server-derived value that differs. Android verifies the
domain-separated envelope signature against the pinned server instance/identity epoch/thumbprint and
rejects issue/expiry intervals outside `[1 ms,7 days]` or issue time more than five minutes ahead of
the current trusted lower bound. On online acceptance it atomically persists `(issued_at_ms,
elapsedRealtime_ms,Settings.Global.BOOT_COUNT,max_seen_wall_ms)` per profile. During the same boot,
effective time is the maximum of current wall time, `max_seen_wall_ms`, and signed server time plus
the nonnegative elapsed-realtime delta from that anchor; it advances monotonically and is persisted
whenever the projection is selected. Wall-clock rollback cannot extend the lease. A boot-count change,
elapsed-time discontinuity, missing tuple, server-identity change, or unverifiable remaining lifetime
fails closed to neutral until online renewal. Tests cover forward/backward clock changes, repeated
reset attempts, reboot, process death, stale signed time, expiry, and identity rotation. Sync/fetch may
renew a still-authorized lease; offline state never renews itself.

Cache quota is configurable and evidence-driven. LRU cleanup cannot evict the timeline attached to
current playback, an in-flight verified write, or a projection retained for offline playback under
an unexpired authorization lease. Profile replacement/account deletion immediately makes known rows
unselectable, then cleanup removes bytes.

Media3 remains the sole playback-download/cache owner required by ADR-021. After its `DownloadIndex`
reports `COMPLETED`, low-priority verification opens the identical download/cache key through a
cache-only `CacheDataSource` with upstream disabled, holds the download generation, reads the complete
known byte extent, verifies source SHA-256/length, probes the decoded sample identity and presentation
map, and stores only the Room proof above. WorkManager may schedule/reconcile this checksum scan, but
must never transfer media, create a parallel app-private audio store, or convert a stream into a hidden
download. Playback and Face both resolve the same Media3 cache key and completed `DownloadIndex`
generation. Removal, restart, index revision, partial span, eviction, corruption, or mutation
invalidates the proof, advances playback generation, and switches Face to neutral.

A SAF/MediaStore source is neutral by default. Only an explicit user action named `Make available for
Semantic Face` may submit a Media3 `DownloadRequest` whose reviewed local-content `DataSource` imports
that source into the existing Media3-managed download cache. It uses the existing per-profile managed-
download quota/free-space reserve and eviction UI, is marked no-backup, is removable through
DownloadManager, and is deleted on profile/account removal. There is no extra Face quota or duplicate
file. A live HTTP stream is neutral unless it is already backed by a complete, explicitly authorized
Media3 download/cache entry; Face never delays playback or opportunistically fills missing spans.

### 8.2 Playback integration

Add a Face projection repository/coordinator between playback state and UI:

1. Resolve the actual Media3 `VerifiedPlaybackSource`, local/server track binding, profile, and user.
   Exact equality is required for AudioVariant ID, source SHA-256, complete download/cache identity,
   `DownloadIndex` generation, decoded sample count/rate, presentation-map digest, and source
   verification generation; metadata/fingerprint matching is forbidden.
2. Load a local projection only when that full source proof, current canonical source, policy,
   activation, redirect, and authorization lease all match. Missing or stale proof selects neutral
   and schedules off-thread verification; selection itself performs no I/O on the render thread.
3. Attach `FaceTimelineSampler` using the current playback-generation token.
4. Map Media3's authoritative presentation position in microseconds through the exact signed
   `SourcePresentationMapV1` segment to an integer decoded-sample index, then sample the v2 timeline.
   Seek, item change, segment gap, mapping incompatibility, profile change, process restore,
   activation/license change, or generation change cancels the old sampler and app-reaction tokens.
5. Compose precedence: `TrackCharacter` + sampled temporal state + bounded moment dynamics + short
   app reaction. Local PCM energy may modulate only allowed low-level motion and cannot fabricate
   missing semantics.
6. Invalid/missing/abstaining state renders the neutral/local Face. Playback controls and navigation
   remain authoritative and unaffected.

Tests replace a SAF URI in place, modify/partially evict a completed Media3 download, exercise nullable
hashes and variant IDs, alter sample identity/presentation mapping, switch canonical sources, and
change source or `DownloadIndex` generation during playback/process restoration. Golden playback tests
exercise trimmed AAC/MP4, MP3 delay/padding, VBR and boundary seeks. In every case semantic rendering
stops before a different byte or presentation source can consume the old timeline, while playback
continues with neutral Face and no second audio store is created.

### 8.3 Renderer and accessibility

- Map continuous semantic axes into the approved brow/eyelid/pupil/palette rig. Do not select a
  named emotion or crossfade fixed emoji states.
- Interpolate palette in HCT or an equivalent perceptual space. Low confidence reduces chroma and
  motion toward neutral; never encode a state by red/green opposition alone.
- Preserve light/dark, monochrome/color-vision, reduced-motion, large-font, rotation/fold, and
  TalkBack behavior. Low-level animation stays decorative; authoritative playback/action semantics
  remain in existing controls.
- Initial user-visible scope is real Now Playing only. Library thumbnails, profiles, Rooms, and
  other surfaces are out of scope.

## 9. Lifecycle, privacy, retention, and recovery

### 9.1 Face

1. A timeline is reusable only for exact equivalent source lineage and current authorized
   references. Similar metadata or recordings never authorize reuse.
2. Account deletion purges owner projection/reference/cache authority. The canonical track-scoped
   timeline may remain only while another live authorized reference or explicit legal/backup
   retention basis exists.
3. The last live reference transaction fences enqueue/claim/retry/publication, tombstones selection,
   and schedules bounded GC. It never deletes shared source bytes or an artifact still required by a
   live reference.
4. Each successor Face activation creates a bounded rollback-retention reference for the immediately
   prior complete encoder/interpreter/preprocessing/execution-profile/schema tuple. It expires 30
   days after successor activation and retains that tuple's artifacts plus existing timelines only
   for exact sources that still have at least one live authorized reference. A later activation does
   not extend an older deadline. Last-reference/account deletion, license revocation, integrity
   quarantine, and legal deletion override rollback retention immediately.
5. GC removes expired prior timelines/artifacts only after locked recheck of activation, rollback
   deadline, current license, and live source references. Missing prior timelines are never guessed:
   if the exact prior artifacts/profile and live source remain valid, rollback schedules deterministic
   regeneration under that same tuple and uses neutral Face until each projection is ready. If the
   artifact is missing, revoked, or not reproducible, `ROLLBACK_SELECT_PRIOR` is not READY; the
   independent immediate `DEACTIVATE` action still provides neutral fallback. Rollback drills prove
   retained bytes or successful exact regeneration before qualification.
6. Backup captures canonical bytes and metadata transactionally through existing database backup.
   Restore starts in ML quarantine: Face selection, Sona training, and Sona serving are disabled;
   the independent privacy, training-consent, and serving-consent ledgers replay all post-backup
   deletion, withdrawal, publication-revocation, last-reference, and projection-tombstone facts
   before exposure.
7. Reconciliation finds orphan references, impossible activation chains, corrupt hashes, missing
   payloads, and expired retention. It quarantines rather than guessing or regenerating silently.
8. Appending a current `DENIED` or `REVOKED` artifact-license decision takes the artifact authority
   lock, increments its generation, invalidates affected activation readiness, fences/cancels new and
   claimed work, and writes a resumable outbox that tombstones every dependent Face projection. Sync
   makes online clients neutral immediately; projection fetch and lease renewal reject the old
   generation. An already-offline client cannot be recalled, so every artifact in the tuple must
   explicitly approve a strictly positive `max_offline_revocation_lag_ms`; the effective residual
   window is their minimum and never above seven days. If any terms require zero/immediate cessation,
   that artifact/tuple is not activatable for Semantic Face. Qualification includes legal sign-off on
   the complete artifact set and this derived residual window.
9. Each license decision freezes `derived_output_disposition` as either
   `DELETE_AFTER_LEASE` or `RETAIN_NON_DISTRIBUTABLE` with its legal basis. On revocation, dependent
   timelines/projections become non-selectable/non-exportable immediately; weights are quarantined and
   deleted when the decision requires it. The tuple uses the strictest required-artifact decision:
   any `DELETE_AFTER_LEASE` dominates every `RETAIN_NON_DISTRIBUTABLE`. `DELETE_AFTER_LEASE` purges
   derived bytes after the tuple's minimum authorized lease horizon and locked live-reference check;
   a more permissive sibling artifact cannot extend that deadline. The audit keeps only artifact/result
   hashes, decision text hash, action, and deletion receipt; rollback retention cannot override it.
   Tests cover one/multiple artifacts, zero/missing lag activation refusal, every min-order permutation,
   mixed dispositions, online clients, fetch/renewal races, already-downloaded projections, stale sync,
   offline lease expiry, worker publication races, and both disposition policies.

### 9.2 Sona

1. Training requires explicit, purpose-specific, current consent. Inference history and ordinary
   recommendation operation remain governed by their existing product/privacy authority.
2. Consent withdrawal prevents new participation, invalidates unfinished runs, and cleans retained
   training inputs. It does not silently rewrite a completed model.
3. Final account deletion writes the irreversible publication revocation fence for every model whose
   signed participant ancestry includes that owner. Active serving immediately falls back to P11;
   retraining without that owner is required before a successor can activate.
4. No raw owner UUID, source path, or secret enters portable dataset/model/evidence artifacts.
5. Restore must replay and reconcile the independent training-consent and serving-consent ledgers,
   then reapply consent/deletion/publication-revocation authority before training or serving.
6. Owner export includes consent revisions, the owner's training participation/run identifiers,
   owner-scoped source/evidence and served-decision/impression/outcome manifests, and revocation
   receipts. It excludes other participants, pooled datasets, model weights, and shared model
   parameters. Shared artifacts appear only as hashes/public license metadata.
7. The independent ledgers are extended with derived-data revocation facts sufficient to replay
   post-backup Face last-reference/deletion and Sona publication revocation. Restore remains
   quarantined if any ledger generation or reconciliation receipt is missing or inconsistent.
8. R1C serving consent uses a new independently stored `FilesystemServingConsentLedger` with its own
   file, key, identity/head/event MAC domains, owner/purpose generation, and append-only
   `GRANTED|DENIED|WITHDRAWN` events. The service writes and fsyncs the independent intent before the
   matching PostgreSQL projection/receipt commits; a grant is usable only when both exact heads agree.
   It is backed up and restored independently from PostgreSQL. A stale database cannot resurrect a
    serving grant, and R1C cannot leave quarantine until complete replay and reconciliation succeed.
9. R1C personal serving data has fixed, separate expiry classes. Complete unserved candidate rows and
   raw inference evidence expire 30 days after the request. The committed serving decision/items,
   version-2 replay payload, existing `recommendation_item` baseline rows, and the request itself expire
   180 days after the request. Version-2 writers never place replay material in the legacy request
   document/features/snapshot columns. Exact replay after that boundary returns `REPLAY_EXPIRED` rather
   than P11 substitution. These bounds are not extended for reproducibility. Scheduled purge deletes
   candidates, deletes the privileged purgeable decision/evidence link, then deletes evidence; the
   immutable decision and its evidence content hash are unchanged during the 30-day cleanup. At 180 days,
   one transaction first inserts the minimal owner-scoped attribution-expiry tombstone from the still
   verifiable hashes, then removes any remaining offline-pack payload, every typed v2 attribution
   association, served items, serving decision, legacy baseline items, replay payload, expired capture/
   shadow bindings, and finally the base request in a migration-tested FK order. Noncausal listening,
   interaction, and normalized taste-event rows survive without request/rank columns or attribution JSON.
   The request delete occurs only after every snapshot retention condition has ended, so
   the existing `protect_sona_shadow_binding` trigger authorizes the normal delete; the worker neither
   updates the protected binding nor disables/bypasses that trigger. The separate tombstone, not a request
   state mutation, produces nonretryable `ATTRIBUTION_EXPIRED` for that authenticated owner through day
   400. No expired pointer is nulled, accepted as unattributed, or rebound to current P11. After tombstone
   expiry the result is the ordinary non-disclosing `ATTRIBUTION_NOT_FOUND`. This contract applies to
   v2 rows; the separately inventoried pre-cutover v1 exception retains its existing graph/policy until
   the cutover privacy review either accepts or migrates it.
   Breaker current state, windows, transition receipts, and any aggregate below the anonymity threshold
   remain owner-scoped, are included in export, are purged on account deletion, and expire no later than
   180 days after their window or cohort deactivation. A 400-day cohort-independent quality aggregate may
   be emitted only from at least ten distinct owners with no owner contributing more than 20%, with no
   owner/cohort/request/recording identifiers or stable pseudonyms; otherwise it is suppressed. The
   one-owner initial canary therefore produces no supposedly anonymous 400-day aggregate.
10. Account deletion and serving-consent withdrawal run the same FK-aware purge under the authority
    locks before exposure can resume: outcome/impression links and capture targets, R1C candidates and
    evidence, served items/decisions, legacy baseline items, replay payloads, source bundles/work, owner
    requests/cohort/breaker rows, and attribution tombstones as applicable, followed by ledger receipts.
    Account deletion always removes the owner-scoped tombstone and breaker material immediately. Export
    states each personal class and expiry. A restartable purge cursor
    `(expires_at,request_id)` uses bounded `FOR UPDATE SKIP LOCKED` batches and idempotent receipts;
    restore replays post-backup deletion/withdrawal facts before reconciliation and refuses serving
    while any expired or revoked personal row remains. Backup/restore and crash tests stop after every
    FK stage and prove convergence without resurrecting expired candidates or decisions.

The expiry instant is authoritative even when the scheduled worker is late. Every v2 attribution write,
exact/algorithmic replay, offline-pack fetch, and lifecycle purge first takes the same transaction-scoped,
domain-separated advisory lock keyed by recommendation request UUID. Under that lock it reads PostgreSQL
`clock_timestamp()` and the immutable `attribution_expires_at`; no application clock participates. If the
database time is at/after expiry, the reader cannot use detailed truth: it idempotently ensures the
tombstone/purge work exists and returns `ATTRIBUTION_EXPIRED`/`REPLAY_EXPIRED`. If time is before expiry,
the validator performs a final locked time check immediately before inserting its association or
linearizing the replay read. That check is the operation's documented linearization point; the purge must
order after it on the same lock. The scheduled/on-demand purge rechecks time under the lock before creating
the tombstone and deleting associations. Tests pause writers on both sides of the boundary and after every
check/insert step, advance database time, delay the worker, and prove exactly one total order—never use of
expired truth merely because physical cleanup has not run.

## 10. Sona shadow quality and activation

### 10.1 Native capture without serving changes

Replace the current per-repository commits with one recommendation unit of work. When native capture
is enabled and consented, the transaction that persists the P11 request and every P11 item also
persists the exact retained baseline snapshot, exact cutoff-bound temporal snapshot/event set,
complete mandatory-filtered candidate membership, P11 ranking hash, model-independent
`sona_capture_bundle`, and one active `sona_capture_lineage_cursor`. The HTTP response is returned only
after this transaction commits. Fault injection must show there is no crash point where a served P11
request lacks its required capture bundle.

Do not call Sona inference inline during R1B. A model-independent bundle may wait for a future
approved lineage. When a lineage becomes eligible, idempotently create `sona_shadow_work` keyed by
the exact request/model/tokenizer/pipeline/execution-profile tuple through a correspondingly keyed
target-dispatch row. `CONSUMED` for one target does not close the cursor: registry generation changes
revisit every unexpired bundle and discover successor model/profile targets exactly once. The
coordinator:

1. loads the exact retained temporal snapshot and complete candidate set;
2. revalidates owner, retention, tokenizer/model/pipeline/execution-profile identities, current
   artifact-license generations, and shadow policy;
3. invokes the Sona inference service through the local-only transport;
4. postprocesses with the same mandatory filter/diversity authority;
5. writes immutable terminal evidence to a separate evidence table keyed by the exact lineage and
   execution profile, without creating served items or impressions;
6. records transient failure only on the retryable work/attempt row. Existing one-time `DEGRADED`
   columns are retained for historical compatibility but are not used as the retry authority;
7. marks `TERMINAL_INELIGIBLE` only for stable source/consent/retention/lineage failures. GPU busy,
   timeout, process death, or temporarily missing model remain bounded retries.

Add production composition for the shadow coordinator, its repository/gateway, a bounded scheduler,
and a separate Compose service/command for Sona inference. Preserve the current ordinary GPU worker.

### 10.2 Local transport and process isolation

The current Sona HTTP mode binds loopback, which is not reachable across ordinary container network
namespaces. Preserve the no-published-port invariant by adding a production Unix-domain-socket
transport on Linux through a narrowly shared runtime socket volume with explicit UID/GID and mode.
Keep loopback TCP only for development/tests where both processes share a namespace. The CPU server
never exposes or proxies the inference endpoint publicly.

Freeze canonical `SonaExecutionProfileV1`. Its digest covers the GPU service OCI image digest and
source commit; Python, model-runtime, ONNX Runtime, CUDA and cuDNN versions; exact NVIDIA driver,
device UUID/model/compute capability; execution-provider and graph-optimization options; precision,
tensor schemas, deterministic flags/seeds, thread/concurrency limits; tokenizer/runtime adapter; and
postprocessor implementation hash. Canonical JSON uses a dedicated
`autplay.sona.execution-profile.v1\0` domain and golden fixture. Even a compatible driver/provider or
precision change creates a successor digest; it cannot reuse work, evidence, R1B approval, R1C
approval, or activation from another profile.

Sona inference verifies the published artifact, model manifest, tokenizer, provider placement, and
GPU admission fence before readiness. Readiness exposes identity only. R1C request v2 additionally
carries training run, publication seal, approval hash/expiry, serving-consent digest/generation,
activation/revocation/cohort/breaker generations, and execution-profile digest. The GPU worker checks
current PostgreSQL authority before and after inference and echoes every field plus request/output
hashes in its signed/hashed output. Requests remain bounded by history/candidate sizes, body bytes,
concurrency, and timeout.

### 10.3 Rehearsal and native quality run

1. Run the complete source planning/materialization/calibration/training/export/benchmark/evaluator
   chain on isolated reconstructed `0026` data. Sign it only as reconstructed rehearsal evidence;
   it cannot approve quality or activation.
2. Allow native evidence to accumulate without changing serving.
3. Freeze and sign source/dataset approvals, chronological split boundaries, exact tokenizer,
   `SONA_P11_TEACHER_V2` calibration, exact `SonaExecutionProfileV1`, and model configuration.
4. Train and export a provenance-eligible candidate. Export rechecks checkpoint weights before and
   after conversion and proves eager/ORT parity at empty, single, and maximum masks.
5. Benchmark only with CUDA execution, CPU fallback disabled, node-placement proof, and raw latency
   samples for every request/iteration.
6. Server evaluator independently reconstructs paired P11/Sona evidence and requires:
   - labels for all required signals;
   - 100% directional scenario pass;
   - zero safety/privacy/replay violations;
   - NDCG@10 regression <= 0.01;
   - diversity regression <= 0.02;
   - artist-concentration HHI increase <= 0.02;
   - repeat-rate increase <= 0.02;
   - Sona p95 <= 300 ms and <= 1.25 times paired P11 p95;
   - signed semantic, performance, evaluation-evidence, and final artifact approvals, all binding
     the same execution-profile digest.
7. Only the complete final verification may derive `quality_eligible=true`. It still does not
   activate R1C.

### 10.4 R1C serving

R1C introduces a new consent purpose `SONA_R1C_DESCENDANT_MODEL_SERVING_V1`, separate from shared
training. Every participant in the exact training-run ancestry must have granted it before the R1C
approval is signed and while the model serves. Withdrawing training consent keeps the previously
agreed completed-model semantics, but withdrawing this serving-use purpose increments the run's
revocation generation and atomically deactivates every descendant R1C cohort until a successor model
without that authority dependency is approved. Account deletion continues to write the irreversible
publication revocation fence.

Add a signed `SONA_R1C_SERVING_APPROVAL_V1` binding training run, publication seal, complete
participant-ancestry digest, current serving-consent digest, tokenizer, model, pipeline, every
required current artifact-license decision sequence/generation, exact
execution-profile digest, R1B approval, eligible surface/context/candidate
rule, breaker policy, and expiry. Add an append-only serving activation chain containing that
approval, exact artifacts and execution profile, cohort policy, sequence/epoch, revocation
generation, actor, action, and previous activation. Start at an empty cohort.

Before inference the router captures one immutable authority token set: approval hash and unexpired
expiry, serving-consent head/digest/generation, activation epoch, publication seal/revocation
generation, cohort generation, breaker generation/state, artifact-license generations, and exact
execution-profile digest. The GPU request and output bind all tokens. Readiness, post-inference
validation, and final commit require byte-for-byte equality with the approved/activated profile and
current authority. Admin kill, approval expiry,
consent/deletion revocation, activation/cohort change, or breaker transition increments its generation
and invalidates every in-flight Sona commit; that request must commit P11 or return an ordinary error.
The first R1C release permits at most 64 participant owner tags in one model ancestry. Consent changes
and serving commits acquire the same domain-separated PostgreSQL advisory locks for all participant
tags in sorted order. A withdrawal/grant holds its tag lock while it appends+fsyncs the independent
intent and updates the database projection; a serving commit holds the full ordered lock set while it
reads both independent head and database projection and validates the digest. Database loss prevents
serving, so there is no cross-store window in which a newly written withdrawal can be ignored.

The public request remains backward compatible but not caller-selectable: production accepts only
`pipeline_key=cpu-baseline`, no explicit version, and `shadow=false` (or removes these fields in a
future API version); every other value is rejected. A server-owned activation router checks owner,
surface `recommendations`, context `GENERAL`, online mode, cohort epoch, candidate count `<=1024`,
breaker, and authority. Home and offline packs always use P11 in the first R1C release. Responses
truthfully report the actual served pipeline and `score_kind` (`heuristic` or `model`); Android treats
both as bounded server output and never requests Sona.

The version-2 serving transaction applies to every recommendation request after cutover, not only to
the Sona cohort:

1. compute the normal P11 snapshot/ranking in memory for exact stored surface `recommendations`,
   `home`, or `offline_pack`, applying `SCORE_E8_V1` after each unchanged raw generator-budget preselection
   and before the final rank/diversity comparison, then carrying only fixed-point values afterward. Build
   the retained temporal/capture bundle only when its independent R1B
   consent and retention policy require it;
2. the server-owned router may additionally build Sona only for an explicitly allowlisted eligible
   online owner on the frozen surface/context. It uses the same complete candidate authority. No
   truncation is allowed; an eligible universe above 1,024 falls back with
   `CANDIDATE_BOUND_EXCEEDED`. All other requests proceed directly to P11 commit;
3. for a Sona attempt, capture the complete authority token set above, infer, recheck it, and enforce
   timeout, output binding, mandatory filters, and diversity;
4. open one transaction and persist the minimal version-2 request row, immutable P11
   `recommendation_item` baseline rows, the separate 180-day replay payload, and any consented capture bundle.
   When Sona ran, also lock approval/consent/activation/revocation/cohort/breaker authority and persist
   the separate complete `sona_r1c_inference_evidence` and candidate rows, put only its immutable content
   hash in the serving decision, and create the separately purgeable evidence-link row. Every path
   computes each served-item hash and the ordered served-set hash in memory, persists one
   `recommendation_serving_decision` with the exact count/digest and the actual
   `recommendation_served_item` rows, and lets the deferred completeness trigger recompute them; Sona authority
   is rechecked immediately before the decision becomes `COMMITTED_SONA`, while every non-Sona or
   fallback path becomes `COMMITTED_P11`;
5. return or package only committed served items. Any inference/authority failure commits P11; a
   database/commit failure returns an ordinary API error and no request truth, never unpersisted
   output. Offline-pack publication likewise occurs only after its P11 decision commits;
6. version-2 impressions and outcomes create the typed attribution association to the committed served
   decision/rank under the per-request expiry lock; their noncausal base rows retain no request pointer.
   Sync
   attribution validation, outcome materialization, exports, and evaluator joins resolve
   the association then `recommendation_served_item` for every `serving_contract_version=2` request.
   Only explicitly
   labeled version-1 rows created before the audited cutover may resolve `recommendation_item`
   directly. Unserved Sona candidates never become impressions and baseline P11 rows never
   impersonate Sona output;
7. while the 180-day replay payload is retained, exact replay always returns persisted served items.
   Algorithmic P11 replay remains available only in that same retention window; algorithmic Sona replay
   additionally requires retained source bundle, semantic IDs, activation/artifact, and execution profile,
   otherwise it returns bounded `REPLAY_INPUT_UNAVAILABLE` without altering exact replay truth. At or
   after the 180-day boundary both replay modes return `REPLAY_EXPIRED` from the owner-scoped tombstone.

Client attribution fields are claims, never serving authority. The committed served item stores the
canonical server provenance in its existing hash-field name `attribution_source` (mapped to association
`serving_provenance`); `request_surface`
and serving-contract version come from the committed server request, and pipeline/score kind from the
committed decision/item. Separately, the existing recommendation-attribution wire contract v1 keeps
required `source` and `surface` as bounded *client presentation* labels, not synonyms for those server
fields. They may validly be `offline_pack` or `local_rerank` on the same committed served item, and UI
surfaces such as `home_for_you` or `offline_for_you` need not equal stored request surfaces
`recommendations`, `home`, or `offline_pack`. Missing or syntactically invalid v1 labels are
`REQUEST_VALIDATION_FAILED`; safe labels are never rejected merely for inequality with server
provenance/surface. The server resolves authenticated owner/request, served rank/recording, event kind,
presentation/impression identity, and unexpired request truth independently. If a v1 event claims an
`offline_pack_id`, a v2 pack requires an exact match to the immutable binding receipt's pack UUID,
authenticated owner/device, request ID, serving decision hash, served count/set digest, and a committed
served rank/recording in that set. The receipt remains queryable after seven-day pack-content expiry until
the request's 180-day attribution horizon; a missing, cross-device, cross-request, or expired receipt is
`ATTRIBUTION_MISMATCH` or `ATTRIBUTION_EXPIRED` as appropriate. V1 packs retain their current owner/request/
item validation. The claim cannot promote a local rerank to a server-model win. Unknown safe presentation
labels remain accepted as non-authoritative hints, preserving the v1 schema's extension tolerance.
The association uses explicit `wire_attribution_version=1` and stores its bounded presentation labels
separately from derived server fields; serving contract v1/v2 is a different axis. A future wire v2 may
rename them `presentation_source`/`presentation_surface` or omit them only behind a separately advertised
sync/API capability and schema; serving-contract v2 alone never changes the v1 wire obligation.

The v1 schema deliberately permits bounded safe extension properties. Unsolicited `pipeline`,
`pipeline_key`, `score_kind`, `serving_contract_version`, or equivalent contract fields are syntax-
validated by the existing safe-value/property-name bounds and otherwise ignored. Before either
`UserInteractionEventRow.payload` or `SyncEventRow.payload` is written, the server constructs a new
 canonical payload from the frozen allowlist for that event type, normalizes each permitted non-attribution
 field. A grandfathered serving-contract-v1 event may receive only request ID, served rank, and the
 bounded *client presentation* `source`/`surface` in its rebuilt legacy payload; canonical server fields
 remain normalized separately. A serving-contract-v2 event stores none of those
causal values in either base payload or legacy normalized columns; they exist only in the typed 180-day
attribution association and are joined for authorized evaluation/export during that lifetime. Pipeline,
score kind, and contract version remain normalized server association/decision values when needed and are
never copied from the client payload. Unknown safe extensions and the
original attribution object are discarded; the inbox/idempotency receipt retains only the domain-separated
SHA-256 of the original bounded request bytes plus validation result, never the original JSON. Rejected or
accepted extensions are also excluded from structured logs, dead-letter payloads, exports, and traces.
 Thus they are not compared,
 persisted, echoed, or admitted to evaluator/breaker/export/training. If a future contract wants them,
 it must name and capability-gate them. Client presentation labels may be retained only in their bounded
 lifecycle association (or grandfathered v1 legacy payload), explicitly tagged as untrusted, and may not
 drive evaluator/breaker/export/training provenance. A forged extra pipeline/contract claim cannot change
 any result because committed server truth is the sole serving input.

Sona source reconstruction, planning, and preflight are migrated together before serving-contract-v2
cutover. Their accepted-event CTEs split by contract version: grandfathered v1 retains its existing
`inbox.payload = sync_event.payload` and legacy JSON-validation path until its privacy disposition;
post-cutover v2 requires matching owner/event/device/type/schema/aggregate IDs across inbox and sync
event, `inbox.apply_status=APPLIED`, the validated 32-byte original request hash, a non-erased
terminal acknowledgement, and `normalized_payload_sha256 BYTEA` with a 32-byte check recorded in both
rows at ingress.
The digest is `SHA-256` of the rebuilt allowlisted sync payload under a domain-separated, versioned
`SYNC_PAYLOAD_PG_V1` PostgreSQL JSONB byte encoder; a database trigger recomputes it from the immutable
`sync_event.payload` and requires equality to both stored values. This local integrity hash is not a
serving-decision/content hash, and a PostgreSQL encoder-version change requires explicit migration and
golden vectors before reads resume. It does **not** compare empty v2 inbox JSON to sanitized sync JSON.
The typed v2 attribution
association supplies request, served rank/recording, impression linkage, server provenance, and expiry
through owner-qualified joins; typed listening/interaction columns and the rebuilt allowlisted sync
payload supply noncausal play/feedback fields. Existing JSON recommendation-pointer predicates and
Python `Sona0026AcceptedEvent`/`_verify_recommendation_payload` checks are version-1-only; v2 uses a
separate normalized accepted-event projection and verifier with no `recommendation` JSON dependency.
Migration adds immutable, nullable-for-legacy `sync.sync_event.ingress_device_sequence BIGINT` and
`ingress_validation_version` (`1|2`). The restricted sync writer sets version 2 and copies the validated
`inbox.device_sequence` into `ingress_device_sequence` for every post-cutover client-origin event in the
same transaction; a deferred owner/device/event/sequence check against the inbox plus a v2 non-null
constraint rejects mismatches or direct-SQL bypass. Legacy and server-origin events, including erasure
tombstones, have a null ingress sequence. V2 source joins compare `inbox.device_sequence` only with
`sync_event.ingress_device_sequence`; the globally allocated `sync_event.server_sequence` is never
equated to a per-device sequence and remains solely a total-order/watermark key. Tests interleave accepted
events from two devices with different device/global sequences through reconstruction, planning, and
preflight, and reject a deliberately mismatched ingress sequence.
An `ERASED` legacy inbox status or absent association is ineligible, and reconstruction, planning, and
preflight must return identical versioned eligibility counts/watermarks for the same frozen source.
The SQL/Python migration and its golden v1/v2 event vectors are a cutover prerequisite, not deferred
cleanup; no valid v2 interaction may disappear merely because its inbox payload is `{}`.

When the authenticated owner has an unexpired
`recommendation_attribution_expiry_tombstone` but the 180-day request/served truth is gone, every
impression, feedback, or recommended listening event carrying that attribution is rejected in full with
nonretryable `ATTRIBUTION_EXPIRED`. The sync acknowledgement is idempotent and tells Android to terminally
archive/drop that pending event; the server never stores a base event without its claimed causal link and
never resolves the pointer against P11. The tombstone lookup is always scoped by authenticated owner;
foreign, never-existing, post-account-deletion, and post-400-day identities all retain the same
non-disclosing `ATTRIBUTION_NOT_FOUND` behavior. Tests cover a client offline for 181+ days, an exact retry,
the 180-day and 400-day boundaries, purged-vs-foreign indistinguishability outside the owner, and every
required/extra field across P11, Sona, Home, offline pack, replay, and sync.

The cohort breaker has exact states `CLOSED`, `OPEN`, and `HALF_OPEN`. In `CLOSED`, use rolling
five-minute windows with at least 50 eligible attempts: open on error/timeout/authority fallback over
5%, Sona p95 over 300 ms or over 1.25x paired P11 for three consecutive one-minute buckets, any
identity/output/owner/mandatory-filter violation, GPU authority loss, or Admin kill. Also open when a
daily window of at least 100 served decisions exceeds diversity/repeat/HHI offline deltas for two
consecutive windows. `OPEN` serves P11 for 15 minutes; repeated failure doubles cooldown up to one
hour. `HALF_OPEN` admits exactly ten sequential eligible probes while all other traffic uses P11;
ten valid probes with zero safety/authority failure and passing latency close it, while any failure
reopens it. Insufficient traffic leaves it half-open. Manual reset may enter `HALF_OPEN`, never skip
probes. Every transition is an append-only receipt and atomically increments cohort generation.
New owners remain on P11 until a later explicit allowlist operation.

Breaker counters are updated under the same authority lock as the serving decision. If the current
attempt itself crosses an immediate safety/authority or rolling latency/error threshold, the
transaction increments breaker generation, records the transition, and may commit only P11 for that
request. Daily post-outcome drift transitions fence subsequent requests and invalidate any inference
whose final commit has not yet acquired and revalidated the new breaker generation.
The live breaker relation, rolling/daily input buckets, and transition receipts are owner-scoped for
authorization, export, deletion, and retention even when physically keyed by cohort. Only the thresholded,
cohort-independent aggregate defined in the lifecycle section may outlive 180 days; a one-owner value is
never relabeled anonymous or copied into that aggregate.

## 11. Shared GPU admission and performance policy

1. Identify devices by stable NVIDIA UUID; never rely on a mutable ordinal alone.
2. One authority records the resident Sona reservation and bounded Face reservation/lease. Requested
   budgets cannot exceed the reviewed device total minus the greater of 1 GiB or 10% measured safety
   margin. Requested budget is advisory; NVML process-used and device-free memory are the measured
   acceptance values.
3. Sona acquires and heartbeats its reservation before loading the CUDA session; readiness is false
   until load-time NVML use fits the reservation. Face claims only the remaining reviewed budget,
   holds the lease while its session exists, and runs short segments so it observes cancellation
   generations between batches.
4. Face OOM halves batch to one, records the attempt, releases admission, and retries only within the
   bounded policy. Repeated batch-one OOM is terminal for that model/device configuration.
5. Sona OOM/unhealthy state removes readiness and causes P11 fallback; it does not evict CPU services.
6. Startup, model swap, and rollback are serialized by activation/admission generation. Before Sona
   load/swap, the authority cancels Face, waits a bounded 30 seconds for session unload/process exit,
   and verifies NVML release; failure keeps Sona unready and Face cancelled. Old and new artifacts may
   coexist on disk but never own the same reservation generation.
7. Qualification on the target RTX 3060 records raw inference durations, tracks/hour, p50/p95,
   peak VRAM, batch reductions, queue wait, Sona latency under concurrent Face pressure, and recovery.
8. Heartbeat expiry, PostgreSQL authority loss, or generation mismatch stops new inference. Face
   aborts after its current bounded kernel/batch and unloads/exits; Sona returns unready and the CPU
   router uses P11. Tests kill each process and database connection at load, batch, swap, and unload
   boundaries and prove actual process-level VRAM recovery with NVML.

Initial Face operational floors, frozen before qualification, are: p95 end-to-end analysis no more
than 120 seconds for a five-minute track on the target RTX 3060; sustained throughput at least 30
representative tracks/hour; peak Face reservation no more than the measured safe residual after the
Sona reservation and safety margin; zero CPU API dependency on CUDA. If Sona is not yet active,
qualification still reserves the reviewed future Sona budget when testing coexistence.

## 12. Implementation milestones and dependencies

### M0 - Isolated ML worktree (after final user approval only)

Deliverables:

- create a managed worktree and branch from the exact `v1.0.0` commit;
- record base commit/tag and prove the release checkout remains clean;
- load repository-local development instructions in the worktree;
- copy the approved plan into that branch as the implementation authority.

Exit: separate clean ML checkout at the exact approved base; no production deployment credentials
or model weights introduced.

### M1 - Contract/doc reconciliation and test skeletons

Dependencies: M0.

Deliverables:

- update stale status claims: Face Contract v1 is implemented, canonical production v2 and Face
  Timeline/Operations are not;
- reconcile the old Sona teacher V1 warning with implemented `SONA_P11_TEACHER_V2` without erasing
  historical evidence;
- freeze API/sync/admin contracts, SQL entity map, lifecycle state machines, reason codes, and signed
  evaluation protocols, including the 0061-0067/Room-v18 map, R1C purpose authority, served-decision
  aggregate/evidence-link split, exact v2 surface constraint, Face v2 envelope/capability, qualification-
  fixture authority/retention, tuple license derivation, retention, exports, and breaker policy;
- add failing/fixture-driven contract tests before runtime implementation.

Exit: reviewed contracts have no ambiguous identity, authority, disable, deletion, or rollback path.

### M2 - Shared artifact/license/GPU authority and native Sona capture

Dependencies: M1.

Deliverables:

- immutable generic artifact/license authority, phased `LEGACY_UNREVIEWED` upgrade/current-decision
  fencing, complete artifact-set/min-lease/strictest-disposition derivation, manual review flow, and typed
  embedding/Face/Sona relations;
- server-side two-key Android step-up registration challenge/credential/revocation authority plus
  participant WebAuthn fallback contracts; no Android credential is enrolled in this milestone;
- GPU admission lease/generation store and process interfaces;
- atomic P11/source-bundle capture plus retryable per-lineage shadow work with no Sona serving;
- independent serving-consent ledger skeleton/provisioning, Sona capture privacy/restore fences,
  metrics/reason codes, and backup/restore metadata coverage;
- migration tests with real PostgreSQL concurrency and crash boundaries.

Exit: CPU and Android behavior are unchanged; P11 remains the only serving pipeline; native evidence
accumulates with exact lineage; absent GPU/model is harmless.

### M3 - Face dataset, bake-off, and interpreter approval

Dependencies: M1 for protocol; M2 for artifact/license/admission authority.

Deliverables:

- operator-owned/licensed smoke/development/final-qualification manifests, fixture/rater authority,
  exact retention/deletion/export/backup rules, and rubric;
- isolated candidate imports/conversions;
- deterministic preprocessing, executable normalized-margin/abstention/tie formula, fixed bootstrap
  fixtures, and candidate technical reports;
- separately versioned interpreter candidates and calibration;
- development bake-off report selecting one frozen candidate, followed by a single final-
  qualification semantic/runtime report and one signed approval or `NO_MODEL_PASS`.

Exit: one exact compatible encoder/interpreter/preprocessing/execution-profile tuple is selected on
development data and then approved on its untouched final qualification set, or production remains
blocked with an explicit failure report. No candidate or threshold changes after unseal.

### M4 - Face Timeline server core

Dependencies: M2; may use a deterministic fake interpreter for tests while M3 runs, but production
activation requires M3 PASS.

Deliverables:

- PostgreSQL schema, repositories, `ml.face-timeline/v2`, canonical Face identity/timeline/envelope
  v2, complete canonical-source trigger matrix, owner sponsorship/coalesced work, worker handler,
  bounded fan-out roots/per-sponsor intents/materialization, publication fence, activation/rollback,
  redirect-generation outbox/reconciliation, authorization, capability-gated API/sync, and bounded
  reconciliation;
- fault injection for enqueue/claim/disable/delete/last-reference/publish conflicts;
- canonical `BYTEA` growth and backup/restore evidence.

Exit: fake and approved-model integrations pass identical contracts; no stale/unauthorized job can
publish; repeated semantic key is idempotent and conflicting bytes fail closed.

### M5 - Android projection/cache/rendering

Dependencies: M4 contracts; production qualification requires M3.

Deliverables:

- Room-v18 migration/entities/DAO, capability-safe sync projection, signed seven-day authorization
  lease derived from every required artifact, authorized transport, cache verifier/evictor,
  transactional download-work rows, unique
  WorkManager scheduling, and startup reconciliation;
- attested Android `ML_CONSENT_STEP_UP_V1` registration/use client with two-key bootstrap, API 26-29
  strong-biometric branch, API 30+ strong-biometric/device-credential branch, revocation handling, and
  participant WebAuthn handoff when Android capability is unavailable;
- content-addressed verified playback-source proof, mismatch/reverification fencing, playback
  projection coordinator, and real `FaceTimelineSampler` integration;
- continuous semantic renderer mapping, neutral fallback, lifecycle/process-death/profile fencing;
- accessibility and performance instrumentation.

Exit: playback never performs network work, stale generations never render, cache corruption degrades
only Face, and real timeline changes are visible by musical section.

### M6 - Face Operations and privacy completion

Dependencies: M4-M5.

Deliverables:

- Web Admin policy, lineage, readiness, queue/status/reasons, activation/rollback, and kill controls;
- explicit bounded backfill with watermark/pause/resume/cancel and truthful accounting;
- deletion, last-reference, frozen export contents, retention, GC, independent-ledger facts,
  30-day prior-activation rollback references/readiness/regeneration, quarantine-first backup/restore,
  and reconciliation;
- exact self-service/admin actor matrix, endpoints/commands, expected generations, idempotency,
  CSRF/reauthentication, and negative-authorization proof;
- operator runbooks and evidence collection commands.

Exit: every state-changing operation is authorized, idempotent/audited, bounded, and recoverable;
no implicit backfill or hidden model download exists.

### M7 - Semantic Face production qualification gate

Dependencies: M3-M6.

Deliverables:

- exact six-axis model/interpreter/license/evidence approval; limited-axis approval cannot pass M7;
- target RTX 3060 throughput/VRAM/OOM/queue/coexistence evidence;
- PostgreSQL crash/concurrency/backup restore proof;
- Android JVM/instrumented/device matrix on target production device(s), including rotation/fold,
  200% font, TalkBack, reduced motion, color modes, process death, offline playback, frame timing,
  battery, and corrupt/stale/missing projection;
- reviewed activation and rollback drill.

Exit: all Face gates PASS for one immutable source/app/server/model tuple. Only then may production
readiness Phase 2 change to PASS and PA3 continue. Qualification does not itself publish/deploy.

### M8 - Sona reconstructed rehearsal and native readiness

Dependencies: M2; can overlap M4-M7 without competing with their production priority.

Deliverables:

- full `0026` reconstructed dry-run marked ineligible;
- native preflight dashboards/reports for span, completeness, outcomes, embedding model, consent,
  retention, and split feasibility;
- no fabricated acceptance when native prerequisites are missing.

Exit: toolchain rehearsal passes and native source is either explicitly READY or remains BLOCKED with
exact missing counts/reasons.

### M9 - Sona production shadow quality

Dependencies: M8 native READY, approved embedding model, M2 shared authority.

Deliverables:

- production composition/scheduler, atomic capture work, and local-only inference transport;
- signed native datasets, tokenizer, teacher V2, exact Sona execution profile,
  training/export/CUDA benchmark;
- paired shadow evidence and server evaluator;
- final `quality_eligible=true` artifact approval or explicit R1B FAIL.

Exit: R1B PASS changes no served recommendation.

### M10 - Sona R1C owner canary and rollback

Dependencies: M9 PASS and a separate explicit activation authorization.

Deliverables:

- independent R1C serving-purpose ledger/projection, consent/approval, append-only activation,
  one-owner server-owned allowlist, immutable R1C inference/candidate and served-decision truth, P11
  baseline/fallback, audited contract-v2 cutover and committed P11 served truth for all surfaces,
  non-null exact surface enforcement, immutable evidence-hash/purgeable-link split, causal attribution
  equality/expiry behavior, exact execution-profile/authority-token fence, breaker, kill switch,
  monitoring, rollback, withdrawal, and
  account-deletion drills;
- evidence window reviewed before any cohort expansion.

Exit: selected owner can be served safely; all unlisted/new owners remain on P11; rollback is atomic
and does not require model deletion or deployment.

## 13. Verification matrix

### 13.1 Static and unit gates

- root, server, GPU, and training Ruff formatting/lint;
- strict mypy across source and tests;
- Python unit/contract tests including Face canonical cross-language fixtures;
- Android lint, unit tests, debug/trusted-LAN/release assembly with dependency verification;
- generated Room schema checked in only on the ML branch.

Use the repository-native commands (`scripts/check.ps1` on Windows or `scripts/check.sh` on Linux)
plus focused project commands during iteration. A final full gate must not rely only on focused tests.

### 13.2 PostgreSQL/integration gates

- upgrade from an exact `v1.0.0` database snapshot and fresh install;
- constraints, unique identities, activation chains, lease/generation fencing, and bounded sizes;
- Face semantic-key uniqueness permits presentation-map/probe/schema/codec/calibration successors while
  the abbreviated lookup tuple is non-unique; upgrade and A -> successor -> reversal fixtures prove it;
- 0061 legacy-artifact derivation, `LEGACY_UNREVIEWED` non-approval, current license-decision
  supersession, and denial/revocation races against Face publication and Sona final commit;
- 0061 device-key migration/fresh-install fixtures cover never-bound `0`, migrated-bound `1`, first
  `0 -> 1` binding, replacement/revocation increments including null-key positive generations, and
  rejection of direct updates, jumps, reuse, decrement, or key change without increment;
- concurrent duplicate enqueue/claim/publication, worker death at every transaction boundary,
  disable/re-enable races, deletion/last-reference races, activation rollback during inference,
  backfill pause/cancel/restart, retention/GC, backup/restore and tombstone replay;
- atomic P11/capture persistence, retryable Sona work, audited serving cutover, and version-2
  `COMMITTED_P11` decision/served-item/attribution/replay on recommendations, Home, offline packs,
  unlisted owners, every fallback, and zero-result responses; R1C consent withdrawal/deletion during
  inference, capability-filtered old-client sync, the full Face source-trigger matrix,
  redirect/rematch, rollback retention/GC/regeneration, and restore quarantine/ledger replay;
- serving-cutover concurrency stalls a v1 insert while activation waits, proves the activation commit
  linearization point, rejects stale instances, and observes no v1 commit afterward using exact stored
  surface literals; application-role/direct-SQL inserts with null, unknown, mixed-case, and all three
  valid surfaces prove the check plus deferred trigger runs for every v2 row; source lifecycle covers
  A -> B -> A, redirect reversal, duplicate event delivery,
  and late workers against immutable sponsor IDs/generations;
- P11/served-truth completeness fixtures require exactly one replay row and resolved limit, then cover
  zero results and fixed empty digests, one/max results, a missing baseline or served suffix, an extra row,
  duplicate recording/rank, rank reorder, a modified score/reason/origin, stale nested/item hash, stale
  P11/served decision count/digest, missing replay, and application-role direct inserts; every malformed
  transaction fails at the deferred constraint before commit. PostgreSQL/Python LP golden vectors and
  mutation/property tests prove the database never relies on `jsonb::text`, locale, float, or client hashes;
- post-commit integrity tests separately `UPDATE|DELETE` the replay row, decision, every baseline/served
  child, and parent through application and privileged roles; reciprocal deferred triggers reject each
  orphan/incomplete graph, while the security-definer expiry/account-delete procedures succeed only with
  the matching purge receipt and zero remaining children. Crash/retry at each purge statement converges;
- `SCORE_E8_V1` fixtures run the live generator/scorer/external-score paths through non-finite/bounds,
  eight-decimal halfway/neighbor/negative-zero/tie cases and prove integer ranking, stored NUMERIC,
  response bytes, hashes, and replay agree without changing the current P11 fixture order. For every
  generator budget, crafted raw binary64 values that differ below E8 resolution on opposite sides of the
  cutoff prove preselection still uses the live raw order and preserves membership;
- canonical Face publication with 0, 1, 100, 101, and 10,000 sponsors proves no unbounded sponsor lock/
  insert transaction, stable cursor resumption at every crash point, per-owner revocation during intent
  discovery/materialization, no gap/duplicate projection, and bounded query/lock counts;
- successor Sona model and execution-profile registry generations both create new target dispatch/work
  from an already-consumed-but-unexpired capture cursor; expiry prevents later discovery;
- 30/180/400-day R1C purge classes, FK-order crash/restart, export, account deletion, and post-backup
  ledger replay converge without resurrecting personal candidate/served truth; tests inspect the actual
  version-2 base request and prove no request document/features/snapshot FK survives outside the purgeable
  replay relation, `recommendation_item` and the base request disappear at day 180, the owner-scoped
  tombstone gives only its owner `ATTRIBUTION_EXPIRED` through day 400, and
  `protect_sona_shadow_binding` remains installed/enabled and allows the final delete only after its
  retention predicate clears. Real PostgreSQL FK and
  immutability triggers prove 30-day cleanup deletes candidate -> lifecycle link -> evidence without any
  serving-decision update, while a direct evidence delete with a live link is rejected. The full live
  dependency inventory includes listening events, interaction rows, temporal evidence, sync payloads, and
  offline packs: v2 tests prove only typed associations carry causal IDs, noncausal history survives their
  purge, remaining pack content is deleted, and no restrictive FK remains. The exact v1 population and
  privacy-review grandfather/migration decision are recorded before cutover;
- attribution-association negative tests attempt every cross-owner request/target, nonexistent or wrong
   served rank/recording, mismatched event kind/server provenance/request surface/profile/device,
   feedback-to-other-impression,
  direct mutation, premature purge, and expired insert. Composite FKs/checks/deferred triggers reject all;
  two devices concurrently create distinct interaction IDs for the same presentation/request/rank and the
  advisory-lock lookup plus partial unique key yields exactly one canonical impression association.
  Offline-pack tests bind 0/1/max-item packs at request level to the exact committed decision/count/set
  digest and payload hash, reject any non-null arbitrary rank, missing/reordered/tampered pack item, or
  mismatched digest, and purge the pack/association before its request;
- after a valid v2 association has committed, direct and privileged target-row tests try to change every
  guarded listening, interaction, temporal, and pack column or delete the target; all fail without the
  lifecycle receipt, including an offline-pack payload/hash update and a target `ON DELETE` attempt.
  Unrelated play-metric/taste-exclusion updates succeed without changing association truth. Concurrent
  association creation versus target update/delete is serialized by the target row lock and has no
  successful invalid final state. `PACK_EXPIRED`, event erasure, day-180 expiry, consent withdrawal, and
   account deletion each exercise the restricted child-before-parent association/target purge order and
   idempotent per-edge receipt. Impression erasure with zero, one, and multiple feedback descendants
   deletes each feedback association and base/sync/temporal materialization before its impression, while
   feedback-only erasure retains the impression; direct impression deletion is blocked by restrictive
   FKs, ordinary expiry leaves v2 base feedback without any impression pointer, v1 grandfathered rows
   keep only still-valid references, and a racing feedback insert loses to the tombstone/request lock
   with no orphaned `impression_interaction_id`;
- pack-binding receipt tests prove atomic creation with pack/decision, unique pack ID, immutable
  owner/device/request/hash/count/digest, exact payload-hash match, seven-day pack-content purge without
  receipt loss, same-device attribution uploaded at day 8 and day 179, cross-owner/device/request or
  tampered claim rejection, day-180 terminal expiry, receipt-before-request purge, owner export,
  consent/account erasure, and backup/restore tombstone replay;
- source reconstruction/planning/preflight tests feed accepted v1 and v2 listening, impression, and
  feedback rows through all three real SQL queries and the Python verifier. V2 hash-only inbox plus
  sanitized sync payload and typed association must match owner/device/event/type/status/hash and yield
  the same eligible outcome as a semantically identical v1 event; wrong identity, digest, missing edge,
  erased inbox, or premature expiry is excluded. Interleaved events from two devices with overlapping
  per-device sequence numbers but distinct global server sequences remain eligible in all three queries;
  a forged v2 ingress device sequence is rejected. Migration runs before any v2 cutover;
- grandfathered v1 per-event erasure tests inspect `sync.device_event_inbox.payload`, `terminal_ack`,
  idempotency response reference, original sync row, and a subsequent duplicate push; only a minimal
  owner-scoped hash/sequence/idempotency receipt remains, `EVENT_ERASED` is terminal, changed hashes
  conflict, and no erased interaction is rebuilt or used by Sona after restore;
- attribution at 180 days, 180 days plus one tick, and after 181+ offline days produces the frozen
   nonretryable outcome, never P11 rebinding; wire-v1 required/bounded presentation `source`/`surface`,
   compatibility with every frozen valid fixture (`hybrid_composer`, `offline_pack`, `local_rerank`,
   `home_for_you`, `offline_for_you`) and Android offline rerank, plus ignored safe extra pipeline/contract
   fields are exercised through sync and direct API paths. Tests prove a forged client label cannot
   override committed server provenance, model attribution, or request surface. Tests inspect both ORM
  payload columns, idempotency receipts, logs, dead letters, export, evaluator, breaker, and training inputs
  to prove only the rebuilt allowlisted/server-derived payload and original-request hash survive;
- delayed-worker and deterministic database-clock tests hold the per-request advisory lock across
  validation/purge, pause before and after the final check/association insert, cross the expiry instant,
  and prove attribution, replay, and offline-pack fetch share one total order with no post-expiry use;
- breaker retention tests prove the one-owner canary state/receipts/metrics are owner-scoped, exported and
  deleted with that owner, expire within 180 days, and emit no 400-day aggregate; threshold-edge tests at
  9/10 owners and 20% contribution prove suppression/admission without identifiers or stable pseudonyms;
- no stale or unauthorized result becomes selectable.

### 13.3 Android gates

- codec/hash/binding/property tests, including source-presentation golden fixtures; Room migration and
  Media3 `DownloadIndex`/cache corruption, partial-span, quota, removal, and eviction tests;
- two-device erasure sync tests: device B has pulled an impression and dependent feedback before owner
  erasure on A; after ordered deletion events B removes both Room facts and derived caches within one
  cursor transaction. A repeated earlier UPSERT, process death between receipt and cursor persistence,
   pull versus bootstrap with already-persisted facts, typed transport mapping, malformed/unknown typed
   tombstone, profile switching, offline reconnect after tombstone compaction, unsupported schema-1 client,
   and pending re-upload cannot reintroduce either fact. A client without erasure-v2 capability receives
   `UPGRADE_REQUIRED` before both pull and bootstrap and must wipe/bootstrap after upgrade;
- profile switch, account deletion, activation replacement, seek/item change, process death, offline,
  expired authorization, late download, and playback-generation fencing;
- two-key step-up registration/use tests on API 26, 29, 30, and current target API cover server nonce,
  current M5 thumbprint plus dedicated `device_key_generation`, old/new key proofs, attestation
  challenge/chain/auth-list/boot/app checks, stolen
  bearer, substitution, missing strong biometric/device credential, WebAuthn fallback, key/device/account
  rotation/revocation, concurrent register-vs-rotate and grant-vs-revoke races, proof that `row_version`
  or session generation cannot substitute, descendant-challenge invalidation, and fail-closed unsupported
  devices;
- mixed two-/three-artifact projection fixtures alter each signed lag/disposition/decision generation in
  turn and prove Android recomputes both list digests, the minimum lease, and strictest disposition; omitted,
  duplicated, cardinality-mismatched, unknown-role, reordered, alternate-hex-case, locale-sorted, zero-lag,
  unsigned, and server-summary-only variants all fail closed to neutral; Python/Kotlin golden canonical
  bytes and digests match for the complete frozen artifact-list codec;
- connected Compose tests and physical-device evidence for accessibility, lifecycle, rendering,
  performance, and battery;
- compare neutral/local and semantic paths; playback/control tests must pass with Face unavailable.

Physical Samsung A55 qualification uses the production build, fixed 60 Hz display mode, 200-nit
brightness, identical verified local source/seek script, radios and background sync disabled, ambient
`22 +/- 1 C`, starting charge `85 +/- 1%`, battery temperature `25 +/- 1 C`, and at least 15 minutes
idle before each run. The signed evidence manifest derives a random seed and schedules four paired
30-minute performance blocks with two baseline-then-semantic and two semantic-then-baseline orders.
Each run is cooled/reset to the start envelope; every attempt, Perfetto trace, thermal status, charge,
and harness event is retained.

All four Semantic runs must have p95 frame time at most 16.7 ms, jank under 1.0%, zero frozen frames
over 700 ms, and no playback underrun increase. Across paired blocks, median Semantic-minus-neutral
p95 regression is at most 2.0 ms and median jank increase at most 0.5 percentage points. A separate
four-pair counterbalanced 60-minute local-playback protocol under the same start envelope records
Battery Historian/Perfetto energy; median paired consumed-charge increase is at most 5% and median
paired peak-temperature increase at most 3 C.

At most six pair attempts may be started to obtain the four predeclared pairs. Only an external call,
OS update/reboot, harness crash, or power/USB fault recorded by the harness may invalidate a pair;
invalid attempts still consume the cap and remain in evidence. Thermal throttling, an over-temperature
event, underrun, renderer/service crash, or limit violation after a run starts is a measured failure,
not a discard reason. In particular, any Semantic run that starts inside the envelope and then
throttles fails qualification. Fewer than four externally valid pairs after six attempts, any thermal
failure in either arm, or inability to restore the start envelope yields FAIL rather than more retries.

### 13.4 GPU/model gates

- no ML/CUDA import in CPU API/server base path or image;
- exact device UUID/runtime/provider/node placement;
- artifact/manifest/preprocessing/license identities and corrupt/missing/tampered cases;
- deterministic decode/segment/interpreter output and eager/ONNX parity;
- real OOM/batch-one terminal behavior, admission cancellation, Face/Sona contention, service restart,
  heartbeat/database loss, session unload/process exit, Sona load/swap serialization, model
  activation/rollback, raw latency and per-process NVML VRAM samples;
- no implicit network/model download.

### 13.5 Privacy and quality gates

- owner/profile isolation, consent grant/withdrawal, account deletion publication revocation,
  export bounds, no raw owner IDs in portable evidence, restore reapplies fences;
- actor/operation matrix, self-only consent, admin-inhibition-not-grant, expected-generation,
  idempotency, CSRF, operation-bound Android/WebAuthn step-up replay/binding/expiry/rotation, and
  authorization-negative cases;
- artifact-license denial/revocation races, projection tombstone/fetch/renewal rejection, offline
  lease horizon across mixed artifact sets, zero-lag activation refusal, strictest derived-output
  disposition/GC, signed per-artifact policy-list verification, and explicit legal approval of residual
  offline lag;
- server-derived serving provenance ignores client presentation source/surface and rejects forged
  causal IDs; client pipeline/contract extras reach neither durable event payload nor logs/dead letters,
  evaluator, breaker, export, or training inputs; bounded presentation labels are stored only as
  non-authoritative lifecycle data or in grandfathered v1 legacy payload;
- qualification fixtures/rater data prove operator license or purpose consent, deadlines, access/export,
  withdrawal/deletion, post-backup tombstone replay, and immediate approval/activation invalidation when
  any required raw source or annotation is no longer authorized or retained;
- sealed single-candidate Face final qualification and signed Sona evaluator recomputation;
- synthetic/reconstructed evidence can never set `quality_eligible=true`.

## 14. Rollout and rollback

### Face

1. Register artifacts as non-active, run technical/quality evidence, then append an activation.
2. Deploy capability-filtering server behavior while Face publication is disabled; then deploy a
   capable Android build; only then generate timelines for a bounded owner-authorized canary set.
3. Android accepts only the exact active binding and verified playback-source proof. The immediately
   prior tuple/timelines coexist only under the 30-day rollback-retention references and live source
   authority defined above.
4. Rollback appends a successor activation selecting the prior compatible encoder/interpreter/
   preprocessing/execution-profile/Face-v2 schema tuple; sync invalidates newer projections and
   selects/downloads the prior lineage. Playback remains on
   neutral fallback until the exact local projection/source proof is ready. If rollback readiness
   fails, `DEACTIVATE` is the safe immediate action and prior selection remains blocked.
5. Kill/disable blocks new work/publication without deleting evidence or source audio; it cannot
   extend rollback retention.

### Sona

1. R1B shadow does not affect serving.
2. R1C activation begins with one owner and an explicit receipt.
3. Per-request failure, open breaker, model unready, serving-consent withdrawal, deletion/publication
   revocation, or Admin rollback commits and serves P11 without caller-selected routing.
4. Rollback changes active/cohort state only; immutable artifact/evidence remains for audit.

No schema downgrade or destructive Room fallback is a rollback mechanism. Use append-only
activations, feature disable, prior compatible binaries where schema permits, and verified backup
restore according to existing runbooks.

## 15. Risks and stop conditions

| Risk | Detection/trigger | Mitigation or stop condition |
|---|---|---|
| Face model license remains ambiguous | artifact review cannot bind one applicable license | Candidate fails; use another candidate or obtain written/proprietary terms |
| Legacy artifact metadata is insufficient | 0061 derives content but cannot reconstruct exact rights | Mark `LEGACY_UNREVIEWED`; preserve old CPU path; block every new Face/Sona activation until manual review |
| No Face candidate meets semantic gate | sealed final-qualification result below required floors | `NO_MODEL_PASS`; production remains blocked; do not tune on final qualification |
| Final-qualification set is too small/biased | split/CI/rater requirements fail | Collect more authorized material; gate remains open |
| Qualification fixture/rater authority expires or evidence is deleted | fixture generation, consent/license, retention, or raw hash no longer validates | Invalidate dependent approval/activation to neutral; purge/reseal/requalify, never preserve approval from hashes alone |
| Multiple Face candidates contaminate final qualification | more than one candidate touches the sealed set | Invalidate the set; collect/reseal a successor after development selects exactly one candidate |
| Timeline database growth is excessive | TOAST/backup/restore/device payload evidence exceeds budgets | Reduce keyframes within contract or review a new sidecar design; do not switch implicitly |
| Android jank/battery regression | device thresholds or baseline comparison fail | Simplify renderer/cache work, keep neutral fallback, block Face PASS |
| Android cannot prove playback bytes/presentation mapping | Media3 completed-cache proof, source hash/variant/sample map absent or changes | Continue playback with neutral Face; verify the existing Media3 cache off-thread without copying audio |
| Prior Face tuple is not rollback-ready | artifact/timeline absent, revoked, corrupt, or past 30-day window | Immediate `DEACTIVATE`; regenerate only from exact still-authorized source/artifacts; never select approximately |
| Stale work publishes after authority loss | race/fault test or audit invariant fails | Block activation; repair fencing before continuing |
| Sona native data is insufficient | preflight span/completeness/outcome reasons | Remain shadow/data-capture only; never reuse reconstructed PASS |
| Serving cutover cannot satisfy all P11 surfaces | deferred decision constraint or surface compatibility test fails | Do not activate cutover; remain contract v1/P11 and repair dual-write path |
| Android cannot establish attested auth-per-use step-up | API branch, secure lock/strong biometric, attestation, or revocation check fails | Fail closed for Android grant; use participant WebAuthn, never bearer/recent-login fallback |
| Any Face artifact requires zero revocation lag | tuple minimum is zero | Tuple is ineligible for activation; select/relicense another complete tuple |
| Face projection fan-out falls behind | bounded root/intent lag or repeated per-sponsor authority conflicts exceeds SLO | Keep affected owners neutral, resume from durable cursor, throttle/pause backfill; never enlarge a publication transaction |
| Shared Sona model revoked by account deletion | participant ancestry matches deleted owner | Immediate P11 fallback and successor retraining without owner |
| R1C serving purpose withdrawn | current model ancestry includes withdrawing participant | Increment revocation/cohort generation, stop serving immediately, require successor authority |
| GPU contention harms Sona | p95/fallback/OOM under concurrent Face | Reduce Face reservation/batch, pause Face, or require separate device; no SLA relaxation |
| Old Android client sees Face event | capability-negative contract/integration test fails | Block Face publication; deploy filtering server before capable client/activation |
| Sona owner has more than 1,024 eligible candidates | complete mandatory-filtered count exceeds bound | Owner remains on P11; no truncation or cohort admission in initial R1C |
| Local transport widens exposure | inference socket/port reachable outside intended boundary | Qualification fails; no Sona activation |
| Full Face delays production materially | any M3-M7 gate remains open | Report the exact blocker; changing release sequence requires a new explicit user decision |
| Documentation/evidence drift | current code contradicts active plan/runbook | Reconcile before approval; historical evidence stays immutable |

## 16. Definition of done

### Semantic Face production gate is complete only when

- one exact encoder/interpreter/preprocessing/execution-profile/license tuple passes all six axes;
- its operator fixture/rater authority, raw evidence retention, approval expiry, complete artifact-set
  min-lease and strictest-disposition derivation are current and independently reproducible;
- all server job, persistence, API/sync, activation, operations, authorization, deletion/export/
  bounded projection fan-out, retention/GC/backup/restore paths pass;
- Android projection/cache/sampler/renderer is integrated in real Now Playing and passes the full
  device/accessibility/performance matrix;
- target GPU and model quality evidence pass immutable thresholds;
- disable, kill, rollback, missing model/GPU, corrupt projection, and offline playback all degrade
  safely;
- a reviewer can trace the release claim to exact commits, artifacts, hashes, datasets, approvals,
  and device evidence.

### Sona R1B is complete only when

- native, consented, chronological, candidate-complete evidence passes the existing evaluator;
- one exact tokenizer/model/pipeline/execution-profile tuple obtains final signed
  `quality_eligible=true` approval;
- P11 remains unchanged and no Sona item has been served by the shadow milestone.

### Sona R1C is complete only when

- separate serving-purpose consents and signed R1C approval plus an explicit activation authorize one
  eligible owner for online `recommendations`/`GENERAL` only;
- the audited contract-v2 cutover gives every new recommendation/Home/offline-pack request committed
  P11 or Sona served truth and causal attribution, including unlisted owners and fallbacks;
- atomic served truth/exact replay, truthful response pipeline/score kind, separate R1C inference
  evidence with immutable hash and purgeable association, exact/non-null surface constraints,
  served-item attribution with frozen v1 equality and terminal expiry, complete authority-token fencing,
  independent serving-consent
  restore, fallback, circuit breaker, monitoring, withdrawal/deletion revocation, and rollback are
  proven;
- unlisted/new owners remain on P11;
- cohort expansion remains a separate explicit decision.

## 17. Out of scope

- commercial distribution or a commercial/proprietary model-license purchase;
- cloud or cross-server training aggregation;
- inferring listener emotion or sensitive traits;
- feeding Face semantics into Sona;
- per-owner Sona adapters/models;
- Android-side model inference;
- automatic library backfill, automatic Sona activation, or automatic cohort expansion;
- Face outside real Now Playing;
- WAN/public inference endpoints;
- publishing, deploying, signing a release, creating a PR, or modifying the `v1.0.0` checkout as
  part of planning/review.
