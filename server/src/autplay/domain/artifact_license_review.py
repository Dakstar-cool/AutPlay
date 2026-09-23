"""Validated, immutable inputs to an artifact-license decision append."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import rfc8785

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None


class ArtifactLicenseReviewError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LicenseDecisionState(StrEnum):
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class ArtifactLicenseReview:
    state: LicenseDecisionState
    license_identifier: str
    license_text_sha256: bytes
    use_restrictions: dict[str, JsonValue]
    redistribution_decision: str
    modification_decision: str
    attribution_payload: dict[str, JsonValue]
    review_reference: str
    max_offline_revocation_lag_ms: int
    derived_output_disposition: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.state, LicenseDecisionState)
            or type(self.license_identifier) is not str
            or not 1 <= len(self.license_identifier) <= 200
            or type(self.license_text_sha256) is not bytes
            or len(self.license_text_sha256) != 32
            or type(self.use_restrictions) is not dict
            or type(self.attribution_payload) is not dict
            or self.redistribution_decision not in {"PERMITTED", "DENIED", "SEPARATE_INSTALL_ONLY"}
            or self.modification_decision not in {"PERMITTED", "DENIED", "UNREVIEWED"}
            or type(self.review_reference) is not str
            or not 1 <= len(self.review_reference) <= 500
            or self.derived_output_disposition
            not in {"DELETE_AFTER_LEASE", "RETAIN_NON_DISTRIBUTABLE"}
            or type(self.max_offline_revocation_lag_ms) is not int
            or not 0 <= self.max_offline_revocation_lag_ms <= 9_007_199_254_740_991
            or (self.state is LicenseDecisionState.APPROVED)
            != (self.max_offline_revocation_lag_ms > 0)
        ):
            raise ArtifactLicenseReviewError("artifact_license_review_invalid")
        for document, limit in (
            (self.use_restrictions, 8192),
            (self.attribution_payload, 8192),
        ):
            try:
                encoded = rfc8785.dumps(document)
            except (TypeError, ValueError, rfc8785.CanonicalizationError) as error:
                raise ArtifactLicenseReviewError("artifact_license_review_invalid") from error
            if len(encoded) > limit:
                raise ArtifactLicenseReviewError("artifact_license_review_invalid")


@dataclass(frozen=True, slots=True)
class ArtifactLicenseDecision:
    artifact_sha256: bytes
    decision_sequence: int
    effective_generation: int
    state: LicenseDecisionState
