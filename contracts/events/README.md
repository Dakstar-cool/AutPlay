# AutPlay event contracts

`v1/` contains the Draft 2020-12 wire schemas for the P04 sync boundary:
device binding, client events, push, pull, bootstrap, status, and stable errors.
Event types and aggregate types are strings so newer clients can preserve
unknown values; a server must reject values it cannot semantically authorize or
apply. Additive object members are accepted and are covered by the event hash.

Known canonical listening, recommendation-impression, and direct-feedback
events use two-stage validation: first the generic `client-event` envelope,
then the specialized schema named by its dispatch annotation. Generated or
delivered recommendations are not impressions; the impression schema records
actual presentation. Preference, playlist, and playback domain events carry
optional attribution instead of creating duplicate generic feedback rows.

`request_hash` is the lowercase SHA-256 hexadecimal digest of the RFC 8785
canonical JSON representation of the immutable client event with
`request_hash` omitted.  It is an integrity/idempotency input, never an
authorization credential.

Validate the immutable v1 set with `uv run --frozen pytest tests/contract`.
