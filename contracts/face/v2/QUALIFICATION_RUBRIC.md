# Face qualification rater rubric v1

This is the human annotation task for Revision 15 §6.3–6.4. A rater describes
the perceived **musical character** of the recording, never their own mood or
preference. The operator gives each rater a random study pseudonym and obtains
an explicit `FACE_QUALIFICATION_ANNOTATION_V1` purpose consent/contract before
any rating. The name-to-pseudonym map stays in a separate operator store.
Participant account IDs, playlists and library data never enter this study.

For each of the twelve sealed, non-overlapping ten-second windows, rate every
axis with one integer from `-3` through `+3`. `-3` strongly expresses the first
pole, `+3` strongly expresses the second pole, and `0` is balanced or genuinely
ambiguous. Values `-2/-1/+1/+2` express intermediate strength. Rate the whole
track separately with the same scale. Listen again if needed; do not infer a
missing section from a title, genre label or another rater's answer.

| Axis | First pole (`-3`) | Second pole (`+3`) | Distinguishing cue |
| --- | --- | --- | --- |
| `POSITIVE_MELANCHOLIC` | Positive, uplifting | Melancholic, sorrowful | Perceived emotional color of the music, not the listener's feelings. |
| `CALM_ENERGETIC` | Calm, low activation | Energetic, high activation | Pace and movement, regardless of whether the mood is pleasant. |
| `SOFT_AGGRESSIVE` | Soft, gentle | Aggressive, forceful | Attack, density and sonic force, independent of loudness alone. |
| `LIGHT_DARK` | Light, bright | Dark, heavy | Timbral and harmonic color, independent of tempo. |
| `RELAXED_TENSE` | Relaxed, resolved | Tense, unsettled | Harmonic and rhythmic tension, independent of energy. |
| `DIRECT_ATMOSPHERIC` | Direct, foregrounded | Atmospheric, spacious | Salience of a lead/gesture versus texture and spatial wash. |

For each window, record confidence `0` (guess), `1` (low), `2` (moderate), or
`3` (high). This is **rater** confidence, not the model confidence used for ECE.
Even a low-confidence rating is retained as raw evidence; the reliability gate
uses all valid raw ratings and never silently removes difficult windows.

After hearing the full track, independently mark meaningful section transitions
at decoded-source sample indices. A transition is a perceptible change in the
music's section or sustained character, not every beat, note, or momentary hit.
Do not see model suggestions or other raters' marks before submitting. Empty
transition lists are allowed as raw submissions but cannot create a vacuous
qualification PASS: the aggregate final set needs at least 60 reference
transitions across at least ten tracks.

The frozen reference-collation rule converts each decoded-sample mark to
`floor(sample_index * 1000 / decoded_sample_rate_hz)` milliseconds. Candidate
pairs from different raters within 3,000 ms are considered by increasing time
difference, then earlier/later time and pseudonym byte order. Each pair may add one
unused mark from each other rater in chronological order only while the whole
group spans at most 3,000 ms. A group needs a strict majority of the current
independent raters; its reference is the lower median millisecond. Each raw mark
can contribute to only one group, and exact duplicate references collapse.
Unpaired marks do not create reference transitions. The bound of 256 total
marks per track prevents unbounded collation work. This rule is fixed before
any final rater submission is viewed.

`qualification-rater-submission.schema.json` freezes one track submission. It
contains twelve sealed segment IDs, all six raw axis scores and confidences,
the track summary and independent transition marks. The strict Python codec
requires canonical bytes and exact sealed ancestry; it collates medians only
from at least three different pseudonyms with distinct **current** consent
receipts. The caller must verify those receipts against the independent rater
ledger. Raw ratings, audio and the identity map stay outside product PostgreSQL
and ordinary product backups. Consent withdrawal removes that rater's raw rows,
recomputes evidence and invalidates any dependent approval as specified in
Revision 15 §6.3.

The unwired `autplay.face.final-technical-report.v1` archive runs the frozen
evaluator itself. It records the exact fixture/candidate manifest hashes,
sealed track and segment ancestry, all model observations, transition and
blinded preference inputs, every one of the 10,000 draws per metric, fixed
threshold parameters, PCG64 seeds as lossless decimal strings, exclusions,
runtime versions and SHA-256 hashes of the participating Python scripts.
Its RFC 8785 canonical bytes and SHA-256 digest are technical evidence only.
They confer no fixture-rights approval, rater-consent approval, signed
qualification approval or activation authority. The archive belongs in the
bounded external evidence store, with only its digest in product metadata.
