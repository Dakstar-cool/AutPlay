# Face v1 contract

This contract is a pure integration seam. It does not activate analysis, select a model,
authorize access, or claim calibrated musical estimates. Existing neutral local Face remains
the runtime default until the separate Timeline/Operations gates are satisfied.

JSON schemas describe structure; both runtime validators additionally enforce these rules:

- UTF-8 input and canonical RFC 8785 timeline are at most 1,048,576 bytes; nesting is at most
  12 containers. Duplicate object keys, non-finite numbers and unknown structural fields fail.
- At most 64 distinct axis names across the entire document; each name is preserved, including
  extensions unknown to the renderer. Missing means unknown. An abstention has null value and
  confidence plus a bounded stable reason code. Zero is an estimate, never a missing-value code.
- Values use [-1, 1], confidence uses [0, 1]. Model scores are not automatically confidence.
  Any mapping/calibration belongs to the versioned interpreter manifest. Uncalibrated axes abstain.
- Source time is integer milliseconds within the exact variant's duration (1..86,400,000 ms).
  Keyframes are strictly increasing and, when present, start at zero. They hold absolute estimates,
  not deltas added to TrackCharacter. Empty keyframes permit a character-only result.
- Events are strictly ordered by (time_ms, event_type); unknown event types survive. Events and
  frames cannot exceed duration. Each collection has at most 4,096 entries.
- UUIDs use lowercase canonical 36-character form; SHA-256 uses lowercase 64-character hex.
  Integer wire fields use JSON integer tokens (no decimal/exponent spelling or booleans).

`semantic_key = SHA256(UTF8("autplay.face.semantic-key.v1\\0") + JCS(identity))`, where
`\\0` denotes one NUL byte, not two textual characters.
`result_hash = SHA256(UTF8("autplay.face.timeline-result.v1\\0") + JCS(timeline))`.
Identity includes the exact recording, audio variant, source bytes/duration/timebase, embedding
model and manifest, interpreter and manifest, and preprocessing digest. Changing any of those
changes semantic identity. Different result hashes under one semantic key are a conflict.

Projection binds that immutable result to a server profile, user and positive activation epoch.
Profile, owner, activation epoch, policy generation, attempt and playback generation never enter
the semantic key. A projection/hash is not an access capability; storage/publication must still
check live owner authorization, policy and retention. Runtime selection must additionally match
current profile, user, identity, activation epoch and playback generation before exposing a result.

Playback sampling uses source time (seek rebases it), binary-search interpolation, and no network
or storage on the frame path. Interpolate only estimates present at both endpoints; propagate
abstention/missing to neutral output, never invent confidence. TrackCharacter is a fallback only
when the timeline has no keyframes. Hash verification happens on attachment, not per frame.

Golden files in `tests/fixtures/face/v1` are synthetic contract vectors, not music-quality evidence.
