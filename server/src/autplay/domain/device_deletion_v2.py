"""Pure D1 deletion proof verification, never current actor/session authority.

SIX route operations use THREE signed models/domains. REQUEST_RECEIPT and
REQUEST_RESOLVE reverify the exact original DeletionRequest under deletion:request.
CANCEL_OUTCOME reverifies the exact original DeletionCancel under deletion:cancel.
Historical continuations require full retained original proof/version/platform
evidence, not a digest-only receipt or a newly signed continuation. The separate
first-resolution candidate API verifies proof before any terminal evidence exists;
it does not decide nonacceptance. There is no bearer input
or implicit active-session requirement for historical request receipt/resolution.
A revoked historical key proves only its exact original; it grants no fresh token,
deletion request, cancellation, or session revival. Provenance is an explicit
service assertion, not something this pure module can discover or authenticate.

Future services own active actor/key/platform for first request, current account,
root/code/authority generations, code verifier/header possession, exact result
identity and live binding/refresh on access outcomes; freshness, gate-off behavior,
30-day deadline/revision, remaining OWNER, quotas, operation hash/receipt expiry
and retention. They also own ATTEMPTED/SEALED ledger evidence, veto/cutover/coverage
and the unchanged independent sealed-proof recipe. Parsing/verifying is not any of
those decisions and never fabricates success. Full nonsecret originals must remain
in typed history for their required historical lifetime; no code/refresh header
belongs in their JSON, storage payloads or logs. K/J/grants/media are not approved.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final

from cryptography.exceptions import UnsupportedAlgorithm

from autplay.domain.device_recovery_v2 import (
    _COMMIT_FIELDS,
    _COMMON_FIELDS,
    MAX_BODY_BYTES,
    DeviceKeyEvidence,
    DevicePlatform,
    DeviceTrustEvidence,
    KeyEvidenceSource,
    RecoveryV2Error,
    _parse_device_document,
    _ParsedDeviceDocument,
    _require_original,
    _verify_device_document,
)
from autplay.domain.profile_pairing import ProfilePairingError


class DeletionOperation(Enum):
    REQUEST = "deletion.request"
    REQUEST_RECEIPT = "deletion.request-receipt"
    REQUEST_RESOLVE = "deletion.request-resolve"
    PREVIEW = "deletion.preview"
    CANCEL = "deletion.cancel"
    CANCEL_OUTCOME = "deletion.cancel-outcome"


_ORIGINALS: Final = MappingProxyType(
    {
        DeletionOperation.REQUEST_RECEIPT: DeletionOperation.REQUEST,
        DeletionOperation.REQUEST_RESOLVE: DeletionOperation.REQUEST,
        DeletionOperation.CANCEL_OUTCOME: DeletionOperation.CANCEL,
    }
)
_DOMAINS: Final = MappingProxyType(
    {
        DeletionOperation.REQUEST: "autplay:device-compatibility:deletion:request:v2\n",
        DeletionOperation.REQUEST_RECEIPT: "autplay:device-compatibility:deletion:request:v2\n",
        DeletionOperation.REQUEST_RESOLVE: "autplay:device-compatibility:deletion:request:v2\n",
        DeletionOperation.PREVIEW: "autplay:device-compatibility:deletion:preview:v2\n",
        DeletionOperation.CANCEL: "autplay:device-compatibility:deletion:cancel:v2\n",
        DeletionOperation.CANCEL_OUTCOME: "autplay:device-compatibility:deletion:cancel:v2\n",
    }
)
_REQUEST_FIELDS: Final = frozenset(
    {
        "confirmed_account_id",
        "expected_code_generation",
        "expected_authority_generation",
    }
)
_CANCEL_FIELDS: Final = _COMMIT_FIELDS | {"deletion_request_id", "expected_revision"}


class DeletionV2Error(RecoveryV2Error):
    def __init__(self, *, proof: bool = False, oversized: bool = False) -> None:
        super().__init__(proof=False, oversized=oversized)
        if proof:
            self.code = "account_deletion_unavailable"
            self.http_status = 401
            self.args = (self.code,)

    def __repr__(self) -> str:
        return "DeletionV2Error(<redacted>)"


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ParsedDeletionRequest(_ParsedDeviceDocument):
    """FULL original nonsecret model with hash/signature, no transport headers."""

    operation: DeletionOperation


@dataclass(frozen=True, slots=True, init=False, repr=False)
class VerifiedDeletionProof:
    """Exact proof possession only; may describe an already revoked actor key.

    No active-authority flag is present: even a locked approved key requires the
    service's independent checks. Retain request's complete nonsecret evidence.
    """

    request: ParsedDeletionRequest
    key_evidence: DeviceKeyEvidence
    trust_evidence: DeviceTrustEvidence

    def __new__(cls, *args: object, **kwargs: object) -> VerifiedDeletionProof:
        raise TypeError("use verify_deletion_proof")

    @property
    def proof_domain(self) -> str:
        return _DOMAINS[self.request.operation]

    @property
    def signature_algorithm(self) -> str:
        return "ES256-P1363"

    def __repr__(self) -> str:
        return "VerifiedDeletionProof(<redacted>)"


@dataclass(frozen=True, slots=True, init=False, repr=False)
class VerifiedDeletionResolutionCandidate:
    """Original request proof only, with NO retained terminal-result comparison.

    This distinct type cannot be mistaken for an exact historical result check.
    It says nothing about whether acceptance was attempted or a veto can be sealed.
    """

    request: ParsedDeletionRequest
    key_evidence: DeviceKeyEvidence
    trust_evidence: DeviceTrustEvidence

    def __new__(cls, *args: object, **kwargs: object) -> VerifiedDeletionResolutionCandidate:
        raise TypeError("use verify_deletion_resolution_candidate")

    @property
    def proof_domain(self) -> str:
        return _DOMAINS[DeletionOperation.REQUEST_RESOLVE]

    @property
    def signature_algorithm(self) -> str:
        return "ES256-P1363"

    def __repr__(self) -> str:
        return "VerifiedDeletionResolutionCandidate(<redacted>)"


def parse_deletion_request(operation: DeletionOperation, raw_body: bytes) -> ParsedDeletionRequest:
    """Strict raw parser; continuations use original models, no new proof fields."""
    if type(operation) is not DeletionOperation:
        raise DeletionV2Error()
    model = _ORIGINALS.get(operation, operation)
    extra = {
        DeletionOperation.REQUEST: _REQUEST_FIELDS,
        DeletionOperation.PREVIEW: frozenset(),
        DeletionOperation.CANCEL: _CANCEL_FIELDS,
    }[model]
    return _parse_device_document(
        operation,
        raw_body,
        _COMMON_FIELDS | extra,
        ParsedDeletionRequest,
        DeletionV2Error,
    )


def verify_deletion_proof(
    request: ParsedDeletionRequest,
    *,
    key_evidence: DeviceKeyEvidence,
    expected_trust: DeviceTrustEvidence,
    retained_original: ParsedDeletionRequest | None = None,
) -> VerifiedDeletionProof:
    """Reverify original-domain P1363 without asserting current service authority.

    REQUEST uses explicit locked actor evidence; PREVIEW/CANCEL may use the client's
    recipient key. All three continuations require locked/historical evidence and
    the complete retained original. No active bearer is needed merely to verify
    historical request proof. First negative resolution still needs current known
    actor/code/authority and ledger coverage in the service; a SEALED replay uses
    its original identity/key/platform, even after revocation/identity rotation.
    For a request with no retained terminal original, use the separate resolution
    candidate verifier before the service's independent first-veto decisions.
    """
    if (
        type(request) is not ParsedDeletionRequest
        or type(request.operation) is not DeletionOperation
    ):
        raise DeletionV2Error(proof=True)
    try:
        if type(key_evidence) is not DeviceKeyEvidence:
            raise ValueError("key evidence")
        if request.operation in _ORIGINALS:
            if (
                type(retained_original) is not ParsedDeletionRequest
                or retained_original.operation is not _ORIGINALS[request.operation]
                or key_evidence.source
                not in {
                    KeyEvidenceSource.LOCKED_APPROVED,
                    KeyEvidenceSource.HISTORICAL_APPROVED,
                }
            ):
                raise ValueError("historical evidence")
        elif request.operation is DeletionOperation.REQUEST:
            if key_evidence.source is not KeyEvidenceSource.LOCKED_APPROVED:
                raise ValueError("actor evidence")
        elif key_evidence.source is KeyEvidenceSource.HISTORICAL_APPROVED:
            raise ValueError("historical recipient")
        if retained_original is not None:
            if type(retained_original) is not ParsedDeletionRequest:
                raise ValueError("original type")
            _require_original(request, retained_original)
        _verify_device_document(request, key_evidence, expected_trust, _DOMAINS[request.operation])
    except ValueError, TypeError, KeyError, UnsupportedAlgorithm, ProfilePairingError:
        raise DeletionV2Error(proof=True) from None
    result = object.__new__(VerifiedDeletionProof)
    object.__setattr__(result, "request", request)
    object.__setattr__(result, "key_evidence", key_evidence)
    object.__setattr__(result, "trust_evidence", expected_trust)
    return result


def verify_deletion_resolution_candidate(
    request: ParsedDeletionRequest,
    *,
    key_evidence: DeviceKeyEvidence,
    expected_trust: DeviceTrustEvidence,
) -> VerifiedDeletionResolutionCandidate:
    """Verify REQUEST_RESOLVE proof when a terminal original may not exist yet.

    The service must supply its current known actor key/platform and identity under
    locks. No active bearer is required here. The service must still check current
    account/code/authority, original timestamp beyond coverage C+120s+1us, and now
    beyond requested_at+120s+1us; ATTEMPTED without matching evidence fails closed.
    Only the ledger/service can decide or durably seal NOT_ACCEPTED. On existing
    accepted/SEALED history use verify_deletion_proof with the full retained
    original instead. This is no new wire operation, signing recipe or authority.
    """
    if (
        type(request) is not ParsedDeletionRequest
        or request.operation is not DeletionOperation.REQUEST_RESOLVE
    ):
        raise DeletionV2Error(proof=True)
    try:
        if (
            type(key_evidence) is not DeviceKeyEvidence
            or key_evidence.source is not KeyEvidenceSource.LOCKED_APPROVED
        ):
            raise ValueError("known actor evidence")
        _verify_device_document(
            request,
            key_evidence,
            expected_trust,
            _DOMAINS[DeletionOperation.REQUEST_RESOLVE],
        )
    except ValueError, TypeError, KeyError, UnsupportedAlgorithm, ProfilePairingError:
        raise DeletionV2Error(proof=True) from None
    result = object.__new__(VerifiedDeletionResolutionCandidate)
    object.__setattr__(result, "request", request)
    object.__setattr__(result, "key_evidence", key_evidence)
    object.__setattr__(result, "trust_evidence", expected_trust)
    return result


__all__ = (
    "MAX_BODY_BYTES",
    "DeletionOperation",
    "DeletionV2Error",
    "DeviceKeyEvidence",
    "DevicePlatform",
    "DeviceTrustEvidence",
    "KeyEvidenceSource",
    "ParsedDeletionRequest",
    "VerifiedDeletionProof",
    "VerifiedDeletionResolutionCandidate",
    "parse_deletion_request",
    "verify_deletion_proof",
    "verify_deletion_resolution_candidate",
)
