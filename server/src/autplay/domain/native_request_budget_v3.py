"""One caller-owned logical work ledger for native served-request proofs V3.

These limits describe serialized payload and logical work, never process memory,
wall-clock time, persistence authority or production resource admission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MAX_RECORD_BYTES: Final = 2_097_152
MAX_PROOF_BYTES: Final = 33_554_432
MAX_RECORDS: Final = 64
MAX_TRACKS: Final = 5_000
MAX_ITEMS: Final = 100
MAX_RECORD_BYTE_WORK: Final = 320 * 1_048_576
MAX_PROOF_BYTE_WORK: Final = 6 * 1_073_741_824
MAX_RECORD_NODE_VISITS: Final = 40_000_000
MAX_PROOF_NODE_VISITS: Final = 768_000_000
MAX_RECORD_LEAF_VISITS: Final = 20_200
MAX_RETAINED_DIGEST_BYTES: Final = 16_384
MAX_RETAINED_SCRATCH_BYTES: Final = 131_072


class NativeRequestV3Error(ValueError):
    """A V3 refusal; arguments contain a stable code, never source content."""


def _amount(value: int) -> int:
    if type(value) is not int or value < 0:
        raise NativeRequestV3Error("V3_BUDGET_AMOUNT")
    return value


def _counts(tracks: int, items: int) -> None:
    if _amount(tracks) > MAX_TRACKS or _amount(items) > MAX_ITEMS:
        raise NativeRequestV3Error("V3_RECORD_COUNT")


def _reservation(raw_bytes: int, tracks: int, items: int) -> tuple[int, int]:
    return (
        144 * raw_bytes + 16 * 1_048_576,
        16 * (raw_bytes + 1) + 128 * (4 * tracks + 2 * items + 16),
    )


@dataclass(frozen=True, slots=True)
class NativeRequestV3Usage:
    """Read-only cumulative diagnostics, without any source values."""

    records: int
    raw_bytes: int
    byte_work: int
    node_visits: int
    reserved_byte_work: int
    reserved_node_visits: int
    retained_digest_bytes: int
    peak_digest_bytes: int
    retained_scratch_bytes: int
    peak_scratch_bytes: int


class NativeRequestV3Budget:
    """An explicit cumulative ledger; records cannot reset its counters."""

    def __init__(self) -> None:
        self._records = 0
        self._raw_bytes = 0
        self._byte_work = 0
        self._node_visits = 0
        self._reserved_byte_work = 0
        self._reserved_node_visits = 0
        self._digests = 0
        self._peak_digests = 0
        self._scratch = 0
        self._peak_scratch = 0
        self._active: NativeRequestV3Meter | None = None
        self._last_finished: NativeRequestV3Meter | None = None

    @property
    def usage(self) -> NativeRequestV3Usage:
        return NativeRequestV3Usage(
            self._records,
            self._raw_bytes,
            self._byte_work,
            self._node_visits,
            self._reserved_byte_work,
            self._reserved_node_visits,
            self._digests,
            self._peak_digests,
            self._scratch,
            self._peak_scratch,
        )

    def charge_wrapper(self, raw_bytes: int) -> None:
        """Charge any enclosing wrapper/descriptors in addition to record bytes."""
        size = _amount(raw_bytes)
        if self._raw_bytes + size > MAX_PROOF_BYTES:
            raise NativeRequestV3Error("V3_PROOF_BYTES")
        self._raw_bytes += size

    def charge_finalization(self, byte_work: int, node_visits: int = 1) -> None:
        """Charge a decision/seal to the last record's existing reservation.

        No serialized record, reservation or quota is recreated by finalization.
        A decision/seal is meaningful only for an already finished record.
        """
        meter = self._last_finished
        if self._active is not None or meter is None:
            raise NativeRequestV3Error("V3_FINALIZATION_STATE")
        size, nodes = _amount(byte_work), _amount(node_visits)
        meter._validate_bytes(size)
        meter._validate_nodes(nodes)
        meter.byte_work += size
        meter.node_visits += nodes
        self._byte_work += size
        self._node_visits += nodes

    def begin_record(
        self, raw_bytes: int, tracks: int = MAX_TRACKS, items: int = MAX_ITEMS
    ) -> NativeRequestV3Meter:
        """Admit complete source bytes and conservative work before scanning."""
        size = _amount(raw_bytes)
        _counts(tracks, items)
        if not 1 <= size <= MAX_RECORD_BYTES:
            raise NativeRequestV3Error("V3_RECORD_BYTES")
        if self._active is not None:
            raise NativeRequestV3Error("V3_RECORD_ACTIVE")
        if self._records >= MAX_RECORDS:
            raise NativeRequestV3Error("V3_PROOF_RECORDS")
        if self._raw_bytes + size > MAX_PROOF_BYTES:
            raise NativeRequestV3Error("V3_PROOF_BYTES")
        byte_work, nodes = _reservation(size, tracks, items)
        if self._reserved_byte_work + byte_work > MAX_PROOF_BYTE_WORK:
            raise NativeRequestV3Error("V3_PROOF_BYTE_WORK")
        if self._reserved_node_visits + nodes > MAX_PROOF_NODE_VISITS:
            raise NativeRequestV3Error("V3_PROOF_NODE_VISITS")
        meter = NativeRequestV3Meter(self, size, tracks, items, byte_work, nodes)
        self._records += 1
        self._raw_bytes += size
        self._reserved_byte_work += byte_work
        self._reserved_node_visits += nodes
        self._active = meter
        return meter


class NativeRequestV3Meter:
    """An admitted record's work; only its owning ledger can activate it."""

    def __init__(
        self,
        budget: NativeRequestV3Budget,
        raw_bytes: int,
        tracks: int,
        items: int,
        reserved_byte_work: int,
        reserved_node_visits: int,
    ) -> None:
        self._budget = budget
        self.raw_bytes = raw_bytes
        self.tracks = tracks
        self.items = items
        self.reserved_byte_work = reserved_byte_work
        self.reserved_node_visits = reserved_node_visits
        self.byte_work = 0
        self.node_visits = 0
        self.leaf_visits = 0
        self._digests = 0
        self._scratch = 0
        self._finished = False

    def _check_active(self) -> None:
        if self._finished or self._budget._active is not self:
            raise NativeRequestV3Error("V3_RECORD_CLOSED")

    def set_counts(self, tracks: int, items: int) -> None:
        """Narrow preflight's conservative counts after validating raw paths."""
        self._check_active()
        _counts(tracks, items)
        if tracks > self.tracks or items > self.items:
            raise NativeRequestV3Error("V3_RECORD_COUNT")
        byte_work, nodes = _reservation(self.raw_bytes, tracks, items)
        if self.node_visits > nodes:
            raise NativeRequestV3Error("V3_RECORD_NODE_VISITS")
        self._budget._reserved_byte_work += byte_work - self.reserved_byte_work
        self._budget._reserved_node_visits += nodes - self.reserved_node_visits
        self.tracks, self.items = tracks, items
        self.reserved_byte_work, self.reserved_node_visits = byte_work, nodes

    def set_raw_bytes(self, raw_bytes: int) -> None:
        """Narrow a builder's admitted maximum to its exact emitted envelope."""
        self._check_active()
        size = _amount(raw_bytes)
        if not 1 <= size <= self.raw_bytes:
            raise NativeRequestV3Error("V3_RECORD_BYTES")
        byte_work, nodes = _reservation(size, self.tracks, self.items)
        if self.byte_work > byte_work or self.node_visits > nodes:
            raise NativeRequestV3Error("V3_RESERVATION_EXCEEDED")
        self._budget._raw_bytes += size - self.raw_bytes
        self._budget._reserved_byte_work += byte_work - self.reserved_byte_work
        self._budget._reserved_node_visits += nodes - self.reserved_node_visits
        self.raw_bytes = size
        self.reserved_byte_work, self.reserved_node_visits = byte_work, nodes

    def charge_bytes(self, n: int) -> None:
        self._check_active()
        size = _amount(n)
        self._validate_bytes(size)
        self.byte_work += size
        self._budget._byte_work += size

    def _validate_bytes(self, size: int) -> None:
        if self.byte_work + size > min(self.reserved_byte_work, MAX_RECORD_BYTE_WORK):
            raise NativeRequestV3Error("V3_RECORD_BYTE_WORK")
        if self._budget._byte_work + size > MAX_PROOF_BYTE_WORK:
            raise NativeRequestV3Error("V3_PROOF_BYTE_WORK")

    def charge_nodes(self, n: int = 1) -> None:
        self._check_active()
        count = _amount(n)
        self._validate_nodes(count)
        self.node_visits += count
        self._budget._node_visits += count

    def _validate_nodes(self, count: int) -> None:
        if self.node_visits + count > min(self.reserved_node_visits, MAX_RECORD_NODE_VISITS):
            raise NativeRequestV3Error("V3_RECORD_NODE_VISITS")
        if self._budget._node_visits + count > MAX_PROOF_NODE_VISITS:
            raise NativeRequestV3Error("V3_PROOF_NODE_VISITS")

    def charge_leaf(self, n: int = 1) -> None:
        self._check_active()
        count = _amount(n)
        if self.leaf_visits + count > min(4 * self.tracks + 2 * self.items, MAX_RECORD_LEAF_VISITS):
            raise NativeRequestV3Error("V3_RECORD_LEAF_VISITS")
        self.leaf_visits += count

    def retain_digests(self, n: int) -> None:
        self._check_active()
        size = _amount(n)
        if self._budget._digests + size > MAX_RETAINED_DIGEST_BYTES:
            raise NativeRequestV3Error("V3_DIGEST_PAYLOAD")
        self._digests += size
        self._budget._digests += size
        self._budget._peak_digests = max(self._budget._peak_digests, self._budget._digests)

    def release_digests(self, n: int) -> None:
        self._check_active()
        size = _amount(n)
        if size > self._digests:
            raise NativeRequestV3Error("V3_DIGEST_RELEASE")
        self._digests -= size
        self._budget._digests -= size

    def retain_scratch(self, n: int) -> None:
        self._check_active()
        size = _amount(n)
        if self._budget._scratch + size > MAX_RETAINED_SCRATCH_BYTES:
            raise NativeRequestV3Error("V3_SCRATCH_PAYLOAD")
        self._scratch += size
        self._budget._scratch += size
        self._budget._peak_scratch = max(self._budget._peak_scratch, self._budget._scratch)

    def release_scratch(self, n: int) -> None:
        self._check_active()
        size = _amount(n)
        if size > self._scratch:
            raise NativeRequestV3Error("V3_SCRATCH_RELEASE")
        self._scratch -= size
        self._budget._scratch -= size

    def finish_record(self) -> None:
        """Close successfully only after all temporary payloads are released."""
        self._check_active()
        if self._digests or self._scratch:
            raise NativeRequestV3Error("V3_LIVE_PAYLOAD")
        self._finished = True
        self._budget._active = None
        self._budget._last_finished = self
