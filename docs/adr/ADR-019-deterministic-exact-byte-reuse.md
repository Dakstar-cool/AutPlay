# ADR-019: Deterministic Exact-Byte Reuse Is Not Probabilistic Auto-Match

- Status: Accepted
- Date: 2026-08-16
- Decision owner: explicitly approved by the user as P00-D004 Variant A on 2026-08-16

## Context

Frozen F-012 requires immutable SHA-256-addressed Vault bytes, while F-014 and F-016 keep
fingerprints as versioned evidence and disable probabilistic Recording auto-match until its labeled
benchmark and calibration gate passes. The physical schema already enforces one VaultObject per
SHA-256 and one AudioVariant per VaultObject.

The unresolved P00-D004 question was narrower: when server-verified bytes already resolve through
one valid AudioVariant to one active Recording, may AutPlay re-reference that existing chain without
waiting for the probabilistic matcher benchmark? Treating exact cryptographic byte equality as a
scored match would either force pointless manual review or tempt an implementation to fabricate a
calibrator/threshold activation.

## Decision

1. F-016 continues to govern probabilistic candidate selection from metadata, external identifiers,
   duration, fingerprint or combined scored evidence. It does not govern byte-level CAS
   idempotency.
2. A server-verified exact SHA-256 and byte size may deterministically re-reference an existing
   `VaultObject -> AudioVariant -> Recording` chain only when all of the following hold:
   - exactly one VaultObject exists and is `COMMITTED`;
   - exactly one non-deleted AudioVariant exists and is `VALID`;
   - its Recording resolves through redirects to exactly one active canonical Recording;
   - an `AVAILABLE` replica exists and no corruption, quarantine, integrity incident or pending
     merge/split makes the chain ambiguous;
   - the authenticated operation establishes authorization independently of the hash, storage key
     or another user's access.
3. Deterministic reuse is an idempotent re-reference, not `AUTO_MATCH`, candidate scoring, merge or
   catalog mutation. It never creates, merges, reassigns or silently edits a Recording and never
   updates canonical metadata from uploaded tags.
4. A conflicting target Recording, ambiguous redirect, missing/corrupt replica, quarantine or
   material metadata/version conflict fails closed to an integrity/quarantine/review path. Exact or
   near-exact fingerprint evidence never qualifies as exact bytes.
5. P06 owns only technical Vault reuse and its redacted audit/result codes. It does not create or
   mutate `UserTrackRef` or import owner projections and does not relax the existing identity-policy
   triggers. P10 owns any later auditable deterministic owner-projection representation through a
   non-destructive migration; it must not use a fake calibrator, fake confidence or probabilistic
   `AUTO_MATCH` row.
6. The accepted operation reports stable outcomes such as `REUSED_EXACT_BYTES`,
   `T4_NOT_APPLICABLE`, `T4_INTEGRITY_CONFLICT` and `OBJECT_QUARANTINED`. Metrics contain no hashes,
   user IDs, Recording IDs, raw paths or storage keys.

## Consequences

- Duplicate delivery converges on one immutable physical object and one valid technical variant
  without weakening false-merge protection.
- A catalog integrity defect cannot spread silently: eligibility is complete and fail-closed.
- Physical deduplication never grants cross-user access or exposes an existence oracle.
- T0-T3 and every probabilistic Recording resolution remain disabled until their original F-016
  benchmark/activation gates pass.
- P06 can complete deterministic ingest/dedup while P10 retains ownership of user/import identity
  resolution and its history model.

## Rejected alternatives

- Technical object reuse while always hiding the already-valid variant/Recording link: safe but
  creates unnecessary review work and conflicts with the idempotent existing-blob architecture.
- Putting T4 through the probabilistic benchmark activation machinery: cryptographic byte equality
  is not a confidence score and must not be represented with fabricated calibration artifacts.
- Treating fingerprint equality, client-provided hash knowledge or another user's access as T4:
  these violate the identity and authorization boundaries.
- Creating or merging a Recording during duplicate upload: P06 does not own catalog identity
  mutation.

## Implementation ownership

P06 implements the strict technical eligibility predicate, dedup result, authorization-independent
CAS handling and integrity outcomes. P10 may later add a distinct immutable deterministic-resolution
record and owner projection after its own migration and tests. F-016 remains unchanged.
