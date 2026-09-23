# Face v2: source presentation map foundation

`SourcePresentationMapV1` binds one encoded-source SHA-256, decoded sample
rate/count, exact decoder and probe versions, observed trim/delay/padding, and
at most 256 ordered half-open presentation segments. Its digest is SHA-256 of
RFC 8785 canonical JSON bytes. The schema freezes the wire fields; Python and
Kotlin additionally reject duplicate keys, unknown edits, noncanonical bytes,
overlapping segments, out-of-range source samples and invalid trim totals.

Within a segment, `sample = source_start_sample + floor((position_us -
presentation_start_us) * decoded_sample_rate / 1_000_000)`. Negative or
out-of-range positions, gaps, the half-open end boundary and a source,
decoder or probe mismatch yield neutral output. No network or allocation is
needed for the Kotlin binary-search lookup. The checked-in AAC edit-list
fixture has identical canonical bytes and digest in Python and Android JVM
tests: `d28b8c2322def220d3b610d9e1b182f2364590d69830e752b4c11847ede31630`.
The VBR MP3 fixture records encoder delay/padding and a second seek segment;
both runtimes agree on its 602 bytes and digest
`2f0cdfe40210d20e521d2b4ae524fb1659055235ebd2bbecfb29bcb674a55143`.

This is a pure v2 identity foundation. Face v1 remains unchanged.

`FaceTimelineIdentityV2` now freezes the decoded-sample timebase, exact source
and map digests, sample rate/count, embedding/interpreter manifests,
preprocessing, calibration and execution-profile digests. Its semantic key is
SHA-256 of `b"autplay.face.semantic-key.v2\0" + JCS(identity)`. The Python and
Kotlin golden key is
`12b13bf6cc0d2d4f71aa014d7a4b89e27986e489dfa22135d55ca0d58be4cce4`.
Both decoders require the identity's source, rate, count and map digest to match
the exact separate map bytes.

`FaceTimelineV2` embeds that map and identity, a track character, at most 4,096
strictly increasing decoded-sample keyframes, and at most 4,096 events ordered
by `(sample_index,event_type)`. No keyframe at source sample zero is required
when an edit list trims the opening samples. Both codecs require exact canonical
JSON bytes, a 1 MiB bound and depth at most 12. Result hash is SHA-256 of
`b"autplay.face.timeline-result.v2\0" + JCS(timeline)`. Its synthetic golden
result is
`b0f49f7565e575b90c890a06fa2494d7e28a92371d33b725903985497e387b17`.
The content type is `application/vnd.autplay.face-timeline.v2+json`.

These pure Python/Kotlin codecs do not sign a projection or authorize access.
Server publication, capability-gated sync, Room v18, playback integration,
quality qualification and production selection remain pending.

`projection-envelope.schema.json` reserves the signed v2 upsert shape for
`FACE_PROJECTION_V2` clients. It binds the exact timeline identity/result/map,
owner/profile, activation/policy/redirect generations, server identity and
authorization interval. The signed `required_role_cardinality` gives Android
the six expected role counts from the activation manifest; the sorted
`artifact_policy` must contain exactly those entries. The verifier will
recompute both policy digests, the minimum positive lease and strictest output
disposition before checking the ES256-P1363 signature over
`autplay.face.projection-lease.v2\0 || JCS(envelope without signature_b64url)`.
The authorization interval must equal the signed lease and be at most seven
days. `projection-tombstone.schema.json` reserves the separate no-timeline
sync payload carrying the projection ID and superseding generation. The Python
`face_projection_v2` and Android `FaceProjectionV2Codec` verifiers check exact
bindings, role counts, both policy digests, minimum lease, strictest
disposition, signed interval and P-256/P1363 signature against one checked-in
signed fixture. Both are pure and unwired. `PROJECTION_SYNC.md` freezes
capability, download, lease-anchor and neutral-selection behavior. Current
license/source authority, sync capability filtering, Room v18 and the
authorized download endpoint remain pending.

The offline [development bake-off selector](BAKEOFF_SELECTION.md) freezes the
six-axis normalized-margin and tie order before model comparisons. It is a
non-activating M3 component and does not supply a quality approval.

The [human qualification rubric](QUALIFICATION_RUBRIC.md) and
`qualification-rater-submission.schema.json` freeze twelve-window raw ratings,
the six axis anchors, confidence and independently marked transitions. The
strict Python preflight binds each submission to one sealed fixture/track and
requires at least three distinct current rater grants before calculating raw
reference medians. Fixture storage, consent ledger, annotation UI and final
quality approval remain separate pending work.
