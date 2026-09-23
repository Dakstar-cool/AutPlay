# Face development candidate selector

`autplay.application.face_bakeoff` is an offline, pure selector for the Revision 15
development bake-off. It does not compute metrics, read audio, authorize an artifact,
or approve final qualification.

Each candidate supplies all six named axes, finite binary64 point estimates,
Krippendorff alpha, at least the directional pole counts, a reviewed manifest hash,
and measured target-GPU tracks/hour. A reliable axis passes only with alpha at least
0.50, at least 20 reference cases at each pole, coverage at least 0.90, Spearman
at least 0.45, directional balanced accuracy at least 0.65, and ECE at most 0.10.
Alpha below 0.50 stops the comparison because the annotation dataset requires repair.

For each passing axis, compute the four normalized margins in Revision 15 §6.2 in
binary64, then quantize each to six decimal places using decimal round-half-even on
the exact binary64 value. Store the integer number of millionths. The axis score
is the smallest canonical margin; candidate score is the smallest passing axis
score. Raw threshold checks precede quantization, so a subfloor point estimate
cannot round into a pass. The arithmetic mean ECE and throughput are likewise
compared as canonical integer millionths.

A six-axis candidate takes precedence over every limited research candidate.
Limited selection requires at least four passing axes and at least one of
`POSITIVE_MELANCHOLIC` or `DIRECT_ATMOSPHERIC`. Within a tier, order by larger
candidate score, smaller mean ECE, larger tracks/hour, then lexicographically
smaller lowercase manifest SHA-256. No candidate yields
`NO_DEVELOPMENT_CANDIDATE`. `SELECT_LIMITED_CANDIDATE` is research only and
cannot be unsealed for final qualification or count as a production Face PASS.

The selector currently requires complete finite metric reports. A metric
calculation that is undefined or non-finite must be represented by an explicit
failed report upstream; it cannot silently enter selection. Annotation reliability,
the golden final bootstrap protocol, datasets, signed approval and target hardware
evidence are separate M3/M7 deliverables.

`autplay.application.face_bakeoff_metrics` now supplies development point
estimates for one axis from complete preregistered segment observations. It uses
average ranks for ties in Spearman, the exact negative/positive reference and
prediction cutoffs, and ten confidence bins `[0,.1),...,[.9,1]`. Abstention is
zero-valued and zero-confidence for rank/ECE, is an error on directional cases,
and is excluded from coverage. The metric fails explicitly if either directional
pole is absent or a rank series is constant. Its `krippendorff_alpha` argument
must be independently derived from retained raw ratings under the fixture/rater
authority; this function does not establish annotation reliability or prove
that the input includes every preregistered segment. Final track-block bootstrap,
transition matching, blinded preference and single-candidate approval remain
separate gates.
