"""B-owned admitted-builder callbacks, extending G's unchanged read protocol."""

from typing import Protocol

from autplay.domain.native_request_meter_v3 import NativeRequestV3MeterProtocol


class NativeRequestV3BuildMeterProtocol(NativeRequestV3MeterProtocol, Protocol):
    """Bind emitted root bytes without opening, closing or renewing a lease."""

    def set_raw_bytes(self, raw_bytes: int) -> None: ...
