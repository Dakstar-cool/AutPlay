# Plan Review Log: AutPlay Face Resonance Lens

Phases 0-1 (recon and interrogation) complete. The plan was locked with the user on 2026-08-31.

- `PLAN_FILE=docs/design/explorations/AutPlay_Face_Resonance_Lens_Plan.md`
- `LOG_FILE=docs/design/explorations/AutPlay_Face_Resonance_Lens_Plan_Review_Log.md`
- `MAX_ROUNDS=5`
- Review sandbox: read-only
- Build authorization: not granted

## Round 1 — Codex

The plan is not yet safe enough to guide implementation. I did not treat the intentionally deferred
physical schema, API, Room, or rendering choices as defects; these findings concern missing
invariants or conflicts that must constrain those choices.

1. **No executable job contract exists for timeline generation.** The plan requires a durable
   timeline job, but ADR-025 permits only `ml.audio-embedding/v1`; unsupported enrichment work must
   not remain queued, and the current schema enforces `AUDIO_EMBEDDING`.
   Fix: Require the future milestone either to introduce an accepted namespaced job
   key/handler/schema contract or to make the timeline an explicitly bounded atomic output of the
   existing reviewed embedding job.
2. **Artifact identity conflicts with retry idempotency.** Including the producing attempt in
   timeline identity means a lease-expiry retry changes identity even when deterministic inputs and
   output are identical, contradicting exact-replay idempotency.
   Fix: Define identity solely from immutable semantic inputs and versions; retain job/attempt only
   as provenance, with exact replay returning the existing artifact.
3. **Ingest-to-enqueue has a crash gap.** “After Vault ingest” does not require job creation to be
   transactionally coupled to variant commit, so a crash can leave a valid playable variant
   permanently unanalyzed without any failed job to observe.
   Fix: Require job creation in the variant-finalization transaction or a durable outbox/reconciler
   that proves eventual idempotent enqueue.
4. **Disable, pause, cancel, and publication are not linearizable.** The plan defines desired
   outcomes but no setting generation or transaction fence, allowing enqueue/claim/toggle,
   cancellation/lease-expiry, model retirement, and source-quarantine races.
   Fix: Add a monotonically versioned analysis policy captured by each job and revalidated atomically
   at enqueue, claim, retry, and publish alongside cancellation, model eligibility, and source
   servability.
5. **Backfill is not a durable operation contract.** Batches, priority, pause/resume/cancel, and
   “stops future claims” do not define operation identity, worker-restart behavior, claim races,
   progress denominators, or starvation bounds.
   Fix: Require a durable backfill operation state machine with generation-fenced child jobs, exact
   cancel semantics, bounded fairness/aging, and crash-recovery tests.
6. **Semantic-interpreter activation has no lifecycle contract.** ADR-025 governs embedding-model
   provenance, benchmark, activation, rollback, and retirement—not the separately versioned
   interpreter that creates user-visible semantics. “Current activation” is therefore ambiguous for
   a model/interpreter pair.
   Fix: Require an immutable interpreter registry, compatibility relation, quality evidence,
   activation history, and atomic model-plus-interpreter selection/rollback.
7. **The Android projection key is insufficient.** The canonical artifact is
   variant/model/interpreter-specific, but the cache key names only server profile, Recording,
   result version, and hash; another AudioVariant or late response can drive the wrong media and
   timebase.
   Fix: Bind projections and active-playback acceptance to exact AudioVariant, source
   duration/timebase, model, interpreter/schema, preprocessing identity, activation epoch, and
   playback-generation token.
8. **Cross-user reuse lacks a complete authorization and revocation boundary.** Calling users
   “authorized” does not require owner-scoped authorization on every fetch or eviction after
   permission loss, deletion, logout, or profile disconnect; a server-wide backfill/count can also
   cross account boundaries despite OWNER/ADMIN not being cross-account superusers.
   Fix: Require current owner/object authorization for every projection query/download,
   owner-scoped backfill and counts, masked denial, and cache invalidation through
   profile/auth/variant tombstone epochs.
9. **“Atomic” sidecar publication is impossible as stated.** The plan allows a filesystem sidecar
   while promising atomic publication under a PostgreSQL lease fence; filesystem and PostgreSQL
   cannot share that atomic commit.
   Fix: If sidecars remain eligible, require staged write, checksum, fsync, same-filesystem rename,
   fenced metadata commit, and bounded orphan/missing-sidecar reconciliation.
10. **The semantic envelope is not stable enough to validate safely.** Axis IDs exist, but domains,
    neutral/missing meanings, confidence/abstention, timestamp basis, duration coverage, NaN/Inf
    rejection, monotonicity, duplicate events, and hard payload/keyframe maxima are undefined.
    Fix: Define a versioned canonical envelope with finite normalized domains, missing/unknown
    preservation, calibrated confidence and abstention, monotonic bounded time coordinates, strict
    maxima, and fail-closed fallback validation.
11. **Derived-data privacy and lifecycle are missing.** “Disable is not deletion” is sound, but the
    plan does not cover account deletion, last-authorized-reference removal, Recording merge/split,
    source quarantine, export, backup, retention, or sidecar garbage collection; existing privacy
    rules require owner projections to be purged and shared bytes retained only while referenced.
    Fix: Require explicit timeline visibility, tombstone, export, retention, merge/split,
    quarantine, and reference-counted GC rules subordinate to privacy deletion and rollback windows.
12. **The application-state contract is incomplete.** The accepted concept requires neutral idle
    plus play, pause, Like, and Dislike/skip; the plan omits explicit no-active-media ownership and
    does not say reactions occur only after authoritative local mutation/outcome, so optimistic Like
    feedback can falsely imply success.
    Fix: Define the full state machine, owners and precedence—including idle, track change,
    seek/discontinuity, failure and stale events—and trigger action reactions only from authoritative
    committed outcomes.
13. **Fallback dynamics have no safe source boundary.** “Available energy, attacks, and pauses”
    could be implemented as a second decoder or unbounded per-frame analyzer, conflicting with
    Media3 ownership and the performance goal.
    Fix: Require the milestone to name a Media3-compatible, lifecycle-bounded source that performs
    no duplicate media fetch/decode and has explicit CPU/battery/update-rate caps.
14. **Operational evidence is too generic to diagnose failures.** Queue state and metrics do not
    define audit actions, lost-response receipts, setting/backfill generations, or machine-readable
    reasons distinguishing disabled, paused, unsupported handler, missing model, revoked source,
    stale policy, cancellation, and GPU failure.
    Fix: Enumerate required command receipts, audit events, stable reason codes, and bounded redacted
    metrics keyed only by opaque job/attempt/model identifiers.
15. **Accessibility proof omits accepted requirements.** The plan covers widths, fonts, themes,
    monochrome, and decorative animation, but omits rotation/fold posture, supported accent
    palettes/contrast, and a concise TalkBack representation of product-useful musical state
    required or recommended by the concept.
    Fix: Add rotation/fold/accent/contrast gates and a stable, rate-limited semantic description
    while keeping per-frame animation decorative.

The simpler delivery path is to split the future work into: semantic/envelope and ownership
contracts; local fallback plus one accessible Now Playing theme; opt-in server timeline generation;
then Web backfill and model rollout. The accepted MVP does not require coupling all four surfaces
into one milestone.

Review was read-only; no files were modified and no tests were run because this was a
contract/design audit.

VERDICT: REVISE

### Plan author's response

All 15 findings were accepted as material and compatible with the user-locked product intent.

- Added a distinct `ml.face-timeline/v1` contract requirement instead of repurposing the closed
  P12 `ml.audio-embedding/v1` handler.
- Separated deterministic artifact identity from job-attempt provenance and added an ingest outbox
  or same-transaction intent plus reconciliation requirement.
- Added monotonic analysis-policy generations and publish-time revalidation across toggle,
  cancellation, model/interpreter eligibility, source servability, and lease races.
- Defined a durable generation-fenced backfill operation with snapshot/watermark progress,
  crash-safe state, fairness, and exact cancel behavior.
- Added immutable semantic-interpreter registry/compatibility/activation/rollback requirements.
- Strengthened Android projection/playback keys, per-fetch authorization, masked denial, and cache
  invalidation on auth/profile/source/activation changes.
- Made sidecar publication explicitly recoverable rather than transactionally atomic.
- Defined a bounded canonical semantic envelope with finite domains, abstention, timebase,
  monotonicity, unknown preservation, and fail-closed validation.
- Added privacy, export, deletion, merge/split, quarantine, retention, and reference-aware GC rules.
- Added the authoritative application-state machine, committed-outcome reactions, and stale-event
  handling.
- Bounded fallback behind a Media3-compatible seam with no duplicate fetch/decode.
- Added command receipts, audit generations, stable operational reason codes, and redacted metrics.
- Restored rotation, fold, accent/contrast, and rate-limited TalkBack evidence.
- Accepted the simpler four-slice delivery sequence while keeping every slice inactive until an
  explicit future prompt.

No finding was rejected.

### Review transport note

The reviewer CLI's WebSocket connection returned HTTP 403 more than once, but the CLI's built-in
HTTPS fallback completed Round 1 successfully in the same review session. Local CLI help and the
official product documentation exposed no supported transport-selection flag. For subsequent
rounds, the review therefore keeps the same session and accepts the proven HTTPS fallback instead
of changing network policy, upgrading tooling without evidence, or losing review context by
starting a new session.

## Round 2 — Codex

Most Round 1 findings were addressed, and the revision did not activate implementation. The reviewer
returned eight remaining material blockers:

1. **The idempotency identity was internally inconsistent.** Including result hash in identity let a
   nondeterministic retry acquire a different identity rather than conflict, while activation epoch
   appeared in only one identity definition. Required correction: one semantic idempotency key from
   exact source variant/timebase, model, interpreter/schema, and preprocessing; result hash is an
   integrity comparison and activation epoch is selection/provenance.
2. **Disable-versus-publication contradicted the policy-generation fence.** Disabling created a new
   generation while old running work was allowed to publish. Required correction: define a precise
   transaction cutoff, durably grace only work already running at that cutoff, reject later old-
   generation claims/retries, and test toggle/claim/publish races.
3. **The ingest intent could silently become automatic backfill.** An intent for every finalized
   variant could later enqueue the disabled-era library and its module/writer ownership was unclear.
   Required correction: capture enabled policy at finalization, require explicit backfill for
   disabled-era variants, and assign the fact to a named owner/permitted writer.
4. **Cache invalidation promises violated offline-first reality.** An offline device cannot know
   remote revocation, account deletion, quarantine, or activation changes immediately. Required
   correction: separate immediate local profile/logout invalidation from convergence on the next
   authenticated sync, while cached playback remains local, isolated, and non-authoritative.
5. **Last-reference removal did not fence running work, and rollback alone was treated as retention
   authority.** Required correction: fence pending intents, claims, and publication after the last
   authorized reference; retain owner-derived data only for a live authorized reference or a
   separately declared legal/backup basis.
6. **Global and temporal semantic layers could mix AudioVariant lineages.** `TrackCharacter` was
   Recording-scoped while the timeline was variant-specific. Required correction: bind all analyzed
   layers to one exact source lineage or accept and verify a distinct cross-variant equivalence
   contract before composition.
7. **The slice order allowed persistent timelines before mandatory lifecycle controls.** Required
   correction: Face Timeline must include minimum authorization, tombstone, deletion/export,
   retention, reconciliation, and safe activation; later operations may add backfill and richer UX.
8. **The completion proof matrix did not cover the accepted fixes.** Required correction: add
   per-slice evidence for policy races, reconciliation, interpreter activation, backfill recovery and
   fairness, sidecar failure points, last-reference fencing, deletion/GC, remote-revocation
   convergence, redaction, accessibility, real PostgreSQL concurrency/crash tests, and an immutable,
   versioned, hashed, authorized evaluation dataset.

Read-only review completed; no files were modified by the reviewer.

VERDICT: REVISE

### Plan author's response

All eight Round 2 findings were accepted.

- Replaced the conflicting identity definitions with one semantic idempotency key; result hash is
  now conflict/integrity evidence, while activation, policy, job, and attempt are provenance.
- Defined the disable transaction cutoff and durable grace eligibility for exactly the jobs already
  running at commit; a lost lease cannot use the exception to retry or republish.
- Assigned analysis intent ownership to the Jobs module and its permitted application writer;
  disabled-era variants create no latent intent and require explicit backfill.
- Split immediate local invalidation from authenticated remote convergence without adding a
  synchronous playback dependency.
- Added live-authorized-reference checks to intent, claim/retry, publication, and retirement; model
  rollback alone no longer authorizes retention of owner-derived data.
- Bound `TrackCharacter` and the temporal layers to one exact AudioVariant/source lineage and
  prohibited cross-variant composition without a separately accepted equivalence contract.
- Moved minimum safe activation and all required lifecycle/privacy controls into Face Timeline;
  Face Operations now owns explicit backfill, richer Web Admin UX, and expanded operations.
- Replaced the short completion list with a per-slice proof matrix covering concurrency, crash,
  failure injection, privacy, accessibility, redaction, model evidence, and backfill fairness.

No finding was rejected.

## Round 3 — Codex

The eight Round 2 corrections were present and compatible with repository invariants. One material
concurrency/lifecycle blocker remained:

1. **Disable/re-enable could strand enabled-at-finalization intents and paused jobs.** An intent
   captured generation G1 but was reconciled only while still current. Disable advanced policy and
   rejected old-generation claims/retries, while re-enable did not define how G1 intent/queued/retry
   work became eligible under G3. A finalized variant could therefore be skipped permanently if
   disable occurred before reconciliation. Required correction: preserve this as durable paused work,
   create or rebind an idempotent successor admission under the new enabled generation while
   retaining original provenance, and prove both affected race sequences with real PostgreSQL crash,
   race, and duplicate-reconciliation tests.

Read-only review completed; no files were modified by the reviewer and implementation was not
activated.

VERDICT: REVISE

### Plan author's response

The finding was accepted. Enabled-at-finalization intent, queued, and retry work now pauses durably
across `OFF`. Re-enable admits exactly one claimable successor under the new generation, supersedes
the predecessor admission, preserves original provenance, and remains protected by the semantic
idempotency key. Variants finalized during `OFF` remain ineligible except through explicit backfill.
The proof matrix now names both required crash/race sequences and duplicate reconciliation.

## Round 4 — Codex

The Round 3 correction is fully represented:

- Enabled-era intents and queued/retry work remain durably paused across `OFF`.
- Re-enable creates exactly one claimable successor admission, supersedes its predecessor, and
  preserves provenance.
- Variants finalized during `OFF` remain explicit-backfill-only.
- The proof matrix names both required PostgreSQL crash/race sequences and duplicate reconciliation.

The physical uniqueness/locking representation is appropriately deferred; the required behavior and
evidence gate are sufficiently constrained. No remaining material blocker was found.

Read-only review completed; no files were modified by the reviewer and implementation remains
inactive.

VERDICT: APPROVED
