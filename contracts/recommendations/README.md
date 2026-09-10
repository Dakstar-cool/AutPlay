# Adaptive recommendation contracts

`v1/` contains the accepted Post-MVP R1A contract for the additive multi-horizon recommendation
layer. These artifacts are executable design evidence only. They do not register a new serving
pipeline, add a PostgreSQL or Room migration, change a public recommendation DTO or activate R1B.

`contract-policy.json` is the machine-readable safety, signal, replay, privacy, retention and
evaluation boundary and is itself validated by `contract-policy.schema.json`.
`feature-policy.schema.json` defines the immutable evaluated policy shape;
exact candidate weights and thresholds are fixture inputs until R1B produces offline and shadow
evidence. The remaining schemas define normalized evidence, adaptive profile/snapshot, bounded
device-local delta and owner export shapes. The additive temporal snapshot is explicitly v2 and
does not reinterpret the existing P11 snapshot v1. RFC 8785/SHA-256 vectors separate original sync
request hashes from normalized evidence and cover every retained document boundary, the stable
offline impression key and the paged owner-export manifest.

The user accepted the contract and ADR-048 on 2026-09-03. Its status is
`ACCEPTED_RUNTIME_NOT_IMPLEMENTED`: acceptance freezes the contract but does not implement runtime
behavior. R1B must use a new immutable policy/pipeline identity and cannot start automatically.
