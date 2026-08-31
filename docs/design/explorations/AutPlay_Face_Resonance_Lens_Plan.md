# Plan: AutPlay Face Resonance Lens and temporal musical-character maps

_Locked via claudex-loop with the user on 2026-08-31._

## Status and authority

This is a non-normative, implementation-ready planning artifact for the accepted post-RC AutPlay
Face direction. It does not activate a product milestone, create P15, reopen P00-P14, modify the
accepted UI contract, select a production model, or claim that AutPlay Face is implemented.

An explicit future milestone prompt remains required before product code, API, schema, migration,
or persistence changes begin. The future prompt must preserve the source precedence and completion
rules in the repository `AGENTS.md` and `docs/build-pack/PROMPT_PROTOCOL.md`.

## Goal

Define a mature AutPlay Face visual and semantic system in which an optional personal server can
precompute a versioned temporal map of a track's musical character. Android consumes a compact,
stable semantic timeline and renders a continuous pair of expressive eyes without requiring a
synchronous server request during playback. The design must remain useful without a server, GPU,
approved model, or completed analysis.

## Canonical terms

- **TrackCharacter**: the slow global prior derived for the exact AudioVariant/source-timebase
  lineage used by the active timeline. It may be presented as Recording character, but it is never
  composed with temporal evidence from another variant without an accepted equivalence contract.
- **FaceSemanticState**: the stable, renderer-facing set of continuous musical axes. It is not a
  raw embedding and does not describe the listener's psychological state.
- **TemporalFaceTimeline**: a bounded, versioned sequence of semantic keyframes and significant
  musical events for one Recording/AudioVariant/model lineage.
- **MomentDynamics**: short-lived deviations driven by local musical structure such as energy,
  density, attacks, pauses, transitions, and drops.
- **AppReaction**: a brief secondary visual response to play, pause, Like, or skip. It never
  replaces authoritative UI feedback.
- **Deep musical-character analysis**: the user-facing name for server-side generation of the full
  timeline. Avoid the unqualified user-facing term "emotion analysis" because the feature analyzes
  music, not a person.
- **SemanticInterpreterRelease**: an immutable, benchmarked mapping from one compatible embedding
  model lineage to one versioned `FaceSemanticState` envelope.
- **AnalysisPolicyGeneration**: a monotonically increasing server policy generation captured by
  every enqueue/backfill command and revalidated before claim, retry, and publication. A disable
  transaction may explicitly mark only jobs already `RUNNING` at its commit cutoff as
  grace-eligible under their captured generation; this exception is durable and auditable.

## Approach

### 1. Preserve a stable semantic boundary

1. Raw audio embeddings remain versioned ML evidence owned by the enrichment subsystem.
2. A separately versioned semantic interpreter converts global and segment-level embeddings into
   `FaceSemanticState` values.
3. The renderer consumes only `FaceSemanticState` and event hints. It never knows embedding
   dimensions, tensor layout, model-specific coordinates, or GPU runtime details.
4. Initial stable axes include positive/melancholic, calm/energetic, soft/aggressive, light/dark,
   relaxed/tense, and direct/atmospheric. The contract preserves unknown future axes.
5. A model or interpreter upgrade creates parallel versioned evidence. It never silently mutates
   an existing timeline or changes every track under an unchanged identity.
6. Semantic interpreters have an immutable registry, compatible embedding-model relation, artifact
   and preprocessing hashes, quality evidence, lifecycle status, activation history, and rollback
   window. Activation selects one compatible embedding-model/interpreter pair atomically.
7. The canonical envelope uses finite normalized axis values in `[-1, 1]`, calibrated confidence in
   `[0, 1]`, and explicit abstention. Missing axes remain unknown rather than silently becoming
   neutral zero. Unknown future axes and events survive round-trip storage.
8. Timeline coordinates are zero-based integer milliseconds against the exact source timebase.
   Keyframes are strictly increasing, begin at zero when a usable interpretation exists, never
   exceed the declared source duration, and contain no NaN/Infinity. Event type/time pairs are
   unique and sorted.
9. Logical v1 bounds are at most 64 axes, 4,096 keyframes, 4,096 event hints, 24 hours of source
   duration, and 1 MiB of canonical encoded timeline data. A future milestone may lower these bounds
   with evidence but may not raise them without a reviewed contract change. Any invalid, truncated,
   over-bound, or unverifiable envelope fails closed to fallback.
10. One semantic idempotency key is derived from the exact AudioVariant and source duration/timebase,
    embedding-model release, semantic-interpreter/schema release, and preprocessing identity. The
    canonical result hash is integrity evidence compared under that key, never part of the key.
    Activation epoch is selection/provenance and cannot create a duplicate artifact for the same
    semantic key.

### 2. Generate a temporal map after Vault ingest

1. A verified, committed, servable AudioVariant is the analysis source. Upload completion and
   playback remain independent from analysis completion.
2. Timeline generation uses a new accepted namespaced executable job contract such as
   `ml.face-timeline/v1`; it must not be queued until its domain target, handler, writer, schema,
   retry/cancel behavior, and worker registration exist. The existing `ml.audio-embedding/v1`
   contract remains unchanged and cannot be relabeled or made to claim unsupported work.
3. The server Jobs module owns a namespaced durable face-analysis intent/outbox fact and is the only
   permitted writer of that fact. Through the established Jobs application writer, the variant
   finalization transaction appends an intent only when deep analysis is enabled and records that
   exact enabled `AnalysisPolicyGeneration`; Vault code never writes an `ml.*` table directly.
4. A bounded Jobs reconciler converts enabled-at-finalization intents into jobs, so a crash after
   ingest cannot leave a permanently invisible analysis gap. The intent retains its original enabled
   policy generation as provenance. If policy advances to disabled before conversion, the intent
   becomes durable paused work rather than stale or discarded. A variant finalized while analysis is
   disabled creates no latent auto-enqueue intent and becomes eligible only through an explicit
   owner-scoped backfill operation.
5. When deep analysis is enabled and a compatible active model/interpreter pair exists, the server
   enqueues a durable job using opaque database identifiers, the exact immutable input identity,
   and the captured `AnalysisPolicyGeneration`.
6. The optional GPU project reads the source through the existing read-only Vault boundary and
   performs bounded, deterministic FFmpeg preprocessing. CPU/API projects do not import CUDA or ML
   runtime packages.
7. Analysis uses bounded overlapping segments. The initial prototype range is 5–15 seconds and
   must be tuned with visual and storage evidence rather than frozen by this plan.
8. Segment embeddings plus a global track embedding are interpreted into a compact sequence of
   semantic keyframes. Meaningful attacks, pauses, transitions, and drops may be represented as
   separate bounded event hints.
9. Keyframe reduction removes imperceptible or redundant changes and enforces limits on duration,
   count, payload size, precision, and update frequency. Per-frame embeddings are forbidden.
10. Enqueue, claim, retry, and publication atomically revalidate policy generation, cancellation,
    model/interpreter eligibility, source servability/quarantine, at least one live authorized source
    reference, and lease ownership. Pending intents and jobs are fenced when the last authorized
    reference disappears. A stale policy or source cannot publish a current result.
11. The unique result identity is the semantic idempotency key defined in section 1. Job, attempt,
    activation epoch, and policy generation are provenance/selection facts, not identity. Under one
    semantic key, an exact canonical result hash returns the existing artifact; a different hash is
    a deterministic conflict that fails with a stable machine-readable error.

### 3. Store derived state without changing Vault bytes

1. Vault audio bytes remain immutable and SHA-256 verified. No map is embedded into or appended to
   the source media file.
2. PostgreSQL remains authoritative for analysis metadata, model lineage, jobs, current activation,
   and the durable relationship among Recording, AudioVariant, and result.
3. A timeline is an immutable derived artifact identified by the one semantic idempotency key: exact
   Recording/AudioVariant and source duration/timebase, embedding-model release,
   semantic-interpreter/schema release, and preprocessing identity. Result hash is integrity
   evidence; activation epoch, policy generation, and producing job/attempt are provenance.
4. Model versions may coexist through a rollback window. Retirement is explicit and bounded;
   disabling analysis is not retirement or deletion.
5. The future implementation milestone must choose and justify the physical timeline representation
   (bounded relational rows, bounded JSONB, or a hash-addressed sidecar plus PostgreSQL metadata)
   using measured payload size and access patterns. Large payloads must not be hidden in unbounded
   JSON documents.
6. A filesystem sidecar is never described as transactionally atomic with PostgreSQL. If selected,
   publication uses staged write, checksum verification, file and parent-directory fsync,
   same-filesystem atomic rename, lease-fenced metadata commit, and bounded reconciliation for
   orphan files, missing files, and stale staging state.

### 4. Deliver a compact Android projection

1. Android synchronizes or downloads only the compact semantic timeline required by the active
   rendering contract, not raw embeddings.
2. The projection is cached locally and bound to the exact server profile, authorized account,
   Recording, AudioVariant, source duration/timebase, embedding model, interpreter/schema,
   preprocessing identity, activation epoch, result hash, and playback-generation token. Unknown
   persisted values are preserved.
3. Playback reads the local projection and interpolates `FaceSemanticState(t)` without a
   synchronous server request.
4. `TrackCharacter`, temporal semantic change, bounded `MomentDynamics`, and `AppReaction` remain
   independent layers with deterministic precedence.
5. Timeline arrival during playback uses a quiet, bounded transition from fallback to analyzed
   state; it never causes a hard pose jump.
6. The Face cannot intercept or delay playback, seek, queue, Like/Dislike, navigation, or other
   authoritative controls.
7. Every remote projection query/download rechecks current owner/object authorization and returns a
   masked denial. Explicit local profile removal, a locally completed authoritative logout, or local
   binding replacement immediately makes that profile's projection inaccessible to other profiles
   and invalidates it under the local privacy contract. Remote permission loss, account deletion,
   tombstone/quarantine, and activation changes converge on the next authenticated sync; an offline
   client cannot claim to know them and does not make a synchronous server trip during playback.
   Until convergence, a previously authorized projection may support local offline playback only in
   its isolated profile and is never authorization for a future server request.
8. A late result is accepted only when all projection identities and the current playback-generation
   token still match; otherwise it is discarded without changing the visible track.

### 4.1. Define application-state ownership and precedence

1. Media3 playback/session state owns idle, active item, playing, paused, seek/discontinuity, and
   track-generation facts. No active media produces a stable neutral idle state.
2. A track change, seek, discontinuity, source replacement, or process-restored generation rebases
   the timeline and cancels stale interpolation and app-reaction tokens.
3. Like, Dislike, and other domain reactions begin only after the authoritative local mutation and
   its Journal/outbox fact commit successfully. Play/pause reactions begin only after authoritative
   playback state observes the outcome. Optimistic UI intent alone never creates a success reaction.
4. Failure/loading/offline signals are secondary overlays with bounded lifetime and ordinary text or
   control semantics. They cannot replace the musical state or claim success.
5. Deterministic precedence is: safety/fallback validation, current playback generation,
   `TrackCharacter` plus timeline, bounded `MomentDynamics`, then a short committed `AppReaction`.
   Stale generations and events are ignored.

### 5. Provide an honest local fallback

1. Before a timeline arrives, when analysis is disabled, or when no server/GPU/model exists,
   Android renders a neutral `TrackCharacter` with restrained local response to available energy,
   attacks, and pauses.
2. Fallback never fabricates semantic precision or claims that deep analysis succeeded.
3. A missing or invalid map degrades only the Face. Local playback and controls remain complete.
4. Reduced-motion fallback is a stable expression with low-distance or no transition.
5. The future milestone must name a lifecycle-bounded, Media3-compatible local feature seam. It may
   not perform a second media fetch, own playback, or introduce an unbounded duplicate decoder.
   Explicit update-rate, CPU, memory, and battery caps are required. When no safe feature source is
   available, fallback remains intentionally neutral.

### 6. Add explicit Web Admin operational control

1. Web Admin exposes a persistent server-level **Deep musical-character analysis** setting to an
   authorized Owner/Admin. It displays the selected model lineage, GPU availability, queue state,
   completed-map count, and a truthful unavailable reason.
2. The setting defaults to off until an approved compatible model lineage is configured. A
   temporarily offline GPU may leave the desired setting enabled while work remains durably queued;
   absence of an eligible model must not look like successful analysis.
3. Every setting mutation appends a new `AnalysisPolicyGeneration` through an authorized,
   CSRF-protected, idempotent command receipt and an audit event. Workers never infer policy from UI
   visibility or mutable process configuration.
4. Turning the setting off:
   - prevents new full-analysis jobs from being enqueued;
   - pauses eligible queued timeline jobs without deleting them;
   - in the same policy-generation transaction, records the exact jobs already `RUNNING` as
     grace-eligible; only those jobs may finish and publish under their captured generation;
   - rejects later claims and retries from the old generation, including a formerly running job whose
     lease was lost after the cutoff;
   - durably pauses enabled-at-finalization intents not yet reconciled and queued/retry work instead
     of discarding or silently marking them complete;
   - preserves and continues serving completed maps;
   - performs no destructive cleanup or model retirement.
5. Turning the setting on atomically and idempotently admits durable paused work under the new
   enabled generation: it creates or rebinds one successor admission per logical workload, marks any
   predecessor admission non-claimable/superseded, and retains original finalization, policy, job,
   and attempt provenance. Duplicate reconciliation cannot create a second claimable successor, and
   the semantic idempotency key still prevents duplicate results. It then enables intents for new
   verified uploads. This re-admission applies only to work originally admitted while enabled; it
   never turns variants finalized during `OFF` into implicit backfill.
6. Authorization, CSRF, audit, stable error handling, and no-secret diagnostics follow the existing
   Web Admin contracts.
7. Required stable reason codes distinguish at least `ANALYSIS_DISABLED`, `POLICY_STALE`,
   `HANDLER_UNSUPPORTED`, `MODEL_MISSING`, `INTERPRETER_INCOMPATIBLE`, `GPU_UNAVAILABLE`,
   `SOURCE_UNSERVABLE`, `SOURCE_REVOKED`, `JOB_PAUSED`, `CANCEL_REQUESTED`, `LEASE_LOST`, and
   `RESULT_CONFLICT`. Metrics remain bounded and redacted, keyed only by opaque identifiers.
8. Transactional concurrency tests must cover enable/disable racing enqueue, claim, lease loss,
   retry, cancellation, publication, and successor re-admission at both sides of the disable cutoff.

### 7. Separate new-upload analysis from library backfill

1. Enabling deep analysis does not automatically enqueue the entire existing library.
2. Web Admin provides an explicit **Analyze existing library** command that creates one durable,
   owner-scoped backfill operation with request hash, operation ID, policy generation, eligibility
   snapshot/watermark, and a terminal lost-response receipt.
3. The operation state machine is `PLANNED -> RUNNING <-> PAUSED -> COMPLETED|CANCELLED|FAILED`.
   Generation-fenced child jobs cannot claim or publish against a stale/cancelled generation.
4. Backfill uses bounded batches, lower initial priority than new uploads, bounded aging/fairness to
   prevent starvation, progress counts against the recorded eligibility watermark, and crash-safe
   pause, resume, and cancel.
5. It skips tracks with a current compatible timeline. Rebuilding timelines made by an older model
   or interpreter is a separate explicit operation.
6. Cancellation prevents new child creation, requests cancellation of queued/running children at
   safe points, and preserves already committed immutable results. Worker restart reconstructs the
   same operation from durable state rather than recounting a different library snapshot.

### 8. Keep the map track-scoped and rendering preferences user-scoped

1. The canonical timeline and every analyzed semantic layer, including `TrackCharacter`, belong to
   the same exact Recording/AudioVariant/source-timebase/model/interpreter/preprocessing lineage,
   not to a user profile. Cross-variant composition is forbidden unless a separately accepted and
   verified equivalence/aggregation contract explicitly permits it.
2. The same eligible result is reusable by authorized users of the personal server without
   repeating GPU inference per user.
3. User preferences may select a Face theme, motion intensity, or other presentation controls, but
   they do not rewrite the canonical timeline.
4. The system never infers, persists, or presents a sensitive emotional state of the listener.
5. Admin counts and backfill candidates are owner/account scoped. OWNER/ADMIN is never treated as a
   cross-account superuser, and result reuse never bypasses authorization to the source Recording or
   AudioVariant.

### 8.1. Define derived-data lifecycle and privacy

1. Timeline visibility follows current source authorization; derived-result existence is not
   disclosed to an unauthorized caller.
2. Account deletion purges owner-scoped projections and cached authorization state. A canonical
   track-scoped artifact remains only while another live authorized reference or a separately
   declared legal/backup retention basis requires it. A model rollback window alone is not authority
   to retain owner-derived data after the last authorized reference disappears.
3. Last-reference removal transactionally fences pending intents, future claims/retries, and result
   publication, then schedules bounded reference-aware retirement/GC. It never deletes shared source
   bytes or artifacts still required by another live authorized reference or declared retention
   basis. A rollback window may delay model/interpreter retirement only while such authority exists.
4. Recording merge/split, AudioVariant replacement, quarantine, corruption, model/interpreter
   retirement, and source duration changes produce explicit tombstone/supersession facts. They never
   silently retarget a timeline to different media.
5. Owner export includes only authorized timeline metadata/projections under a versioned manifest;
   it excludes model artifacts, private paths, raw embeddings unless separately authorized, and
   another account's data.
6. Backups, rollback windows, sidecar GC, and permanent deletion follow existing recovery/privacy
   policy. Destructive cleanup requires a separately authorized procedure.

### 9. Define the Resonance Lens visual language

1. Perceived aliveness is fixed at 5/10: equal parts musical instrument and living presence.
2. The rig uses a shared visor field, upper/lower aperture masks, a dark optical core, two or three
   spectral iris arcs, one resonance filament, and bounded 3–6% micro-asymmetry.
3. Geometry, occlusion, aperture, focus, and timing carry meaning before color. White eyeballs,
   round dot pupils, literal eyebrow glyphs, emoji symbols, and fixed angry-eye triangles are
   excluded.
4. The validation matrix uses nine reference anchors rather than a closed mood enum: neutral,
   calm-soft, positive-light, melancholic-dark, dreamy-atmospheric, energetic-bright,
   aggressive-tense, euphoric, and ominous.
5. Intermediate mixtures and temporal transitions are first-class validation targets. A track is
   not snapped to the nearest named anchor.
6. `TrackCharacter` changes slowly, temporal semantic keyframes may evolve every musical section,
   and app reactions remain brief. Normal playback includes visible rest rather than continuous BPM
   pulsing.

### 10. Require accessibility, performance, and rollout evidence

1. State and action outcomes are never communicated only through Face motion, shape, or color.
2. Reduced motion disables continuous modulation and depth simulation, retaining a stable semantic
   expression or restrained crossfade.
3. TalkBack treats low-level Face animation as decorative; concise authoritative playback and
   action semantics remain on existing UI elements.
4. The future milestone must prove compact/medium/expanded layouts, large font, light/dark themes,
   rotation, fold posture, supported accent palettes and contrast, monochrome distinction, Android
   lifecycle restoration, profile switching, and no-analysis behavior.
5. TalkBack exposes a stable, concise, rate-limited summary of product-useful musical character and
   playback/app state when useful, while per-frame geometry and low-level axis changes remain
   decorative and silent.
6. Performance evidence must include frame timing, CPU/GPU/battery cost on Android, timeline payload
   bounds, server tracks/hour, job p95, peak VRAM, and queue/backfill behavior.
7. Model quality evidence must use a named dataset and human review rubric that tests musical fit,
   temporal stability, transition timing, and false certainty. Recommendation quality alone is not
   evidence that a model produces useful Face semantics.
8. Rollout begins with an approved model in shadow/benchmark evidence, the admin setting off, and no
   automatic library backfill. Activation and rollback remain explicit and auditable.

### 11. Split future delivery into explicit sequential slices

No slice starts automatically. Each requires an explicit prompt, prerequisites, proof commands, and
handoff; names below are planning labels, not P15 or active milestones.

1. **Face Contract:** freeze the semantic envelope, interpreter/model compatibility, ownership,
   authorization, lifecycle, state precedence, and degraded behavior.
2. **Face Local:** deliver one accessible Resonance Lens theme in real Now Playing with neutral
   fallback and no server dependency.
3. **Face Timeline:** add the opt-in server job contract, minimum persistent Web Admin activation
   control, versioned timeline production/projection delivery, authorization and last-reference
   fencing, tombstones, deletion/export/retention/GC and sidecar reconciliation, remote-revocation
   convergence, plus target-GPU/model evidence. It cannot ship persistent artifacts without these
   lifecycle controls.
4. **Face Operations:** add the explicit durable library backfill UX, richer Web Admin counts/status,
   expanded observability and recovery operations, and controlled rollout.

Later slices cannot be pulled into an earlier one merely to pass a gate. In particular, the local
theme can ship without server analysis, and the server pipeline cannot ship unsupported durable job
types or pretend deferred GPU evidence is complete.

## Key decisions and tradeoffs

1. **Stable semantics over raw embeddings.** This permits model replacement and multiple visual
   themes at the cost of maintaining a separately versioned semantic interpreter.
2. **Global prior plus temporal semantic evolution.** This preserves track identity while allowing
   the expression itself—not just energy—to change by musical section.
3. **Precompute on the optional server.** This enables deeper analysis without Android inference
   cost, while requiring a deliberately modest local fallback.
4. **Separate immutable timeline artifact.** This preserves Vault immutability and provenance at the
   cost of new metadata, synchronization, and lifecycle contracts in a future milestone.
5. **Operational toggle controls computation, not availability.** Completed maps remain useful when
   analysis is disabled; disabling never becomes hidden deletion.
6. **Explicit backfill.** New uploads receive timely analysis without a surprise GPU storm over the
   existing library.
7. **Track-scoped maps.** GPU work is reusable and privacy-safe; user individuality belongs in
   themes and presentation settings.
8. **Nine anchors, continuous state.** The larger matrix improves validation without recreating a
   fixed emotion enum.

## Toolchain

- `claudex-loop`: lock and adversarially review this plan before any promotion or implementation.
- `autplay-development`: required for any later substantial AutPlay implementation or review/fix
  loop; future work must run from the repository root through the accepted harness workflow.
- `imagegen`: permitted for non-normative raster exploration and visual variants only. It is not a
  runtime renderer choice and does not replace deterministic Android visual evidence.
- The repository exposes `autplay-development` on the Codex project bench; no matching Claude-side
  project skill inventory was found during recon. Any future delegated build must not assume the
  same skill availability on both benches without a smoke test.

## Assumptions

1. AutPlay Face is an accepted product direction but remains `NOT_STARTED`; explicit activation is
   required. Source: `README.md`, `docs/design/AutPlay_Face_Product_Concept_v1.md`, and
   `docs/implementation/PLAN.md`.
2. Local Android actions and playback cannot require a synchronous personal-server trip. Source:
   repository `AGENTS.md` and the accepted Face product concept.
3. PostgreSQL owns server metadata, durable jobs, and derived-result lineage; filesystem/NAS is the
   initial immutable Vault backend. Source: repository `AGENTS.md` and PostgreSQL schema.
4. P12 already provides an isolated optional GPU project, approved model registry, deterministic
   bounded audio segmentation, fenced idempotent embedding writes, parallel model versions, and a
   read-only Vault mount. Source: `docs/adr/ADR-025-p12-isolated-gpu-enrichment-and-model-rollout.md`.
5. No production Face model is selected or approved, and real target-GPU quality/throughput evidence
   remains deferred. Source: ADR-025 and current implementation plan.
6. Perceived aliveness is 5/10 and all decision points in this plan were explicitly confirmed by the
   user during the claudex-loop interview on 2026-08-31.

## Risks and intentionally deferred implementation choices

1. A general music embedding model may be good for retrieval but poor at calibrated temporal
   musical-character axes. Model and interpreter quality require separate evidence.
2. Segment duration, overlap, keyframe reduction, axis calibration, confidence policy, and event
   detection thresholds require prototype measurements.
3. The physical timeline schema, API/sync envelope, Room projection, rendering stack, and exact
   Android frame budget are intentionally left to an explicitly activated implementation milestone.
4. Model artifact license, training-data implications, provenance, runtime compatibility, and RTX
   measurements remain prerequisites for activation.
5. Maps derived from distinct AudioVariants may differ. The implementation milestone must define
   source-variant selection and invalidation without conflating Recording identity with file bytes;
   all analyzed semantic layers remain bound to the selected exact lineage unless an explicit
   equivalence/aggregation contract is separately accepted and proven.
6. Long tracks and unusual structures require explicit keyframe/payload bounds and degradation.

These deferred choices are not authorization to begin code. The future milestone prompt must resolve
them before claiming implementation readiness for its own scope.

## Out of scope

- Implementing Android rendering, animation, Room state, API endpoints, PostgreSQL migrations, GPU
  handlers, model inference, or Web Admin controls in this planning task.
- Selecting or downloading model weights, using paid resources, or producing real RTX evidence.
- Inferring a listener's emotion, health, psychology, or sensitive state.
- Replacing artwork, adding a mouth/avatar/pet, or building a full-screen visualizer.
- Automatically processing the existing library or deleting completed maps.
- Starting P15, reopening P00-P14, modifying the P14 RC claim, or declaring AutPlay Face delivered.

## Proof required before future implementation completion

Each future slice owns its evidence and cannot defer a safety/privacy invariant to a later slice.

1. **Face Contract proof:** canonical serialization and schema tests prove finite bounds, unknown
   preservation, exact source lineage, one semantic idempotency key, result-hash conflict behavior,
   interpreter/model compatibility, atomic activation/rollback, authorization, state precedence,
   and degraded behavior. Model/interpreter evaluation uses an immutable, versioned, hashed dataset
   with recorded authorization/provenance and a versioned human-review rubric.
2. **Face Local proof:** Android tests prove offline playback with no synchronous server dependency,
   profile-isolated cache behavior, local invalidation versus later remote-revocation convergence,
   process death, seek/discontinuity, source/profile switching, fallback resource caps, stale-result
   rejection, and committed app reactions. Visual/device evidence covers nine anchors, intermediate
   blends, meaningful rest, reduced motion, TalkBack summaries, rotation/fold, large font,
   monochrome/contrast/accent states, supported widths, and physical-device frame performance.
3. **Face Timeline contract/integration proof:** real PostgreSQL concurrency and crash tests cover
   ingest-intent reconciliation, disabled-era no-autobackfill, enable/disable races at enqueue/claim/
   retry/lease-loss/publish, cancellation, exact grace eligibility, last-reference fencing,
   interpreter activation, replay conflict, tombstone/quarantine, deletion/export/retention/GC, and
   rollback-safe parallel versions. Every staged sidecar failure point is injected and reconciled,
   including pre/post rename, fsync, metadata commit, orphan, missing, and stale staging cases.
   Required race sequences include `finalize while enabled -> disable before reconcile -> enable`
   and `queued/retry -> disable -> enable`, with crashes and duplicate reconciliation proving one
   claimable successor admission, preserved provenance, and no disabled-era implicit backfill.
4. **Face Timeline runtime proof:** CPU isolation tests prove API/CPU imports and images remain free of
   GPU/CUDA dependencies. Real approved-model evidence proves temporal fit, payload bounds, bounded
   GPU operation, queue p95/tracks-per-hour/peak VRAM, and safe behavior when GPU/model/interpreter is
   unavailable before the feature can default on or be described as available.
5. **Face Operations proof:** durable backfill tests prove fixed watermark accounting, bounded batch
   and payload limits, crash recovery, pause/resume/cancel, generation fencing, fairness/no
   starvation, exact lost-response replay, and no automatic older-library scan on toggle. Admin/API
   tests prove CSRF/authz, command receipts, stable reason codes, redacted audit/metrics/diagnostics,
   bounded cardinality, truthful counts/status, and safe controlled rollout/rollback.
