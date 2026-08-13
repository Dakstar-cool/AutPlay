# ADR-015: Immutable identity decision evidence and history

**Status:** Accepted

**Date:** 2026-08-13

**Owners:** AutPlay Identity Catalog and persistence maintainers

**Approved:** 2026-08-13

**Approved proposal revision:** `c108c109d8eb1ab71631ea79e831a20e6cc6811bff5264cb6d1bb38f7433ac71`

## Context

The narrower Track Identity specification requires every automatic and manual identity decision to remain reproducible from immutable versioned evidence. The current PostgreSQL reference model stores only an import-specific candidate subset in `importing.match_candidate`, uses a different decision vocabulary, and cannot retain a general supersession history.

This mismatch was tracked as P00-D003. This accepted decision resolves its design prerequisite; executable P02 persistence remains separately `NOT_STARTED`. The approved normative and physical delta is defined in [`P00-D003_CHANGESET.md`](../implementation/P00-D003_CHANGESET.md).

## Decision drivers

- Preserve F-014 and F-015: fingerprint is evidence, and unresolved identity is safer than a false merge.
- Preserve frozen F-016 unchanged: no applied auto-match before its benchmark/calibration gate.
- Explain an old decision without calling an external provider or recomputing with current code.
- Support import rows, local audio, external references, UserTrackRef and Vault/audio queries through one Identity Catalog model.
- Keep resolution separate from global Recording merge/split.
- Keep core FK/query/status fields relational while retaining unknown versioned feature values safely.
- Make re-score, manual review and rollback append-only.

## Options considered

| Option | Benefits | Problems | Result |
| --- | --- | --- | --- |
| Extend `importing.match_candidate` | Smallest immediate SQL diff | Import-only ownership, cascade retention, mixed candidate/decision states, no general lineage | Rejected |
| One generic JSONB event ledger | Flexible payload evolution | Weak FK/range/state enforcement and expensive current/history queries | Rejected |
| Identity-owned registries, activation log, decisions and candidate evidence | General, queryable, reproducible and constraint-friendly | Adds tables, triggers and an inventory update before P02 | Accepted |

## Decision

The physical reference model will:

1. Replace `importing.match_candidate` with six Identity Catalog tables:
   - `identity.matcher_release`;
   - `identity.calibrator_release`;
   - `identity.threshold_set`;
   - `identity.match_policy_activation`;
   - `identity.match_decision`;
   - `identity.match_candidate_evidence`.
2. Make every row in those six tables append-only. A decision is inserted together with its exact candidate count and aggregate evidence hash; late candidate insertion, re-score and review cannot mutate the sealed snapshot. Matcher/threshold release and activation metadata never embeds benchmark datasets: only immutable report hashes/provenance are retained.
3. Use backward `supersedes_decision_id` links. A predecessor is never updated merely to point to its successor; the conceptual `superseded_by` value is obtained by the inverse relation.
4. Keep `importing.import_entry` as a mutable workflow projection with `current_match_decision_id`; a database trigger verifies query identity, execution mode, state and selected target. Import payload retention clears bounded raw columns instead of deleting the referenced query envelope or Identity Catalog history.
5. Store the complete decision field set, sanitized query snapshot, top-two evidence, origins, actor, versions, execution mode and idempotency identity. A shadow evaluation can never be `AUTO_MATCH` or `NO_MATCH`: ordinary ranked output uses `REVIEW_REQUIRED`, while genuine hard-integrity and unavailable-evidence outcomes remain `INTEGRITY_CONFLICT`/`DEFERRED_EVIDENCE` without projection.
6. Store all candidates for a decision independently from the selected candidate and preserve unknown feature keys.
7. Keep matcher/calibrator/threshold definitions immutable and record activation/rollback as append-only events.
8. Create no active policy event in the initial schema. `AUTO_MATCH` exists only in applied SYSTEM decisions and requires the exact policy scope to be activated from benchmark evidence plus all threshold, tier, margin and hard-conflict gates.
9. Treat `SHADOW` and `APPLIED` as execution modes. A shadow prediction cannot update UserTrackRef, Recording, import selection or catalog state.
10. Keep manual review outcome separate from the five resolver states. Manual review is always applied, never encoded as `AUTO_MATCH`, and updates its owner projection atomically without implying a global Recording merge.
11. Serialize policy activation/deactivation and applied auto decisions per policy scope, and enforce single-chain decision and activation supersession with database uniqueness constraints.

## Frozen-decision boundary

This decision does not resolve P00-D004 and does not reinterpret F-016. In particular, it does not declare T4 deterministic reuse to be a pre-benchmark exception. It can retain T4 evidence and a shadow prediction; it cannot activate or apply that path until the separate frozen-decision conflict is approved.

## Consequences

### Positive

- Every historical decision remains explainable after model, threshold, provider or import-retention changes.
- P02 can enforce named persistence constraints against one coherent contract.
- P10 can add matcher behavior and benchmark evidence without redesigning history storage.
- A manual resolution, an automatic resolution and a global merge remain distinct audited actions.

### Negative

- The reference inventory grows from 52 to 57 tables and from 48 to 53 explicit indexes.
- The reference DDL gains three helper functions and eight triggers.
- Current-state queries require a projection or latest-lineage query rather than mutation of historical rows.
- P02 implementation remains a separate, explicitly requested phase after the coordinated source changes validate.

## Compatibility and migration

There is no production database or executable P02 schema yet, so this decision updates the clean-install reference contract before its first Alembic implementation. No data migration is authorized by this decision.

If a database were ever created from the old draft DDL, conversion would require an explicit expand/backfill/verify plan rather than dropping candidate history. That situation is outside this decision and must stop for separate review.

## Acceptance record

- The user explicitly approved this ADR and proposal revision `c108c109d8eb1ab71631ea79e831a20e6cc6811bff5264cb6d1bb38f7433ac71` on 2026-08-13.
- The frozen proposal revision was independently recomputed from every canonical manifest record before normative application.
- The structural SQL probe passed against pinned PostgreSQL 18.4 with pgvector 0.8.6.
- Coordinated source, reference-DDL, Markdown and scope validation is required before the dedicated local change-set commit.

## Approval effect

Approval authorizes only the coordinated normative files enumerated in the change set and one dedicated local change-set commit after validation. It does not authorize Alembic, SQLAlchemy mappings, P02 tests, API/matcher behavior, P00-D004, a P02 phase commit, push or deployment.
