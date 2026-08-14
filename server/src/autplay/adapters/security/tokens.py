"""Strict HS256 access tokens and opaque rotating refresh credentials."""

from __future__ import annotations

import base64
import binascii
import hashlib
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Final, cast
from uuid import UUID

import jwt
from jwt.exceptions import PyJWTError

from autplay.domain.auth import AccessTokenClaims, InvalidAccessTokenError, Principal
from autplay.ports.auth import RefreshCredential

ACCESS_TOKEN_TYPE: Final = "access"
ACCESS_TOKEN_HEADER_TYPE: Final = "at+jwt"
ACCESS_TOKEN_ALGORITHM: Final = "HS256"
REFRESH_TOKEN_BYTES: Final = 32
REFRESH_TOKEN_LENGTH: Final = 43
MAX_ACCESS_TOKEN_LENGTH: Final = 4096


class Hs256AccessTokenCodec:
    """Issue and validate short-lived, session-bound access JWTs."""

    __slots__ = ("_audience", "_clock_skew", "_issuer", "_max_ttl", "_secret")

    def __init__(
        self,
        secret: str | bytes,
        *,
        issuer: str,
        audience: str,
        max_ttl: timedelta = timedelta(minutes=15),
        clock_skew: timedelta = timedelta(seconds=30),
    ) -> None:
        secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
        if len(secret_bytes) < 32:
            raise ValueError("HS256 access-token secret must contain at least 32 bytes")
        if not issuer or len(issuer) > 200 or not audience or len(audience) > 200:
            raise ValueError("access-token issuer/audience must contain 1..200 characters")
        if not timedelta(0) < max_ttl <= timedelta(minutes=15):
            raise ValueError("maximum access-token TTL must be within 15 minutes")
        if not timedelta(0) <= clock_skew <= timedelta(minutes=1):
            raise ValueError("access-token clock skew must be within 0..1 minute")
        self._secret = bytes(secret_bytes)
        self._issuer = issuer
        self._audience = audience
        self._max_ttl = max_ttl
        self._clock_skew = clock_skew

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(secret=<redacted>, issuer={self._issuer!r}, "
            f"audience={self._audience!r})"
        )

    def issue(
        self,
        principal: Principal,
        *,
        token_id: UUID,
        issued_at: datetime,
        expires_at: datetime,
    ) -> str:
        """Issue one JWT with a fixed algorithm and complete claim set."""

        issued_at = _require_aware(issued_at)
        expires_at = _require_aware(expires_at)
        lifetime = expires_at - issued_at
        if not timedelta(0) < lifetime <= self._max_ttl:
            raise ValueError("access-token lifetime exceeds configured short TTL")
        issued_timestamp = int(issued_at.timestamp())
        payload: dict[str, str | int] = {
            "iss": self._issuer,
            "aud": self._audience,
            "sub": str(principal.user_id),
            "sid": str(principal.session_id),
            "did": str(principal.device_id),
            "jti": str(token_id),
            "iat": issued_timestamp,
            "nbf": issued_timestamp,
            "exp": int(expires_at.timestamp()),
            "token_type": ACCESS_TOKEN_TYPE,
        }
        return jwt.encode(
            payload,
            self._secret,
            algorithm=ACCESS_TOKEN_ALGORITHM,
            headers={"typ": ACCESS_TOKEN_HEADER_TYPE},
        )

    def decode(self, token: str, *, now: datetime) -> AccessTokenClaims:
        """Verify signature, fixed metadata, required claims, types, and times."""

        now = _require_aware(now)
        if not token or len(token) > MAX_ACCESS_TOKEN_LENGTH or token.strip() != token:
            raise InvalidAccessTokenError
        try:
            decoded = jwt.decode_complete(
                token,
                self._secret,
                algorithms=[ACCESS_TOKEN_ALGORITHM],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": [
                        "iss",
                        "aud",
                        "sub",
                        "sid",
                        "did",
                        "jti",
                        "iat",
                        "nbf",
                        "exp",
                        "token_type",
                    ],
                    "strict_aud": True,
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
            header = _mapping(decoded.get("header"))
            payload = _mapping(decoded.get("payload"))
            if (
                header.get("alg") != ACCESS_TOKEN_ALGORITHM
                or header.get("typ") != ACCESS_TOKEN_HEADER_TYPE
                or payload.get("token_type") != ACCESS_TOKEN_TYPE
            ):
                raise InvalidAccessTokenError
            issued_at = _claim_time(payload, "iat")
            not_before = _claim_time(payload, "nbf")
            expires_at = _claim_time(payload, "exp")
            if not_before != issued_at:
                raise InvalidAccessTokenError
            if not timedelta(0) < expires_at - issued_at <= self._max_ttl:
                raise InvalidAccessTokenError
            if issued_at > now + self._clock_skew or not_before > now + self._clock_skew:
                raise InvalidAccessTokenError
            if expires_at <= now - self._clock_skew:
                raise InvalidAccessTokenError
            return AccessTokenClaims(
                user_id=_claim_uuid(payload, "sub"),
                device_id=_claim_uuid(payload, "did"),
                session_id=_claim_uuid(payload, "sid"),
                token_id=_claim_uuid(payload, "jti"),
                issued_at=issued_at,
                not_before=not_before,
                expires_at=expires_at,
            )
        except InvalidAccessTokenError:
            raise
        except (PyJWTError, TypeError, ValueError, OverflowError) as error:
            raise InvalidAccessTokenError from error


class OpaqueRefreshTokenCodec:
    """Generate canonical URL-safe tokens with exactly 256 bits of entropy."""

    __slots__ = ("_random_bytes",)

    def __init__(self, random_bytes: Callable[[int], bytes] = secrets.token_bytes) -> None:
        self._random_bytes = random_bytes

    def issue(self) -> RefreshCredential:
        """Return a token once while retaining only its SHA-256 digest."""

        random_value = self._random_bytes(REFRESH_TOKEN_BYTES)
        if len(random_value) != REFRESH_TOKEN_BYTES:
            raise RuntimeError("secure random source returned an invalid refresh-token length")
        token = base64.urlsafe_b64encode(random_value).rstrip(b"=").decode("ascii")
        return RefreshCredential(token=token, sha256=_sha256(token))

    def digest(self, token: str) -> bytes | None:
        """Hash only canonical 32-byte URL-safe refresh-token encodings."""

        if len(token) != REFRESH_TOKEN_LENGTH or token.strip() != token:
            return None
        try:
            encoded = token.encode("ascii")
            decoded = base64.b64decode(encoded + b"=", altchars=b"-_", validate=True)
        except UnicodeEncodeError, binascii.Error, ValueError:
            return None
        canonical = base64.urlsafe_b64encode(decoded).rstrip(b"=")
        if len(decoded) != REFRESH_TOKEN_BYTES or not secrets.compare_digest(canonical, encoded):
            return None
        return _sha256(token)


def _sha256(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii")).digest()


def _require_aware(instant: datetime) -> datetime:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("token timestamps must be timezone-aware")
    return instant


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidAccessTokenError
    return cast(Mapping[str, object], value)


def _claim_time(payload: Mapping[str, object], name: str) -> datetime:
    value = payload.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise InvalidAccessTokenError
    return datetime.fromtimestamp(value, tz=UTC)


def _claim_uuid(payload: Mapping[str, object], name: str) -> UUID:
    value = payload.get(name)
    if not isinstance(value, str):
        raise InvalidAccessTokenError
    try:
        return UUID(value)
    except ValueError as error:
        raise InvalidAccessTokenError from error


__all__ = (
    "ACCESS_TOKEN_ALGORITHM",
    "ACCESS_TOKEN_HEADER_TYPE",
    "ACCESS_TOKEN_TYPE",
    "REFRESH_TOKEN_BYTES",
    "Hs256AccessTokenCodec",
    "OpaqueRefreshTokenCodec",
)
