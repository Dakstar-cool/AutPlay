# Owner-verified recommendation policy v2

The catalogue's `PROVISIONAL` identity is intentional: explicit owner upload creates a resolved
user-track reference, not a globally reconciled Recording. Do not bulk-promote these identities.
P11 v1 required ACTIVE and offline LOCAL, which excluded this catalogue entirely.

`cpu-baseline:2` accepts PROVISIONAL only with an owned resolved reference, its current applied
USER review (CREATE_RECORDING or ACCEPT, bound to this owner/reference/Recording), and a canonical
VALID audio variant. Audio must satisfy the existing playback authority: an active owned library
entry, no Recording redirect, COMMITTED object and AVAILABLE, verified LOCAL_FILESYSTEM replica.
Historical owner uploads need not have an acquisition record; playback uses the library ACL.
Dislikes, exclusion, missing audio, foreign ownership and merged/deprecated identity still fail closed.

Offline packs can rank such owned VAULT tracks. They do not claim that the device has downloaded
them: Android's existing localCandidates/baseOrder/rerank intersects the pack with local availability.
The public RAW_JSON pack remains version 1. No Android protocol change or identity migration is needed.

The input document is schema 2, with a declared 1024-track selection bound and the existing stable
most-recent ordering. This is the complete frozen candidate universe for Sona, not an adapter-time
truncation. Owner decision and audio variant IDs are frozen into the snapshot and respective hashes.
V1 pipeline manifests, 5000-track snapshots and replay remain readable with their original rules.
The native dataset adapter verifies either version, including original ranking replay.

2026-11-11 read-only production comparison: v1 selected 5000, admitted 0, returned 0; v2 selected
1024, admitted 1015, returned 25 on recommendations, home and offline_pack. Nine candidates lacked
verified canonical audio. SQL times were 0.315 s and 0.262 s in this observation, not a load benchmark.
Production data and identity statuses were not changed by that comparison.

This resolves candidate eligibility only. Old source-provenance gaps remain quarantined, and real
recommended impressions/finalized listens plus real audio embeddings are still necessary for training.
