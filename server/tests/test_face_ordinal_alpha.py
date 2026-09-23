"""Raw ordinal reliability checks for Face qualification."""

import pytest

from autplay.application.face_ordinal_alpha import (
    FaceAnnotationReliabilityError,
    face_ordinal_krippendorff_alpha,
)


def test_perfect_agreement_with_two_occupied_poles() -> None:
    assert face_ordinal_krippendorff_alpha(((-3, -3, -3), (3, 3, 3))) == 1.0


def test_crossed_three_rater_units_have_negative_alpha() -> None:
    assert face_ordinal_krippendorff_alpha(((-3, -3, 3), (-3, 3, 3))) == pytest.approx(-1 / 9)


def test_constant_axis_is_undefined() -> None:
    with pytest.raises(FaceAnnotationReliabilityError, match="undefined"):
        face_ordinal_krippendorff_alpha(((0, 0, 0), (0, 0, 0)))


@pytest.mark.parametrize(
    "units",
    [
        ((-3, -3, 3),),
        ((-3, -3), (3, 3, 3)),
        ((-3, False, 3), (3, 3, 3)),
        ((-3, -3, 4), (3, 3, 3)),
        ((-3, -3, 3), [3, 3, 3]),
    ],
)
def test_invalid_raw_units_fail_closed(units: object) -> None:
    with pytest.raises(FaceAnnotationReliabilityError):
        face_ordinal_krippendorff_alpha(units)  # type: ignore[arg-type]
