"""Transaction-owned connected V3 admission and cumulative work accounting.

Reservations authorize work before allocation. They are not credits: consumed
work survives failed projections and closed leases. No SQL authority is created
by this module; completion requires the shared verified record value.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from enum import StrEnum
from hashlib import sha256
from struct import unpack
from typing import Final, Protocol, cast
from uuid import UUID

from autplay.domain.native_connected_request_v3 import (
    MAX_CONNECTED_AGGREGATE_BYTES,
    MAX_CONNECTED_AGGREGATE_RECORDS,
    MAX_CONNECTED_DEPENDENCY_BYTES,
    MAX_CONNECTED_PACKETS,
    MAX_CONNECTED_PROOF_BYTES,
    MAX_CONNECTED_RECORD_BYTES,
    NativeConnectedCountsV3,
    NativeConnectedDependencyDescriptorV3,
    NativeConnectedDependencyKindV3,
    NativeConnectedDependencySizeModeV3,
    NativeConnectedRangeV3,
    NativeConnectedSelectionV3,
    NativeConnectedV3Error,
    NativeP11UnvalidatedHeadV3,
    VerifiedConnectedRecordV3,
    require_digest_v3,
    require_hex_v3,
    require_int_v3,
    require_uuid_v3,
)
from autplay.domain.native_original_capture_v3 import MAX_ORIGINAL_BASELINE_V4_BYTES
from autplay.domain.native_connected_wire_v3 import (
    NativeConnectedFrameSegmentsV3,
    revoke_native_connected_frame_v3,
)
from autplay.domain.native_original_capture_v3 import NATIVE_SOURCE3_CODEC_SHA256
from autplay.domain.native_request_roots_v3 import (
    NativeP11CompletionAttestationV3,
    _consume_owned_native_p11_completion_v3,
)

MAX_BYTE_WORK: Final = 6 * 1024**3
MAX_NODE_VISITS: Final = 768_000_000
MAX_RECORD_BYTE_WORK: Final = 320 * 1024**2
MAX_RECORD_NODE_VISITS: Final = 40_000_000
MAX_LIVE_DIGEST_BYTES: Final = 16 * 1024
MAX_LIVE_SCRATCH_BYTES: Final = 128 * 1024
_PRIVATE: Final = object()
_SOURCE3_CODEC_SHA256: Final = bytes.fromhex(NATIVE_SOURCE3_CODEC_SHA256)


class _StreamingDigestV3(Protocol):
    def update(self, data: bytes | memoryview, /) -> None: ...

    def digest(self) -> bytes: ...


_SHARED: Final = frozenset(
    (
        NativeConnectedDependencyKindV3.MODEL,
        NativeConnectedDependencyKindV3.LICENSE,
        NativeConnectedDependencyKindV3.TOKENIZER,
        NativeConnectedDependencyKindV3.SQL_COMPILER,
        NativeConnectedDependencyKindV3.NEGATIVE_LEDGER,
        NativeConnectedDependencyKindV3.VECTORS,
    )
)


def _fail(code: str) -> None:
    raise NativeConnectedV3Error("CONNECTED_V3_" + code)


def _transaction(value: tuple[int, int]) -> tuple[int, int]:
    if type(value) is not tuple or len(value) != 2:
        _fail("TRANSACTION")
    for part in value:
        require_int_v3(part, 1, 2**64 - 1)
    return value


def _work(read: int, written: int, hashed: int, nodes: int) -> tuple[int, int]:
    for amount in (read, written, hashed, nodes):
        require_int_v3(amount, 0, MAX_BYTE_WORK)
    return read + written + hashed, nodes


def wire_reservation_v3(size: int, counts: NativeConnectedCountsV3) -> tuple[int, int]:
    """Frozen conservative record formula, before source hydration."""
    require_int_v3(size, 1, MAX_CONNECTED_RECORD_BYTES)
    if type(counts) is not NativeConnectedCountsV3:
        _fail("COUNTS")
    structural = (
        4 * counts.tracks
        + 2 * counts.items
        + 8 * counts.facts
        + 8 * counts.evidence
        + 8 * counts.outcomes
        + 32 * counts.dimensions
        + 16
    )
    return 144 * size + 16 * 1024**2, 16 * (size + 1) + 128 * structural


class NativeConnectedPhaseV3(StrEnum):
    INITIAL = "INITIAL"
    RECHECK = "RECHECK"
    CONSUMER = "CONSUMER"


@dataclass(slots=True)
class _Record:
    owner: UUID
    served: UUID
    ordinal: int
    upper: int
    counts: NativeConnectedCountsV3
    byte_limit: int
    node_limit: int
    byte_work: int = 0
    node_visits: int = 0
    dependency_byte_limit: int = 0
    dependency_node_limit: int = 0
    discovery: bool = False
    length: int | None = None
    ranges: tuple[NativeConnectedRangeV3, ...] | None = None
    digest: bytes | None = None
    original: UUID | None = None
    completed: bool = False
    head_digest: bytes | None = None
    dependencies: set[tuple[NativeConnectedDependencyKindV3, UUID, UUID]] = field(
        default_factory=set
    )


@dataclass(slots=True)
class _Dependency:
    descriptor: NativeConnectedDependencyDescriptorV3
    digest: bytes | None = None
    actual_length: int | None = None


@dataclass(frozen=True, slots=True)
class NativeConnectedUsageV3:
    byte_work: int
    node_visits: int
    raw_proof_bytes: int
    physical_records: int
    dependency_bytes: int
    live_digest_bytes: int
    live_scratch_bytes: int
    reserved_byte_work: int
    reserved_node_visits: int
    live_document_output_bytes: int = 0


def _initialize_connected_aggregate_v3(parent: NativeConnectedAggregateBudgetV3) -> None:
    """Initialize one accounting registry without inventing a transaction."""
    if hasattr(parent, "_byte_work") or hasattr(parent, "_proofs"):
        _fail("AGGREGATE_ALREADY_INITIALIZED")
    parent._byte_work = 0
    parent._node_visits = 0
    parent._raw_proof_bytes = 0
    parent._physical_records = 0
    parent._dependency_bytes = 0
    parent._live_digest_bytes = 0
    parent._live_scratch_bytes = 0
    parent._reserved_bytes = 0
    parent._reserved_nodes = 0
    parent._proofs = {}
    parent._selected_served = set()
    parent._identities = {}
    parent._document_outputs = {}
    parent._shared_document_outputs = 0
    parent._live_document_output_bytes = 0
    parent._output_closing = False
    parent._poisoned = False
    parent._lease = None
    parent._finished = False


class NativeConnectedAggregateBudgetV3:
    """One operation's actual work, physical admissions and shared live payload."""

    _byte_work: int
    _node_visits: int
    _raw_proof_bytes: int
    _physical_records: int
    _dependency_bytes: int
    _live_digest_bytes: int
    _live_scratch_bytes: int
    _reserved_bytes: int
    _reserved_nodes: int
    _proofs: dict[bytes, NativeConnectedV3Budget]
    _selected_served: set[UUID]
    _identities: dict[int, _RetainedIdentityV3]
    _document_outputs: dict[
        int, NativeConnectedDocumentOutputLeaseV3 | NativeConnectedSharedDocumentOutputLeaseV3
    ]
    _shared_document_outputs: int
    _live_document_output_bytes: int
    _output_closing: bool
    _poisoned: bool
    _lease: NativeConnectedPacketLeaseV3 | None
    _finished: bool

    def __init__(self, transaction_binding: tuple[int, int]) -> None:
        if hasattr(self, "_byte_work") or hasattr(self, "_proofs"):
            _fail("AGGREGATE_ALREADY_INITIALIZED")
        self._transaction_binding = _transaction(transaction_binding)
        _initialize_connected_aggregate_v3(self)

    @property
    def byte_work(self) -> int:
        return self._byte_work

    @property
    def node_visits(self) -> int:
        return self._node_visits

    @property
    def raw_proof_bytes(self) -> int:
        return self._raw_proof_bytes

    @property
    def physical_records(self) -> int:
        return self._physical_records

    @property
    def dependency_bytes(self) -> int:
        return self._dependency_bytes

    @property
    def live_digest_bytes(self) -> int:
        return self._live_digest_bytes

    @property
    def live_scratch_bytes(self) -> int:
        return self._live_scratch_bytes

    @property
    def live_document_output_bytes(self) -> int:
        return self._live_document_output_bytes

    def _check_usable(self) -> None:
        if self._poisoned:
            _fail("OUTPUT_OPERATION_POISONED")

    @property
    def transaction_binding(self) -> tuple[int, int]:
        return self._transaction_binding

    @property
    def usage(self) -> NativeConnectedUsageV3:
        return NativeConnectedUsageV3(
            self._byte_work,
            self._node_visits,
            self._raw_proof_bytes,
            self._physical_records,
            self._dependency_bytes,
            self._live_digest_bytes,
            self._live_scratch_bytes,
            self._reserved_bytes,
            self._reserved_nodes,
            self._live_document_output_bytes,
        )

    def open_proof(
        self,
        *,
        packet_identity: bytes,
        selection: NativeConnectedSelectionV3,
        raw_proof_upper_bound: int,
    ) -> NativeConnectedV3Budget:
        self._check_usable()
        require_digest_v3(packet_identity)
        require_int_v3(raw_proof_upper_bound, 1, MAX_CONNECTED_PROOF_BYTES)
        if type(selection) is not NativeConnectedSelectionV3 or self._finished:
            _fail("PROOF_STATE")
        previous = self._proofs.get(packet_identity)
        if previous is not None:
            if previous.selection != selection or previous.raw_upper != raw_proof_upper_bound:
                _fail("PROOF_IDENTITY")
            return previous
        if len(self._proofs) >= MAX_CONNECTED_PACKETS:
            _fail("PACKET_LIMIT")
        if any(served in self._selected_served for _, served in selection.members):
            _fail("PACKET_SELECTION_OVERLAP")
        self._retain(32, digest=True)
        proof = NativeConnectedV3Budget(
            _PRIVATE, self, packet_identity, selection, raw_proof_upper_bound
        )
        self._proofs[packet_identity] = proof
        self._selected_served.update(served for _, served in selection.members)
        return proof

    def retain_identity(self, value: bytes | str) -> NativeConnectedIdentityLeaseV3:
        """Retain one concrete digest object under the operation's live limit."""
        self._check_usable()
        if self._finished:
            _fail("IDENTITY_LEASE_STATE")
        if type(value) is bytes:
            require_digest_v3(value)
        elif type(value) is str:
            require_hex_v3(value)
        else:
            _fail("IDENTITY_LEASE_VALUE")
        key = id(value)
        entry = self._identities.get(key)
        if entry is None:
            self._retain(len(value), digest=True)
            entry = _RetainedIdentityV3(value)
            self._identities[key] = entry
        elif entry.value is not value:
            _fail("IDENTITY_LEASE_STATE")
        lease = NativeConnectedIdentityLeaseV3(_PRIVATE, self, value)
        entry.leases[id(lease)] = lease
        return lease

    def begin_packet_lease(
        self, proof: NativeConnectedV3Budget, *, phase: NativeConnectedPhaseV3
    ) -> NativeConnectedPacketLeaseV3:
        self._check_usable()
        if (
            self._finished
            or self._lease is not None
            or type(proof) is not NativeConnectedV3Budget
            or proof._parent is not self
            or type(phase) is not NativeConnectedPhaseV3
        ):
            _fail("LEASE_STATE")
        lease = NativeConnectedPacketLeaseV3(_PRIVATE, proof, phase)
        # Opaque fragment wire work is separately bounded from record work.
        self._reserve(8 * proof.raw_upper + 65_536, proof.raw_upper + 65_536)
        lease._wire_bytes = 8 * proof.raw_upper + 65_536
        lease._wire_nodes = proof.raw_upper + 65_536
        self._lease = lease
        proof._complete = False
        return lease

    def close_packet_lease(self, lease: NativeConnectedPacketLeaseV3) -> None:
        if self._output_closing:
            self._poisoned = True
            _fail("OUTPUT_REENTRANT")
        if (
            type(lease) is not NativeConnectedPacketLeaseV3
            or self._lease is not lease
            or lease._closing
        ):
            _fail("LEASE_STATE")
        lease._closing = True
        shared_work_uncleared = False
        try:
            while self._document_outputs:
                output = next(reversed(self._document_outputs.values()))
                if output._packet is not lease:
                    self._poisoned = True
                    _fail("OUTPUT_LEASE_STATE")
                output.close()
            try:
                if lease._wire_meter is not None:
                    revoke_native_connected_frame_v3(lease._wire_meter)
            finally:
                if lease._record is not None:
                    try:
                        revoke_native_connected_frame_v3(lease._record._source_meter)
                    finally:
                        lease._record._run_projection_cleanup()
        finally:
            while lease._shared_work_meters:
                shared = next(iter(lease._shared_work_meters.values()))
                if shared._close_for_packet():
                    shared_work_uncleared = True
                    self._poisoned = True
            if lease._record is not None:
                lease._record._active = False
                lease._record._bound_digest = None
                lease._record._fragment_digests = None
                lease._record._digests = 0
                lease._record._scratch = 0
                lease._record = None
            for dependency in lease._dependencies:
                dependency._active = False
                dependency._stream = None
                dependency._stream_retained = False
                dependency._bound_digest = None
            self._reserved_bytes = 0
            self._reserved_nodes = 0
            # Temporary projections cannot survive a lease; persistent digests remain.
            if not self._poisoned:
                self._live_digest_bytes -= lease._temporary_digests
                self._live_scratch_bytes -= lease._temporary_scratch
            lease._closed = True
            self._lease = None
        if shared_work_uncleared:
            _fail("SHARED_WORK_UNCLEARED")

    def finish_source_compilation(self, *, packet_identities: tuple[bytes, ...]) -> None:
        self._check_usable()
        if self._lease is not None or self._finished or type(packet_identities) is not tuple:
            _fail("COMPILATION_STATE")
        if (
            not self._proofs
            or self._identities
            or self._document_outputs
            or len(packet_identities) != len(self._proofs)
            or len(set(packet_identities)) != len(packet_identities)
            or set(packet_identities) != set(self._proofs)
            or any(not proof._complete for proof in self._proofs.values())
        ):
            _fail("COMPILATION_INCOMPLETE")
        self._finished = True

    def _reserve(self, byte_work: int, nodes: int) -> None:
        if (
            self._byte_work + self._reserved_bytes + byte_work > MAX_BYTE_WORK
            or self._node_visits + self._reserved_nodes + nodes > MAX_NODE_VISITS
        ):
            _fail("AGGREGATE_WORK_LIMIT")
        self._reserved_bytes += byte_work
        self._reserved_nodes += nodes

    def _consume(self, byte_work: int, nodes: int) -> None:
        if byte_work > self._reserved_bytes or nodes > self._reserved_nodes:
            _fail("UNRESERVED_WORK")
        self._byte_work += byte_work
        self._node_visits += nodes
        self._reserved_bytes -= byte_work
        self._reserved_nodes -= nodes

    def _retain(self, amount: int, *, digest: bool) -> None:
        require_int_v3(amount, 0, MAX_LIVE_SCRATCH_BYTES)
        current = self._live_digest_bytes if digest else self._live_scratch_bytes
        maximum = MAX_LIVE_DIGEST_BYTES if digest else MAX_LIVE_SCRATCH_BYTES
        if current + amount > maximum:
            _fail("LIVE_DIGEST_LIMIT" if digest else "LIVE_SCRATCH_LIMIT")
        if digest:
            self._live_digest_bytes += amount
        else:
            self._live_scratch_bytes += amount


@dataclass(slots=True)
class _RetainedIdentityV3:
    value: bytes | str
    leases: dict[int, NativeConnectedIdentityLeaseV3] = field(default_factory=dict)


class NativeConnectedIdentityLeaseV3:
    """An object-identity retention receipt, never a source or SQL grant."""

    __slots__ = ("_closed", "_identity", "_parent", "_value")

    def __init__(
        self, token: object, parent: NativeConnectedAggregateBudgetV3, value: bytes | str
    ) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_ADMISSION")
        self._parent = parent
        self._value = value
        self._identity = id(self)
        self._closed = False

    def check_current(self, value: bytes | str | None = None) -> None:
        self._parent._check_usable()
        entry = self._parent._identities.get(id(self._value))
        if (
            id(self) != self._identity
            or self._closed
            or self._parent._finished
            or entry is None
            or entry.value is not self._value
            or entry.leases.get(id(self)) is not self
            or (value is not None and value is not entry.value)
        ):
            _fail("IDENTITY_LEASE_STATE")

    def close(self) -> None:
        if id(self) != self._identity:
            _fail("IDENTITY_LEASE_STATE")
        if self._closed:
            return
        self.check_current()
        key = id(self._value)
        entry = self._parent._identities[key]
        del entry.leases[id(self)]
        if not entry.leases:
            self._parent._live_digest_bytes -= len(entry.value)
            del self._parent._identities[key]
        self._closed = True
        # The owner already keeps the actual identity value; the closed receipt
        # must not retain its own additional payload reference across scopes.
        self._value = b""


class NativeConnectedPacketLeaseV3:
    def __init__(
        self, token: object, proof: NativeConnectedV3Budget, phase: NativeConnectedPhaseV3
    ) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_ADMISSION")
        self.proof = proof
        self.phase = phase
        self._closed = False
        self._closing = False
        self._record: NativeConnectedRecordAdmissionV3 | None = None
        self._dependencies: list[NativeConnectedDependencyAdmissionV3] = []
        self._wire_bytes = 0
        self._wire_nodes = 0
        self._wire_admitted = False
        self._wire_narrowed = False
        self._wire_meter: NativeConnectedWireMeterV3 | None = None
        self._shared_work_meters: dict[int, NativeConnectedSharedWorkMeterV3] = {}
        self._temporary_digests = 0
        self._temporary_scratch = 0
        self._completed_records: set[tuple[UUID, UUID]] = set()
        self._completed_dependencies: set[tuple[NativeConnectedDependencyKindV3, UUID, UUID]] = (
            set()
        )
        self._completed_dependency_uses: set[
            tuple[UUID, UUID, NativeConnectedDependencyKindV3, UUID, UUID]
        ] = set()

    def __enter__(self) -> NativeConnectedPacketLeaseV3:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.proof._parent.close_packet_lease(self)


class NativeConnectedV3Budget:
    """Physical packet registry; never a fresh operation work quota."""

    def __init__(
        self,
        token: object,
        parent: NativeConnectedAggregateBudgetV3,
        identity: bytes,
        selection: NativeConnectedSelectionV3,
        raw_upper: int,
    ) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_ADMISSION")
        self._parent = parent
        self._packet_identity = identity
        self._selection = selection
        self._raw_upper = raw_upper
        self._raw_proof_bytes: int | None = None
        self._dependency_bytes = 0
        self._live_document_output_bytes = 0
        self._metadata_bytes = 0
        self._sql_bytes = 0
        self._vector_bytes = 0
        self._proof_digest: bytes | None = None
        self._complete = False
        self._records: dict[tuple[UUID, UUID], _Record] = {}
        self._dependencies: dict[
            tuple[NativeConnectedDependencyKindV3, UUID, UUID], _Dependency
        ] = {}

    @property
    def packet_identity(self) -> bytes:
        return self._packet_identity

    @property
    def selection(self) -> NativeConnectedSelectionV3:
        return self._selection

    @property
    def raw_upper(self) -> int:
        return self._raw_upper

    @property
    def raw_proof_bytes(self) -> int | None:
        return self._raw_proof_bytes

    @property
    def dependency_bytes(self) -> int:
        return self._dependency_bytes

    def _lease(self) -> NativeConnectedPacketLeaseV3:
        self._parent._check_usable()
        lease = self._parent._lease
        if lease is None or lease.proof is not self or lease._closed:
            _fail("NO_ACTIVE_PACKET")
        assert lease is not None
        return lease

    def wire_meter(self) -> NativeConnectedWireMeterV3:
        lease = self._lease()
        if lease._wire_meter is None:
            lease._wire_meter = NativeConnectedWireMeterV3(_PRIVATE, self, lease)
        return lease._wire_meter

    def shared_work_meter(
        self, *, byte_work_bound: int, node_visit_bound: int
    ) -> NativeConnectedSharedWorkMeterV3:
        """Prepay packet work; exact input admission remains a separate operation."""
        lease = self._lease()
        if self._parent._output_closing:
            self._parent._poisoned = True
            _fail("OUTPUT_REENTRANT")
        if lease._closing:
            _fail("SHARED_WORK_STATE")
        require_int_v3(byte_work_bound, 0, MAX_BYTE_WORK)
        require_int_v3(node_visit_bound, 0, MAX_NODE_VISITS)
        self._parent._reserve(byte_work_bound, node_visit_bound)
        try:
            self._parent._retain(96, digest=False)
        except BaseException:
            self._parent._reserved_bytes -= byte_work_bound
            self._parent._reserved_nodes -= node_visit_bound
            raise
        lease._temporary_scratch += 96
        meter = NativeConnectedSharedWorkMeterV3(
            _PRIVATE, self, lease, byte_work_bound, node_visit_bound
        )
        lease._shared_work_meters[id(meter)] = meter
        return meter

    def begin_record(
        self,
        *,
        owner: UUID,
        served_request: UUID,
        total_wire_upper_bound: int,
        counts_upper_bound: NativeConnectedCountsV3,
    ) -> NativeConnectedRecordAdmissionV3:
        return self._begin_record(
            owner=owner,
            served_request=served_request,
            total_wire_upper_bound=total_wire_upper_bound,
            counts_upper_bound=counts_upper_bound,
            discovery=False,
        )

    def begin_record_discovery(
        self, *, owner: UUID, served_request: UUID
    ) -> NativeConnectedRecordAdmissionV3:
        """Prepay one physical record before its bounded scalar head is known.

        This grants work accounting only. Whole JSON hydration and source/root
        validation still require complete closure admission and exact narrowing.
        """
        return self._begin_record(
            owner=owner,
            served_request=served_request,
            total_wire_upper_bound=MAX_CONNECTED_RECORD_BYTES,
            counts_upper_bound=NativeConnectedCountsV3(5000, 100, 10_000, 20_000, 5000, 512),
            discovery=True,
        )

    def _begin_record(
        self,
        *,
        owner: UUID,
        served_request: UUID,
        total_wire_upper_bound: int,
        counts_upper_bound: NativeConnectedCountsV3,
        discovery: bool,
    ) -> NativeConnectedRecordAdmissionV3:
        lease = self._lease()
        require_uuid_v3(owner)
        require_uuid_v3(served_request)
        if lease._record is not None or (owner, served_request) in self._records:
            _fail("RECORD_STATE")
        try:
            ordinal = self._selection.members.index((owner, served_request)) + 1
        except ValueError:
            _fail("RECORD_SELECTION")
            raise AssertionError from None
        if discovery:
            byte_limit, node_limit = MAX_RECORD_BYTE_WORK, MAX_RECORD_NODE_VISITS
        else:
            byte_limit, node_limit = wire_reservation_v3(total_wire_upper_bound, counts_upper_bound)
        if byte_limit > MAX_RECORD_BYTE_WORK or node_limit > MAX_RECORD_NODE_VISITS:
            _fail("RECORD_WORK_LIMIT")
        if self._parent.physical_records >= MAX_CONNECTED_AGGREGATE_RECORDS:
            _fail("RECORD_LIMIT")
        self._parent._reserve(byte_limit, node_limit)
        state = _Record(
            owner,
            served_request,
            ordinal,
            total_wire_upper_bound,
            counts_upper_bound,
            byte_limit,
            node_limit,
            discovery=discovery,
        )
        self._records[(owner, served_request)] = state
        self._parent._physical_records += 1
        admission = NativeConnectedRecordAdmissionV3(_PRIVATE, self, lease, state)
        lease._record = admission
        return admission

    def begin_recheck(
        self, handle: NativeConnectedRecheckV3, *, transaction_binding: tuple[int, int]
    ) -> NativeConnectedRecordAdmissionV3:
        lease = self._lease()
        if (
            type(handle) is not NativeConnectedRecheckV3
            or handle._proof is not self
            or transaction_binding != self._parent.transaction_binding
            or _transaction(transaction_binding) != handle._transaction
            or lease._record is not None
            or not handle._state.completed
        ):
            _fail("RECHECK_BINDING")
        return _begin_registered_recheck_v3(self, lease, handle._state)

    def reserve_dependency(
        self,
        *,
        record: NativeConnectedRecordAdmissionV3 | None,
        descriptor: NativeConnectedDependencyDescriptorV3,
        byte_work_bound: int,
        node_visit_bound: int,
    ) -> NativeConnectedDependencyAdmissionV3:
        if self._parent._output_closing:
            self._parent._poisoned = True
            _fail("OUTPUT_REENTRANT")
        lease = self._lease()
        if type(descriptor) is not NativeConnectedDependencyDescriptorV3:
            _fail("DEPENDENCY_DESCRIPTOR")
        require_int_v3(byte_work_bound, descriptor.byte_length, MAX_BYTE_WORK)
        require_int_v3(node_visit_bound, 0, MAX_NODE_VISITS)
        shared = descriptor.kind in _SHARED
        if shared != (record is None):
            _fail("DEPENDENCY_SCOPE")
        if record is not None:
            record._check()
            if record._proof is not self or descriptor.owner_user_id != record._state.owner:
                _fail("DEPENDENCY_SCOPE")
        if descriptor.kind is NativeConnectedDependencyKindV3.TOKENIZER:
            require_int_v3(descriptor.byte_length, 0, 65_536)
        key = (descriptor.kind, descriptor.owner_user_id, descriptor.entity_id)
        state = self._dependencies.get(key)
        if state is not None:
            if state.descriptor != descriptor:
                _fail("DEPENDENCY_IDENTITY")
        else:
            if (
                self._dependency_bytes + self._live_document_output_bytes + descriptor.byte_length
                > MAX_CONNECTED_DEPENDENCY_BYTES
                or self._parent.dependency_bytes
                + self._parent.live_document_output_bytes
                + descriptor.byte_length
                > MAX_CONNECTED_AGGREGATE_BYTES
            ):
                _fail("DEPENDENCY_INPUT_LIMIT")
            metadata = descriptor.kind in (
                NativeConnectedDependencyKindV3.MODEL,
                NativeConnectedDependencyKindV3.LICENSE,
                NativeConnectedDependencyKindV3.TOKENIZER,
            )
            if metadata and self._metadata_bytes + descriptor.byte_length > 1024**2:
                _fail("METADATA_LIMIT")
            if (
                descriptor.kind is NativeConnectedDependencyKindV3.SQL_COMPILER
                and self._sql_bytes + descriptor.byte_length > 8 * 1024**2
            ):
                _fail("SQL_COMPILER_LIMIT")
            if (
                descriptor.kind is NativeConnectedDependencyKindV3.VECTORS
                and self._vector_bytes + descriptor.byte_length > 16 * 1024**2
            ):
                _fail("VECTOR_LIMIT")
            state = _Dependency(descriptor)
            if descriptor.size_mode is NativeConnectedDependencySizeModeV3.EXACT:
                state.actual_length = descriptor.byte_length
            if descriptor.sha256 is not None:
                self._parent._retain(32, digest=True)
            self._dependencies[key] = state
            self._dependency_bytes += descriptor.byte_length
            self._parent._dependency_bytes += descriptor.byte_length
            if metadata:
                self._metadata_bytes += descriptor.byte_length
            if descriptor.kind is NativeConnectedDependencyKindV3.SQL_COMPILER:
                self._sql_bytes += descriptor.byte_length
            if descriptor.kind is NativeConnectedDependencyKindV3.VECTORS:
                self._vector_bytes += descriptor.byte_length
        if record is None:
            self._parent._reserve(byte_work_bound, node_visit_bound)
        elif key not in record._state.dependencies:
            record._extend(byte_work_bound, node_visit_bound)
            record._state.dependencies.add(key)
        else:
            record.source_meter().reserve(
                byte_work_bound=byte_work_bound, node_visit_bound=node_visit_bound
            )
        admission = NativeConnectedDependencyAdmissionV3(
            _PRIVATE, self, lease, state, record, byte_work_bound, node_visit_bound
        )
        lease._dependencies.append(admission)
        return admission

    def finish_proof(self, *, exact_outer_bytes: int, proof_sha256: bytes) -> None:
        lease = self._lease()
        require_digest_v3(proof_sha256)
        if (
            lease._record is not None
            or self._raw_proof_bytes != exact_outer_bytes
            or len(self._records) != len(self._selection.members)
            or any(not record.completed for record in self._records.values())
            or lease._completed_records != set(self._selection.members)
            or lease._completed_dependencies != set(self._dependencies)
            or any(dependency.digest is None for dependency in self._dependencies.values())
            or any(dependency._active for dependency in lease._dependencies)
            or sum(record.length or 0 for record in self._records.values()) > exact_outer_bytes
            or not lease._wire_narrowed
            or lease._temporary_digests != 0
            or lease._temporary_scratch != 0
            or self._live_document_output_bytes != 0
        ):
            _fail("PROOF_INCOMPLETE")
        if self._proof_digest is not None and self._proof_digest != proof_sha256:
            _fail("PROOF_IDENTITY")
        if self._proof_digest is None:
            self._parent._retain(32, digest=True)
            self._proof_digest = proof_sha256
        self._complete = True


def _begin_registered_recheck_v3(
    proof: NativeConnectedV3Budget,
    lease: NativeConnectedPacketLeaseV3,
    state: _Record,
) -> NativeConnectedRecordAdmissionV3:
    """Re-admit existing physical state using only remaining cumulative work."""
    proof._parent._reserve(state.byte_limit - state.byte_work, state.node_limit - state.node_visits)
    admission = NativeConnectedRecordAdmissionV3(_PRIVATE, proof, lease, state)
    lease._record = admission
    return admission


def _clear_document_output_v3(
    output: NativeConnectedDocumentOutputLeaseV3 | NativeConnectedSharedDocumentOutputLeaseV3,
) -> None:
    """Shared exact-owner cleanup; preserve all charges on uncertain clearing."""
    parent = output._parent
    parent._output_closing = output._closing = True
    succeeded = False
    try:
        assert output._callback is not None
        result = cast(Callable[[], object], output._callback)()
        if inspect.isawaitable(result):
            cancel = getattr(result, "close", None)
            if not callable(cancel):
                cancel = getattr(result, "cancel", None)
            if callable(cancel):
                cancel()
            _fail("OUTPUT_ASYNCHRONOUS_CLEAR")
        if result is not None:
            _fail("OUTPUT_CLEAR_RESULT")
        parent._check_usable()
        succeeded = True
    except BaseException:
        parent._poisoned = True
        raise
    finally:
        parent._output_closing = output._closing = False
        if succeeded:
            output._callback = None
            del parent._document_outputs[id(output)]
            parent._live_document_output_bytes -= output._owned
            output._proof._live_document_output_bytes -= output._owned
            parent._live_digest_bytes -= output._digests
            parent._live_scratch_bytes -= 96
            if type(output) is NativeConnectedSharedDocumentOutputLeaseV3:
                parent._shared_document_outputs -= 1
                output._meter._outputs -= 1
            output._closed = True


class NativeConnectedDocumentOutputLeaseV3:
    """One current derived output; clear ownership before releasing its payload."""

    __slots__ = (
        "_callback",
        "_closed",
        "_closing",
        "_digests",
        "_identity",
        "_owned",
        "_packet",
        "_parent",
        "_proof",
        "_record",
    )

    def __init__(
        self,
        token: object,
        record: NativeConnectedRecordAdmissionV3,
        owned_payload_upper: int,
        callback: Callable[[], None],
    ) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_ADMISSION")
        self._record = record
        self._proof = record._proof
        self._parent = record._proof._parent
        self._packet = record._lease_owner
        self._owned = owned_payload_upper
        self._callback: Callable[[], None] | None = callback
        self._identity = id(self)
        self._closed = self._closing = False
        self._digests = 0

    def _reentrant(self) -> None:
        if self._parent._output_closing:
            self._parent._poisoned = True
            _fail("OUTPUT_REENTRANT")

    def check_current(self) -> None:
        if self._closing:
            self._reentrant()
        self._parent._check_usable()
        if (
            type(self) is not NativeConnectedDocumentOutputLeaseV3
            or id(self) != self._identity
            or self._closed
            or self._parent._document_outputs.get(id(self)) is not self
        ):
            _fail("OUTPUT_LEASE_STATE")
        self._record._check()
        if self._proof._lease() is not self._packet:
            _fail("OUTPUT_LEASE_STATE")

    def retain_new_digests(self, n: int) -> None:
        self._reentrant()
        self.check_current()
        self._parent._retain(n, digest=True)
        self._digests += n

    def close(self) -> None:
        if type(self) is not NativeConnectedDocumentOutputLeaseV3 or id(self) != self._identity:
            _fail("OUTPUT_LEASE_STATE")
        self._reentrant()
        if self._closed:
            return
        self.check_current()
        if (
            self._parent._shared_document_outputs
            and next(reversed(self._parent._document_outputs.values())) is not self
        ):
            _fail("OUTPUT_CLOSE_ORDER")
        _clear_document_output_v3(self)


class NativeConnectedSharedDocumentOutputLeaseV3:
    """One proof-owned shared graph; no fabricated record or source authority."""

    __slots__ = (
        "_callback",
        "_closed",
        "_closing",
        "_digests",
        "_identity",
        "_meter",
        "_owned",
        "_packet",
        "_parent",
        "_proof",
    )
    _record: None = None

    def __init__(
        self,
        token: object,
        meter: NativeConnectedSharedWorkMeterV3,
        owned_payload_upper: int,
        callback: Callable[[], None],
    ) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_ADMISSION")
        self._meter = meter
        self._proof = meter._proof
        self._parent = meter._proof._parent
        self._packet = meter._owner
        self._owned = owned_payload_upper
        self._callback: Callable[[], None] | None = callback
        self._identity = id(self)
        self._closed = self._closing = False
        self._digests = 0

    def _reentrant(self) -> None:
        if self._parent._output_closing:
            self._parent._poisoned = True
            _fail("OUTPUT_REENTRANT")

    def check_current(self) -> None:
        if self._closing:
            self._reentrant()
        self._parent._check_usable()
        if (
            type(self) is not NativeConnectedSharedDocumentOutputLeaseV3
            or id(self) != self._identity
            or self._closed
            or self._parent._document_outputs.get(id(self)) is not self
        ):
            _fail("OUTPUT_LEASE_STATE")
        # Ancestor checks are read-only and valid during LIFO packet teardown.
        self._meter._check_readonly()

    def retain_new_digests(self, n: int) -> None:
        self._reentrant()
        self.check_current()
        self._parent._retain(n, digest=True)
        self._digests += n

    def close(self) -> None:
        if (
            type(self) is not NativeConnectedSharedDocumentOutputLeaseV3
            or id(self) != self._identity
        ):
            _fail("OUTPUT_LEASE_STATE")
        self._reentrant()
        if self._closed:
            return
        self.check_current()
        if next(reversed(self._parent._document_outputs.values())) is not self:
            _fail("OUTPUT_CLOSE_ORDER")
        _clear_document_output_v3(self)


class NativeConnectedProjectionCleanupV3:
    """One exact admission-owned cleanup registration, without source authority."""

    __slots__ = ("_admission", "_callback", "_closed", "_identity")

    def __init__(
        self,
        token: object,
        admission: NativeConnectedRecordAdmissionV3,
        callback: Callable[[], None],
    ) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_ADMISSION")
        self._admission = admission
        self._callback: Callable[[], None] | None = callback
        self._closed = False
        self._identity = id(self)

    def _take(self) -> Callable[[], None] | None:
        if type(self) is not NativeConnectedProjectionCleanupV3 or id(self) != self._identity:
            _fail("PROJECTION_CLEANUP_STATE")
        if self._closed:
            return None
        admission = self._admission
        admission._check()
        if admission._projection_cleanup is not self:
            _fail("PROJECTION_CLEANUP_STATE")
        callback = self._callback
        self._callback = None
        self._closed = True
        admission._projection_cleanup = None
        admission._release(48, digest=False)
        return callback

    def close(self) -> None:
        """Unregister without invoking; only the original may close repeatedly."""
        self._take()


class NativeConnectedSourceMeterV3:
    """Typed common callbacks for new root and source validators."""

    def __init__(self, token: object, admission: NativeConnectedRecordAdmissionV3) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_ADMISSION")
        self._admission = admission

    def reserve(self, *, byte_work_bound: int, node_visit_bound: int) -> None:
        self._admission._check()
        require_int_v3(byte_work_bound, 0, MAX_BYTE_WORK)
        require_int_v3(node_visit_bound, 0, MAX_NODE_VISITS)
        state = self._admission._state
        if (
            byte_work_bound > state.byte_limit - state.byte_work
            or node_visit_bound > state.node_limit - state.node_visits
        ):
            _fail("RECORD_WORK_LIMIT")

    def set_counts(self, tracks: int, items: int) -> None:
        """Validate exact narrowed root counts; never re-reserve or reset work."""
        counts = self._admission.counts
        require_int_v3(tracks, counts.tracks, counts.tracks)
        require_int_v3(items, counts.items, counts.items)

    def set_raw_bytes(self, raw_bytes: int) -> None:
        """Validate the root fragment width, independently of whole-frame size."""
        self._admission.source_meter()
        ranges = self._admission._state.ranges
        if ranges is None:
            _fail("RECORD_RAW_BINDING")
        assert ranges is not None
        require_int_v3(raw_bytes, ranges[0].size, ranges[0].size)

    def retain_document_output(
        self,
        *,
        canonical_byte_upper: int,
        owned_payload_upper: int,
        clear_callback: Callable[[], None],
    ) -> NativeConnectedDocumentOutputLeaseV3:
        return self._admission.retain_document_output(
            canonical_byte_upper=canonical_byte_upper,
            owned_payload_upper=owned_payload_upper,
            clear_callback=clear_callback,
        )

    def charge(
        self,
        *,
        bytes_read: int = 0,
        bytes_written: int = 0,
        sha_input_bytes: int = 0,
        node_visits: int = 0,
    ) -> None:
        self._admission.charge(
            bytes_read=bytes_read,
            bytes_written=bytes_written,
            sha_input_bytes=sha_input_bytes,
            node_visits=node_visits,
        )

    def charge_bytes(self, amount: int) -> None:
        self.charge(bytes_read=amount)

    def charge_nodes(self, amount: int = 1) -> None:
        self.charge(node_visits=amount)

    def charge_leaf(self, amount: int = 1) -> None:
        self._admission._check()
        require_int_v3(amount, 0, 20_200)
        counts = self._admission._state.counts
        if self._admission._leaves + amount > min(4 * counts.tracks + 2 * counts.items, 20_200):
            _fail("ROOT_LEAF_LIMIT")
        self._admission._leaves += amount

    def retain_digests(self, amount: int) -> None:
        self._admission._retain(amount, digest=True)

    def release_digests(self, amount: int) -> None:
        self._admission._release(amount, digest=True)

    def retain_scratch(self, amount: int) -> None:
        self._admission._retain(amount, digest=False)

    def release_scratch(self, amount: int) -> None:
        self._admission._release(amount, digest=False)


class NativeConnectedRecordAdmissionV3:
    def __init__(
        self,
        token: object,
        proof: NativeConnectedV3Budget,
        lease: NativeConnectedPacketLeaseV3,
        state: _Record,
    ) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_ADMISSION")
        self._proof = proof
        self._lease_owner = lease
        self._state = state
        self._active = True
        self._bound_digest: bytes | None = None
        self._fragment_digests: tuple[bytes, ...] | None = None
        self._narrowed = False
        self._leaves = 0
        self._digests = 0
        self._scratch = 0

        self._source_meter = NativeConnectedSourceMeterV3(_PRIVATE, self)
        self._projection_cleanup: NativeConnectedProjectionCleanupV3 | None = None

    def _check(self) -> None:
        self._proof._parent._check_usable()
        if not self._active or self._proof._lease()._record is not self:
            _fail("RECORD_STATE")

    def retain_identity(self, value: bytes | str) -> NativeConnectedIdentityLeaseV3:
        self._check()
        return self._proof._parent.retain_identity(value)

    def retain_document_output(
        self,
        *,
        canonical_byte_upper: int,
        owned_payload_upper: int,
        clear_callback: Callable[[], None],
    ) -> NativeConnectedDocumentOutputLeaseV3:
        require_int_v3(canonical_byte_upper, 1, MAX_CONNECTED_RECORD_BYTES)
        return self._retain_output(owned_payload_upper, clear_callback)

    def retain_original_output(
        self, *, baseline_bytes: int, other_bytes: int, owned_payload_upper: int,
        clear_callback: Callable[[], None],
    ) -> NativeConnectedDocumentOutputLeaseV3:
        require_int_v3(baseline_bytes, 1, MAX_ORIGINAL_BASELINE_V4_BYTES)
        require_int_v3(other_bytes, 1, MAX_CONNECTED_RECORD_BYTES)
        if baseline_bytes <= MAX_CONNECTED_RECORD_BYTES:
            require_int_v3(baseline_bytes + other_bytes, 1, MAX_CONNECTED_RECORD_BYTES)
        return self._retain_output(owned_payload_upper, clear_callback)

    def _retain_output(
        self, owned_payload_upper: int, clear_callback: Callable[[], None]
    ) -> NativeConnectedDocumentOutputLeaseV3:
        parent = self._proof._parent
        if parent._output_closing:
            parent._poisoned = True
            _fail("OUTPUT_REENTRANT")
        self._check()
        require_int_v3(owned_payload_upper, 1, MAX_CONNECTED_DEPENDENCY_BYTES)
        if not callable(clear_callback) or self._lease_owner._closing:
            _fail("OUTPUT_LEASE_STATE")
        if (
            self._proof._dependency_bytes
            + self._proof._live_document_output_bytes
            + owned_payload_upper
            > MAX_CONNECTED_DEPENDENCY_BYTES
            or parent.dependency_bytes + parent.live_document_output_bytes + owned_payload_upper
            > MAX_CONNECTED_AGGREGATE_BYTES
        ):
            _fail("OUTPUT_INPUT_LIMIT")
        # Bounded private registry/callback bookkeeping, independent of the
        # retained derived graph; failure keeps this charge as well.
        parent._retain(96, digest=False)
        lease = NativeConnectedDocumentOutputLeaseV3(
            _PRIVATE, self, owned_payload_upper, clear_callback
        )
        parent._document_outputs[id(lease)] = lease
        parent._live_document_output_bytes += owned_payload_upper
        self._proof._live_document_output_bytes += owned_payload_upper
        return lease

    def register_projection_cleanup(
        self, callback: Callable[[], None]
    ) -> NativeConnectedProjectionCleanupV3:
        self._check()
        if (
            not callable(callback)
            or self._projection_cleanup is not None
            or self._lease_owner._closing
        ):
            _fail("PROJECTION_CLEANUP_STATE")
        self._retain(48, digest=False)
        registration = NativeConnectedProjectionCleanupV3(_PRIVATE, self, callback)
        self._projection_cleanup = registration
        return registration

    def _run_projection_cleanup(self) -> None:
        self._check()
        registration = self._projection_cleanup
        if registration is None:
            return
        callback = registration._take()
        assert callback is not None
        result = cast(Callable[[], object], callback)()
        if inspect.isawaitable(result):
            close = getattr(result, "close", None)
            if not callable(close):
                close = getattr(result, "cancel", None)
            if callable(close):
                close()
            _fail("ASYNCHRONOUS_PROJECTION_CLEANUP")
        if result is not None:
            _fail("PROJECTION_CLEANUP_RESULT")

    @property
    def counts(self) -> NativeConnectedCountsV3:
        self._check()
        if not self._narrowed:
            _fail("RECORD_NOT_NARROWED")
        return self._state.counts

    @property
    def record_ordinal(self) -> int:
        self._check()
        return self._state.ordinal

    @property
    def selected_identity(self) -> tuple[UUID, UUID]:
        self._check()
        return self._state.owner, self._state.served

    @property
    def transaction_binding(self) -> tuple[int, int]:
        self._check()
        return self._proof._parent.transaction_binding

    @property
    def bound_record_sha256(self) -> bytes:
        self._check()
        if self._bound_digest is None:
            _fail("RECORD_RAW_BINDING")
        assert self._bound_digest is not None
        return self._bound_digest

    def authorize_root_input(
        self,
        head: NativeP11UnvalidatedHeadV3,
        document: bytes,
        *,
        transaction_binding: tuple[int, int],
    ) -> None:
        """Admit an actual root bridge input within the same selected raw record."""
        self._check()
        state = self._state
        if (
            type(head) is not NativeP11UnvalidatedHeadV3
            or type(document) is not bytes
            or self._bound_digest is None
            or self._fragment_digests is None
            or state.ranges is None
            or _transaction(transaction_binding) != self._proof._parent.transaction_binding
            or head.owner_user_id != state.owner
            or head.served_request_id != state.served
            or (state.original is not None and state.original != head.original_request_id)
        ):
            _fail("ROOT_INPUT_BINDING")
        assert state.ranges is not None and self._fragment_digests is not None
        if len(document) != state.ranges[0].size or len(document) != head.root_byte_length:
            _fail("ROOT_INPUT_BINDING")
        self.charge(bytes_read=len(document), sha_input_bytes=len(document))
        self._retain(32, digest=True)
        try:
            digest = sha256(document).digest()
            if digest != head.root_proof_sha256 or digest != self._fragment_digests[0]:
                _fail("ROOT_INPUT_BINDING")
        finally:
            self._release(32, digest=True)
        _pin_head(head, self, self)
        state.original = head.original_request_id

    def authorize_fact_projection(
        self, head: NativeP11UnvalidatedHeadV3, *, transaction_binding: tuple[int, int]
    ) -> None:
        """Check a data projection against an already pinned bound record."""
        self._check()
        state = self._state
        if (
            type(head) is not NativeP11UnvalidatedHeadV3
            or not self._narrowed
            or self._bound_digest is None
            or state.head_digest is None
            or _transaction(transaction_binding) != self._proof._parent.transaction_binding
            or (state.owner, state.served) != (head.owner_user_id, head.served_request_id)
            or state.original != head.original_request_id
        ):
            _fail("FACT_PROJECTION_BINDING")
        _pin_head(head, self, self)

    def narrow(
        self,
        *,
        exact_wire_bytes: int,
        actual_counts: NativeConnectedCountsV3,
        root_range: NativeConnectedRangeV3,
        capture_range: NativeConnectedRangeV3,
        request_range: NativeConnectedRangeV3,
        outcomes_range: NativeConnectedRangeV3,
    ) -> None:
        self._check()
        state = self._state
        require_int_v3(exact_wire_bytes, 1, state.upper)
        if type(actual_counts) is not NativeConnectedCountsV3:
            _fail("COUNTS")
        for actual, upper in zip(_counts(actual_counts), _counts(state.counts), strict=True):
            if actual > upper:
                _fail("COUNT_UPPER_BOUND")
        ranges = (root_range, capture_range, request_range, outcomes_range)
        previous = 0
        for part in ranges:
            if (
                type(part) is not NativeConnectedRangeV3
                or part.start < previous
                or part.stop > exact_wire_bytes
                or part.size == 0
            ):
                _fail("RECORD_RANGE")
            previous = part.stop
        if state.length is not None:
            if (
                state.length != exact_wire_bytes
                or state.ranges != ranges
                or state.counts != actual_counts
            ):
                _fail("RECORD_IDENTITY")
        else:
            byte_limit, node_limit = wire_reservation_v3(exact_wire_bytes, actual_counts)
            byte_limit += state.dependency_byte_limit
            node_limit += state.dependency_node_limit
            if byte_limit > MAX_RECORD_BYTE_WORK or node_limit > MAX_RECORD_NODE_VISITS:
                _fail("RECORD_WORK_LIMIT")
            if byte_limit < state.byte_work or node_limit < state.node_visits:
                _fail("NARROW_CONSUMED_WORK")
            parent = self._proof._parent
            parent._reserved_bytes -= state.byte_limit - byte_limit
            parent._reserved_nodes -= state.node_limit - node_limit
            state.byte_limit, state.node_limit = byte_limit, node_limit
            state.length, state.ranges, state.counts = exact_wire_bytes, ranges, actual_counts
        self._narrowed = True

    def bind_raw(self, raw: bytes | memoryview) -> bytes:
        self._check()
        if (
            not self._narrowed
            or not _immutable(raw)
            or len(raw) != self._state.length
            or self._bound_digest is not None
        ):
            _fail("RECORD_RAW_BINDING")
        self.charge(bytes_read=len(raw), sha_input_bytes=len(raw))
        self._retain(128, digest=True)
        digest = sha256(raw).digest()
        if self._state.digest is not None and self._state.digest != digest:
            _fail("RECORD_IDENTITY")
        ranges = self._state.ranges
        assert ranges is not None
        fragments: list[bytes] = []
        for part in ranges[:3]:
            self.charge(bytes_read=part.size, sha_input_bytes=part.size)
            fragments.append(sha256(memoryview(raw)[part.start : part.stop]).digest())
        self._bound_digest = digest
        self._fragment_digests = tuple(fragments)
        return digest

    def bind_raw_segments(self, segments: NativeConnectedFrameSegmentsV3) -> bytes:
        """Bind an issued piece chain without allocating its complete frame."""
        self._check()
        if type(segments) is not NativeConnectedFrameSegmentsV3:
            _fail("RECORD_RAW_BINDING")
        frame = segments._state()
        if (
            not self._narrowed
            or self._bound_digest is not None
            or type(frame.meter) is not NativeConnectedSourceMeterV3
            or frame.meter._admission is not self
            or frame.meter is not self._source_meter
            or segments.record_length != self._state.length
            or frame.ranges != self._state.ranges
            or frame.counts != self._state.counts
            or (frame.header.owner_user_id, frame.header.served_request_id)
            != self.selected_identity
        ):
            _fail("RECORD_RAW_BINDING")
        pieces = frame.pieces
        self.charge(bytes_read=24, node_visits=16)
        if (
            type(pieces) is not tuple
            or len(pieces) != 6
            or any(type(piece) is not bytes for piece in pieces)
            or len(pieces[0]) != 24
            or not 1 <= len(pieces[1]) <= 8192
            or any(not piece for piece in pieces[2:])
            or unpack(">6I", pieces[0])
            != (segments.record_length, len(pieces[1]), *map(len, pieces[2:]))
        ):
            _fail("RECORD_RAW_BINDING")
        start = 24 + len(pieces[1])
        if len(frame.ranges) != 4:
            _fail("RECORD_RAW_BINDING")
        for part, piece in zip(frame.ranges, pieces[2:], strict=True):
            if (
                type(part) is not NativeConnectedRangeV3
                or part.start != start
                or part.stop != start + len(piece)
            ):
                _fail("RECORD_RAW_BINDING")
            start = part.stop
        self.charge(bytes_read=segments.record_length, sha_input_bytes=segments.record_length)
        self._retain(128, digest=True)
        state = sha256()
        for piece in pieces:
            state.update(piece)
        digest = state.digest()
        if self._state.digest is not None and self._state.digest != digest:
            _fail("RECORD_IDENTITY")
        fragments: list[bytes] = []
        for piece in pieces[2:5]:
            self.charge(bytes_read=len(piece), sha_input_bytes=len(piece))
            fragments.append(sha256(piece).digest())
        self._bound_digest = digest
        self._fragment_digests = tuple(fragments)
        return digest

    def charge(
        self,
        *,
        bytes_read: int = 0,
        bytes_written: int = 0,
        sha_input_bytes: int = 0,
        node_visits: int = 0,
    ) -> None:
        self._check()
        byte_work, nodes = _work(bytes_read, bytes_written, sha_input_bytes, node_visits)
        state = self._state
        if (
            state.byte_work + byte_work > state.byte_limit
            or state.node_visits + nodes > state.node_limit
        ):
            _fail("RECORD_WORK_LIMIT")
        self._proof._parent._consume(byte_work, nodes)
        state.byte_work += byte_work
        state.node_visits += nodes

    def _extend(self, byte_work: int, nodes: int) -> None:
        self._check()
        state = self._state
        if state.discovery and state.length is None:
            # The maximum record allowance was already reserved before head
            # discovery. Record dependency plans without reserving it twice.
            if (
                state.dependency_byte_limit + byte_work > MAX_RECORD_BYTE_WORK
                or state.dependency_node_limit + nodes > MAX_RECORD_NODE_VISITS
            ):
                _fail("RECORD_WORK_LIMIT")
            state.dependency_byte_limit += byte_work
            state.dependency_node_limit += nodes
            return
        if (
            self._state.byte_limit + byte_work > MAX_RECORD_BYTE_WORK
            or self._state.node_limit + nodes > MAX_RECORD_NODE_VISITS
        ):
            _fail("RECORD_WORK_LIMIT")
        self._proof._parent._reserve(byte_work, nodes)
        self._state.byte_limit += byte_work
        self._state.node_limit += nodes
        self._state.dependency_byte_limit += byte_work
        self._state.dependency_node_limit += nodes

    def source_meter(self) -> NativeConnectedSourceMeterV3:
        self._check()
        if not self._narrowed:
            _fail("RECORD_NOT_NARROWED")
        return self._source_meter

    def frame_meter(self) -> NativeConnectedSourceMeterV3:
        """Use the existing upper reservation to prepare exact framing first."""
        self._check()
        return self._source_meter

    def discovery_meter(self) -> NativeConnectedSourceMeterV3:
        """Use the same prepaid record counters for bounded scalar discovery."""
        self._check()
        if not self._state.discovery:
            _fail("RECORD_DISCOVERY_STATE")
        return self._source_meter

    def expected_meter(self) -> NativeConnectedSourceMeterV3:
        """Use this admission's same phase-neutral source counters and outputs."""
        self._check()
        return self._source_meter

    def root_meter(self) -> NativeConnectedSourceMeterV3:
        return self.source_meter()

    def _retain(self, amount: int, *, digest: bool) -> None:
        self._check()
        self._proof._parent._retain(amount, digest=digest)
        if digest:
            self._digests += amount
            self._lease_owner._temporary_digests += amount
        else:
            self._scratch += amount
            self._lease_owner._temporary_scratch += amount

    def _release(self, amount: int, *, digest: bool) -> None:
        self._check()
        require_int_v3(amount, 0, self._digests if digest else self._scratch)
        if digest:
            self._digests -= amount
            self._lease_owner._temporary_digests -= amount
            self._proof._parent._live_digest_bytes -= amount
        else:
            self._scratch -= amount
            self._lease_owner._temporary_scratch -= amount
            self._proof._parent._live_scratch_bytes -= amount

    def finish(
        self, *, verified_record: VerifiedConnectedRecordV3, exact_record_sha256: bytes
    ) -> NativeConnectedRecheckV3:
        self._check()
        if type(verified_record) is not VerifiedConnectedRecordV3:
            _fail("VERIFIED_RECORD")
        return self._finish_head_binding_v3(
            verified_record.context.head,
            transaction_binding=verified_record.context.transaction_binding,
            record_ordinal=verified_record.record_ordinal,
            exact_record_sha256=exact_record_sha256,
        )

    def finish_owned(
        self, completion: NativeP11CompletionAttestationV3
    ) -> NativeConnectedRecheckV3:
        """Consume exact scoped verification, discard graphs, then complete once."""
        self._check()
        head, transaction, ordinal, digest = _consume_owned_native_p11_completion_v3(
            completion, admission=self
        )
        return self._finish_head_binding_v3(
            head,
            transaction_binding=transaction,
            record_ordinal=ordinal,
            exact_record_sha256=digest,
        )

    def _finish_head_binding_v3(
        self,
        head: NativeP11UnvalidatedHeadV3,
        *,
        transaction_binding: tuple[int, int],
        record_ordinal: int,
        exact_record_sha256: bytes,
    ) -> NativeConnectedRecheckV3:
        self._check()
        if any(output._record is self for output in self._proof._parent._document_outputs.values()):
            _fail("OUTPUT_LEASE_ACTIVE")
        require_digest_v3(exact_record_sha256)
        state = self._state
        ranges = state.ranges
        if ranges is None or self._fragment_digests is None:
            _fail("VERIFIED_BINDING")
        assert ranges is not None
        if (
            self._fragment_digests
            != (head.root_proof_sha256, head.capture_sha256, head.original_request_sha256)
            or tuple(part.size for part in ranges[:3])
            != (head.root_byte_length, head.capture_byte_length, head.original_request_byte_length)
            or (
                state.counts.tracks,
                state.counts.items,
                state.counts.facts,
                state.counts.evidence,
                state.counts.dimensions,
            )
            != (
                head.source_snapshot_track_count,
                head.p11_item_count,
                head.source_event_count,
                head.source_evidence_count,
                head.profile_dimension_count,
            )
        ):
            _fail("VERIFIED_RAW_BINDING")
        if (
            not self._narrowed
            or self._bound_digest != exact_record_sha256
            or transaction_binding != self._proof._parent.transaction_binding
            or head.owner_user_id != state.owner
            or head.served_request_id != state.served
            or record_ordinal != state.ordinal
            or head.original_request_id == state.served
            or (state.original is not None and state.original != head.original_request_id)
            or any(
                other is not state and other.original == head.original_request_id
                for other in self._proof._records.values()
            )
            or self._digests != 128
            or self._scratch
            or self._projection_cleanup is not None
            or any(dep._active and dep._record is self for dep in self._lease_owner._dependencies)
            or any(
                (state.owner, state.served, *key)
                not in self._lease_owner._completed_dependency_uses
                for key in state.dependencies
            )
        ):
            _fail("VERIFIED_BINDING")
        if state.digest is None:
            self._proof._parent._retain(32, digest=True)
            state.digest = exact_record_sha256
        self._fragment_digests = None
        self._bound_digest = None
        self._release(128, digest=True)
        state.original = head.original_request_id
        _pin_head(head, self, self)
        state.completed = True
        self._lease_owner._completed_records.add((state.owner, state.served))
        parent = self._proof._parent
        parent._reserved_bytes -= state.byte_limit - state.byte_work
        parent._reserved_nodes -= state.node_limit - state.node_visits
        self._active = False
        self._lease_owner._record = None
        return NativeConnectedRecheckV3(_PRIVATE, self._proof, state)


def _counts(value: NativeConnectedCountsV3) -> tuple[int, ...]:
    return (
        value.tracks,
        value.items,
        value.facts,
        value.evidence,
        value.outcomes,
        value.dimensions,
    )


def _immutable(raw: bytes | memoryview) -> bool:
    return type(raw) is bytes or (
        type(raw) is memoryview
        and raw.readonly
        and type(raw.obj) is bytes
        and raw.ndim == 1
        and raw.itemsize == 1
        and raw.c_contiguous
    )


class NativeConnectedRecheckV3:
    __slots__ = ("_proof", "_state", "_transaction")

    def __init__(self, token: object, proof: NativeConnectedV3Budget, state: _Record) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_ADMISSION")
        self._proof = proof
        self._state = state
        self._transaction = proof._parent.transaction_binding


class NativeConnectedSharedWorkMeterV3:
    """Private packet work reservation, with no record or dependency authority.

    Consumed work is permanent. Closing returns unused reservation only after
    the caller has discarded and released every retained temporary payload.
    """

    __slots__ = (
        "_bytes",
        "_closed",
        "_digests",
        "_identity",
        "_nodes",
        "_outputs",
        "_owner",
        "_phase",
        "_proof",
        "_scratch",
    )

    def __init__(
        self,
        token: object,
        proof: NativeConnectedV3Budget,
        lease: NativeConnectedPacketLeaseV3,
        byte_bound: int,
        node_bound: int,
    ) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_ADMISSION")
        self._proof = proof
        self._owner = lease
        self._phase = lease.phase
        self._identity = id(self)
        self._bytes = byte_bound
        self._nodes = node_bound
        self._digests = 0
        self._scratch = 0
        self._outputs = 0
        self._closed = False

    def _check_readonly(self) -> None:
        parent = self._proof._parent
        parent._check_usable()
        if (
            type(self) is not NativeConnectedSharedWorkMeterV3
            or id(self) != self._identity
            or self._closed
            or parent._lease is not self._owner
            or self._owner.proof is not self._proof
            or self._owner._closed
            or self._owner.phase is not self._phase
            or self._owner._shared_work_meters.get(id(self)) is not self
            or parent._finished
        ):
            _fail("SHARED_WORK_STATE")

    def _check(self) -> None:
        if self._proof._parent._output_closing:
            self._proof._parent._poisoned = True
            _fail("OUTPUT_REENTRANT")
        self._check_readonly()
        if self._owner._closing:
            _fail("SHARED_WORK_STATE")

    def retain_document_output(
        self,
        *,
        canonical_byte_upper: int,
        owned_payload_upper: int,
        clear_callback: Callable[[], None],
    ) -> NativeConnectedSharedDocumentOutputLeaseV3:
        self._check()
        require_int_v3(canonical_byte_upper, 1, MAX_CONNECTED_RECORD_BYTES)
        require_int_v3(owned_payload_upper, 1, MAX_CONNECTED_DEPENDENCY_BYTES)
        if not callable(clear_callback):
            _fail("OUTPUT_LEASE_STATE")
        parent = self._proof._parent
        if (
            self._proof._dependency_bytes
            + self._proof._live_document_output_bytes
            + owned_payload_upper
            > MAX_CONNECTED_DEPENDENCY_BYTES
            or parent.dependency_bytes + parent.live_document_output_bytes + owned_payload_upper
            > MAX_CONNECTED_AGGREGATE_BYTES
        ):
            _fail("OUTPUT_INPUT_LIMIT")
        parent._retain(96, digest=False)
        output = NativeConnectedSharedDocumentOutputLeaseV3(
            _PRIVATE, self, owned_payload_upper, clear_callback
        )
        parent._document_outputs[id(output)] = output
        parent._live_document_output_bytes += owned_payload_upper
        self._proof._live_document_output_bytes += owned_payload_upper
        parent._shared_document_outputs += 1
        self._outputs += 1
        return output

    def reserve(self, *, byte_work_bound: int, node_visit_bound: int) -> None:
        """Check a forthcoming operation against this already prepaid bucket."""
        self._check()
        require_int_v3(byte_work_bound, 0, MAX_BYTE_WORK)
        require_int_v3(node_visit_bound, 0, MAX_NODE_VISITS)
        if byte_work_bound > self._bytes or node_visit_bound > self._nodes:
            _fail("SHARED_WORK_LIMIT")

    def charge(
        self,
        *,
        bytes_read: int = 0,
        bytes_written: int = 0,
        sha_input_bytes: int = 0,
        node_visits: int = 0,
    ) -> None:
        self._check()
        byte_work, nodes = _work(bytes_read, bytes_written, sha_input_bytes, node_visits)
        self.reserve(byte_work_bound=byte_work, node_visit_bound=nodes)
        self._proof._parent._consume(byte_work, nodes)
        self._bytes -= byte_work
        self._nodes -= nodes

    def retain_digests(self, amount: int) -> None:
        self._retain(amount, digest=True)

    def release_digests(self, amount: int) -> None:
        self._release(amount, digest=True)

    def retain_scratch(self, amount: int) -> None:
        self._retain(amount, digest=False)

    def release_scratch(self, amount: int) -> None:
        self._release(amount, digest=False)

    def _retain(self, amount: int, *, digest: bool) -> None:
        self._check()
        self._proof._parent._retain(amount, digest=digest)
        if digest:
            self._digests += amount
            self._owner._temporary_digests += amount
        else:
            self._scratch += amount
            self._owner._temporary_scratch += amount

    def _release(self, amount: int, *, digest: bool) -> None:
        self._check()
        require_int_v3(amount, 0, self._digests if digest else self._scratch)
        if digest:
            self._digests -= amount
            self._owner._temporary_digests -= amount
            self._proof._parent._live_digest_bytes -= amount
        else:
            self._scratch -= amount
            self._owner._temporary_scratch -= amount
            self._proof._parent._live_scratch_bytes -= amount

    def _discard_registration(self, *, release_payload: bool) -> None:
        parent = self._proof._parent
        parent._reserved_bytes -= self._bytes
        parent._reserved_nodes -= self._nodes
        self._bytes = self._nodes = 0
        if release_payload:
            parent._live_scratch_bytes -= 96
            self._owner._temporary_scratch -= 96
        del self._owner._shared_work_meters[id(self)]
        self._closed = True

    def close(self) -> None:
        if type(self) is not NativeConnectedSharedWorkMeterV3 or id(self) != self._identity:
            _fail("SHARED_WORK_STATE")
        if self._closed:
            return
        self._check()
        if self._digests or self._scratch or self._outputs:
            _fail("SHARED_WORK_UNCLEARED")
        self._discard_registration(release_payload=True)

    def _close_for_packet(self) -> bool:
        """Invalidate on packet exit; uncertain live references remain charged."""
        dirty = bool(self._digests or self._scratch or self._outputs)
        self._discard_registration(release_payload=not dirty)
        return dirty


class NativeConnectedWireMeterV3:
    def __init__(
        self, token: object, proof: NativeConnectedV3Budget, lease: NativeConnectedPacketLeaseV3
    ) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_ADMISSION")
        self._proof = proof
        self._owner = lease
        self._digests = 0
        self._scratch = 0

    def _check(self) -> None:
        if self._proof._lease() is not self._owner:
            _fail("WIRE_STATE")

    def admit_wire(self, total_bytes: int, record_count: int) -> None:
        self._check()
        require_int_v3(total_bytes, 1, self._proof.raw_upper)
        if record_count != len(self._proof.selection.members) or type(record_count) is not int:
            _fail("WIRE_RECORD_COUNT")
        if self._proof.raw_proof_bytes is not None and total_bytes < self._proof.raw_proof_bytes:
            _fail("PROOF_IDENTITY")
        self._owner._wire_admitted = True

    def narrow_wire(self, exact_bytes: int, record_count: int) -> None:
        self.admit_wire(exact_bytes, record_count)
        if self._proof.raw_proof_bytes is not None and exact_bytes != self._proof.raw_proof_bytes:
            _fail("PROOF_IDENTITY")
        if self._proof.raw_proof_bytes is None:
            parent = self._proof._parent
            if parent.raw_proof_bytes + exact_bytes > MAX_CONNECTED_AGGREGATE_BYTES:
                _fail("RAW_PROOF_LIMIT")
            parent._raw_proof_bytes += exact_bytes
            self._proof._raw_proof_bytes = exact_bytes
        if not self._owner._wire_narrowed:
            byte_reduction = 8 * (self._proof.raw_upper - exact_bytes)
            node_reduction = self._proof.raw_upper - exact_bytes
            if byte_reduction > self._owner._wire_bytes or node_reduction > self._owner._wire_nodes:
                _fail("NARROW_CONSUMED_WORK")
            self._proof._parent._reserved_bytes -= byte_reduction
            self._proof._parent._reserved_nodes -= node_reduction
            self._owner._wire_bytes -= byte_reduction
            self._owner._wire_nodes -= node_reduction
            self._owner._wire_narrowed = True

    def charge(
        self,
        *,
        bytes_read: int = 0,
        bytes_written: int = 0,
        sha_input_bytes: int = 0,
        node_visits: int = 0,
    ) -> None:
        self._check()
        if not self._owner._wire_admitted:
            _fail("WIRE_NOT_ADMITTED")
        byte_work, nodes = _work(bytes_read, bytes_written, sha_input_bytes, node_visits)
        if byte_work > self._owner._wire_bytes or nodes > self._owner._wire_nodes:
            _fail("WIRE_WORK_LIMIT")
        self._proof._parent._consume(byte_work, nodes)
        self._owner._wire_bytes -= byte_work
        self._owner._wire_nodes -= nodes

    def retain_digests(self, amount: int) -> None:
        self._retain(amount, digest=True)

    def release_digests(self, amount: int) -> None:
        self._release(amount, digest=True)

    def retain_scratch(self, amount: int) -> None:
        self._retain(amount, digest=False)

    def release_scratch(self, amount: int) -> None:
        self._release(amount, digest=False)

    def _retain(self, amount: int, *, digest: bool) -> None:
        self._check()
        self._proof._parent._retain(amount, digest=digest)
        if digest:
            self._digests += amount
            self._owner._temporary_digests += amount
        else:
            self._scratch += amount
            self._owner._temporary_scratch += amount

    def _release(self, amount: int, *, digest: bool) -> None:
        self._check()
        require_int_v3(amount, 0, self._digests if digest else self._scratch)
        if digest:
            self._digests -= amount
            self._owner._temporary_digests -= amount
            self._proof._parent._live_digest_bytes -= amount
        else:
            self._scratch -= amount
            self._owner._temporary_scratch -= amount
            self._proof._parent._live_scratch_bytes -= amount


class NativeConnectedDependencyAdmissionV3:
    def __init__(
        self,
        token: object,
        proof: NativeConnectedV3Budget,
        lease: NativeConnectedPacketLeaseV3,
        state: _Dependency,
        record: NativeConnectedRecordAdmissionV3 | None,
        byte_bound: int,
        node_bound: int,
    ) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_ADMISSION")
        self._proof = proof
        self._lease_owner = lease
        self._state = state
        self._record = record
        self._bytes = byte_bound
        self._nodes = node_bound
        self._active = True
        self._read = 0
        self._bound_digest: bytes | None = None
        self._mode: str | None = None
        self._stream: _StreamingDigestV3 | None = None
        self._stream_retained = False
        self._stream_bytes = 0

    def authorize_head_fetch(
        self, head: NativeP11UnvalidatedHeadV3, *, transaction_binding: tuple[int, int]
    ) -> None:
        """Bind a root read to a locked head before the caller fetches bytes."""
        if not self._active or self._proof._lease() is not self._lease_owner:
            _fail("DEPENDENCY_STATE")
        descriptor = self._state.descriptor
        record = self._record
        if (
            type(head) is not NativeP11UnvalidatedHeadV3
            or descriptor.kind is not NativeConnectedDependencyKindV3.ROOT
            or record is None
            or _transaction(transaction_binding) != self._proof._parent.transaction_binding
        ):
            _fail("HEAD_FETCH_BINDING")
        assert record is not None
        record._check()
        state = record._state
        if (
            descriptor.owner_user_id != head.owner_user_id
            or descriptor.entity_id != head.served_request_id
            or descriptor.byte_length != head.root_byte_length
            or descriptor.sha256 != head.root_proof_sha256
            or state.owner != head.owner_user_id
            or state.served != head.served_request_id
            or (state.original is not None and state.original != head.original_request_id)
            or any(
                other is not state and other.original == head.original_request_id
                for other in self._proof._records.values()
            )
        ):
            _fail("HEAD_FETCH_BINDING")
        state.original = head.original_request_id
        _pin_head(head, self, record)

    def authorize_origin_fetch(
        self,
        head: NativeP11UnvalidatedHeadV3,
        kind: NativeConnectedDependencyKindV3,
        *,
        transaction_binding: tuple[int, int],
    ) -> None:
        """Authorize one exact admitted original BYTEA, without SQL authority."""
        if (
            not self._active
            or self._proof._lease() is not self._lease_owner
            or not any(admitted is self for admitted in self._lease_owner._dependencies)
        ):
            _fail("DEPENDENCY_STATE")
        descriptor, record = self._state.descriptor, self._record
        if (
            type(head) is not NativeP11UnvalidatedHeadV3
            or type(kind) is not NativeConnectedDependencyKindV3
            or descriptor.kind is not kind
            or record is None
            or _transaction(transaction_binding) != self._proof._parent.transaction_binding
            or head.source_codec_sha256 != _SOURCE3_CODEC_SHA256
            or self._mode is not None
        ):
            _fail("ORIGIN_FETCH_BINDING")
        assert record is not None
        record._check()
        generation = head.source_generation
        expected = {
            NativeConnectedDependencyKindV3.CAPTURE: (
                head.original_request_id,
                3,
                head.capture_byte_length,
                head.capture_sha256,
            ),
            NativeConnectedDependencyKindV3.ORIGINAL_REQUEST: (
                head.original_request_id,
                generation,
                head.original_request_byte_length,
                head.original_request_sha256,
            ),
            NativeConnectedDependencyKindV3.ORIGINAL_BASELINE: (
                head.baseline_snapshot_id,
                generation,
                head.baseline_byte_length,
                head.baseline_snapshot_sha256,
            ),
            NativeConnectedDependencyKindV3.ORIGINAL_RANKING: (
                head.original_request_id,
                generation,
                head.original_ranking_byte_length,
                head.original_ranking_sha256,
            ),
            NativeConnectedDependencyKindV3.ORIGINAL_NUMERIC_PROFILE: (
                head.original_request_id,
                generation,
                head.original_profile_byte_length,
                head.original_profile_document_sha256,
            ),
            NativeConnectedDependencyKindV3.PIPELINE: (
                head.original_request_id,
                generation,
                head.pipeline_byte_length,
                head.pipeline_manifest_sha256,
            ),
        }.get(kind)
        state = record._state
        key = (kind, descriptor.owner_user_id, descriptor.entity_id)
        if (
            expected is None
            or (descriptor.entity_id, descriptor.version, descriptor.byte_length, descriptor.sha256)
            != expected
            or descriptor.size_mode is not NativeConnectedDependencySizeModeV3.EXACT
            or descriptor.rows != 1
            or self._state.actual_length != descriptor.byte_length
            or descriptor.owner_user_id != head.owner_user_id
            or key not in state.dependencies
            or self._proof._dependencies.get(key) is not self._state
            or state.owner != head.owner_user_id
            or state.served != head.served_request_id
            or (state.original is not None and state.original != head.original_request_id)
            or (
                kind is NativeConnectedDependencyKindV3.PIPELINE
                and head.pipeline_byte_length > 65_536
            )
            or any(
                other is not state and other.original == head.original_request_id
                for other in self._proof._records.values()
            )
        ):
            _fail("ORIGIN_FETCH_BINDING")
        state.original = head.original_request_id
        _pin_head(head, self, record)

    def charge(
        self,
        *,
        bytes_read: int = 0,
        bytes_written: int = 0,
        sha_input_bytes: int = 0,
        node_visits: int = 0,
    ) -> None:
        if not self._active or self._proof._lease() is not self._lease_owner:
            _fail("DEPENDENCY_STATE")
        if sha_input_bytes and self._state.actual_length is None:
            _fail("DEPENDENCY_SIZE_NOT_NARROWED")
        byte_work, nodes = _work(bytes_read, bytes_written, sha_input_bytes, node_visits)
        if byte_work > self._bytes or nodes > self._nodes:
            _fail("DEPENDENCY_WORK_LIMIT")
        if self._record is None:
            self._proof._parent._consume(byte_work, nodes)
        else:
            self._record.charge(
                bytes_read=bytes_read,
                bytes_written=bytes_written,
                sha_input_bytes=sha_input_bytes,
                node_visits=node_visits,
            )
        self._bytes -= byte_work
        self._nodes -= nodes
        self._read += bytes_read

    def bind_bytes(self, raw: bytes | memoryview) -> bytes:
        if (
            not _immutable(raw)
            or self._state.actual_length is None
            or len(raw) != self._state.actual_length
            or self._mode is not None
        ):
            _fail("DEPENDENCY_BYTES")
        self._mode = "ONE_SHOT"
        self.charge(bytes_read=len(raw), sha_input_bytes=len(raw))
        parent = self._proof._parent
        parent._retain(32, digest=True)
        self._lease_owner._temporary_digests += 32
        try:
            digest = sha256(raw).digest()
            result = self._accept_digest(digest)
        finally:
            parent._live_digest_bytes -= 32
            self._lease_owner._temporary_digests -= 32
        self.finish()
        return result

    def narrow_bytes(self, exact_length: int) -> None:
        """Pin admitted legacy JSON's exact size before any scan or hashing.

        The original trusted upper bound remains charged as distinct input.
        Narrowing never refunds consumed work or changes the descriptor identity.
        """
        if not self._active or self._proof._lease() is not self._lease_owner:
            _fail("DEPENDENCY_STATE")
        descriptor = self._state.descriptor
        if (
            descriptor.size_mode is not NativeConnectedDependencySizeModeV3.UPPER_BOUND
            or self._mode is not None
        ):
            _fail("DEPENDENCY_SIZE_MODE")
        require_int_v3(exact_length, 1, descriptor.byte_length)
        if self._state.actual_length is not None and self._state.actual_length != exact_length:
            _fail("DEPENDENCY_IDENTITY")
        self._state.actual_length = exact_length

    def _accept_digest(self, digest: bytes) -> bytes:
        expected = self._state.descriptor.sha256
        if (expected is not None and expected != digest) or (
            self._state.digest is not None and self._state.digest != digest
        ):
            _fail("DEPENDENCY_IDENTITY")
        if self._state.digest is None:
            if expected is None:
                self._proof._parent._retain(32, digest=True)
                self._state.digest = digest
            else:
                # The descriptor already retains this exact immutable payload.
                # The computed comparison digest is temporary, not a second pin.
                self._state.digest = expected
        self._bound_digest = self._state.digest
        assert self._state.digest is not None
        return self._state.digest

    def update_bytes(self, chunk: bytes | memoryview) -> None:
        """Hash an immutable chunk before discarding its admitted input buffer."""
        if not self._active or self._proof._lease() is not self._lease_owner:
            _fail("DEPENDENCY_STATE")
        if (
            not _immutable(chunk)
            or self._mode == "ONE_SHOT"
            or self._state.actual_length is None
            or self._stream_bytes + len(chunk) > self._state.actual_length
        ):
            _fail("DEPENDENCY_STREAM_BYTES")
        if self._stream is None:
            self._proof._parent._retain(32, digest=True)
            self._lease_owner._temporary_digests += 32
            self._stream_retained = True
            self._stream = sha256()
            self._mode = "STREAM"
        self.charge(bytes_read=len(chunk), sha_input_bytes=len(chunk))
        self._stream.update(chunk)
        self._stream_bytes += len(chunk)

    def finish_stream(self) -> bytes:
        """Complete only an exact internally hashed stream, never a claimed SHA."""
        if (
            not self._active
            or self._proof._lease() is not self._lease_owner
            or self._mode != "STREAM"
            or self._stream is None
            or self._stream_bytes != self._state.actual_length
        ):
            _fail("DEPENDENCY_STREAM_INCOMPLETE")
        assert self._stream is not None
        parent = self._proof._parent
        parent._retain(32, digest=True)
        self._lease_owner._temporary_digests += 32
        try:
            result = self._accept_digest(self._stream.digest())
            self._stream = None
            if self._stream_retained:
                parent._live_digest_bytes -= 32
                self._lease_owner._temporary_digests -= 32
                self._stream_retained = False
        finally:
            parent._live_digest_bytes -= 32
            self._lease_owner._temporary_digests -= 32
        self.finish()
        return result

    def finish(self) -> None:
        if (
            not self._active
            or self._proof._lease() is not self._lease_owner
            or self._state.digest is None
            or self._bound_digest is None
            or self._state.actual_length is None
            or self._read < self._state.actual_length
        ):
            _fail("DEPENDENCY_INCOMPLETE")
        if self._record is None:
            self._proof._parent._reserved_bytes -= self._bytes
            self._proof._parent._reserved_nodes -= self._nodes
        self._bound_digest = None
        self._active = False
        descriptor = self._state.descriptor
        key = (descriptor.kind, descriptor.owner_user_id, descriptor.entity_id)
        self._lease_owner._completed_dependencies.add(key)
        if self._record is not None:
            record_state = self._record._state
            self._lease_owner._completed_dependency_uses.add(
                (record_state.owner, record_state.served, *key)
            )


def _pin_head(
    head: NativeP11UnvalidatedHeadV3,
    meter: NativeConnectedRecordAdmissionV3 | NativeConnectedDependencyAdmissionV3,
    record: NativeConnectedRecordAdmissionV3,
) -> None:
    """Stream a fixed typed head; retain one charged digest, never its SQL graph."""
    record._retain(32, digest=True)
    try:
        hasher = sha256()
        for field in fields(head):
            value = getattr(head, field.name)
            if type(value) is bytes:
                size = len(value)
            elif type(value) is str:
                size = 4 * len(value)
            elif type(value) is UUID:
                size = 16
            elif type(value) is int:
                size = 8
            elif value is None:
                size = 0
            else:
                _fail("HEAD_FETCH_BINDING")
                raise AssertionError
            record._retain(size + 9, digest=False)
            try:
                meter.charge(
                    bytes_read=size, bytes_written=size + 9, sha_input_bytes=size + 9, node_visits=1
                )
                if type(value) is bytes:
                    payload, tag = value, b"b"
                elif type(value) is str:
                    payload, tag = value.encode("utf-8"), b"s"
                elif type(value) is UUID:
                    payload, tag = value.bytes, b"u"
                elif type(value) is int:
                    payload, tag = value.to_bytes(8), b"i"
                else:
                    payload, tag = b"", b"n"
                hasher.update(tag)
                hasher.update(len(payload).to_bytes(8))
                hasher.update(payload)
            finally:
                record._release(size + 9, digest=False)
        digest = hasher.digest()
        if record._state.head_digest is not None and record._state.head_digest != digest:
            _fail("HEAD_FETCH_BINDING")
        if record._state.head_digest is None:
            record._proof._parent._retain(32, digest=True)
            record._state.head_digest = digest
    finally:
        record._release(32, digest=True)
