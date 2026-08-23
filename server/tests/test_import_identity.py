from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from autplay.application.source_adapters import (
    AuthorizedLocalMetadataSourceAdapter,
    GenericPublicMetadataSourceAdapter,
    GenericUserExportSourceAdapter,
)
from autplay.domain.import_identity import (
    BenchmarkCase,
    CatalogCandidate,
    FingerprintEvidence,
    IdentityTrack,
    ImportEnvelope,
    ImportEnvelopeError,
    ImportFormat,
    evaluate_identity,
    parse_import,
    run_benchmark,
)
from autplay.ports.source_adapters import CredentialRequirement, NetworkPolicy

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "import"


@pytest.mark.parametrize(
    ("filename", "format_name", "expected_rows", "expected_malformed"),
    (
        ("p10_export_v1.csv", ImportFormat.CSV, 5, 2),
        ("p10_export_v1.json", ImportFormat.JSON, 3, 1),
        ("p10_export_v1.html", ImportFormat.HTML, 3, 1),
    ),
)
def test_golden_exports_are_deterministic_and_isolate_malformed_rows(
    filename: str,
    format_name: ImportFormat,
    expected_rows: int,
    expected_malformed: int,
) -> None:
    payload = (FIXTURES / filename).read_bytes()
    envelope = ImportEnvelope(format_name, payload)
    first = parse_import(envelope)
    second = parse_import(envelope)

    assert first == second
    assert len(first.rows) == expected_rows
    assert first.malformed_count == expected_malformed
    assert len({row.source_row_key for row in first.rows}) == expected_rows
    assert first.envelope.input_sha256 == second.envelope.input_sha256
    assert any(row.availability == "UNAVAILABLE" for row in first.rows)
    assert any(row.unknown_field_count for row in first.rows)


def test_cp1251_encoding_duplicates_and_unknown_fields_are_preserved() -> None:
    payload = (
        "title,artist,unknown\n"
        "\u041f\u0435\u0441\u043d\u044f,\u0418\u0441\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c,\u043f\u0435\u0440\u0432\u044b\u0439\n"
        "\u041f\u0435\u0441\u043d\u044f,\u0418\u0441\u043f\u043e\u043b\u043d\u0438\u0442\u0435\u043b\u044c,\u0432\u0442\u043e\u0440\u043e\u0439\n"
    ).encode("cp1251")
    parsed = parse_import(ImportEnvelope(ImportFormat.CSV, payload))

    assert parsed.encoding == "cp1251"
    assert [row.title for row in parsed.rows] == [
        "\u041f\u0435\u0441\u043d\u044f",
        "\u041f\u0435\u0441\u043d\u044f",
    ]
    assert parsed.rows[0].source_row_key != parsed.rows[1].source_row_key
    assert parsed.rows[0].raw_fields["unknown"] == "\u043f\u0435\u0440\u0432\u044b\u0439"


def test_invalid_outer_envelope_fails_before_row_persistence() -> None:
    with pytest.raises(ImportEnvelopeError, match=r"import\.json_malformed"):
        parse_import(ImportEnvelope(ImportFormat.JSON, b"{"))
    with pytest.raises(ImportEnvelopeError, match=r"import\.schema_version_unsupported"):
        ImportEnvelope(ImportFormat.CSV, b"title,artist\na,b", schema_version="2")


def test_txt_import_accepts_bounded_tab_and_dash_lines() -> None:
    parsed = parse_import(
        ImportEnvelope(
            ImportFormat.TXT,
            (
                b"artist\ttitle\talbum\n"
                b"Open Artist\tMorning Light\tOpen Album\n"
                b"Open Artist - Evening Light\n"
                b"Malformed line\n"
            ),
        )
    )

    assert [(row.artist, row.title, row.album) for row in parsed.rows if row.valid] == [
        ("Open Artist", "Morning Light", "Open Album"),
        ("Open Artist", "Evening Light", None),
    ]
    assert parsed.malformed_count == 1
    assert parsed.rows[-1].error_code == "import.txt_row_malformed"


def test_txt_import_rejects_an_empty_collection() -> None:
    with pytest.raises(ImportEnvelopeError, match=r"import\.txt_empty"):
        parse_import(ImportEnvelope(ImportFormat.TXT, b"\n\r\n"))


def test_initial_source_adapters_are_explicitly_safe_and_bounded() -> None:
    generic = GenericUserExportSourceAdapter()
    assert generic.manifest.credential_requirement is CredentialRequirement.NONE
    assert generic.manifest.network_policy is NetworkPolicy.OFFLINE
    assert (
        generic.parse(
            b"title,artist\nSong,Artist", format_name="CSV", schema_version="1"
        ).valid_count
        == 1
    )

    local = AuthorizedLocalMetadataSourceAdapter()
    with pytest.raises(ValueError, match="forbidden"):
        local.normalize({"title": "Song", "artist": "Artist", "path": "private"})

    class Transport:
        def search(
            self, query: IdentityTrack, *, limit: int, timeout_seconds: float
        ) -> tuple[object, ...]:
            del query, limit, timeout_seconds
            return ()

    public = GenericPublicMetadataSourceAdapter(Transport())  # type: ignore[arg-type]
    assert public.manifest.network_policy is NetworkPolicy.PUBLIC_ALLOWLIST_ONLY
    assert public.manifest.credential_requirement is CredentialRequirement.NONE


def test_matcher_blocks_version_conflicts_fingerprint_mismatch_and_ties() -> None:
    query = IdentityTrack(
        "Song (Live)",
        ("Artist",),
        duration_ms=180_000,
        version_markers=("LIVE",),
        fingerprint=FingerprintEvidence("CHROMAPRINT", "1.6.1", "aa", 0.95),
    )
    wrong = CatalogCandidate(
        uuid4(),
        "Song",
        ("Artist",),
        duration_ms=180_000,
        version_markers=(),
        fingerprint=FingerprintEvidence("CHROMAPRINT", "1.6.1", "bb", 0.95),
    )
    result = evaluate_identity(query, (wrong,))
    assert result.state == "INTEGRITY_CONFLICT"
    assert "VERSION_LIVE" in result.candidates[0].hard_conflicts
    assert "FINGERPRINT_MISMATCH" in result.candidates[0].hard_conflicts
    assert result.explanation["auto_match_enabled"] is False

    first_id, second_id = uuid4(), uuid4()
    tie_query = IdentityTrack("Common", ("Artist",), duration_ms=200_000)
    tie = evaluate_identity(
        tie_query,
        (
            CatalogCandidate(first_id, "Common", ("Artist",), duration_ms=200_000),
            CatalogCandidate(second_id, "Common", ("Artist",), duration_ms=200_000),
        ),
    )
    assert tie.state == "REVIEW_REQUIRED"
    assert tie.margin == 0
    reason_codes = tie.explanation["reason_codes"]
    assert isinstance(reason_codes, list)
    assert any(item == "TOP_TWO_MARGIN" for item in reason_codes)


@pytest.mark.parametrize(
    ("query_title", "candidate_title", "conflict"),
    (
        ("Song (Remix)", "Song", "VERSION_REMIX"),
        ("Song (Radio Edit)", "Song", "VERSION_EDIT"),
        ("Song (Remastered)", "Song", "VERSION_REMASTER_REVIEW"),
        ("Song (Instrumental)", "Song", "VERSION_INSTRUMENTAL"),
    ),
)
def test_version_hard_negative_slices(
    query_title: str, candidate_title: str, conflict: str
) -> None:
    result = evaluate_identity(
        IdentityTrack(query_title, ("Artist",), duration_ms=180_000),
        (CatalogCandidate(uuid4(), candidate_title, ("Artist",), duration_ms=180_000),),
    )
    assert conflict in result.candidates[0].hard_conflicts
    assert result.state == "REVIEW_REQUIRED"


def test_same_recording_fingerprint_across_codec_is_ranked_but_never_auto_applied() -> None:
    recording_id = uuid4()
    fingerprint = FingerprintEvidence("CHROMAPRINT", "1.6.1", "abc", 1.0)
    result = evaluate_identity(
        IdentityTrack("Song", ("Artist",), duration_ms=180_000, fingerprint=fingerprint),
        (
            CatalogCandidate(
                recording_id,
                "Song",
                ("Artist",),
                duration_ms=180_000,
                fingerprint=fingerprint,
                origins=("FINGERPRINT", "DIFFERENT_CODEC"),
            ),
        ),
    )
    assert result.candidates[0].recording_id == recording_id
    assert result.candidates[0].evidence_tier == "T3"
    assert result.state == "REVIEW_REQUIRED"
    assert result.explanation["auto_match_enabled"] is False


def test_benchmark_reports_precision_recall_confusion_and_error_slices() -> None:
    fixture = json.loads((FIXTURES / "p10_benchmark_v1.json").read_text(encoding="utf-8"))
    assert "live-vs-studio" in fixture["classes"]
    positive_id = uuid4()
    cases = (
        BenchmarkCase(
            "positive",
            "same-recording-different-codec",
            IdentityTrack("Song", ("Artist",), duration_ms=180_000),
            (CatalogCandidate(positive_id, "Song", ("Artist",), duration_ms=180_000),),
            positive_id,
        ),
        BenchmarkCase(
            "negative",
            "live-vs-studio",
            IdentityTrack("Song Live", ("Artist",), version_markers=("LIVE",)),
            (CatalogCandidate(uuid4(), "Song", ("Artist",)),),
            None,
        ),
    )
    report = run_benchmark(
        cases,
        dataset_id=fixture["dataset_id"],
        dataset_version=fixture["dataset_version"],
    )
    replayed = run_benchmark(
        cases,
        dataset_id=fixture["dataset_id"],
        dataset_version=fixture["dataset_version"],
    )
    changed = run_benchmark(
        (*cases, cases[1]),
        dataset_id=fixture["dataset_id"],
        dataset_version=fixture["dataset_version"],
    )
    assert report.dataset_id == "autplay.p10.identity-fixture"
    assert report.dataset_version == "1.0.0"
    assert len(report.dataset_sha256) == 64
    assert replayed.dataset_sha256 == report.dataset_sha256
    assert changed.dataset_sha256 != report.dataset_sha256
    assert report.confusion == {"tp": 1, "fp": 0, "tn": 1, "fn": 0}
    assert report.precision == 1
    assert report.recall == 1
    assert report.error_slices["live-vs-studio"]["true_negative"] == 1
    assert report.auto_match_eligible is False
    assert "AUTO_MATCH_DISABLED" in report.gate_reasons
