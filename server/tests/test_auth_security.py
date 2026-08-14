"""CPU-only password and token security tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
import pytest
from argon2 import PasswordHasher as RawPasswordHasher
from argon2.low_level import Type
from autplay.adapters.security.passwords import (
    MAX_PASSWORD_UTF8_BYTES,
    Argon2idPasswordHasher,
)
from autplay.adapters.security.tokens import (
    ACCESS_TOKEN_ALGORITHM,
    Hs256AccessTokenCodec,
    OpaqueRefreshTokenCodec,
)
from autplay.domain.auth import AccountRole, InvalidAccessTokenError, Principal, TokenPair

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
SECRET = b"test-only-access-signing-secret-32-bytes-minimum"
ISSUER = "autplay-test"
AUDIENCE = "autplay-api"
USER_ID = UUID("0198ac00-0000-7000-8000-000000000001")
DEVICE_ID = UUID("0198ac00-0000-7000-8000-000000000002")
SESSION_ID = UUID("0198ac00-0000-7000-8000-000000000003")
TOKEN_ID = UUID("0198ac00-0000-7000-8000-000000000004")


def test_argon2id_hashes_use_explicit_safe_parameters_and_random_salts() -> None:
    """The future password path is Argon2id v19 with auditable work factors."""

    hasher = Argon2idPasswordHasher()
    first = hasher.hash_password("correct horse battery staple")
    second = hasher.hash_password("correct horse battery staple")

    assert first != second
    assert first.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert second.startswith("$argon2id$v=19$m=65536,t=3,p=4$")
    assert hasher.verify_password("correct horse battery staple", first)
    assert not hasher.verify_password("wrong password", first)
    assert not hasher.verify_password("correct horse battery staple", "not-an-argon-hash")
    assert not hasher.needs_rehash(first)


def test_argon2id_reports_weaker_hash_for_rehash_and_bounds_input() -> None:
    """A weaker valid hash is accepted for verification but marked for upgrade."""

    hasher = Argon2idPasswordHasher()
    weaker = RawPasswordHasher(
        time_cost=2,
        memory_cost=19_456,
        parallelism=1,
        hash_len=32,
        salt_len=16,
        type=Type.ID,
    ).hash("upgrade me")

    assert hasher.verify_password("upgrade me", weaker)
    assert hasher.needs_rehash(weaker)
    assert hasher.needs_rehash("malformed")
    with pytest.raises(ValueError, match="must not be empty"):
        hasher.hash_password("")
    with pytest.raises(ValueError, match="1024"):
        hasher.hash_password("x" * (MAX_PASSWORD_UTF8_BYTES + 1))


def test_opaque_refresh_token_has_256_bits_and_only_digest_is_repr_safe() -> None:
    """Refresh credentials are canonical 32-byte random values hashed with SHA-256."""

    codec = OpaqueRefreshTokenCodec(lambda size: bytes(range(size)))
    credential = codec.issue()

    assert len(credential.token) == 43
    assert credential.sha256 == hashlib.sha256(credential.token.encode("ascii")).digest()
    assert codec.digest(credential.token) == credential.sha256
    assert credential.token not in repr(credential)
    assert codec.digest(credential.token + "=") is None
    assert codec.digest("x" * 43) is None
    assert codec.digest(" token") is None


def test_access_jwt_round_trip_has_fixed_short_session_bound_claims() -> None:
    """A valid JWT decodes only with the fixed issuer/audience/algorithm contract."""

    codec = _access_codec()
    principal = Principal(USER_ID, DEVICE_ID, SESSION_ID, AccountRole.OWNER)
    token = codec.issue(
        principal,
        token_id=TOKEN_ID,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )

    claims = codec.decode(token, now=NOW)
    assert claims.user_id == USER_ID
    assert claims.device_id == DEVICE_ID
    assert claims.session_id == SESSION_ID
    assert claims.token_id == TOKEN_ID
    assert claims.expires_at - claims.issued_at == timedelta(minutes=10)
    assert "test-only-access-signing-secret" not in repr(codec)
    header = jwt.get_unverified_header(token)
    assert header == {"alg": ACCESS_TOKEN_ALGORITHM, "typ": "at+jwt"}


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_sid",
        "wrong_token_type",
        "wrong_header_type",
        "wrong_algorithm",
        "boolean_iat",
        "overlong_lifetime",
    ),
)
def test_access_jwt_rejects_incomplete_or_confused_claim_sets(mutation: str) -> None:
    """Required claims, claim types, header type, algorithm, and TTL fail closed."""

    payload: dict[str, str | int | bool] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": str(USER_ID),
        "did": str(DEVICE_ID),
        "sid": str(SESSION_ID),
        "jti": str(TOKEN_ID),
        "iat": int(NOW.timestamp()),
        "nbf": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(minutes=10)).timestamp()),
        "token_type": "access",
    }
    algorithm = "HS256"
    header_type = "at+jwt"
    if mutation == "missing_sid":
        del payload["sid"]
    elif mutation == "wrong_token_type":
        payload["token_type"] = "refresh"
    elif mutation == "wrong_header_type":
        header_type = "JWT"
    elif mutation == "wrong_algorithm":
        algorithm = "HS384"
    elif mutation == "boolean_iat":
        payload["iat"] = True
        payload["nbf"] = True
    elif mutation == "overlong_lifetime":
        payload["exp"] = int((NOW + timedelta(minutes=16)).timestamp())
    token = jwt.encode(payload, SECRET, algorithm=algorithm, headers={"typ": header_type})

    with pytest.raises(InvalidAccessTokenError):
        _access_codec().decode(token, now=NOW)


def test_access_jwt_rejects_tampering_wrong_context_and_expiry() -> None:
    """Signature, issuer, audience, and expiration checks are mandatory."""

    principal = Principal(USER_ID, DEVICE_ID, SESSION_ID, AccountRole.USER)
    token = _access_codec().issue(
        principal,
        token_id=TOKEN_ID,
        issued_at=NOW,
        expires_at=NOW + timedelta(minutes=1),
    )
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(InvalidAccessTokenError):
        _access_codec().decode(tampered, now=NOW)
    with pytest.raises(InvalidAccessTokenError):
        Hs256AccessTokenCodec(
            b"another-test-secret-that-is-at-least-32-bytes",
            issuer=ISSUER,
            audience=AUDIENCE,
        ).decode(token, now=NOW)
    with pytest.raises(InvalidAccessTokenError):
        Hs256AccessTokenCodec(SECRET, issuer="other", audience=AUDIENCE).decode(token, now=NOW)
    with pytest.raises(InvalidAccessTokenError):
        Hs256AccessTokenCodec(SECRET, issuer=ISSUER, audience="other").decode(token, now=NOW)
    with pytest.raises(InvalidAccessTokenError):
        _access_codec().decode(token, now=NOW + timedelta(minutes=2))


def test_token_pair_representation_never_contains_bearer_values() -> None:
    """Accidental structured logging of a token result is redacted by construction."""

    pair = TokenPair(
        access_token="access-secret-value",
        refresh_token="refresh-secret-value",
        access_expires_at=NOW + timedelta(minutes=10),
        refresh_expires_at=NOW + timedelta(days=30),
        user_id=USER_ID,
        device_id=DEVICE_ID,
        session_id=SESSION_ID,
    )
    rendered = repr(pair)
    assert "access-secret-value" not in rendered
    assert "refresh-secret-value" not in rendered


def _access_codec() -> Hs256AccessTokenCodec:
    return Hs256AccessTokenCodec(
        SECRET,
        issuer=ISSUER,
        audience=AUDIENCE,
        max_ttl=timedelta(minutes=15),
        clock_skew=timedelta(0),
    )
