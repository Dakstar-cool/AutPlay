"""Local authorization and encrypted-source binding tests for the preflight CLI."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest
import rfc8785

from autplay.application.sona_source_preflight import SonaSourcePreflightResult
from autplay.entrypoints.sona_source_preflight import (
    _load_generation_summary,
    _verify_encrypted_archive,
    _write_result,
    main,
)


def _source_files(root: Path, payload: bytes = b"encrypted-age-envelope") -> tuple[Path, Path]:
    archive = root / "backup.tar.age"
    archive.write_bytes(payload)
    summary = root / "generation-summary.json"
    summary.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "generation_id": "20260901T110359Z-ebb958b5e91e",
                "created_at_utc": "2026-09-01T11:04:54.0012884Z",
                "alembic_head": "0026_s1d_guest_room_access",
                "application_revision": "5" * 40,
                "payload_sha256": "6" * 64,
                "encrypted_archive_sha256": sha256(payload).hexdigest(),
                "encryption": "age-v1.3.2-x25519",
                "recipient_fingerprint": "f3cc95914e897fe0",
                "encrypted_archive_bytes": len(payload),
                "roundtrip_verified": True,
                "vault_objects": 24,
                "vault_replicas": 24,
            }
        ),
        encoding="utf-8",
    )
    return summary, archive


def test_cli_requires_explicit_owner_data_authority_before_reading_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    output = tmp_path / "report.json"

    status = main(
        (
            "--generation-summary",
            str(tmp_path / "missing-summary"),
            "--encrypted-archive",
            str(tmp_path / "missing-archive"),
            "--output",
            str(output),
        )
    )

    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert captured.err == '{"error":"owner_data_authorization_required"}\n'
    assert not output.exists()


def test_generation_summary_and_archive_are_exactly_bound(tmp_path: Path) -> None:
    summary_path, archive_path = _source_files(tmp_path)

    summary = _load_generation_summary(summary_path)
    _verify_encrypted_archive(archive_path, summary)

    assert summary.created_at_ms == 1_788_260_694_001
    archive_path.write_bytes(b"different-envelope")
    with pytest.raises(ValueError, match="size mismatch"):
        _verify_encrypted_archive(archive_path, summary)


def test_generation_summary_rejects_unverified_or_extra_metadata(tmp_path: Path) -> None:
    summary_path, _archive_path = _source_files(tmp_path)
    document = json.loads(summary_path.read_text(encoding="utf-8"))
    document["roundtrip_verified"] = False
    document["unexpected"] = "value"
    summary_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="keys"):
        _load_generation_summary(summary_path)


def test_preflight_report_publication_is_canonical_and_never_overwrites(tmp_path: Path) -> None:
    output = tmp_path / "preflight.json"
    result = SonaSourcePreflightResult(
        document={"quality_eligible": False},
        report_sha256="a" * 64,
        ready_for_authorized_extraction=False,
    )
    expected = rfc8785.dumps(
        {
            "report": result.document,
            "report_sha256": result.report_sha256,
        }
    )

    _write_result(output, result)

    assert output.read_bytes() == expected
    with pytest.raises(FileExistsError):
        _write_result(output, result)
    assert output.read_bytes() == expected
    assert not tuple(tmp_path.glob(".preflight.json.*"))
