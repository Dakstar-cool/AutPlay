"""Exact ordinal Krippendorff alpha for complete Face rater units.

Uses Krippendorff's pooled-frequency ordinal distance and coincidence
normalization. Input is the retained raw integer rating for each segment,
never a median or a model output. Undefined chance disagreement fails closed.
"""

from __future__ import annotations

from fractions import Fraction


class FaceAnnotationReliabilityError(ValueError):
    """Raw annotations cannot establish an ordinal reliability value."""


def face_ordinal_krippendorff_alpha(units: tuple[tuple[int, ...], ...]) -> float:
    """Calculate alpha over complete, independent -3..+3 segment ratings.

    Each unit has at least three ratings; the caller verifies distinct current
    consent grants and sealed segment ancestry before assembling this matrix.
    The intermediate disagreement ratio is an exact rational number.
    """

    if type(units) is not tuple or not 2 <= len(units) <= 100_000:
        raise FaceAnnotationReliabilityError("invalid annotation unit count")
    pooled = [0] * 7
    unit_counts: list[tuple[list[int], int]] = []
    for unit in units:
        if (
            type(unit) is not tuple
            or not 3 <= len(unit) <= 20
            or any(type(value) is not int or not -3 <= value <= 3 for value in unit)
        ):
            raise FaceAnnotationReliabilityError("invalid raw annotation unit")
        counts = [0] * 7
        for value in unit:
            counts[value + 3] += 1
            pooled[value + 3] += 1
        unit_counts.append((counts, len(unit)))

    def ordinal_distance_four(left: int, right: int) -> int:
        between = sum(pooled[left + 1 : right])
        return (pooled[left] + pooled[right] + 2 * between) ** 2

    observed = Fraction(0)
    expected = 0
    for left in range(7):
        for right in range(left + 1, 7):
            distance_four = ordinal_distance_four(left, right)
            expected += 2 * pooled[left] * pooled[right] * distance_four
            for counts, size in unit_counts:
                observed += Fraction(2 * counts[left] * counts[right] * distance_four, size - 1)
    if expected == 0:
        raise FaceAnnotationReliabilityError("undefined ordinal chance disagreement")
    total = sum(pooled)
    return float(1 - Fraction(total - 1, expected) * observed)
