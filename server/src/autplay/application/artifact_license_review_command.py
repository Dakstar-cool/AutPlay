"""Exact, bounded command bytes for a future operation-bound ML admin review."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID

import rfc8785

from autplay.domain.artifact_license_review import (
    ArtifactLicenseReview,
    ArtifactLicenseReviewError,
    LicenseDecisionState,
)

MAX_ARTIFACT_REVIEW_COMMAND_BYTES = 32_768
ARTIFACT_REVIEW_COMMAND_DOMAIN = b"autplay.ml.artifact-license-review.v1\0"
_MAX_INTEGER = 9_007_199_254_740_991
_FIELDS = frozenset(
    {
        "operation_id",
        "expected_generation",
        "artifact_sha256",
        "state",
        "license_identifier",
        "license_text_sha256",
        "use_restrictions",
        "redistribution_decision",
        "modification_decision",
        "attribution_payload",
        "review_reference",
        "reason_code",
        "max_offline_revocation_lag_ms",
        "derived_output_disposition",
    }
)
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_REASON = re.compile(r"[A-Z][A-Z0-9_]{0,79}\Z")


class ArtifactReviewCommandError(ValueError):
    def __init__(self) -> None:
        super().__init__("artifact_license_review_command_invalid")


@dataclass(frozen=True, slots=True)
class ArtifactReviewCommand:
    operation_id: UUID
    expected_generation: int
    artifact_sha256: bytes
    reason_code: str
    review: ArtifactLicenseReview
    request_sha256: bytes


def parse_artifact_review_command(
    raw: bytes, *, path_artifact_sha256: str
) -> ArtifactReviewCommand:
    """Reject ambiguous wire bytes before issuing a challenge or mutating SQL."""

    try:
        if type(raw) is not bytes or not 1 <= len(raw) <= MAX_ARTIFACT_REVIEW_COMMAND_BYTES:
            raise ArtifactReviewCommandError()
        if _HASH.fullmatch(path_artifact_sha256) is None:
            raise ArtifactReviewCommandError()
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
        if type(document) is not dict or set(document) != _FIELDS:
            raise ArtifactReviewCommandError()
        _validate_json(document, depth=0)
        if document["artifact_sha256"] != path_artifact_sha256:
            raise ArtifactReviewCommandError()
        operation_id = _uuid(document["operation_id"])
        generation = _integer(document["expected_generation"], minimum=1, maximum=_MAX_INTEGER - 1)
        reason = document["reason_code"]
        if type(reason) is not str or _REASON.fullmatch(reason) is None:
            raise ArtifactReviewCommandError()
        state = LicenseDecisionState(document["state"])
        review = ArtifactLicenseReview(
            state=state,
            license_identifier=document["license_identifier"],
            license_text_sha256=bytes.fromhex(_digest(document["license_text_sha256"])),
            use_restrictions=document["use_restrictions"],
            redistribution_decision=document["redistribution_decision"],
            modification_decision=document["modification_decision"],
            attribution_payload=document["attribution_payload"],
            review_reference=document["review_reference"],
            max_offline_revocation_lag_ms=_integer(
                document["max_offline_revocation_lag_ms"], minimum=0
            ),
            derived_output_disposition=document["derived_output_disposition"],
        )
        canonical = rfc8785.dumps(
            {
                "action": "ARTIFACT_LICENSE_REVIEW",
                "path_artifact_sha256": path_artifact_sha256,
                "command": document,
            }
        )
        return ArtifactReviewCommand(
            operation_id=operation_id,
            expected_generation=generation,
            artifact_sha256=bytes.fromhex(path_artifact_sha256),
            reason_code=reason,
            review=review,
            request_sha256=sha256(ARTIFACT_REVIEW_COMMAND_DOMAIN + canonical).digest(),
        )
    except (
        AttributeError,
        KeyError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
        rfc8785.CanonicalizationError,
        ArtifactLicenseReviewError,
    ) as error:
        if isinstance(error, ArtifactReviewCommandError):
            raise
        raise ArtifactReviewCommandError() from error


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ArtifactReviewCommandError()
        result[key] = value
    return result


def _reject_constant(_: str) -> None:
    raise ArtifactReviewCommandError()


def _validate_json(value: object, *, depth: int) -> None:
    if depth > 12:
        raise ArtifactReviewCommandError()
    if value is None or type(value) in (str, bool):
        return
    if type(value) is int:
        if not -_MAX_INTEGER <= value <= _MAX_INTEGER:
            raise ArtifactReviewCommandError()
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ArtifactReviewCommandError()
        return
    if type(value) is list:
        for item in value:
            _validate_json(item, depth=depth + 1)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ArtifactReviewCommandError()
            _validate_json(item, depth=depth + 1)
        return
    raise ArtifactReviewCommandError()


def _uuid(value: object) -> UUID:
    if type(value) is not str:
        raise ArtifactReviewCommandError()
    identifier = UUID(value)
    if str(identifier) != value:
        raise ArtifactReviewCommandError()
    return identifier


def _integer(value: object, *, minimum: int, maximum: int = _MAX_INTEGER) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ArtifactReviewCommandError()
    return value


def _digest(value: object) -> str:
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ArtifactReviewCommandError()
    return value
