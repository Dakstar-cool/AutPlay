"""Caller-issued ordinary P11 creation accounting, separate from G transport.

The owning writer must issue a lease before baseline, consent, negative-ledger
or other source reads. A lease accounts all source/root/SQL work for one actual
creation; it does not confer SQL authority or make an input Sona eligible.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, cast
from uuid import UUID

from autplay.domain.native_original_capture_v3 import MAX_ORIGINAL_BASELINE_V4_BYTES
from autplay.domain.native_request_budget_v3 import NativeRequestV3Error

MAX_CREATION_REQUESTS: Final = 16
MAX_CREATION_REQUEST_INPUT_BYTES: Final = 32 * 1024**2
MAX_CREATION_PARENT_INPUT_BYTES: Final = 512 * 1024**2
MAX_CREATION_DOCUMENT_BYTES: Final = 2 * 1024**2
MAX_CREATION_REQUEST_BYTE_WORK: Final = 320 * 1024**2
MAX_CREATION_REQUEST_NODE_VISITS: Final = 40_000_000
MAX_CREATION_PARENT_BYTE_WORK: Final = 6 * 1024**3
MAX_CREATION_PARENT_NODE_VISITS: Final = 768_000_000
MAX_CREATION_DIGEST_BYTES: Final = 16 * 1024
MAX_CREATION_SCRATCH_BYTES: Final = 128 * 1024
MAX_CREATION_SQL_WORKSPACE_BYTES: Final = 512 * 1024
_PRIVATE: Final = object()


class NativeCaptureCreationBudgetV3Error(NativeRequestV3Error):
    """A stable refusal without source or identity details."""


def _fail(code: str) -> None:
    raise NativeCaptureCreationBudgetV3Error("CAPTURE_CREATION_V3_" + code)


def _amount(value: int) -> int:
    if type(value) is not int or value < 0:
        _fail("AMOUNT")
    return value


def _uuid(value: UUID) -> None:
    if type(value) is not UUID:
        _fail("IDENTITY")


@dataclass(frozen=True, slots=True)
class NativeCaptureCreationUsageV3:
    requests: int
    input_bytes: int
    byte_work: int
    node_visits: int
    retained_digest_bytes: int
    peak_digest_bytes: int
    retained_scratch_bytes: int
    peak_scratch_bytes: int
    sql_workspace_bytes: int
    peak_sql_workspace_bytes: int
    live_document_output_bytes: int
    peak_document_output_bytes: int


class NativeCaptureCreationBudgetV3:
    """One bounded writer operation; failures never refund consumed work."""

    def __init__(self, transaction_binding: tuple[int, int]) -> None:
        if (
            type(transaction_binding) is not tuple
            or len(transaction_binding) != 2
            or any(
                type(value) is not int or not 1 <= value <= 2**64 - 1
                for value in transaction_binding
            )
        ):
            _fail("TRANSACTION")
        self._transaction_binding = transaction_binding
        self._requests = 0
        self._input_bytes = 0
        self._byte_work = 0
        self._node_visits = 0
        self._digests = self._peak_digests = 0
        self._scratch = self._peak_scratch = 0
        self._sql_workspace = self._peak_sql_workspace = 0
        self._live_document_output_bytes = self._peak_document_output_bytes = 0
        self._output_closing = self._poisoned = False
        self._originals: set[UUID] = set()
        self._served: set[UUID] = set()
        self._active: NativeCaptureCreationLeaseV3 | None = None
        self._finished = False

    @property
    def transaction_binding(self) -> tuple[int, int]:
        return self._transaction_binding

    def _check_usable(self) -> None:
        if self._output_closing:
            self._poisoned = True
            _fail("OUTPUT_REENTRANT")
        if self._poisoned:
            _fail("OUTPUT_OPERATION_POISONED")

    @property
    def usage(self) -> NativeCaptureCreationUsageV3:
        return NativeCaptureCreationUsageV3(
            self._requests,
            self._input_bytes,
            self._byte_work,
            self._node_visits,
            self._digests,
            self._peak_digests,
            self._scratch,
            self._peak_scratch,
            self._sql_workspace,
            self._peak_sql_workspace,
            self._live_document_output_bytes,
            self._peak_document_output_bytes,
        )

    def begin_request(
        self, *, owner_user_id: UUID, original_request_id: UUID, served_request_id: UUID
    ) -> NativeCaptureCreationLeaseV3:
        """Issue before any source read; no baseline/count/grant is needed."""

        self._check_usable()
        for value in (owner_user_id, original_request_id, served_request_id):
            _uuid(value)
        if self._finished or self._active is not None:
            _fail("PARENT_STATE")
        if self._requests == MAX_CREATION_REQUESTS:
            _fail("REQUEST_COUNT")
        if (
            original_request_id == served_request_id
            or original_request_id in self._originals
            or served_request_id in self._served
        ):
            _fail("REQUEST_IDENTITY")
        # Keep the bounded deduplication indexes inside the same scratch cap.
        index_bytes = 32
        if self._scratch + index_bytes > MAX_CREATION_SCRATCH_BYTES:
            _fail("SCRATCH_PAYLOAD")
        self._scratch += index_bytes
        self._peak_scratch = max(self._peak_scratch, self._scratch)
        self._originals.add(original_request_id)
        self._served.add(served_request_id)
        self._requests += 1
        lease = NativeCaptureCreationLeaseV3(
            _PRIVATE, self, owner_user_id, original_request_id, served_request_id
        )
        self._active = lease
        return lease

    def finish(self) -> None:
        self._check_usable()
        if (
            self._finished
            or self._active is not None
            or self._digests
            or self._sql_workspace
            or self._live_document_output_bytes
        ):
            _fail("PARENT_STATE")
        self._scratch -= 32 * self._requests
        if self._scratch:
            _fail("LIVE_PAYLOAD")
        self._finished = True


class NativeCaptureCreationLeaseV3:
    """All owned creation stages draw from this one remaining allowance."""

    def __init__(
        self,
        token: object,
        parent: NativeCaptureCreationBudgetV3,
        owner: UUID,
        original: UUID,
        served: UUID,
    ) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_LEASE")
        self._parent = parent
        self._owner, self._original, self._served = (
            owner,
            original,
            served,
        )
        self._input_bytes = self._byte_work = self._node_visits = 0
        self._digests = self._scratch = self._sql_workspace = 0
        self._document_outputs: dict[int, _NativeCreationDocumentOutputLeaseV3] = {}
        self._next_output_ordinal = 0
        self._closing_output_ordinal: int | None = None
        self._live_document_output_bytes = self._peak_document_output_bytes = 0
        self._output_digests = self._output_scratch = 0
        self._closing = False
        self._closed = False
        self._build: NativeCaptureCreationRootMeterV3 | None = None
        self._verify: NativeCaptureCreationRootMeterV3 | None = None
        self._root_bytes: int | None = None
        self._tracks: int | None = None
        self._items: int | None = None

    def _check(self) -> None:
        self._parent._check_usable()
        if self._closed or self._parent._finished or self._parent._active is not self:
            _fail("LEASE_CLOSED")

    @property
    def owner_user_id(self) -> UUID:
        return self._owner

    @property
    def original_request_id(self) -> UUID:
        return self._original

    @property
    def served_request_id(self) -> UUID:
        return self._served

    def assert_binding(
        self,
        *,
        transaction_binding: tuple[int, int],
        owner_user_id: UUID,
        original_request_id: UUID,
        served_request_id: UUID,
    ) -> None:
        self._check()
        if (
            type(transaction_binding) is not tuple
            or len(transaction_binding) != 2
            or any(type(value) is not int for value in transaction_binding)
            or transaction_binding != self._parent.transaction_binding
        ):
            _fail("TRANSACTION")
        for value in (owner_user_id, original_request_id, served_request_id):
            _uuid(value)
        if (owner_user_id, original_request_id, served_request_id) != (
            self._owner,
            self._original,
            self._served,
        ):
            _fail("REQUEST_IDENTITY")

    @property
    def usage(self) -> NativeCaptureCreationUsageV3:
        return NativeCaptureCreationUsageV3(
            1,
            self._input_bytes,
            self._byte_work,
            self._node_visits,
            self._digests,
            self._parent._peak_digests,
            self._scratch,
            self._parent._peak_scratch,
            self._sql_workspace,
            self._parent._peak_sql_workspace,
            self._live_document_output_bytes,
            self._peak_document_output_bytes,
        )

    def reserve(self, *, byte_work_bound: int, node_visit_bound: int) -> None:
        """Check the current remainder; this never creates a new allowance."""

        self._check()
        byte_work, nodes = _amount(byte_work_bound), _amount(node_visit_bound)
        if (
            byte_work > MAX_CREATION_REQUEST_BYTE_WORK - self._byte_work
            or nodes > MAX_CREATION_REQUEST_NODE_VISITS - self._node_visits
        ):
            _fail("REQUEST_WORK")
        if (
            byte_work > MAX_CREATION_PARENT_BYTE_WORK - self._parent._byte_work
            or nodes > MAX_CREATION_PARENT_NODE_VISITS - self._parent._node_visits
        ):
            _fail("PARENT_WORK")

    def charge(
        self,
        *,
        bytes_read: int = 0,
        bytes_written: int = 0,
        sha_input_bytes: int = 0,
        node_visits: int = 0,
    ) -> None:
        byte_work = _amount(bytes_read) + _amount(bytes_written) + _amount(sha_input_bytes)
        nodes = _amount(node_visits)
        self.reserve(byte_work_bound=byte_work, node_visit_bound=nodes)
        self._byte_work += byte_work
        self._node_visits += nodes
        self._parent._byte_work += byte_work
        self._parent._node_visits += nodes

    def admit_document(self, raw_bytes: int) -> None:
        """Charge an ordinary document before fetching/hydrating its payload."""

        self._check()
        size = _amount(raw_bytes)
        if not 1 <= size <= MAX_CREATION_DOCUMENT_BYTES:
            _fail("DOCUMENT_BYTES")
        self.admit_rows(size)

    def admit_original_baseline(self, raw_bytes: int) -> None:
        size = _amount(raw_bytes)
        if not 1 <= size <= MAX_ORIGINAL_BASELINE_V4_BYTES:
            _fail("DOCUMENT_BYTES")
        self.admit_rows(size)

    def admit_rows(self, raw_bytes: int) -> None:
        """Charge aggregate scalar/row bytes; individual documents use admit_document."""

        self._check()
        size = _amount(raw_bytes)
        if (
            self._input_bytes + self._live_document_output_bytes + size
            > MAX_CREATION_REQUEST_INPUT_BYTES
        ):
            _fail("REQUEST_INPUT_BYTES")
        if (
            self._parent._input_bytes + self._parent._live_document_output_bytes + size
            > MAX_CREATION_PARENT_INPUT_BYTES
        ):
            _fail("PARENT_INPUT_BYTES")
        self._input_bytes += size
        self._parent._input_bytes += size

    def _retain(self, n: int, *, digest: bool) -> None:
        self._check()
        if self._sql_workspace:
            _fail("SQL_WORKSPACE_PHASE")
        size = _amount(n)
        parent = self._parent
        if digest:
            if parent._digests + size > MAX_CREATION_DIGEST_BYTES:
                _fail("DIGEST_PAYLOAD")
            self._digests += size
            parent._digests += size
            parent._peak_digests = max(parent._peak_digests, parent._digests)
        else:
            if parent._scratch + size > MAX_CREATION_SCRATCH_BYTES:
                _fail("SCRATCH_PAYLOAD")
            self._scratch += size
            parent._scratch += size
            parent._peak_scratch = max(parent._peak_scratch, parent._scratch)

    def _release(self, n: int, *, digest: bool) -> None:
        self._check()
        size = _amount(n)
        available = (
            self._digests - self._output_digests if digest else self._scratch - self._output_scratch
        )
        if size > available:
            _fail("PAYLOAD_RELEASE")
        if digest:
            self._digests -= size
            self._parent._digests -= size
        else:
            self._scratch -= size
            self._parent._scratch -= size

    def retain_digests(self, n: int) -> None:
        self._retain(n, digest=True)

    def release_digests(self, n: int) -> None:
        self._release(n, digest=True)

    def retain_scratch(self, n: int) -> None:
        self._retain(n, digest=False)

    def release_scratch(self, n: int) -> None:
        self._release(n, digest=False)

    def retain_document_output(
        self,
        *,
        canonical_byte_upper: int,
        owned_payload_upper: int,
        clear_callback: Callable[[], None],
    ) -> _NativeCreationDocumentOutputLeaseV3:
        """Hold a derived graph under input limits until its actual clear succeeds."""

        self._check()
        canonical, owned = _amount(canonical_byte_upper), _amount(owned_payload_upper)
        if not 1 <= canonical <= MAX_CREATION_DOCUMENT_BYTES:
            _fail("DOCUMENT_BYTES")
        if not 1 <= owned <= MAX_CREATION_REQUEST_INPUT_BYTES:
            _fail("OUTPUT_INPUT_LIMIT")
        if not callable(clear_callback) or self._closing:
            _fail("OUTPUT_LEASE_STATE")
        if (
            self._input_bytes + self._live_document_output_bytes + owned
            > MAX_CREATION_REQUEST_INPUT_BYTES
            or self._parent._input_bytes + self._parent._live_document_output_bytes + owned
            > MAX_CREATION_PARENT_INPUT_BYTES
        ):
            _fail("OUTPUT_INPUT_LIMIT")
        self._retain(96, digest=False)
        output = _NativeCreationDocumentOutputLeaseV3(
            _PRIVATE, self, owned, clear_callback, self._next_output_ordinal
        )
        self._next_output_ordinal += 1
        self._document_outputs[id(output)] = output
        self._output_scratch += 96
        self._live_document_output_bytes += owned
        self._peak_document_output_bytes = max(
            self._peak_document_output_bytes, self._live_document_output_bytes
        )
        self._parent._live_document_output_bytes += owned
        self._parent._peak_document_output_bytes = max(
            self._parent._peak_document_output_bytes, self._parent._live_document_output_bytes
        )
        return output

    def retain_sql_workspace(self, n: int) -> None:
        """One separate SQL preimage phase; it supplies no additional work."""

        self._check()
        size = _amount(n)
        if (
            self._scratch - self._output_scratch
            or any(
                meter is not None and (meter._digests or meter._scratch)
                for meter in (self._build, self._verify)
            )
            or self._sql_workspace
            or self._parent._sql_workspace
            or not 1 <= size <= MAX_CREATION_SQL_WORKSPACE_BYTES
        ):
            _fail("SQL_WORKSPACE")
        self._sql_workspace = self._parent._sql_workspace = size
        self._parent._peak_sql_workspace = max(self._parent._peak_sql_workspace, size)

    def release_sql_workspace(self) -> None:
        self._check()
        if not self._sql_workspace:
            _fail("SQL_WORKSPACE")
        self._sql_workspace = self._parent._sql_workspace = 0

    def root_builder_meter(self, *, tracks: int, items: int) -> NativeCaptureCreationRootMeterV3:
        self._check()
        if self._build is not None or self._verify is not None:
            _fail("ROOT_PHASE")
        if not 0 <= _amount(tracks) <= 5000 or not 0 <= _amount(items) <= 100:
            _fail("ROOT_COUNTS")
        self._tracks, self._items = tracks, items
        self._build = NativeCaptureCreationRootMeterV3(_PRIVATE, self, "BUILD")
        return self._build

    def root_verifier_meter(self, *, raw_bytes: int) -> NativeCaptureCreationRootMeterV3:
        self._check()
        if (
            self._build is None
            or self._verify is not None
            or self._root_bytes != _amount(raw_bytes)
        ):
            _fail("ROOT_PHASE")
        assert self._build is not None
        if self._build._digests or self._build._scratch or self._scratch - self._output_scratch:
            _fail("LIVE_PAYLOAD")
        self._verify = NativeCaptureCreationRootMeterV3(_PRIVATE, self, "VERIFY")
        return self._verify

    def charge_finalization(self, byte_work: int, node_visits: int = 1) -> None:
        if self._verify is None:
            _fail("ROOT_PHASE")
        self.charge(bytes_read=byte_work, node_visits=node_visits)

    def close(self, *, success: bool) -> None:
        self._check()
        if type(success) is not bool or self._closing:
            _fail("CLOSE_STATE")
        if success and (
            self._digests or self._scratch or self._sql_workspace or self._document_outputs
        ):
            _fail("LIVE_PAYLOAD")
        self._closing = True
        try:
            # Callbacks run with the original source lease current. A failed
            # clear poisons the operation and leaves all its charges intact.
            while self._document_outputs:
                next(reversed(self._document_outputs.values())).close()
        finally:
            self._closing = False
        # Failure releases logical payload, never consumed input/work/counters.
        self._parent._digests -= self._digests
        self._parent._scratch -= self._scratch
        self._parent._sql_workspace -= self._sql_workspace
        self._digests = self._scratch = self._sql_workspace = 0
        self._closed = True
        self._parent._active = None


class _NativeCreationDocumentOutputLeaseV3:
    """One private graph owner, with no authority or work allowance of its own."""

    def __init__(
        self,
        token: object,
        lease: NativeCaptureCreationLeaseV3,
        owned_payload_upper: int,
        callback: Callable[[], None],
        ordinal: int,
    ) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_LEASE")
        self._lease = lease
        self._owned = owned_payload_upper
        self._callback: Callable[[], None] | None = callback
        self._identity = id(self)
        self._ordinal = ordinal
        self._closed = False
        self._digests = 0

    def _registered(self) -> bool:
        return (
            type(self) is _NativeCreationDocumentOutputLeaseV3
            and id(self) == self._identity
            and not self._closed
            and self._lease._document_outputs.get(id(self)) is self
        )

    def check_current(self) -> None:
        lease = self._lease
        if lease._parent._output_closing:
            # A descendant's clear may validate its borrowed ancestor. No
            # mutation uses this exception, and self/later-issued validation
            # is reentry even if its caller catches the resulting refusal.
            if (
                lease._closing_output_ordinal is not None
                and self._ordinal < lease._closing_output_ordinal
                and not lease._parent._poisoned
                and lease._parent._active is lease
                and not lease._closed
                and not lease._parent._finished
                and self._registered()
            ):
                return
            lease._parent._check_usable()
        lease._check()
        if not self._registered():
            _fail("OUTPUT_LEASE_STATE")

    def retain_new_digests(self, n: int) -> None:
        self._lease._check()
        self.check_current()
        size = _amount(n)
        self._lease._retain(size, digest=True)
        self._digests += size
        self._lease._output_digests += size

    def close(self) -> None:
        lease = self._lease
        lease._parent._check_usable()
        if type(self) is not _NativeCreationDocumentOutputLeaseV3 or id(self) != self._identity:
            _fail("OUTPUT_LEASE_STATE")
        if self._closed:
            return
        self.check_current()
        lease._parent._output_closing = True
        lease._closing_output_ordinal = self._ordinal
        try:
            assert self._callback is not None
            result = cast(Callable[[], object], self._callback)()
            if inspect.isawaitable(result):
                cancel = getattr(result, "close", None)
                if not callable(cancel):
                    cancel = getattr(result, "cancel", None)
                if callable(cancel):
                    cancel()
                _fail("OUTPUT_ASYNCHRONOUS_CLEAR")
            if result is not None:
                _fail("OUTPUT_CLEAR_RESULT")
            if lease._parent._poisoned:
                _fail("OUTPUT_OPERATION_POISONED")
        except BaseException:
            lease._parent._poisoned = True
            raise
        finally:
            lease._closing_output_ordinal = None
            lease._parent._output_closing = False
        # Drop the closure (and its graph references) before returning its own
        # output, digest and registry charges. No input/work is refunded.
        self._callback = None
        del lease._document_outputs[id(self)]
        lease._live_document_output_bytes -= self._owned
        lease._parent._live_document_output_bytes -= self._owned
        lease._digests -= self._digests
        lease._parent._digests -= self._digests
        lease._output_digests -= self._digests
        lease._scratch -= 96
        lease._parent._scratch -= 96
        lease._output_scratch -= 96
        self._digests = 0
        self._closed = True


class NativeCaptureCreationRootMeterV3:
    """A lease-issued pass; only its leaf count is local, never work quota."""

    def __init__(
        self, token: object, lease: NativeCaptureCreationLeaseV3, phase: Literal["BUILD", "VERIFY"]
    ) -> None:
        if token is not _PRIVATE:
            _fail("PRIVATE_ROOT_METER")
        self._lease, self._phase = lease, phase
        self._leaves = self._digests = self._scratch = 0

    def _check(self) -> None:
        self._lease._check()
        if self._lease._sql_workspace:
            _fail("SQL_WORKSPACE_PHASE")
        if self._phase == "BUILD" and self._lease._verify is not None:
            _fail("ROOT_PHASE")

    def set_raw_bytes(self, raw_bytes: int) -> None:
        self._check()
        size = _amount(raw_bytes)
        if (
            self._phase != "BUILD"
            or not 1 <= size <= MAX_CREATION_DOCUMENT_BYTES
            or self._lease._root_bytes is not None
        ):
            _fail("ROOT_BYTES")
        self._lease._root_bytes = size

    def set_counts(self, tracks: int, items: int) -> None:
        self._check()
        if _amount(tracks) != self._lease._tracks or _amount(items) != self._lease._items:
            _fail("ROOT_COUNTS")

    def charge_bytes(self, n: int) -> None:
        self._check()
        self._lease.charge(bytes_read=n)

    def charge_nodes(self, n: int = 1) -> None:
        self._check()
        self._lease.charge(node_visits=n)

    def charge_leaf(self, n: int = 1) -> None:
        self._check()
        amount = _amount(n)
        maximum = 4 * (self._lease._tracks or 0) + 2 * (self._lease._items or 0)
        if self._leaves + amount > min(maximum, 20_200):
            _fail("ROOT_LEAF_LIMIT")
        self._leaves += amount

    def retain_digests(self, n: int) -> None:
        self._check()
        self._lease._retain(n, digest=True)
        self._digests += n

    def release_digests(self, n: int) -> None:
        self._check()
        if _amount(n) > self._digests:
            _fail("PAYLOAD_RELEASE")
        self._lease._release(n, digest=True)
        self._digests -= n

    def retain_scratch(self, n: int) -> None:
        self._check()
        self._lease._retain(n, digest=False)
        self._scratch += n

    def release_scratch(self, n: int) -> None:
        self._check()
        if _amount(n) > self._scratch:
            _fail("PAYLOAD_RELEASE")
        self._lease._release(n, digest=False)
        self._scratch -= n
