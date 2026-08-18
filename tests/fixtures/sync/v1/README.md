# Sync golden vectors v1

These bounded, non-personal JSON vectors express protocol semantics rather than
a sync engine implementation. `valid-cases.json` contains machine-readable
expected outcomes for duplicate/reorder/gap/conflict/tombstone/cursor/bootstrap,
unknown-value, size-limit, reset, and P00-D006/R1 cases. `invalid-cases.json`
contains schema rejection examples. `schema-examples.json` proves one valid
language-neutral instance for every public response/request schema beyond the
standalone client-event example embedded in push.

`user-interaction-*.json` freezes canonical logical listening,
actual-impression and direct-feedback validation, attribution, semantic
rejection and RFC 8785 hash outcomes. P07-P09 implementations consume these
cases; P11 preserves the request/rank/recording contract when candidate
generators change.
