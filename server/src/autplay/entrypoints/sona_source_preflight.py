"""Local counts-only preflight for an explicitly authorized isolated restore."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Final, TextIO, cast

import rfc8785
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from autplay.adapters.postgresql.runtime_database import create_runtime_engine
from autplay.adapters.postgresql.sona_source_preflight import (
    SqlAlchemySonaSourceAggregateReader,
)
from autplay.application.sona_source_acceptance import (
    SonaSourceProvenanceAcceptance,
    load_sona_source_provenance_acceptance,
)
from autplay.application.sona_source_preflight import (
    SonaSourcePreflightResult,
    build_sona_source_preflight_result,
)
from autplay.domain.recommendations import JsonValue
from autplay.runtime.settings import SettingsLoadError, load_worker_settings

SERVICE_NAME: Final = "autplay-sona-source-preflight"
_MAX_SUMMARY_BYTES: Final = 16_384
_READ_BLOCK_BYTES: Final = 1024 * 1024
_SUMMARY_KEYS: Final = frozenset(
    {
        "schema_version",
        "generation_id",
        "created_at_utc",
        "alembic_head",
        "application_revision",
        "payload_sha256",
        "encrypted_archive_sha256",
        "encryption",
        "recipient_fingerprint",
        "encrypted_archive_bytes",
        "roundtrip_verified",
        "vault_objects",
        "vault_replicas",
    }
)


@dataclass(frozen=True, slots=True)
class _GenerationSummary:
    generation_id: str
    created_at_ms: int
    alembic_head: str
    encrypted_archive_sha256: str
    encrypted_archive_bytes: int


def build_parser() -> argparse.ArgumentParser:
    """Build the non-interactive local preflight parser."""

    parser = argparse.ArgumentParser(prog=SERVICE_NAME)
    parser.add_argument("--generation-summary", required=True)
    parser.add_argument("--encrypted-archive", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--provenance-acceptance",
        help="content-addressed acceptance for the exact reconstructed 0026 generation",
    )
    parser.add_argument(
        "--authorized-owner-data-use",
        action="store_true",
        help="confirm explicit authority for this isolated local owner-data preflight",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Verify the archive, read aggregate counts in one read-only snapshot, and write a report."""

    namespace = build_parser().parse_args(sys.argv[1:] if arguments is None else list(arguments))
    if not namespace.authorized_owner_data_use:
        _write_error(sys.stderr, "owner_data_authorization_required")
        return 2
    try:
        summary = _load_generation_summary(Path(str(namespace.generation_summary)))
        _verify_encrypted_archive(Path(str(namespace.encrypted_archive)), summary)
        provenance_acceptance = (
            load_sona_source_provenance_acceptance(Path(str(namespace.provenance_acceptance)))
            if namespace.provenance_acceptance is not None
            else None
        )
        output = Path(str(namespace.output))
        if output.exists():
            raise FileExistsError
    except FileExistsError:
        _write_error(sys.stderr, "preflight_output_already_exists")
        return 5
    except OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError:
        _write_error(sys.stderr, "source_archive_verification_failed")
        return 4

    try:
        settings = load_worker_settings()
    except SettingsLoadError as error:
        _write_error(sys.stderr, error.code)
        return 2
    engine = create_runtime_engine(settings)
    try:
        result = _read_preflight(engine, summary, provenance_acceptance)
        _write_result(output, result)
    except SQLAlchemyError, RuntimeError:
        _write_error(sys.stderr, "source_database_preflight_failed")
        return 3
    except FileExistsError:
        _write_error(sys.stderr, "preflight_output_already_exists")
        return 5
    except OSError, ValueError:
        _write_error(sys.stderr, "preflight_output_failed")
        return 5
    finally:
        engine.dispose()

    json.dump(
        {
            "status": "ready" if result.ready_for_authorized_extraction else "blocked",
            "report_sha256": result.report_sha256,
        },
        sys.stdout,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")
    return 0 if result.ready_for_authorized_extraction else 4


def _read_preflight(
    engine: Engine,
    summary: _GenerationSummary,
    provenance_acceptance: SonaSourceProvenanceAcceptance | None,
) -> SonaSourcePreflightResult:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
            snapshot = SqlAlchemySonaSourceAggregateReader(connection).read(
                source_captured_at_ms=summary.created_at_ms
            )
            return build_sona_source_preflight_result(
                snapshot,
                generation_id=summary.generation_id,
                encrypted_archive_sha256=summary.encrypted_archive_sha256,
                source_captured_at_ms=summary.created_at_ms,
                declared_alembic_head=summary.alembic_head,
                provenance_acceptance=provenance_acceptance,
            )
        finally:
            transaction.rollback()


def _load_generation_summary(path: Path) -> _GenerationSummary:
    if path.stat().st_size > _MAX_SUMMARY_BYTES:
        raise ValueError("generation summary exceeds its size limit")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or set(value) != _SUMMARY_KEYS:
        raise ValueError("generation summary keys are invalid")
    document = cast(dict[str, Any], value)
    if document.get("schema_version") != 1 or document.get("roundtrip_verified") is not True:
        raise ValueError("generation summary is not restore-verified")
    generation_id = _string(document, "generation_id")
    alembic_head = _string(document, "alembic_head")
    archive_sha256 = _sha256_value(document, "encrypted_archive_sha256")
    _sha256_value(document, "payload_sha256")
    _sha1_value(document, "application_revision")
    _string(document, "encryption")
    _string(document, "recipient_fingerprint")
    archive_bytes = _positive_integer(document, "encrypted_archive_bytes")
    _non_negative_integer(document, "vault_objects")
    _non_negative_integer(document, "vault_replicas")
    created_at = datetime.fromisoformat(_string(document, "created_at_utc").replace("Z", "+00:00"))
    if created_at.tzinfo is None:
        raise ValueError("generation timestamp lacks an offset")
    created_at_ms = int(created_at.astimezone(UTC).timestamp() * 1000)
    if created_at_ms < 0:
        raise ValueError("generation timestamp is invalid")
    return _GenerationSummary(
        generation_id,
        created_at_ms,
        alembic_head,
        archive_sha256,
        archive_bytes,
    )


def _verify_encrypted_archive(path: Path, summary: _GenerationSummary) -> None:
    if path.stat().st_size != summary.encrypted_archive_bytes:
        raise ValueError("encrypted archive size mismatch")
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(_READ_BLOCK_BYTES):
            digest.update(block)
    if digest.hexdigest() != summary.encrypted_archive_sha256:
        raise ValueError("encrypted archive digest mismatch")


def _write_result(path: Path, result: SonaSourcePreflightResult) -> None:
    envelope: dict[str, JsonValue] = {
        "report": result.document,
        "report_sha256": result.report_sha256,
    }
    payload = rfc8785.dumps(envelope)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # A same-directory hard link publishes the fully flushed bytes without
        # the check-then-replace overwrite race. It fails atomically when the
        # destination already exists; unlinking the temporary name leaves the
        # published link and its inode intact.
        os.link(temporary, path)
        temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _string(value: Mapping[str, object], field: str) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str) or not 1 <= len(candidate) <= 500:
        raise ValueError(f"invalid {field}")
    return candidate


def _sha256_value(value: Mapping[str, object], field: str) -> str:
    candidate = _string(value, field)
    if len(candidate) != 64 or any(character not in "0123456789abcdef" for character in candidate):
        raise ValueError(f"invalid {field}")
    return candidate


def _sha1_value(value: Mapping[str, object], field: str) -> str:
    candidate = _string(value, field)
    if len(candidate) != 40 or any(character not in "0123456789abcdef" for character in candidate):
        raise ValueError(f"invalid {field}")
    return candidate


def _positive_integer(value: Mapping[str, object], field: str) -> int:
    candidate = value.get(field)
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate <= 0:
        raise ValueError(f"invalid {field}")
    return candidate


def _non_negative_integer(value: Mapping[str, object], field: str) -> int:
    candidate = value.get(field)
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate < 0:
        raise ValueError(f"invalid {field}")
    return candidate


def _write_error(stream: TextIO, code: str) -> None:
    json.dump({"error": code}, stream, ensure_ascii=True, separators=(",", ":"))
    stream.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ("build_parser", "main")
