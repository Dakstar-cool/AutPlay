from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import cast
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.vault_uow import TransactionalIngestRepository
from autplay.application.job_worker import JobLeaseLost
from autplay.application.vault_ingest import IngestRepository, IngestSession, VaultIngestHandler
from autplay.application.vault_streaming import parse_single_range
from autplay.application.vault_uploads import (
    UploadInfo,
    UploadRepository,
    VaultCapacityError,
    VaultPrincipal,
    VaultUploadService,
)
from autplay.domain.discovery import AcquisitionAuthorizationReceipt, DiscoveryError
from autplay.domain.jobs import JobKey, RetryableJobError, TerminalJobError
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    CommitResult,
    OpaqueStorageKey,
    Sha256Digest,
    StagedFileNotFoundError,
    VerifiedStagedFile,
)
from autplay.entrypoints.worker_cpu import vault_ingest_handlers
from autplay.ports.vault import FingerprintGenerator, MediaInspector, VaultStorage


def test_single_range_parser_covers_full_partial_suffix_and_unsatisfiable() -> None:
    etag = '"sha256-' + "a" * 64 + '"'
    assert parse_single_range(None, if_range=None, etag=etag, byte_size=10).status_code == 200
    partial = parse_single_range("bytes=2-4", if_range=None, etag=etag, byte_size=10)
    assert (partial.status_code, partial.byte_range) == (206, partial.byte_range)
    assert partial.byte_range is not None
    assert (partial.byte_range.start, partial.byte_range.end) == (2, 4)
    suffix = parse_single_range("bytes=-3", if_range=None, etag=etag, byte_size=10)
    assert suffix.byte_range is not None
    assert (suffix.byte_range.start, suffix.byte_range.end) == (7, 9)
    assert (
        parse_single_range("bytes=10-", if_range=None, etag=etag, byte_size=10).status_code == 416
    )


def test_if_range_mismatch_falls_back_to_full_representation() -> None:
    result = parse_single_range("bytes=2-4", if_range='"older"', etag='"current"', byte_size=8)
    assert result.status_code == 200
    assert result.byte_range is not None
    assert (result.byte_range.start, result.byte_range.end) == (0, 7)


def test_transactional_ingest_repository_commits_each_durable_boundary() -> None:
    session = IngestSession(uuid4(), uuid4(), OpaqueStorageKey("stage"), 1, None)
    factory = _Factory(session)
    repository = TransactionalIngestRepository(factory)
    verified = VerifiedStagedFile(1, Sha256Digest(b"x" * 32))
    metadata = AudioTechnicalMetadata("opus", "ogg", 48_000, 2, 1, None, None)
    evidence = ChromaprintEvidence("chromaprint", "1.6.1", 1, b"fp")

    assert repository.start_ingest(session.upload_session_id, uuid4()) == session
    assert repository.prepare_commit(session, verified, metadata, evidence) == "PUBLISH"
    repository.finalize_published(
        session, OpaqueStorageKey("a" * 64), metadata, evidence, reused=False
    )
    repository.quarantine(session, "vault.integrity_mismatch")

    assert factory.commits == 4


def test_vault_ingest_registration_is_exactly_version_one() -> None:
    handler = object()
    handlers = vault_ingest_handlers(cast(VaultIngestHandler, handler))
    assert handlers == {JobKey("vault.ingest", 1): handler}


def test_low_disk_blocks_complete_and_worker_before_enqueue_or_claim() -> None:
    upload_id = uuid4()
    principal = VaultPrincipal(uuid4(), uuid4())
    repository = _UploadRepository(
        UploadInfo(
            upload_id,
            3,
            3,
            "OPEN",
            datetime.now(UTC) + timedelta(hours=1),
            3,
        )
    )
    storage = _CapacityStorage(9)
    service = VaultUploadService(
        repository=cast(UploadRepository, repository),
        storage=cast(VaultStorage, storage),
        minimum_free_bytes=10,
    )
    with pytest.raises(VaultCapacityError):
        service.complete(principal, upload_id)
    assert not repository.sealed

    handler = VaultIngestHandler(
        repository=cast(object, repository),  # type: ignore[arg-type]
        storage=cast(VaultStorage, storage),
        media=cast(MediaInspector, object()),
        fingerprints=cast(FingerprintGenerator, object()),
        minimum_free_bytes=10,
    )
    with pytest.raises(RetryableJobError, match=r"vault\.capacity_low"):
        handler(cast(object, _Context()), cast(object, _Lease(upload_id)))  # type: ignore[arg-type]


def test_missing_staging_is_classified_and_upload_is_quarantined() -> None:
    upload_id = uuid4()
    session = IngestSession(upload_id, uuid4(), OpaqueStorageKey("missing"), 1, None)
    repository = _MissingRepository(session)
    handler = VaultIngestHandler(
        repository=cast(IngestRepository, repository),
        storage=cast(VaultStorage, _MissingStorage()),
        media=cast(MediaInspector, object()),
        fingerprints=cast(FingerprintGenerator, object()),
    )
    with pytest.raises(TerminalJobError, match="staged_file_not_found"):
        handler(cast(object, _Context()), cast(object, _Lease(upload_id)))  # type: ignore[arg-type]
    assert repository.quarantine_code == "staged_file_not_found"


@pytest.mark.parametrize(
    ("failed_boundary", "expected_publish_count"),
    (("PRE_PUBLISH", 0), ("PRE_MATERIALIZE", 1)),
)
def test_fresh_provider_revocation_blocks_publish_or_finalization(
    tmp_path: Path,
    failed_boundary: str,
    expected_publish_count: int,
) -> None:
    candidate_id = uuid4()
    owner_id = uuid4()
    attempt_id = uuid4()
    session = IngestSession(
        uuid4(),
        uuid4(),
        OpaqueStorageKey("provider-stage"),
        1,
        None,
        source_candidate_id=candidate_id,
        source_provider_track_id="10",
        source_owner_user_id=owner_id,
        source_acquisition_attempt_id=attempt_id,
    )
    repository = _BoundaryRepository(session)
    storage = _BoundaryStorage(tmp_path)
    authorizer = _BoundaryAuthorizer(candidate_id, failed_boundary)
    handler = VaultIngestHandler(
        repository=cast(IngestRepository, repository),
        storage=cast(VaultStorage, storage),
        media=cast(MediaInspector, _Media()),
        fingerprints=cast(FingerprintGenerator, _Fingerprints()),
        source_authorizer=authorizer,
    )

    with pytest.raises(TerminalJobError, match="source_authorization_unavailable"):
        handler(cast(object, _Context()), cast(object, _Lease(session.upload_session_id)))  # type: ignore[arg-type]

    assert authorizer.calls == ["PRE_PUBLISH"] + (
        ["PRE_MATERIALIZE"] if failed_boundary == "PRE_MATERIALIZE" else []
    )
    assert storage.publish_count == expected_publish_count
    assert repository.finalize_count == 0
    assert repository.quarantine_code == "source_authorization_unavailable"


@pytest.mark.parametrize(
    ("lost_stage", "expected_publish_count"),
    (
        ("SOURCE_AUTHORIZED_PRE_PUBLISH", 0),
        ("SOURCE_AUTHORIZED_PRE_MATERIALIZE", 1),
    ),
)
def test_lost_lease_after_authorization_blocks_irreversible_boundary(
    tmp_path: Path,
    lost_stage: str,
    expected_publish_count: int,
) -> None:
    candidate_id = uuid4()
    session = IngestSession(
        uuid4(),
        uuid4(),
        OpaqueStorageKey("provider-stage"),
        1,
        None,
        source_candidate_id=candidate_id,
        source_provider_track_id="10",
        source_owner_user_id=uuid4(),
        source_acquisition_attempt_id=uuid4(),
    )
    repository = _BoundaryRepository(session)
    storage = _BoundaryStorage(tmp_path)
    handler = VaultIngestHandler(
        repository=cast(IngestRepository, repository),
        storage=cast(VaultStorage, storage),
        media=cast(MediaInspector, _Media()),
        fingerprints=cast(FingerprintGenerator, _Fingerprints()),
        source_authorizer=_BoundaryAuthorizer(candidate_id, "NEVER"),
    )

    with pytest.raises(JobLeaseLost):
        handler(
            cast(object, _LeaseLosingContext(lost_stage)),  # type: ignore[arg-type]
            cast(object, _Lease(session.upload_session_id)),  # type: ignore[arg-type]
        )

    assert storage.publish_count == expected_publish_count
    assert repository.finalize_count == 0


class _Vault:
    def __init__(self, session: IngestSession) -> None:
        self._session = session

    def start_ingest(self, upload_session_id: object, job_id: object) -> IngestSession:
        del upload_session_id, job_id
        return self._session

    def prepare_commit(
        self, session: object, verified: object, metadata: object, evidence: object
    ) -> str:
        del session, verified, metadata, evidence
        return "PUBLISH"

    def finalize_published(
        self,
        session: object,
        storage_key: object,
        metadata: object,
        evidence: object,
        *,
        reused: bool,
        authorization_receipt: object | None = None,
    ) -> bool:
        del session, storage_key, metadata, evidence, reused, authorization_receipt
        return True

    def quarantine(self, session: object, code: str) -> None:
        del session, code


class _Unit:
    def __init__(self, factory: _Factory) -> None:
        self._factory = factory
        self.vault = _Vault(factory.session)

    def __enter__(self) -> _Unit:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        return None

    def commit(self) -> None:
        self._factory.commits += 1

    def rollback(self) -> None:
        return None


class _Factory:
    def __init__(self, session: IngestSession) -> None:
        self.session = session
        self.commits = 0

    def __call__(self) -> _Unit:
        return _Unit(self)


class _UploadRepository:
    def __init__(self, info: UploadInfo) -> None:
        self.info = info
        self.sealed = False

    def get_owned_for_update(self, principal: object, upload_session_id: object) -> UploadInfo:
        del principal, upload_session_id
        return self.info

    def seal_and_enqueue(self, principal: object, upload_session_id: object) -> UploadInfo:
        del principal, upload_session_id
        self.sealed = True
        return self.info


class _CapacityStorage:
    def __init__(self, available: int) -> None:
        self.available = available

    def available_bytes(self) -> int:
        return self.available


class _Lease:
    def __init__(self, upload_id: object) -> None:
        self.payload = {"upload_session_id": str(upload_id)}
        self.fence = type("Fence", (), {"job_id": uuid4()})()


class _Context:
    def checkpoint(self, value: object) -> None:
        del value


class _LeaseLosingContext:
    def __init__(self, lost_stage: str) -> None:
        self.lost_stage = lost_stage

    def checkpoint(self, value: object) -> None:
        if isinstance(value, dict) and value.get("stage") == self.lost_stage:
            raise JobLeaseLost


class _MissingRepository:
    def __init__(self, session: IngestSession) -> None:
        self.session = session
        self.quarantine_code: str | None = None

    def start_ingest(self, upload_session_id: object, job_id: object) -> IngestSession:
        del upload_session_id, job_id
        return self.session

    def quarantine(self, session: IngestSession, code: str) -> None:
        assert session == self.session
        self.quarantine_code = code


class _MissingStorage(_CapacityStorage):
    def __init__(self) -> None:
        super().__init__(100)

    def verify_staging(self, key: OpaqueStorageKey) -> VerifiedStagedFile:
        del key
        raise StagedFileNotFoundError()


class _BoundaryRepository:
    def __init__(self, session: IngestSession) -> None:
        self.session = session
        self.finalize_count = 0
        self.quarantine_code: str | None = None

    def start_ingest(self, upload_session_id: object, job_id: object) -> IngestSession:
        del upload_session_id, job_id
        return self.session

    def prepare_commit(self, *_: object) -> str:
        return "PUBLISH"

    def finalize_published(self, *_: object, **__: object) -> bool:
        self.finalize_count += 1
        return True

    def quarantine(self, session: IngestSession, code: str) -> None:
        assert session == self.session
        self.quarantine_code = code


class _BoundaryStorage(_CapacityStorage):
    def __init__(self, root: Path) -> None:
        super().__init__(100)
        self.path = root / "provider-stage"
        self.path.write_bytes(b"x")
        self.publish_count = 0

    def verify_staging(self, key: OpaqueStorageKey) -> VerifiedStagedFile:
        del key
        return VerifiedStagedFile(1, Sha256Digest(b"x" * 32))

    def staging_path_for_media(self, key: OpaqueStorageKey) -> Path:
        del key
        return self.path

    def commit_staging(self, key: OpaqueStorageKey, verified: VerifiedStagedFile) -> CommitResult:
        del key, verified
        self.publish_count += 1
        return CommitResult(OpaqueStorageKey("x" * 64), False)

    def cleanup_staging(self, key: OpaqueStorageKey) -> None:
        del key


class _Media:
    def inspect(self, path: Path) -> AudioTechnicalMetadata:
        del path
        return AudioTechnicalMetadata("opus", "ogg", 48_000, 2, 1, None, None)


class _Fingerprints:
    def fingerprint(self, path: Path) -> ChromaprintEvidence:
        del path
        return ChromaprintEvidence("chromaprint", "1.6.1", 1, b"fp")


class _BoundaryAuthorizer:
    def __init__(self, candidate_id: UUID, failed_boundary: str) -> None:
        self.candidate_id = candidate_id
        self.failed_boundary = failed_boundary
        self.calls: list[str] = []

    def authorize(
        self,
        candidate_id: UUID,
        provider_track_id: str,
        *,
        boundary: str,
        owner_user_id: UUID,
        acquisition_attempt_id: UUID,
    ) -> AcquisitionAuthorizationReceipt:
        del owner_user_id, acquisition_attempt_id
        assert candidate_id == self.candidate_id and provider_track_id == "10"
        self.calls.append(boundary)
        if boundary == self.failed_boundary:
            raise DiscoveryError("source_authorization_unavailable")
        return AcquisitionAuthorizationReceipt(
            candidate_id=candidate_id,
            provider_track_id=provider_track_id,
            provider_artist_id="20",
            boundary=boundary,
            checked_at=datetime.now(UTC),
        )
