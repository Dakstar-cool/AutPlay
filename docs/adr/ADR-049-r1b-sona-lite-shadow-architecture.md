# ADR-049: Sona-Lite as the R1B shadow architecture

Status: Accepted target architecture; model configuration remains experimental  
Date: 2026-09-04  
Owners: AutPlay recommendation, GPU and privacy boundaries

## Context

R1A froze temporal evidence, maturity, replay, retention and fallback semantics without selecting a
trainable model. The first R1B slice implemented that temporal foundation and an heuristic adaptive
shadow control. The user then clarified that the intended implementation is based directly on the
Sona architecture described in the Sona Technical Report v2 (`arXiv:2608.11015`), rather than an
heuristic adaptive ranker.

AutPlay does not have Yandex-scale traffic or catalog data. It must therefore preserve the Sona
architecture while reducing capacity and history bounds to something trainable and benchmarkable on
the existing RTX 3060 12 GB host.

## Decision

R1B targets `Sona-Lite`, a shadow-only single-model recommender with:

1. one encoder over the chronological normalized engagement-event sequence;
2. learned three-level Semantic IDs from an immutable tokenizer version;
3. one autoregressive Semantic-ID decoder for candidate generation;
4. one multi-head Ranking Module consuming the same encoded user state;
5. request-level next-token supervision plus offline teacher-distillation targets;
6. deterministic SID-to-canonical-Recording expansion followed by the existing mandatory filters
   and diversity/repeat authority.

The R1A temporal event and snapshot layer remains the model input/replay boundary. The aggregate
adaptive profile and heuristic adaptive ranker remain deterministic controls for evaluation; they
are not inputs to the Sona model and are not the target serving architecture.

The first prototype uses bounded 512-event histories and at most 1,024 candidates. Capacity,
encoder depth, beam width, tokenizer construction and ranking-head weights are immutable model
configuration, not mutable runtime flags. Changes require a new model/pipeline version.

The first trainable configuration is intentionally compact: a three-layer, 192-dimensional GRU
chronological encoder, a three-step autoregressive decoder and a four-head ranking MLP. The initial
export emits one deterministic greedy Semantic ID while ranking the bounded candidate set; broader
beam generation remains an artifact-versioned experiment. Semantic IDs are learned by a
deterministic three-level residual k-means tokenizer over an approved immutable embedding snapshot,
with code zero reserved for padding/unknown and hard-masked from decoder output.

PyTorch is confined to the independent `gpu/training` uv project and never enters the CPU server or
runtime-only GPU worker environment. The current reproducible training toolchain pins PyTorch
2.14.0, ONNX 1.22.0 and CPU ONNX Runtime 1.26.0 for local export verification, with the official
PyTorch `cu130` wheel selected only on Linux. Export uses fixed batch size one and ONNX opset 20 to
match the shadow runtime contract.

The model executes only in the isolated GPU process through an allowlisted immutable artifact.
Missing GPU, artifact, tokenizer coverage or model output always leaves P11 independently
available. R1B creates no served order or impression. Any serving activation remains a separate
explicit R1C decision.

## Consequences

- Generation and ranking can learn from the same raw sequence instead of consuming hand-engineered
  temporal scores.
- The temporal persistence already implemented remains useful and is not discarded.
- Initial quality may be data-limited; offline comparisons must include coverage and a non-neural
  P11/control baseline rather than assuming the paper's production uplift transfers to AutPlay.
- The Semantic-ID tokenizer and teacher are training artifacts and are absent from final serving
  except for the frozen track-to-SID mapping required for deterministic expansion.
- Owner isolation, replay hashes, privacy deletion, retention, availability, identity, Dislike,
  exclusion and impression truth remain application/database authorities outside model discretion.

## Evidence boundary

The first implementation checkpoint is the bounded model-facing request contract,
`sona-lite-shadow:1` manifest and multi-input ONNX CUDA adapter. The second checkpoint adds an
immutable request-level training example bound to the original temporal/P11 snapshots, observed
post-request labels, exact candidate set and offline teacher snapshot, plus shared tensor packing
and reference next-token, masked ranking and teacher-distillation losses. Shadow output is not a
training label.

These checkpoints establish architecture and objective semantics only. An immutable tokenizer
fitted on approved embeddings, training quality, a reviewed exported model artifact and an RTX 3060
benchmark remain required before R1B can pass. The tokenizer/model/export implementation does not
itself claim that an artifact has been trained or approved.

The fourth implementation checkpoint adds a deterministic owner-safe tensor dataset format,
content-addressed tokenizer materialization, bounded deterministic-schedule training, a pickle-free
checkpoint, training-provenance-bound ONNX export, and explicit latency-smoke evidence. Synthetic
fixtures are permanently marked `quality_eligible=false`; they may prove the local or RTX toolchain
but can never be promoted into the reviewed artifact or quality evidence required for R1B PASS.
The dataset retains only keyed owner-lineage deletion tokens, not owner UUIDs, and the tokenizer
verifies its canonical embedding snapshot hash before bounded residual mini-batch fitting.
The dataset also binds the tokenizer's active code count. Training and checkpoint loading require
the decoder vocabulary to equal that count plus reserved code zero, and latency smoke evidence
rejects both out-of-range codes and full Semantic IDs absent from the exact hash-bound tokenizer
mapping. Categorical embeddings and checkpoint payloads have fixed pre-allocation/file-size bounds;
every NumPy header is validated against its canonical shape and dtype before loading. ONNX graph
and sidecar publication is complete only after a hash-bound commit marker is present.

The fifth implementation checkpoint adds the shadow execution boundary. The CPU server builds and
hashes a strict bounded request envelope, resolves Semantic IDs only through the exact immutable
tokenizer mapping, and calls a loopback-only GPU worker. The worker accepts only a content-addressed
ONNX artifact whose bytes, model manifest, tokenizer hash and commit marker all match the configured
identity. Its response is parsed and hash-bound before any evidence can be attached to the existing
P11 request.

Shadow evidence is owner-scoped, one-time and immutable in PostgreSQL. Exact response replay remains
the persisted P11 response; algorithmic shadow replay requires the original request plus its exact
temporal and P11 snapshots and never substitutes current state. Worker/network/artifact/tokenizer
failure records a bounded degraded reason and leaves the P11 result byte-for-byte unchanged. The
shadow path creates neither `recommendation_item` rows nor impressions and cannot activate serving.
Synthetic checkpoint evidence remains `quality_eligible=false`; an approved quality dataset and
reviewed artifact are still required before R1B can pass its model-quality gate.

The sixth implementation checkpoint adds the quality-candidate and offline-evaluation trust chain.
Source, dataset, evaluation-evidence, and final artifact approvals are signed by one deployment-
pinned reviewer trust anchor. Evaluation admits only a canonical persisted-execution bundle for the
P11 request/snapshot/ranking and Sona Shadow evidence, canonical held-out outcomes and safety
evidence, plus exact raw ORT samples for every approved test request and measured iteration. The
server recomputes every evidence hash, latency summary, gate verdict, NumPy structure, and ONNX
runtime contract with the official ONNX checker before a passing report can participate in final
artifact approval. This protocol is implemented, but it does not manufacture approval: without an
authorized non-synthetic dataset and reviewer-signed inputs, no quality-eligible artifact exists.

The synthetic checkpoint-5 qualification is recorded in
`gpu/training/evidence/r1b-checkpoint5-20260904-02/run-evidence.json`. It verifies the exact artifact,
model, tokenizer and pipeline identities on the Tailscale-connected RTX 3060, the loopback-only
worker and SSH tunnel, one successful end-to-end `SonaShadowService` request, PostgreSQL one-time
attachment/retention cleanup, and unchanged P11 serving truth. This evidence proves execution and
safety boundaries only; `quality_eligible=false`, R1C remains inactive, and no user dataset or
production state participated.
