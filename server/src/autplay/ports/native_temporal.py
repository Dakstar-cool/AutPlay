"""Private SQL materialization boundary, deliberately absent from public readers."""

from typing import Protocol

from autplay.domain.native_temporal import NativeTemporalSelection, NativeTemporalSnapshot


class NativeTemporalReader(Protocol):
    def materialize(self, selection: NativeTemporalSelection) -> NativeTemporalSnapshot: ...
