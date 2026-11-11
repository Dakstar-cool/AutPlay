"""R1 read-only authority for one retained portable v2 original grant."""

from __future__ import annotations

from typing import Protocol

from autplay.domain.auth import (
    AccessTokenClaims,
    InvalidAccessTokenError,
    PortableAccessAdmission,
)


class PortableAccessTtlExceeded(InvalidAccessTokenError):
    """Only a fully verified JWT exceeding the current TTL can carry a candidate.

    This is a rejection, never authentication. The owned receipt reader must
    authorize it before any public auth/decode seam returns its claims.
    """

    def __init__(self, claims: AccessTokenClaims) -> None:
        super().__init__()
        self.claims = claims


class PortableAccessReader(Protocol):
    """Reader bound to the auth UoW's exact database session and lock lifetime."""

    def authorize_original(self, claims: AccessTokenClaims) -> PortableAccessAdmission:
        """Require one committed receipt and current authority; fail closed."""
        ...
