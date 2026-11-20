"""Phase-neutral counters and scoped derived-document ownership, without quota.

Implementations use their existing operation/child input and work limits. A live
output occupies those same byte caps in addition to permanent unique inputs.
Canonical width is bounded independently from conservative owned-container and
owned-string footprint; neither value claims to measure a Python process heap.
"""

from collections.abc import Callable
from typing import Protocol


class NativeRequestV3DocumentOutputLeaseProtocol(Protocol):
    def check_current(self) -> None: ...
    def retain_new_digests(self, n: int, /) -> None: ...
    def close(self) -> None: ...


class NativeRequestV3ExpectedMeterProtocol(Protocol):
    """The owning source lease remains current through both BUILD and VERIFY."""

    def reserve(self, *, byte_work_bound: int, node_visit_bound: int) -> None: ...
    def charge(
        self,
        *,
        bytes_read: int = 0,
        bytes_written: int = 0,
        sha_input_bytes: int = 0,
        node_visits: int = 0,
    ) -> None: ...
    def retain_scratch(self, n: int, /) -> None: ...
    def release_scratch(self, n: int, /) -> None: ...
    def retain_digests(self, n: int, /) -> None: ...
    def release_digests(self, n: int, /) -> None: ...
    def retain_document_output(
        self,
        *,
        canonical_byte_upper: int,
        owned_payload_upper: int,
        clear_callback: Callable[[], None],
    ) -> NativeRequestV3DocumentOutputLeaseProtocol: ...
