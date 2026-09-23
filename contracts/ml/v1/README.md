# ML Revision 15 contract map

The approved implementation authority is
[`PLAN_REVISION_15.md`](../../../docs/operations/ml/PLAN_REVISION_15.md). This file locates the
frozen boundaries and prevents a partial implementation from being mistaken for an activated
product. Historical Face Contract v1 remains in `contracts/face/v1`; it is not the production
Face v2 envelope.

| Boundary | Frozen plan section | Initial executable artifact | Activation rule |
| --- | --- | --- | --- |
| Required Face artifact set and policy list | 5.1, 7.1 | `server/src/autplay/application/face_artifact_policy.py`; `tests/fixtures/face/artifact-policy-v1` | Exact complete roles; every current decision `APPROVED`; positive minimum lease. |
| Face v2 decoded-sample timeline | 7.1, 7.2 | [`contracts/face/v2/README.md`](../../face/v2/README.md); Python/Kotlin map, semantic-key and result codecs with cross-language golden vectors | Pure result contract; no publication, projection or playback activation. |
| Face v2 projection lease | 7.4, 8.1 | Disabled upsert/tombstone schemas; [sync contract](../../face/v2/PROJECTION_SYNC.md); unwired Python/Android signature and policy verifiers with one signed vector | Current license/source checks, capability sync, authorized download and Room v18 are pending. |
| Generic artifact/license and device-key generation | 5.1, 7.1 | Additive Alembic `0061`; typed SQLAlchemy inventory, internal generation-fenced review/freeze adapter and disposable-DB tests | No legacy metadata is promoted from `LEGACY_UNREVIEWED`; no admin review operation is exposed before operation-bound WebAuthn authority. |
| ML artifact review control plane | 7.5 | [`CONTROL_PLANE.md`](CONTROL_PLANE.md), disabled review-command JSON Schema | Current admin session, CSRF and one-time operation-bound WebAuthn must precede any review mutation. |
| Face self-service analysis policy | 7.5 | Disabled self-only policy-command JSON Schema; unwired transaction-bound policy writer | Authenticated principal is the only target; safety inhibition is a separate OWNER/ADMIN operation. |
| ML operation-bound step-up | 7.5 | [`STEP_UP.md`](STEP_UP.md); additive `0061` credential/challenge/receipt storage | Two-key attestation, fresh per-use Android grant and WebAuthn admin proof remain disabled until transactional validation is complete. |
| Sona serving-use consent API | 7.5, 9.2, 10.4 | [`CONTROL_PLANE.md`](CONTROL_PLANE.md), disabled self-only consent-command JSON Schema | Grant needs independent R1C purpose plus fresh participant step-up; deny/withdraw needs only a valid session. |
| Sona owner cohort and breaker administration | 7.5, 10.4 | [`SONA_COHORT.md`](SONA_COHORT.md), disabled generation-fenced cohort command schema | Explicit owner allowlist, fresh Admin WebAuthn and live approval/consent/license authority; half-open probes cannot be skipped. |
| GPU admission | 5.1, 11 | Alembic `0062`; [`GPU_ADMISSION.md`](GPU_ADMISSION.md) | Sona serving priority; Face yields at bounded batch boundaries. |
| Native Sona capture and restore fences | 7.1, 10.1 | Additive Alembic `0063`; [`SONA_CAPTURE.md`](SONA_CAPTURE.md) | Capture only after privacy/restore authority exists; shadow never serves. |
| Fixed-point serving score | 7.1, 10.4 | `server/src/autplay/domain/score_e8.py`; `tests/fixtures/ml/score-e8-v1.json` | Pure v2 codec and vectors; current P11 scoring is unchanged. |
| Nested serving item fields | 7.1 | [`CANONICAL_JSON_LP.md`](CANONICAL_JSON_LP.md); `tests/fixtures/ml/canonical-json-lp-v1.json` | Pre-SQL typed LP codec only; SQL parity and v2 cutover pending. |
| Work and decision state graphs | 7.1, 10.1 | [`LIFECYCLE.md`](LIFECYCLE.md); `server/src/autplay/domain/ml_lifecycle.py` | Pure transition guards; SQL state mutation fences pending. |
| Sona execution profile | 10.2 | [`SONA_EXECUTION_PROFILE.md`](SONA_EXECUTION_PROFILE.md); `tests/fixtures/ml/sona-execution-profile-v1.json` | Exact GPU/runtime identity; pure codec is not live process attestation. |
| Face activation and qualification authority | 6, 7.1 | Dormant Alembic `0064` and typed ORM | All six musical axes plus live fixture/rater/license authority; no activation path is wired. |
| Face timeline, fan-out and lifecycle | 7.1-7.5, 9.1 | Planned Alembic `0065`; pure sponsor lineage guard and unwired canonical-source metadata reader | Exact semantic/source/owner/epoch fences; bounded fan-out of 100. The reader does not prove Vault bytes or authorize publication. |
| Face sync and Android projection | 7.4, 8 | Planned Alembic `0066`, Room v18 | Capability-gated; authorized exact bytes before playback; neutral fallback. |
| Sona R1C serving and attribution | 9.2, 10.4 | Planned Alembic `0067` | Separate serving consent and approval; v2 cutover; committed served truth. |

## Current contract decisions

- `FACE_ARTIFACT_POLICY_LIST_V1` sorts role ASCII bytes then raw SHA-256 bytes. The required-set
  and policy-list JCS documents use separate NUL-delimited SHA-256 domains. Their checked-in
  empty, minimal, multi-role and shared-hash/two-role vectors freeze bytes and digests.
- Required role cardinalities come from the signed activation manifest. Encoder and interpreter
  counts are positive. Optional zero counts are valid only when the semantic key proves no
  separate artifact; that proof is a future activation-validator responsibility.
- A license decision's state is not encoded in the policy list; the transactionally current
  decision is a separate live authority. A stored policy digest cannot override a later denial.
- `0061` migrates derivable legacy embedding artifacts to `LEGACY_UNREVIEWED` only. Conflicting
  content metadata, oversized manifests, and conflicting legacy license claims are recorded as
  `CONTENT_METADATA_CONFLICT`, `MANIFEST_OVERSIZE`, and `LICENSE_CLAIM_CONFLICT`; none grants a
  review decision. The current projection advances only through an append-only decision with the
  next sequence and generation. `LEGACY_UNREVIEWED` is permitted only for the initial decision.
  A migration issue may name a hash with no artifact row; when its artifact row does exist, the
  issue-insert trigger takes a key-share lock that conflicts with a review or activation read's
  artifact-row update lock. This closes the concurrent issue-insert gap around a clean check.
- The internal artifact review writer requires a caller-owned transaction, a live `OWNER`/`ADMIN`
  reviewer, an exact current generation and no unresolved migration issue. It appends one immutable
  successor; the database trigger derives current state in the same transaction. It is not wired to
  an HTTP route or runtime composition. The future admin caller must verify and consume an exact
  WebAuthn operation challenge and write its idempotent audit receipt in that same transaction.
  A Face tuple read locks every current decision through its caller's activation transaction,
  then derives the minimum positive offline lease and strictest disposition from those live rows.
- `0061` refuses downgrade after a reviewed decision, an unresolved migration issue, a new
  unlinked artifact, or any Face/Sona release or step-up record. A clean disposable database can
  still downgrade to base and re-upgrade exactly.
- `SONA_P11_TEACHER_V2` is implemented and binds fitted temperatures and calibration ancestry.
  The `0026` reconstructed rehearsal remains permanently ineligible for native quality approval.
- Public recommendation callers cannot select Sona. P11 remains the only serving pipeline until
  the distinct R1C purpose, quality, cutover, activation and owner-cohort gates pass.
- R1C serving intent uses the exact separate purpose `SONA_R1C_DESCENDANT_MODEL_SERVING_V1`.
- `SCORE_E8_V1` accepts only finite binary64 values within the exact signed-int64/1e8 range,
  preserves legacy `round(raw, 8)` before Decimal half-even conversion, normalizes negative zero,
  and renders exact JSON number tokens without a float round trip. Golden vectors include binary64
  neighbors, halfway cases and signed bounds. The existing raw-score generator preselection remains
  ahead of quantization; serving v2 has not been cut over.
