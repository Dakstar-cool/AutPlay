"""Paid immutable-original borrows; no source or SQL authority is created.

Only the actual original producer registers its already owned baseline. Each
borrow occupies a separate output receipt before a dependent expected graph is
allocated. Normal close and packet cleanup therefore use the same LIFO order.
"""

from __future__ import annotations

from typing import Final, NoReturn

from autplay.domain.native_request_budget_v3 import NativeRequestV3Error
from autplay.domain.native_request_expected_meter_v3 import (
    NativeRequestV3DocumentOutputLeaseProtocol,
    NativeRequestV3ExpectedMeterProtocol,
)
from autplay.domain.recommendations import (
    PipelineDefinition,
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationResponse,
)

_PRIVATE: Final = object()
_OWNERS: dict[tuple[int, int], _NativeImmutableBaselineOwnerV3] = {}


def _fail(code: str) -> NoReturn:
    raise NativeRequestV3Error("CONNECTED_V3_ORIGINAL_OWNER_" + code)


class _NativeImmutableBaselineOwnerV3:
    __slots__ = (
        "_baseline",
        "_borrows",
        "_cleanup_failed",
        "_identity",
        "_meter",
        "_output",
        "_pipeline",
        "_query",
        "_response",
        "_reference_snapshot",
    )

    def __init__(
        self,
        token: object,
        baseline: RecommendationInputSnapshot,
        meter: NativeRequestV3ExpectedMeterProtocol,
        output: NativeRequestV3DocumentOutputLeaseProtocol,
    ) -> None:
        if token is not _PRIVATE:
            _fail("ISSUER")
        self._baseline: RecommendationInputSnapshot | None = baseline
        self._meter = meter
        self._output = output
        self._reference_snapshot = False
        self._borrows = 0
        self._identity = id(self)
        self._query: RecommendationQuery | None = None
        self._response: RecommendationResponse | None = None
        self._pipeline: PipelineDefinition | None = None
        self._cleanup_failed = False

    def _check_registered(self) -> None:
        baseline = self._baseline
        if (
            type(self) is not _NativeImmutableBaselineOwnerV3
            or id(self) != self._identity
            or baseline is None
            or _OWNERS.get((id(self._meter), id(baseline))) is not self
        ):
            _fail("CLOSED")

    def check_current(self) -> None:
        self._check_registered()
        self._output.check_current()

    def close(self) -> None:
        """Unregister inside the original output's already checked clear callback.

        The output receipt checks its own lifetime before invoking the callback.
        Checking that same receipt again from the callback is forbidden; only
        an earlier ancestor receipt may be checked while an output clears.
        Producers call check_current before requesting explicit output close.
        """
        self._check_registered()
        if self._cleanup_failed:
            _fail("FAILURE_CLEANUP_RETAINED")
        if self._borrows:
            _fail("BORROWED")
        assert self._baseline is not None
        del _OWNERS[(id(self._meter), id(self._baseline))]
        self._baseline = self._query = self._response = self._pipeline = None


class NativeImmutableBaselineBorrowV3:
    __slots__ = ("_cleanup_failed", "_identity", "_output", "_owner")
    _identity: int
    _cleanup_failed: bool
    _output: NativeRequestV3DocumentOutputLeaseProtocol
    _owner: _NativeImmutableBaselineOwnerV3 | None

    def __new__(cls) -> NativeImmutableBaselineBorrowV3:
        _fail("ISSUER")

    def check_current(self) -> None:
        if (
            type(self) is not NativeImmutableBaselineBorrowV3
            or id(self) != self._identity
            or self._owner is None
        ):
            _fail("BORROW_CLOSED")
        self._output.check_current()
        self._owner.check_current()

    def close(self) -> None:
        if id(self) != self._identity:
            _fail("ISSUER")
        self._output.close()

    @property
    def reference_snapshot(self) -> bool:
        self.check_current()
        assert self._owner is not None
        return self._owner._reference_snapshot

    def _retain_failed_cleanup(self) -> None:
        self.check_current()
        self._cleanup_failed = True
        self._output.close()

    def _clear(self) -> None:
        if self._cleanup_failed:
            _fail("FAILURE_CLEANUP_RETAINED")
        owner = self._owner
        if owner is None:
            _fail("BORROW_CLOSED")
        owner.check_current()
        if owner._borrows < 1:
            _fail("BORROW_COUNT")
        owner._borrows -= 1
        self._owner = None


def _register_native_original_owner_v3(
    baseline: RecommendationInputSnapshot,
    *,
    meter: NativeRequestV3ExpectedMeterProtocol,
    output: NativeRequestV3DocumentOutputLeaseProtocol,
    query: RecommendationQuery | None = None,
    response: RecommendationResponse | None = None,
    pipeline: PipelineDefinition | None = None,
    reference_snapshot: bool = False,
) -> _NativeImmutableBaselineOwnerV3:
    """Producer-only registration after independent original validation.

    The original output's conservative payload bound includes this fixed
    registry entry and owner. This function never validates source provenance.
    """
    if type(baseline) is not RecommendationInputSnapshot or type(reference_snapshot) is not bool:
        _fail("TYPE")
    output.check_current()
    key = (id(meter), id(baseline))
    if key in _OWNERS:
        _fail("ALREADY_OWNED")
    if (query is not None or response is not None or pipeline is not None) and (
        type(query) is not RecommendationQuery
        or type(response) is not RecommendationResponse
        or type(pipeline) is not PipelineDefinition
        or response.request.query is not query
        or response.request.pipeline is not pipeline
        or response.request.snapshot is not baseline.reference
    ):
        _fail("INPUT_BINDING")
    # Three extra references (24B on the reviewed64-bit ABI) plus owner/registry
    # controls fit the already retained16KiB producer fixed payload. No graph is
    # copied or traversed. These actual metadata writes use the same work meter.
    meter.reserve(byte_work_bound=256, node_visit_bound=16)
    meter.charge(bytes_written=256, node_visits=16)
    owner = _NativeImmutableBaselineOwnerV3(_PRIVATE, baseline, meter, output)
    owner._reference_snapshot = reference_snapshot
    owner._query, owner._response, owner._pipeline = query, response, pipeline
    _OWNERS[key] = owner
    return owner


def borrow_native_immutable_baseline_v3(
    snapshot: RecommendationInputSnapshot,
    *,
    meter: NativeRequestV3ExpectedMeterProtocol,
) -> NativeImmutableBaselineBorrowV3:
    owner = _OWNERS.get((id(meter), id(snapshot)))
    if owner is None or owner._baseline is not snapshot or owner._meter is not meter:
        _fail("BINDING")
    owner.check_current()
    meter.reserve(byte_work_bound=256, node_visit_bound=2)
    # Fixed construction/control state is paid before allocating the receipt's
    # dependent payload. The permanent payload belongs to the new output.
    meter.retain_scratch(128)
    try:
        meter.charge(bytes_written=256, node_visits=2)
        value = object.__new__(NativeImmutableBaselineBorrowV3)
        value._identity = id(value)
        value._owner = owner
        value._cleanup_failed = False
        output = meter.retain_document_output(
            canonical_byte_upper=1,
            owned_payload_upper=256,
            clear_callback=value._clear,
        )
        value._output = output
        owner._borrows += 1
        return value
    finally:
        meter.release_scratch(128)


def _verify_native_original_inputs_v3(
    borrow: NativeImmutableBaselineBorrowV3,
    query: RecommendationQuery,
    response: RecommendationResponse,
    pipeline: PipelineDefinition,
) -> None:
    borrow.check_current()
    owner = borrow._owner
    assert owner is not None
    # Reject foreign graphs by identity BEFORE touching any caller-sized tuple.
    if (
        owner._query is None
        or query is not owner._query
        or response is not owner._response
        or pipeline is not owner._pipeline
    ):
        _fail("INPUT_BINDING")


def _retain_native_original_failure_v3(
    baseline: RecommendationInputSnapshot,
    *,
    meter: NativeRequestV3ExpectedMeterProtocol,
) -> None:
    owner = _OWNERS.get((id(meter), id(baseline)))
    if owner is not None:
        owner._cleanup_failed = True
        owner._output.close()
