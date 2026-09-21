"""Strict purpose and activation headers for authenticated Vault byte routes."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from starlette.requests import Request

from autplay.domain.resource_admission import ActivationFence
from autplay.runtime.http import ApiError

_NAMES = (
    "autplay-resource-type",
    "autplay-operation-id",
    "autplay-activation-id",
    "autplay-generation",
)
_DEVICE_TYPES = frozenset({"PLAY_INSTANCE", "DOWNLOAD_INTENT", "UPLOAD_INTENT"})


@dataclass(frozen=True, slots=True)
class ResourceIoHeaders:
    resource_type: str
    fence: ActivationFence


def require_resource_io_headers(request: Request, *, allowed: frozenset[str]) -> ResourceIoHeaders:
    """Header identities are not bearer capabilities; the caller also authenticates.

    The URL supplies the actual audio variant or upload target. A recording or
    target identifier is never accepted from these headers.
    """
    values = [request.headers.getlist(name) for name in _NAMES]
    if not any(values):
        raise ApiError("resource_admission_required", "Resource admission is required.", 409)
    if any(len(items) != 1 or not items[0] or len(items[0]) > 36 for items in values):
        raise _invalid()
    resource_type, operation, activation, generation = (items[0] for items in values)
    if resource_type not in _DEVICE_TYPES:
        raise _invalid()
    if resource_type not in allowed:
        raise ApiError("resource_purpose_mismatch", "The resource purpose does not match.", 409)
    try:
        number = int(generation)
        if str(number) != generation or not 1 <= number <= 2**53 - 1:
            raise ValueError
        operation_id, activation_id = UUID(operation), UUID(activation)
        if str(operation_id) != operation or str(activation_id) != activation:
            raise ValueError
    except ValueError:
        raise _invalid() from None
    return ResourceIoHeaders(resource_type, ActivationFence(operation_id, activation_id, number))


def _invalid() -> ApiError:
    return ApiError("resource_request_invalid", "The resource headers are invalid.", 400)
