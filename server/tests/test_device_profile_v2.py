"""D1 source fixtures only: not executed or cryptographically precomputed.

All key generation/signing below is deferred until a later authorized pytest run.
These pure-domain tests do not qualify routes, storage, account locks or retention.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from typed_assertions import set_field

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, asdict
from typing import Any
from uuid import UUID

import pytest
import rfc8785
from autplay.domain.device_profile_v2 import (
    MAX_DOCUMENT_BYTES,
    MAX_SAFE_INTEGER,
    DevicePlatformV2,
    DeviceProfileOperation,
    DeviceProfileV2Error,
    ParsedDeviceProfileV2,
    VerifiedDeviceProfileV2,
    parse_device_profile_v2,
    verify_device_profile_v2,
)
from autplay.domain.profile_pairing import ProfilePairingError, verify_p1363
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

Op = DeviceProfileOperation
ID = "abcdef12-3456-4789-abcd-0123456789ab"
DOMAINS = {
    Op.ENROLLMENT_EXCHANGE: b"autplay:device-compatibility:enrollment:exchange:v2\n",
    Op.ADMISSION_SUBMIT: b"autplay:device-compatibility:admission:submit:v2\n",
    Op.ADMISSION_POLL: b"autplay:device-compatibility:admission:poll:v2\n",
    Op.ADMISSION_RECOVER: b"autplay:device-compatibility:admission:recover:v2\n",
    Op.ADMISSION_EXCHANGE: b"autplay:device-compatibility:admission:exchange:v2\n",
    Op.TRUSTED_CHALLENGE: b"autplay:device-compatibility:trusted:challenge:v2\n",
    Op.TRUSTED_EXCHANGE: b"autplay:device-compatibility:trusted:exchange:v2\n",
    Op.SESSION_ROTATE: b"autplay:device-compatibility:session:rotate:v2\n",
}
V1_DOMAINS = {
    Op.ENROLLMENT_EXCHANGE: b"AutPlay enrollment exchange v1\n",
    Op.ADMISSION_SUBMIT: b"autplay:s1b:admission-request:v1\n",
    Op.ADMISSION_POLL: b"autplay:s1b:admission-poll:v1\n",
    Op.ADMISSION_RECOVER: b"autplay:s1b:admission-recovery:v1\n",
    Op.ADMISSION_EXCHANGE: b"autplay:s1b:admission-exchange:v1\n",
    Op.TRUSTED_CHALLENGE: b"autplay:s1b:trusted-reenrollment:v1\n",
    Op.TRUSTED_EXCHANGE: b"autplay:s1b:trusted-reenrollment:v1\n",
    Op.SESSION_ROTATE: b"AutPlay session rotation v1\n",
}


@pytest.fixture
def key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _spki(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _own(operation: Op) -> bool:
    return operation in (Op.ENROLLMENT_EXCHANGE, Op.SESSION_ROTATE)


def _proof_name(operation: Op) -> str:
    return "device_signature_b64url" if _own(operation) else "proof_b64url"


def _unsigned(
    operation: Op, key: ec.EllipticCurvePrivateKey, platform: str = "MACOS"
) -> dict[str, Any]:
    """Handwritten model fixtures, independent of the implementation's registry."""
    spki = _spki(key)
    numbers = key.public_key().public_numbers()
    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": _url(numbers.x.to_bytes(32, "big")),
        "y": _url(numbers.y.to_bytes(32, "big")),
    }
    common: dict[str, Any] = {
        "contract_version": "v2",
        "schema_version": 2,
        "platform": platform,
        "expected_server_instance_id": ID,
        "expected_identity_epoch": 1,
        "expected_identity_thumbprint_sha256": "1" * 64,
        "expected_api_origin": "https://api.test.invalid",
        "expected_stream_origin": "https://stream.test.invalid:8443",
    }
    body_key = {
        "device_public_key_jwk": jwk,
        "device_key_thumbprint_sha256": hashlib.sha256(spki).hexdigest(),
    }
    nonce = _url(b"n" * 32)
    models: dict[Op, dict[str, Any]] = {
        Op.ENROLLMENT_EXCHANGE: {
            "exchange_id": ID,
            "binding_commit_id": ID,
            "invitation_id": ID,
            "invitation_secret": _url(b"i" * 32),
            "expected_user_id": ID,
            "device_name": "Cafe\u0301 device",
            "app_version": "2.0",
            "device_public_key_spki_b64": base64.b64encode(spki).decode(),
            "device_key_thumbprint_sha256": hashlib.sha256(spki).hexdigest(),
            "next_refresh_token_sha256": "2" * 64,
            "client_nonce_b64url": _url(b"n" * 16),
            "signature_algorithm": "ES256-P1363",
        },
        Op.ADMISSION_SUBMIT: {
            **body_key,
            "request_id": ID,
            "client_nonce_b64url": nonce,
            "api_major": 1,
            "nickname": "Cafe\u0301 device",
            "app_version": "2.0",
            "requested_at": "2026-10-16T12:34:56.123456789Z",
        },
        Op.ADMISSION_POLL: {
            "request_id": ID,
            "device_key_thumbprint_sha256": body_key["device_key_thumbprint_sha256"],
            "client_nonce_b64url": nonce,
        },
        Op.ADMISSION_RECOVER: {
            "request_id": ID,
            "device_key_thumbprint_sha256": body_key["device_key_thumbprint_sha256"],
            "request_sha256": "3" * 64,
            "recovery_nonce_b64url": nonce,
        },
        Op.ADMISSION_EXCHANGE: {
            **body_key,
            "request_id": ID,
            "exchange_id": ID,
            "binding_commit_id": ID,
            "request_sha256": "3" * 64,
            "poll_bearer_sha256": "4" * 64,
            "approved_account_id": ID,
            "client_nonce_b64url": nonce,
            "next_refresh_token_sha256": "2" * 64,
        },
        Op.TRUSTED_CHALLENGE: {
            **body_key,
            "challenge_request_id": ID,
            "account_id": ID,
            "client_nonce_b64url": nonce,
        },
        Op.TRUSTED_EXCHANGE: {
            **body_key,
            "challenge_id": ID,
            "challenge": _url(b"c" * 16),
            "exchange_id": ID,
            "binding_commit_id": ID,
            "account_id": ID,
            "next_refresh_token_sha256": "2" * 64,
            "client_nonce_b64url": nonce,
        },
        Op.SESSION_ROTATE: {
            "rotation_id": ID,
            "device_id": ID,
            "parent_session_id": ID,
            "current_generation": 0,
            "current_refresh_token": _url(b"r" * 32),
            "next_refresh_token_sha256": "2" * 64,
            "signature_algorithm": "ES256-P1363",
        },
    }
    return {**common, **models[operation]}


def _digest(operation: Op, document: Mapping[str, Any]) -> bytes:
    omit = {"request_sha256", "device_signature_b64url"} if _own(operation) else {"proof_b64url"}
    return hashlib.sha256(
        rfc8785.dumps({name: value for name, value in document.items() if name not in omit})
    ).digest()


def _sign(
    operation: Op,
    document: dict[str, Any],
    key: ec.EllipticCurvePrivateKey,
    *,
    domain: bytes | None = None,
    mode: str = "message",
) -> dict[str, Any]:
    document = dict(document)
    digest = _digest(operation, document)
    if _own(operation):
        document["request_sha256"] = digest.hex()
    prefix = DOMAINS[operation] if domain is None else domain
    if mode == "prehashed":
        der = key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    else:
        material = digest.hex().encode("ascii") if mode == "hex" else digest
        der = key.sign(prefix + material, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    document[_proof_name(operation)] = _url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return document


def _raw(document: Mapping[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _invalid(operation: Op, raw: bytes, *, status: int = 400) -> None:
    with pytest.raises(DeviceProfileV2Error) as caught:
        parse_device_profile_v2(operation, raw)
    assert caught.value.code == "device_contract_invalid"
    assert str(caught.value) == "device_contract_invalid"
    assert caught.value.status == status
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("operation", list(Op))
@pytest.mark.parametrize("platform", ["ANDROID", "MACOS"])
def test_all_eight_exact_models_and_message_proofs(
    operation: Op,
    platform: str,
    key: ec.EllipticCurvePrivateKey,
) -> None:
    assert len(Op) == len(DOMAINS) == 8
    document = _sign(operation, _unsigned(operation, key, platform), key)
    raw = _raw(document)
    parsed = parse_device_profile_v2(operation, raw)
    verified = verify_device_profile_v2(parsed, _spki(key))
    evidence = verified.safe_evidence()
    assert parsed.operation is operation
    assert parsed.platform is DevicePlatformV2(platform)
    assert parsed.trust.server_instance_id == UUID(ID)
    assert parsed.trust.identity_epoch == 1
    assert parsed.trust.identity_thumbprint_sha256 == bytes.fromhex("1" * 64)
    assert parsed.trust.api_origin == document["expected_api_origin"]
    assert parsed.trust.stream_origin == document["expected_stream_origin"]
    assert parsed.proof_digest == _digest(operation, document)
    assert parsed.original_document_sha256 == hashlib.sha256(rfc8785.dumps(document)).digest()
    assert parsed.original_document_sha256 != parsed.proof_digest
    assert len(raw) <= MAX_DOCUMENT_BYTES and len(rfc8785.dumps(document)) <= MAX_DOCUMENT_BYTES
    assert evidence.domain == DOMAINS[operation]
    assert evidence.signature_algorithm == "ES256-P1363"
    assert evidence.contract_version == "v2" and evidence.schema_version == 2
    assert evidence.key.spki == _spki(key)
    assert evidence.key.thumbprint_sha256 == hashlib.sha256(_spki(key)).digest()
    encoded = document[_proof_name(operation)]
    assert evidence.signature == base64.urlsafe_b64decode(encoded + "==")
    # Pretty/reordered wire JSON hashes to the same exact canonical original.
    pretty = json.dumps(dict(reversed(list(document.items()))), indent=1).encode()
    assert (
        parse_device_profile_v2(operation, pretty).original_document_sha256
        == evidence.original_document_sha256
    )


@pytest.mark.parametrize("operation", list(Op))
def test_missing_null_extra_and_wrong_version_are_never_defaulted(
    operation: Op,
    key: ec.EllipticCurvePrivateKey,
) -> None:
    document = _sign(operation, _unsigned(operation, key), key)
    for name in document:
        omitted = dict(document)
        del omitted[name]
        _invalid(operation, _raw(omitted))
        _invalid(operation, _raw({**document, name: None}))
    for fields in (
        {"extra": 1},
        {"domain": "caller-chosen"},
        {"contract_version": "v1"},
        {"schema_version": True},
        {"schema_version": 2.0},
        {"schema_version": "2"},
        {"platform": "OTHER"},
        {"platform": "macos"},
    ):
        _invalid(operation, _raw({**document, **fields}))


def test_optional_hint_absent_is_distinct_from_present_and_null(
    key: ec.EllipticCurvePrivateKey,
) -> None:
    op = Op.ADMISSION_SUBMIT
    absent = _sign(op, _unsigned(op, key), key)
    present = _sign(op, {**_unsigned(op, key), "device_model_hint": "Mac"}, key)
    a, b = parse_device_profile_v2(op, _raw(absent)), parse_device_profile_v2(op, _raw(present))
    assert a.proof_digest != b.proof_digest
    with pytest.raises(KeyError):
        a.field("device_model_hint")
    assert b.field("device_model_hint") == "Mac"
    for bad in (None, "", "a" * 97, "bad\x7f"):
        _invalid(op, _raw({**present, "device_model_hint": bad}))


@pytest.mark.parametrize("operation", list(Op))
def test_wrong_key_and_every_other_operation_reject(
    operation: Op,
    key: ec.EllipticCurvePrivateKey,
) -> None:
    document = _sign(operation, _unsigned(operation, key), key)
    parsed = parse_device_profile_v2(operation, _raw(document))
    with pytest.raises(DeviceProfileV2Error) as caught:
        verify_device_profile_v2(parsed, _spki(ec.generate_private_key(ec.SECP256R1())))
    assert caught.value.status == 401
    assert caught.value.__context__ is None
    for other in Op:
        if other is not operation:
            _invalid(other, _raw(document))
    for missing_key in (None, b"", bytearray(_spki(key))):
        with pytest.raises(DeviceProfileV2Error):
            verify_device_profile_v2(parsed, missing_key)  # type: ignore[arg-type]


@pytest.mark.parametrize("operation", list(Op))
@pytest.mark.parametrize("recipe", ["v1", "other-operation", "literal-lf", "hex", "prehashed"])
def test_no_cross_domain_v1_hex_or_prehashed_proofs(
    operation: Op,
    recipe: str,
    key: ec.EllipticCurvePrivateKey,
) -> None:
    domain = DOMAINS[operation]
    if recipe == "v1":
        domain = V1_DOMAINS[operation]
    elif recipe == "other-operation":
        domain = DOMAINS[
            Op.ADMISSION_POLL if operation is Op.ADMISSION_SUBMIT else Op.ADMISSION_SUBMIT
        ]
    elif recipe == "literal-lf":
        domain = domain.replace(b"\n", b"\\n")
    document = _sign(operation, _unsigned(operation, key), key, domain=domain, mode=recipe)
    parsed = parse_device_profile_v2(operation, _raw(document))
    with pytest.raises(DeviceProfileV2Error):
        verify_device_profile_v2(parsed, _spki(key))


@pytest.mark.parametrize("operation", list(Op))
def test_platform_and_all_five_trust_fields_are_proof_bound(
    operation: Op,
    key: ec.EllipticCurvePrivateKey,
) -> None:
    document = _sign(operation, _unsigned(operation, key), key)
    changes = {
        "platform": "ANDROID",
        "expected_server_instance_id": "11111111-1111-4111-8111-111111111111",
        "expected_identity_epoch": 2,
        "expected_identity_thumbprint_sha256": "9" * 64,
        "expected_api_origin": "https://changed.test.invalid",
        "expected_stream_origin": "https://changed.test.invalid:8443",
    }
    for name, value in changes.items():
        changed = {**document, name: value}
        if _own(operation):
            # Recomputing the own hash cannot repair the old signature.
            changed["request_sha256"] = _digest(operation, changed).hex()
        parsed = parse_device_profile_v2(operation, _raw(changed))
        with pytest.raises(DeviceProfileV2Error):
            verify_device_profile_v2(parsed, _spki(key))


@pytest.mark.parametrize("operation", [Op.ENROLLMENT_EXCHANGE, Op.SESSION_ROTATE])
def test_explicit_own_hash_is_checked(operation: Op, key: ec.EllipticCurvePrivateKey) -> None:
    document = _sign(operation, _unsigned(operation, key), key)
    _invalid(operation, _raw({**document, "request_sha256": "0" * 64}))


@pytest.mark.parametrize("operation", [Op.ADMISSION_RECOVER, Op.ADMISSION_EXCHANGE])
def test_admission_reference_hash_stays_in_digest(
    operation: Op, key: ec.EllipticCurvePrivateKey
) -> None:
    document = _sign(operation, _unsigned(operation, key), key)
    assert document["request_sha256"] != _digest(operation, document).hex()
    parsed = parse_device_profile_v2(operation, _raw(document))
    verify_device_profile_v2(parsed, _spki(key))
    changed = parse_device_profile_v2(operation, _raw({**document, "request_sha256": "8" * 64}))
    assert parsed.proof_digest != changed.proof_digest
    with pytest.raises(DeviceProfileV2Error):
        verify_device_profile_v2(changed, _spki(key))


def test_no_unicode_normalization_and_nanoseconds_preserved(
    key: ec.EllipticCurvePrivateKey,
) -> None:
    op = Op.ADMISSION_SUBMIT
    document = _sign(op, _unsigned(op, key), key)
    parsed = parse_device_profile_v2(op, _raw(document))
    assert parsed.field("nickname") == "Cafe\u0301 device"
    assert parsed.field("requested_at") == "2026-10-16T12:34:56.123456789Z"
    changed = parse_device_profile_v2(op, _raw({**document, "nickname": "Caf\u00e9 device"}))
    assert changed.proof_digest != parsed.proof_digest
    with pytest.raises(DeviceProfileV2Error):
        verify_device_profile_v2(changed, _spki(key))


@pytest.mark.parametrize(
    "bad",
    [
        b"",
        b"[]",
        b"null",
        b"true",
        b"\xef\xbb\xbf{}",
        b'{"nickname":"\xff"}',
        b'{"x":"\\ud800"}',
        b'{"\\udfff":1}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b'{"x":1e999}',
        b'{"x":9007199254740992}',
        b'{"x":-1}',
        b'{"x":1, "x":2}',
        b'{"x":{"a":1,"\\u0061":2}}',
        b'{"x":[{"a":1,"a":1}]}',
        b"[" * 2000 + b"0" + b"]" * 2000,
    ],
)
def test_malformed_input_has_only_fixed_sanitized_error(bad: bytes) -> None:
    _invalid(Op.ADMISSION_SUBMIT, bad)


def test_decoder_recursion_exception_does_not_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_decoder(*args: object, **kwargs: object) -> object:
        raise RecursionError("private request material must not escape")

    monkeypatch.setattr(json, "loads", fail_decoder)
    _invalid(Op.ADMISSION_SUBMIT, b"{}")


@pytest.mark.parametrize(
    "credential",
    [
        "invitation_secret",
        "current_refresh_token",
        "challenge",
    ],
)
def test_secret_bearing_decode_errors_have_no_document_or_exception_context(
    credential: str,
) -> None:
    secret = "test-credential-never-in-exception"
    raw = ('{"' + credential + '":"' + secret + '",').encode("ascii")
    with pytest.raises(DeviceProfileV2Error) as caught:
        parse_device_profile_v2(Op.ENROLLMENT_EXCHANGE, raw)
    assert caught.value.args == ("device_contract_invalid",)
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert secret not in repr(caught.value) + repr(vars(caught.value))


def test_duplicates_at_root_and_jwk_depth_reject(key: ec.EllipticCurvePrivateKey) -> None:
    op = Op.ADMISSION_SUBMIT
    raw = _raw(_sign(op, _unsigned(op, key), key))
    _invalid(op, raw[:-1] + b',"platform":"ANDROID"}')
    _invalid(op, raw.replace(b'"kty":"EC"', b'"kty":"EC","kty":"EC"'))
    _invalid(op, raw.replace(b'"kty":"EC"', b'"kty":"EC","\\u006bty":"EC"'))


@pytest.mark.parametrize("operation", list(Op))
def test_exact_raw_limit_before_decode(operation: Op, key: ec.EllipticCurvePrivateKey) -> None:
    raw = _raw(_sign(operation, _unsigned(operation, key), key))
    exact = raw + b" " * (MAX_DOCUMENT_BYTES - len(raw))
    verify_device_profile_v2(parse_device_profile_v2(operation, exact), _spki(key))
    _invalid(operation, exact + b" ", status=413)
    _invalid(operation, b"\xff" * (MAX_DOCUMENT_BYTES + 1), status=413)


@pytest.mark.parametrize("bad", [True, False, -1, MAX_SAFE_INTEGER + 1, 1.0, "1", None])
def test_strict_safe_integer_fields(bad: object, key: ec.EllipticCurvePrivateKey) -> None:
    op = Op.SESSION_ROTATE
    document = _sign(op, _unsigned(op, key), key)
    for field in ("expected_identity_epoch", "current_generation"):
        _invalid(op, _raw({**document, field: bad}))


def test_integer_zero_and_maximum_and_lexical_tokens(key: ec.EllipticCurvePrivateKey) -> None:
    op = Op.SESSION_ROTATE
    for generation in (0, MAX_SAFE_INTEGER):
        document = _sign(
            op,
            {
                **_unsigned(op, key),
                "current_generation": generation,
                "expected_identity_epoch": MAX_SAFE_INTEGER,
            },
            key,
        )
        verify_device_profile_v2(parse_device_profile_v2(op, _raw(document)), _spki(key))
    document = _sign(op, _unsigned(op, key), key)
    _invalid(op, _raw({**document, "expected_identity_epoch": 0}))
    raw = _raw(document)
    for token in (b"2.0", b"2e0", b"true", b'"2"'):
        _invalid(op, raw.replace(b'"schema_version":2', b'"schema_version":' + token))


@pytest.mark.parametrize(
    "time",
    [
        "2026-02-29T12:00:00Z",
        "2026-13-01T12:00:00Z",
        "0000-01-01T12:00:00Z",
        "2026-10-16T24:00:00Z",
        "2026-10-16T12:00:60Z",
        "2026-10-16T12:00:00+00:00",
        "2026-10-16T12:00:00.1234567890Z",
        "2026-10-16t12:00:00Z",
        "2026-10-16T12:00:00Z\n",
    ],
)
def test_timestamp_calendar_and_exact_z(time: str, key: ec.EllipticCurvePrivateKey) -> None:
    op = Op.ADMISSION_SUBMIT
    document = _sign(op, _unsigned(op, key), key)
    _invalid(op, _raw({**document, "requested_at": time}))


@pytest.mark.parametrize("field", ["nickname", "app_version", "device_model_hint"])
def test_text_bounds_controls_and_scalar_count(field: str, key: ec.EllipticCurvePrivateKey) -> None:
    op = Op.ADMISSION_SUBMIT
    maximum = {"nickname": 120, "app_version": 32, "device_model_hint": 96}[field]
    for bad in ("", "x" * (maximum + 1), "bad\x00", "bad\x1f", "bad\x7f", "\ud800"):
        document = {**_unsigned(op, key), field: bad, "proof_b64url": _url(b"p" * 64)}
        _invalid(op, json.dumps(document).encode())
    for text in ("\U0001f3b5" * maximum, "  name  "):
        document = _sign(op, {**_unsigned(op, key), field: text}, key)
        parsed = parse_device_profile_v2(op, _raw(document))
        assert parsed.field(field) == text
        verify_device_profile_v2(parsed, _spki(key))


@pytest.mark.parametrize(
    "origin",
    [
        "https://api.test.invalid/",
        "https://u:p@api.test.invalid",
        "https://api.test.invalid?",
        "https://api.test.invalid#",
        "https://api.test.invalid/path",
        "https://api.test.invalid\n",
        "https://api.test.invalid:0",
        "https://api.test.invalid:65536",
        "https://api.test.invalid:",
        "https://api.test.invalid:0443",
        "https://API.test.invalid",
        "https://\u00e9.test.invalid",
        "https://api.test.invalid\\other",
        "https://api..test",
        "https://[fe80::1%25zone]",
        "https://127.001.0.1",
        "https://api.test.invalid..",
        "ftp://api.test.invalid",
    ],
)
def test_origin_shape_and_canonical_ascii(origin: str, key: ec.EllipticCurvePrivateKey) -> None:
    op = Op.ADMISSION_POLL
    document = _sign(op, _unsigned(op, key), key)
    for field in ("expected_api_origin", "expected_stream_origin"):
        _invalid(op, _raw({**document, field: origin}))


@pytest.mark.parametrize(
    "origin",
    [
        "https://api.test.invalid",
        "https://[::1]:8443",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ],
)
def test_valid_origin_does_not_grant_transport_policy(
    origin: str, key: ec.EllipticCurvePrivateKey
) -> None:
    op = Op.ADMISSION_POLL
    document = _sign(op, {**_unsigned(op, key), "expected_api_origin": origin}, key)
    parsed = parse_device_profile_v2(op, _raw(document))
    assert verify_device_profile_v2(parsed, _spki(key)).parsed.trust.api_origin == origin


def test_uuid_and_hash_text_are_canonical(key: ec.EllipticCurvePrivateKey) -> None:
    op = Op.ADMISSION_POLL
    document = _sign(op, _unsigned(op, key), key)
    for bad in (ID.upper(), ID.replace("-", ""), "{" + ID + "}", ID + "\n"):
        _invalid(op, _raw({**document, "request_id": bad}))
    for bad in ("A" * 64, "0" * 63, "0" * 65, "g" * 64):
        _invalid(op, _raw({**document, "device_key_thumbprint_sha256": bad}))


@pytest.mark.parametrize(
    "operation,field,size",
    [
        (Op.ENROLLMENT_EXCHANGE, "client_nonce_b64url", 16),
        (Op.ENROLLMENT_EXCHANGE, "invitation_secret", 32),
        (Op.ADMISSION_SUBMIT, "client_nonce_b64url", 32),
        (Op.ADMISSION_POLL, "client_nonce_b64url", 32),
        (Op.ADMISSION_RECOVER, "recovery_nonce_b64url", 32),
        (Op.ADMISSION_EXCHANGE, "client_nonce_b64url", 32),
        (Op.TRUSTED_CHALLENGE, "client_nonce_b64url", 32),
        (Op.TRUSTED_EXCHANGE, "client_nonce_b64url", 32),
        (Op.TRUSTED_EXCHANGE, "challenge", 16),
        (Op.SESSION_ROTATE, "current_refresh_token", 32),
    ],
)
def test_nonce_and_credential_decoded_bounds_and_round_trip(
    operation: Op,
    field: str,
    size: int,
    key: ec.EllipticCurvePrivateKey,
) -> None:
    document = _sign(operation, _unsigned(operation, key), key)
    canonical = _url(b"n" * size)
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    noncanonical = canonical[:-1] + alphabet[alphabet.index(canonical[-1]) | 1]
    for bad in (
        _url(b"n" * (size - 1)),
        _url(b"n" * (size + 1)),
        canonical + "=",
        noncanonical,
        "!" * len(canonical),
        "",
        canonical + "\n",
    ):
        changed = _sign(operation, {**document, field: bad}, key)
        _invalid(operation, _raw(changed))


@pytest.mark.parametrize("operation", list(Op))
def test_p1363_encoding_bounds_and_invalid_rs(
    operation: Op, key: ec.EllipticCurvePrivateKey
) -> None:
    document = _sign(operation, _unsigned(operation, key), key)
    field = _proof_name(operation)
    original = document[field]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    noncanonical = original[:-1] + alphabet[alphabet.index(original[-1]) | 1]
    for bad in (_url(b"p" * 63), _url(b"p" * 65), original + "=", noncanonical, "+" * 86):
        _invalid(operation, _raw({**document, field: bad}))
    for invalid_signature in (bytes(64), b"\xff" * 64):
        parsed = parse_device_profile_v2(
            operation, _raw({**document, field: _url(invalid_signature)})
        )
        with pytest.raises(DeviceProfileV2Error):
            verify_device_profile_v2(parsed, _spki(key))


@pytest.mark.parametrize("operation", list(Op))
def test_high_s_remains_valid_and_exact_signature_evidence_is_preserved(
    operation: Op,
    key: ec.EllipticCurvePrivateKey,
) -> None:
    document = _sign(operation, _unsigned(operation, key), key)
    field = _proof_name(operation)
    signature = base64.urlsafe_b64decode(document[field] + "==")
    r, s = int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big")
    order = ec.SECP256R1().group_order
    high_s = max(s, order - s)
    exact_signature = r.to_bytes(32, "big") + high_s.to_bytes(32, "big")
    document[field] = _url(exact_signature)
    parsed = parse_device_profile_v2(operation, _raw(document))
    evidence = verify_device_profile_v2(parsed, _spki(key)).safe_evidence()
    assert evidence.signature == exact_signature
    assert evidence.original_document_sha256 == hashlib.sha256(rfc8785.dumps(document)).digest()


def test_spki_canonical_der_p256_and_recomputed_thumbprint(key: ec.EllipticCurvePrivateKey) -> None:
    op = Op.ENROLLMENT_EXCHANGE
    document = _sign(op, _unsigned(op, key), key)
    p384 = _spki(ec.generate_private_key(ec.SECP384R1()))
    encoded = document["device_public_key_spki_b64"]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    # Canonical P-256 SPKI has 91 DER bytes, so its base64 ends with two pads.
    noncanonical = encoded[:-3] + alphabet[alphabet.index(encoded[-3]) | 1] + "=="
    for bad in (
        base64.b64encode(b"not DER").decode(),
        base64.b64encode(p384).decode(),
        base64.b64encode(_spki(key) + b"\x00").decode(),
        document["device_public_key_spki_b64"] + "=",
        noncanonical,
        document["device_public_key_spki_b64"] + "\n",
    ):
        changed = _sign(op, {**document, "device_public_key_spki_b64": bad}, key)
        _invalid(op, _raw(changed))
    changed = _sign(op, {**document, "device_key_thumbprint_sha256": "0" * 64}, key)
    _invalid(op, _raw(changed))


def test_jwk_exact_coordinates_curve_and_recomputed_thumbprint(
    key: ec.EllipticCurvePrivateKey,
) -> None:
    op = Op.ADMISSION_SUBMIT
    document = _sign(op, _unsigned(op, key), key)
    jwk = document["device_public_key_jwk"]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    x = jwk["x"]
    noncanonical_x = x[:-1] + alphabet[alphabet.index(x[-1]) | 1]
    for bad in (
        {**jwk, "extra": "x"},
        {name: value for name, value in jwk.items() if name != "y"},
        {**jwk, "kty": "RSA"},
        {**jwk, "crv": "P-384"},
        {**jwk, "x": None},
        {**jwk, "x": _url(bytes(31))},
        {**jwk, "x": _url(bytes(33))},
        {**jwk, "x": _url(bytes(32)), "y": _url(bytes(32))},
        {**jwk, "y": jwk["y"] + "="},
        {**jwk, "x": noncanonical_x},
    ):
        _invalid(op, _raw({**document, "device_public_key_jwk": bad}))
    _invalid(op, _raw({**document, "device_key_thumbprint_sha256": "0" * 64}))


@pytest.mark.parametrize(
    "operation,credential",
    [
        (Op.ENROLLMENT_EXCHANGE, "invitation_secret"),
        (Op.TRUSTED_EXCHANGE, "challenge"),
        (Op.SESSION_ROTATE, "current_refresh_token"),
    ],
)
def test_no_recoverable_credentials_in_repr_or_safe_evidence(
    operation: Op,
    credential: str,
    key: ec.EllipticCurvePrivateKey,
) -> None:
    document = _sign(operation, _unsigned(operation, key), key)
    secret = document[credential]
    parsed = parse_device_profile_v2(operation, _raw(document))
    verified = verify_device_profile_v2(parsed, _spki(key))
    evidence = verified.safe_evidence()
    assert parsed.field(credential) == secret  # Explicit transient service processing only.
    assert secret not in repr(parsed) + repr(verified) + repr(evidence)
    exported = asdict(evidence)
    assert secret not in repr(exported)
    assert credential not in dict(evidence.fields)
    assert evidence.credential_hashes == (
        (credential + "_sha256", hashlib.sha256(secret.encode("ascii")).digest()),
    )
    assert evidence.original_document_sha256 == hashlib.sha256(rfc8785.dumps(document)).digest()
    assert evidence.proof_digest == _digest(operation, document)
    # A redacted reconstruction has neither the original digest nor a valid proof.
    redacted = {**document, credential: _url(b"z" * (16 if credential == "challenge" else 32))}
    assert hashlib.sha256(rfc8785.dumps(redacted)).digest() != evidence.original_document_sha256
    if _own(operation):
        redacted["request_sha256"] = _digest(operation, redacted).hex()
    with pytest.raises(DeviceProfileV2Error):
        verify_device_profile_v2(parse_device_profile_v2(operation, _raw(redacted)), _spki(key))
    with pytest.raises(TypeError):
        asdict(cast("DataclassInstance", parsed))
    with pytest.raises(TypeError):
        asdict(cast("DataclassInstance", verified))


@pytest.mark.parametrize("operation", list(Op))
def test_immutable_snapshot_and_public_nonce_evidence(
    operation: Op, key: ec.EllipticCurvePrivateKey
) -> None:
    document = _sign(operation, _unsigned(operation, key), key)
    parsed = parse_device_profile_v2(operation, _raw(document))
    verified = verify_device_profile_v2(parsed, _spki(key))
    evidence = verified.safe_evidence()
    for obj, name, value in (
        (parsed, "platform", DevicePlatformV2.ANDROID),
        (parsed, "proof_digest", b"changed"),
        (verified, "parsed", None),
        (verified, "key", None),
    ):
        with pytest.raises(AttributeError):
            setattr(obj, name, value)
        with pytest.raises(AttributeError):
            delattr(obj, name)
    with pytest.raises(FrozenInstanceError):
        set_field(parsed.trust, "identity_epoch", 99)
    with pytest.raises(FrozenInstanceError):
        set_field(evidence, "platform", DevicePlatformV2.ANDROID)
    if parsed.body_spki is not None and "device_public_key_jwk" in document:
        frozen = parsed.field("device_public_key_jwk")
        assert isinstance(frozen, Mapping)
        with pytest.raises(TypeError):
            frozen["x"] = "changed"  # type: ignore[index]
        document["device_public_key_jwk"]["x"] = "changed"
        assert frozen["x"] != "changed"
    document["platform"] = "ANDROID"
    assert verified.parsed.platform is DevicePlatformV2.MACOS
    nonce = "recovery_nonce_b64url" if operation is Op.ADMISSION_RECOVER else "client_nonce_b64url"
    if operation is not Op.SESSION_ROTATE:
        assert dict(evidence.fields)[nonce] == parsed.field(nonce)
    else:
        assert parsed.body_spki is None and not evidence.key.matched_body_key
    assert evidence.key.matched_body_key is (parsed.body_spki is not None)


def test_closed_enum_bytes_only_and_factory_construction() -> None:
    for bad_operation in ("admission.submit", "self.claim", "portable.register", None):
        with pytest.raises(DeviceProfileV2Error):
            parse_device_profile_v2(bad_operation, b"{}")  # type: ignore[arg-type]
    for raw in ("{}", bytearray(b"{}"), memoryview(b"{}")):
        with pytest.raises(DeviceProfileV2Error):
            parse_device_profile_v2(Op.ADMISSION_SUBMIT, raw)  # type: ignore[arg-type]
    for factory_only in (ParsedDeviceProfileV2, VerifiedDeviceProfileV2):
        with pytest.raises(TypeError):
            factory_only()


@pytest.mark.parametrize("operation", list(Op))
def test_v1_schema_and_signature_are_not_reinterpreted(
    operation: Op, key: ec.EllipticCurvePrivateKey
) -> None:
    document = _sign(operation, _unsigned(operation, key, "ANDROID"), key)
    _invalid(operation, _raw({**document, "contract_version": "v1", "schema_version": 1}))
    with pytest.raises(ProfilePairingError):
        verify_p1363(
            _spki(key),
            V1_DOMAINS[operation].decode("ascii"),
            _digest(operation, document),
            document[_proof_name(operation)],
        )
