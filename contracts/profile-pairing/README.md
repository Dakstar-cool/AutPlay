# Profile and pairing contracts

These Draft 2020-12 schemas describe the proposed Product M5A profile/pairing protocol. They are
`DRAFT_NOT_IMPLEMENTED` until M5B implementation evidence exists. They do not alter P04 sync events.

Canonical hashes use RFC 8785 JSON serialization followed by lowercase SHA-256. UUIDs are lowercase
canonical strings. Timestamps are RFC 3339 UTC values. Secret-bearing schemas are marked
`x-autplay-sensitive: true` and are forbidden from logs, diagnostics and exports.
