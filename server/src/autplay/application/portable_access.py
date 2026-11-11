"""R1 authorization of a cryptographic TTL candidate through the owned UoW."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from autplay.domain.auth import (
    AccessTokenClaims,
    InvalidAccessTokenError,
    PortableAccessAdmission,
    Principal,
)
from autplay.ports.auth import AuthUnitOfWorkFactory
from autplay.ports.clock import Clock


def authorize_original_portable_access(
    claims: AccessTokenClaims,
    *,
    units: AuthUnitOfWorkFactory,
    clock: Clock,
) -> tuple[Principal, AccessTokenClaims]:
    """Never return a candidate without current receipt/session/identity authority.

    Receipt authorization and its final database clock run in this exact UoW.
    The final local clock also includes rollback/connection-release delay. This
    boundary intentionally sanitizes failures from any storage implementation.
    """
    try:
        with units() as unit:
            reader = unit.portable_access
            if reader is None:
                raise InvalidAccessTokenError
            admission = reader.authorize_original(claims)
            if type(admission) is not PortableAccessAdmission:
                raise InvalidAccessTokenError
        now = clock.now()
        if (
            now.tzinfo is None
            or now.utcoffset() is None
            or not timedelta(0) < claims.expires_at - claims.issued_at <= timedelta(seconds=900)
            or not admission.accepts(admission.principal, claims, now)
        ):
            raise InvalidAccessTokenError
        return admission.principal, replace(claims, portable_admission=admission)
    except Exception:
        # No driver/SQL parameters, raw token or nested exception escapes auth.
        pass
    raise InvalidAccessTokenError from None
