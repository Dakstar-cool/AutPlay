"""Decoded-sample mapping vectors for the separate Face v2 contract."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest
import rfc8785

from autplay.domain.face_presentation_map import (
    PresentationSegmentV1,
    SourcePresentationMapError,
    SourcePresentationMapV1,
    parse_source_presentation_map,
)

FIXTURE = (
    Path(__file__).resolve().parents[2] / "tests/fixtures/face/source-presentation-map-v1.json"
)
GOLDEN_SHA256 = "d28b8c2322def220d3b610d9e1b182f2364590d69830e752b4c11847ede31630"


def _fixture() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(FIXTURE.read_text(encoding="utf-8")))


def _map() -> SourcePresentationMapV1:
    document = _fixture()
    document.pop("schema_version")
    document.pop("kind")
    document["segments"] = tuple(PresentationSegmentV1(**row) for row in document["segments"])
    return SourcePresentationMapV1(**document)


def _sample(value: SourcePresentationMapV1, position_us: int, **overrides: str) -> int | None:
    identity = {
        "encoded_source_sha256": value.encoded_source_sha256,
        "decoder_id": value.decoder_id,
        "decoder_version": value.decoder_version,
        "probe_id": value.probe_id,
        "probe_version": value.probe_version,
        **overrides,
    }
    return value.sample_at(position_us, **identity)


def test_aac_edit_list_golden_bytes_digest_and_half_open_segments() -> None:
    value = _map()
    assert value.sha256() == GOLDEN_SHA256
    assert len(value.canonical_bytes()) == 598
    assert parse_source_presentation_map(value.canonical_bytes()) == value
    assert _sample(value, 0) == 1024
    assert _sample(value, 999_999) == 49_023
    assert _sample(value, 1_000_000) is None
    assert _sample(value, 1_499_999) is None
    assert _sample(value, 1_500_000) == 49_024
    assert _sample(value, 1_999_999) == 73_023
    assert _sample(value, 2_000_000) is None


def test_vbr_mp3_delay_and_exact_seek_mapping_use_integer_arithmetic() -> None:
    value = parse_source_presentation_map(
        (FIXTURE.parent / "source-presentation-map-v1-mp3.canonical.json").read_bytes()
    )
    assert value.sha256() == "2f0cdfe40210d20e521d2b4ae524fb1659055235ebd2bbecfb29bcb674a55143"
    assert value.encoder_delay_samples == 576 and value.encoder_padding_samples == 1152
    assert _sample(value, 1_000_000) == 44_676
    assert _sample(value, 1_499_999) == 66_725
    assert _sample(value, 1_500_000) == 66_726
    assert _sample(value, 1_999_999) == 88_775
    assert _sample(value, 1_750_000) == 77_751


@pytest.mark.parametrize("position", [-1, 2_000_000, 86_400_000_001, 2**63, True, 1.5])
def test_out_of_range_positions_are_neutral(position: int) -> None:
    assert _sample(_map(), position) is None


def test_source_and_decoder_probe_mismatch_are_neutral() -> None:
    value = _map()
    assert _sample(value, 100, encoded_source_sha256="b" * 64) is None
    assert _sample(value, 100, decoder_version="different") is None
    assert _sample(value, 100, probe_id="different") is None


@pytest.mark.parametrize(
    "segments",
    [
        (PresentationSegmentV1(0, 0, 0),),
        (PresentationSegmentV1(0, 1_000_000, 79_999),),
        (PresentationSegmentV1(0, 1_000_000, 0), PresentationSegmentV1(999_999, 2_000_000, 0)),
        (PresentationSegmentV1(1, 2, -1),),
        (PresentationSegmentV1(0, 86_400_000_001, 0),),
    ],
)
def test_invalid_edits_cannot_be_published(segments: tuple[PresentationSegmentV1, ...]) -> None:
    with pytest.raises(SourcePresentationMapError):
        replace(_map(), segments=segments)


def test_noncanonical_duplicate_or_unknown_edit_is_rejected() -> None:
    value = _map()
    pretty = FIXTURE.read_bytes()
    with pytest.raises(SourcePresentationMapError):
        parse_source_presentation_map(pretty)
    duplicate = value.canonical_bytes().replace(
        b'"schema_version":1', b'"schema_version":1,"schema_version":1'
    )
    with pytest.raises(SourcePresentationMapError):
        parse_source_presentation_map(duplicate)
    unknown = value.document()
    unknown["segments"][0]["unknown_edit"] = 1
    with pytest.raises(SourcePresentationMapError):
        parse_source_presentation_map(rfc8785.dumps(unknown))
