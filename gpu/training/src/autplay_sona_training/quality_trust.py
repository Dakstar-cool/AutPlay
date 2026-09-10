"""Deployment-only pinned reviewer trust for Sona quality admission."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from autplay.domain.profile_pairing import public_key_thumbprint

SONA_REVIEWER_SPKI_PATH_ENV = "AUTPLAY_SONA_QUALITY_REVIEWER_SPKI_PATH"
SONA_REVIEWER_THUMBPRINT_ENV = "AUTPLAY_SONA_QUALITY_REVIEWER_THUMBPRINT_SHA256"
SONA_MAX_REVIEWER_SPKI_BYTES = 4_096


@dataclass(frozen=True, slots=True)
class SonaReviewerTrustAnchor:
    """An SPKI already matched to an independently configured deployment pin."""

    thumbprint_sha256: str
    reviewer_spki: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            len(self.thumbprint_sha256) != 64
            or any(value not in "0123456789abcdef" for value in self.thumbprint_sha256)
            or not 1 <= len(self.reviewer_spki) <= SONA_MAX_REVIEWER_SPKI_BYTES
        ):
            raise ValueError("Sona reviewer trust anchor is invalid")
        try:
            actual = public_key_thumbprint(self.reviewer_spki).hex()
        except (TypeError, ValueError) as error:
            raise ValueError("Sona reviewer SPKI is invalid") from error
        if actual != self.thumbprint_sha256:
            raise ValueError("Sona reviewer SPKI does not match its deployment pin")


def load_deployment_sona_reviewer_trust_anchor(
    environment: Mapping[str, str] | None = None,
) -> SonaReviewerTrustAnchor:
    """Load the trust root only from process deployment configuration, never CLI payloads."""

    values = os.environ if environment is None else environment
    raw_path = values.get(SONA_REVIEWER_SPKI_PATH_ENV)
    thumbprint = values.get(SONA_REVIEWER_THUMBPRINT_ENV)
    if raw_path is None or thumbprint is None:
        raise RuntimeError("Sona quality reviewer trust is not configured")
    path = Path(raw_path)
    if not path.is_absolute() or path.is_symlink():
        raise RuntimeError("Sona quality reviewer SPKI path is unsafe")
    try:
        with path.open("rb") as stream:
            reviewer_spki = stream.read(SONA_MAX_REVIEWER_SPKI_BYTES + 1)
    except OSError as error:
        raise RuntimeError("Sona quality reviewer SPKI is unavailable") from error
    if len(reviewer_spki) > SONA_MAX_REVIEWER_SPKI_BYTES:
        raise RuntimeError("Sona quality reviewer SPKI exceeds the accepted bound")
    try:
        return SonaReviewerTrustAnchor(thumbprint, reviewer_spki)
    except ValueError as error:
        raise RuntimeError("Sona quality reviewer trust is invalid") from error


__all__ = (
    "SONA_MAX_REVIEWER_SPKI_BYTES",
    "SONA_REVIEWER_SPKI_PATH_ENV",
    "SONA_REVIEWER_THUMBPRINT_ENV",
    "SonaReviewerTrustAnchor",
    "load_deployment_sona_reviewer_trust_anchor",
)
