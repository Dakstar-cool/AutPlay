"""Ingest ports over an already registered, contained child; no parent filesystem I/O."""

from pathlib import Path

from autplay.domain.ingest_execution import IngestExecutionTicket
from autplay.domain.vault import (
    AudioTechnicalMetadata,
    ChromaprintEvidence,
    CommitResult,
    ImmutableObjectConflictError,
    MediaToolOutputError,
    MediaToolTimeoutError,
    MediaValidationError,
    OpaqueStorageKey,
    StagedFileNotFoundError,
    StorageOperationError,
    StorageSafetyError,
    VaultError,
    VerifiedStagedFile,
)

from .ingest_protocol import (
    MAX_INGEST_REPLY,
    IngestChildSettings,
    integer,
    parse_analysis,
    parse_verified,
)
from .vault_child import ChildProtocolError, decode_document
from .vault_process import RetainedVaultProcess

_ERRORS: tuple[type[VaultError], ...] = (
    ImmutableObjectConflictError,
    MediaToolOutputError,
    MediaToolTimeoutError,
    MediaValidationError,
    StagedFileNotFoundError,
    StorageOperationError,
    StorageSafetyError,
)


def ingest_reply(tag: bytes, payload: bytes) -> dict[str, object]:
    document = decode_document(payload, maximum=MAX_INGEST_REPLY)
    if tag == b"E" and set(document) == {"code"}:
        for error in _ERRORS:
            if document["code"] == error.code:
                raise error()
    if tag != b"R":
        raise ChildProtocolError()
    return document


class ProcessIngestStorage:
    """One WORK stream exposes only its ticket's staging key and verified CAS hash.

    The returned media path is an opaque local token, never a path opened by the
    parent. Both media ports share the child's single analysis result. Cleanup
    requires a separate durable claim/execution after this process has exited.
    """

    def __init__(
        self, child: RetainedVaultProcess[IngestExecutionTicket], settings: IngestChildSettings
    ) -> None:
        self._child, self._settings = child, settings
        self._key = child.ticket.staging_key
        self._path = Path(self._key.value)
        self._verified: VerifiedStagedFile | None = None
        self._analysis: tuple[AudioTechnicalMetadata, ChromaprintEvidence] | None = None

    def begin(self) -> None:
        self._child.go(
            {
                "version": 1,
                "execution_id": str(self._child.ticket.execution_id),
                "mode": "WORK",
                "key": self._key.value,
                "settings": self._settings.document(),
                "expected": None,
            }
        )
        if ingest_reply(*self._child.read_result()) != {
            "execution_id": str(self._child.ticket.execution_id),
            "ready": True,
        }:
            raise ChildProtocolError()

    def _exchange(self, action: str) -> dict[str, object]:
        return ingest_reply(*self._child.ingest_exchange(action))

    def _require_key(self, key: OpaqueStorageKey) -> None:
        self._child.deadline.check()
        if key != self._key:
            raise StorageSafetyError()

    def available_bytes(self) -> int:
        reply = self._exchange("CAPACITY")
        if set(reply) != {"available_bytes"}:
            raise ChildProtocolError()
        return integer(reply["available_bytes"])

    def verify_staging(self, key: OpaqueStorageKey) -> VerifiedStagedFile:
        self._require_key(key)
        if self._verified is None:
            self._verified = parse_verified(
                self._exchange("VERIFY"), maximum=self._settings.limits.max_object_bytes
            )
        return self._verified

    def staging_path_for_media(self, key: OpaqueStorageKey) -> Path:
        self._require_key(key)
        if self._verified is None:
            raise ChildProtocolError()
        return self._path

    def _analyze(self, path: Path) -> tuple[AudioTechnicalMetadata, ChromaprintEvidence]:
        self._require_key(self._key)
        if path != self._path or self._verified is None:
            raise StorageSafetyError()
        if self._analysis is None:
            self._analysis = parse_analysis(self._exchange("ANALYZE"))
        return self._analysis

    def inspect(self, path: Path) -> AudioTechnicalMetadata:
        return self._analyze(path)[0]

    def fingerprint(self, path: Path) -> ChromaprintEvidence:
        return self._analyze(path)[1]

    def commit_staging(self, key: OpaqueStorageKey, verified: VerifiedStagedFile) -> CommitResult:
        self._require_key(key)
        if verified != self._verified or self._analysis is None:
            raise ImmutableObjectConflictError()
        reply = self._exchange("PUBLISH")
        if (
            set(reply) != {"key", "already_present"}
            or reply["key"] != verified.sha256.hex
            or type(reply["already_present"]) is not bool
        ):
            raise ChildProtocolError()
        return CommitResult(OpaqueStorageKey(verified.sha256.hex), reply["already_present"])

    def cleanup_staging(self, key: OpaqueStorageKey) -> None:
        self._require_key(key)
        raise StorageSafetyError()

    def finish(self) -> None:
        if self._exchange("FINISH") != {"finished": True}:
            raise ChildProtocolError()
