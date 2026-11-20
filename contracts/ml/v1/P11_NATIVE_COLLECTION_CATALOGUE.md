# Original P11 v2 catalogue

The production recommendation service selects the 1,024 most recently added
owner-visible tracks, breaking ties by recording UUID, and passes them to the
ranker in recording UUID order. An explicit catalogue owner setting and a
current independently verified consent revision enable an additional full
catalogue in the same repeatable-read transaction. Other requests continue
through the existing collection path.

| Raw collection envelope | Full catalogue | Representation |
|---|---|---|
| V1 | Unavailable | Original serving snapshot and ranking |
| V2 | Up to 5,000 tracks | Canonical JSON document and SHA-256 |
| V3 | Above 5,000 through 20,000 tracks | Canonical JSON length and SHA-256, zlib stream in base64 |

The V3 canonical catalogue has schema `P11_V2_FULL_CATALOGUE_V2`,
`max_tracks=20000`, the owner, interaction watermark and ordered P11 v2 track
documents, including the original decision and audio variant when present.
The expanded size limit is 32 MiB. The stored envelope limit is 16 MiB.
A 20,001st track, a byte limit or a serving-window mismatch records an
explicit catalogue rejection. The ordinary P11 response remains available.

Readers reconstruct the serving selection from the complete original and
validate its order and contents against the actual serving snapshot. Collection
status, consent revision, expiry, erasure and exact ranking replay remain
independent checks. Every raw envelope has `quality_eligible=false`. Recording
a raw envelope supplies no Source3 training admission.
