"""Explicit metered independent-ledger seam; the old ledger API is unchanged."""

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol
from uuid import UUID

from autplay.domain.native_original_capture_v3 import NativeOriginalCaptureV3DigestMeter
from autplay.domain.training_consent_v3 import (
    NativeConsentLedgerDescriptorV3,
    NativeConsentSnapshotPreludeV3,
    NativeTrainingConsentSnapshotV3,
)
from autplay.ports.training_consent import TrainingConsentLedger


class NativeConsentSnapshotSinkV3(Protocol):
    def update_bytes(self, data: bytes) -> None: ...

    def finish_stream(self) -> bytes: ...

    def abort(self) -> None: ...


type NativeConsentSnapshotAdmitterV3 = Callable[
    [NativeConsentSnapshotPreludeV3], NativeConsentSnapshotSinkV3
]


class TrainingConsentLedgerV3(TrainingConsentLedger, Protocol):
    def read_snapshot_v3(
        self,
        *,
        meter: NativeOriginalCaptureV3DigestMeter,
        expected: NativeConsentLedgerDescriptorV3 | None = None,
        admit_snapshot: NativeConsentSnapshotAdmitterV3 | None = None,
    ) -> NativeTrainingConsentSnapshotV3: ...

    def owner_tag_v3(
        self, user_id: UUID, *, meter: NativeOriginalCaptureV3DigestMeter
    ) -> AbstractContextManager[str]: ...

    def actor_tag_v3(
        self, device_id: UUID, *, meter: NativeOriginalCaptureV3DigestMeter
    ) -> AbstractContextManager[str]: ...
