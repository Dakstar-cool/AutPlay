"""Immutable global independent-ledger snapshot descriptors for source3."""

from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from autplay.domain.native_original_capture_v3 import NativeOriginalCaptureV3DigestMeter
from autplay.domain.training_consent import (
    CompactedErasureFloor,
    SonaEventErasureIntent,
    TrainingConsentEvidenceError,
    TrainingConsentHistory,
    TrainingConsentIntent,
)

MAX_NATIVE_CONSENT_SNAPSHOT_BYTES_V3 = 2_097_152
NATIVE_CONSENT_LEGACY_VERIFIER_SHA256_V3 = (
    "2bca50277982fc41d7a7a1d95e09f107a88c16d30924881892e72debe1b8a525"
)


@dataclass(frozen=True, slots=True)
class NativeConsentStreamDescriptorV3:
    table: str
    rows: int
    utf8_bytes: int
    integer_fields: int
    text_fields: int
    document_bytes: int
    framed_bytes: int
    maximum_document_bytes: int = 0


@dataclass(frozen=True, slots=True)
class NativeConsentSnapshotPreludeV3:
    """Actual scalar counts from the same locked snapshot, before payload reads."""

    byte_length: int
    streams: tuple[NativeConsentStreamDescriptorV3, ...]
    file_version: tuple[int, ...]
    sha256: None = None
    codec_version: int = 3


@dataclass(frozen=True, slots=True)
class NativeConsentLedgerDescriptorV3:
    """Exact input counts and current file/SQLite version, without private paths."""

    device: int
    inode: int
    file_size: int
    modified_ns: int
    changed_ns: int
    page_count: int
    page_size: int
    schema_version: int
    byte_length: int
    snapshot_sha256: str
    streams: tuple[NativeConsentStreamDescriptorV3, ...]
    verifier_sha256: str = NATIVE_CONSENT_LEGACY_VERIFIER_SHA256_V3


@dataclass(slots=True)
class _SnapshotOwnership:
    meter: NativeOriginalCaptureV3DigestMeter
    history: TrainingConsentHistory | None
    operations: dict[UUID, TrainingConsentIntent]
    latest: dict[str, TrainingConsentIntent]
    erasures: tuple[SonaEventErasureIntent, ...] | None
    floors: tuple[CompactedErasureFloor, ...] | None
    descriptor: NativeConsentLedgerDescriptorV3 | None
    retained: int

    def close(self) -> None:
        if self.history is not None:
            self.operations.clear()
            self.latest.clear()
            self.history = self.erasures = self.floors = self.descriptor = None
            self.meter.release_digests(self.retained)
            self.retained = 0


@dataclass(frozen=True, slots=True, init=False)
class NativeTrainingConsentSnapshotV3:
    """Scoped globally verified histories; consumers must not export digest aliases."""

    _ownership: _SnapshotOwnership

    def __new__(cls) -> NativeTrainingConsentSnapshotV3:
        raise TrainingConsentEvidenceError()

    @classmethod
    def _from_verified(
        cls,
        history: TrainingConsentHistory,
        erasures: tuple[SonaEventErasureIntent, ...],
        floors: tuple[CompactedErasureFloor, ...],
        descriptor: NativeConsentLedgerDescriptorV3,
        *,
        meter: NativeOriginalCaptureV3DigestMeter,
        retained: int,
    ) -> NativeTrainingConsentSnapshotV3:
        nodes = len(history.operations) + len(history.latest)
        meter.reserve(byte_work_bound=0, node_visit_bound=nodes)
        meter.charge(node_visits=nodes)
        operations, latest = dict(history.operations), dict(history.latest)
        value = object.__new__(cls)
        object.__setattr__(
            value,
            "_ownership",
            _SnapshotOwnership(
                meter,
                TrainingConsentHistory(MappingProxyType(operations), MappingProxyType(latest)),
                operations,
                latest,
                erasures,
                floors,
                descriptor,
                retained,
            ),
        )
        return value

    @property
    def history(self) -> TrainingConsentHistory:
        value = self._ownership.history
        if value is None:
            raise TrainingConsentEvidenceError()
        return value

    @property
    def erasures(self) -> tuple[SonaEventErasureIntent, ...]:
        value = self._ownership.erasures
        if value is None:
            raise TrainingConsentEvidenceError()
        return value

    @property
    def floors(self) -> tuple[CompactedErasureFloor, ...]:
        value = self._ownership.floors
        if value is None:
            raise TrainingConsentEvidenceError()
        return value

    @property
    def descriptor(self) -> NativeConsentLedgerDescriptorV3:
        value = self._ownership.descriptor
        if value is None:
            raise TrainingConsentEvidenceError()
        return value

    def close(self) -> None:
        self._ownership.close()

    def __enter__(self) -> NativeTrainingConsentSnapshotV3:
        _ = self.history
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
