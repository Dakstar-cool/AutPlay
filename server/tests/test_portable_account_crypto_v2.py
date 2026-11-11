"""Future D1 source tests. Not executed/qualified by the source-only slice.

Only these test sources generate private keys/signatures when explicitly run in
a future authorized gate. The server adapter never performs private-key work.
"""

import base64
import copy
import hashlib
import json
import pickle
from dataclasses import FrozenInstanceError, asdict, fields
from typing import Any, cast
from uuid import UUID

import pytest
import rfc8785
from autplay.adapters.portable_account_crypto import (
    canonical_request,
    validate_account_document,
    verify_account_proof,
)
from autplay.adapters.portable_account_crypto_v2 import (
    ParsedPortableAccountV2,
    PortableAccountPlatformV2,
    PortableAccountPurposeV2,
    PortableAccountV2Error,
    VerifiedPortableAccountV2,
    parse_portable_account_v2,
    verify_portable_account_v2,
)
from autplay.domain.canonical_json import JsonValue
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _spki(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _case(
    purpose: PortableAccountPurposeV2 = PortableAccountPurposeV2.REGISTER,
    platform: PortableAccountPlatformV2 = PortableAccountPlatformV2.MACOS,
) -> tuple[ec.EllipticCurvePrivateKey, ec.EllipticCurvePrivateKey, dict[str, Any]]:
    root = ec.generate_private_key(ec.SECP256R1())
    device = ec.generate_private_key(ec.SECP256R1())
    account = {
        "format": "autplay-portable-account",
        "version": 1,
        "account_id": "12345678-1234-5678-9abc-123456789abc",
        "public_id": "test_account",
        "public_key_spki_b64": _b64(_spki(root)),
        "kdf": "PBKDF2-HMAC-SHA256",
        "iterations": 600_000,
        "password_salt_b64": _b64(bytes(16)),
        "password_nonce_b64": _b64(bytes(12)),
        "password_ciphertext_b64": _b64(bytes(100)),
        "recovery_nonce_b64": _b64(bytes(12)),
        "recovery_ciphertext_b64": _b64(bytes(100)),
    }
    challenge = {
        "contract_version": "v2",
        "schema_version": 2,
        "platform": platform.value,
        "server_instance_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        "identity_epoch": 1,
        "identity_thumbprint_sha256": "ab" * 32,
        "api_origin": "https://api.example.test",
        "stream_origin": "https://stream.example.test:8443",
        "challenge_id": "11111111-2222-3333-4444-555555555555",
        "purpose": purpose.value,
        "account_id": account["account_id"],
        "document_sha256": hashlib.sha256(rfc8785.dumps(cast(JsonValue, account))).hexdigest(),
        "nonce": base64.urlsafe_b64encode(bytes(32)).rstrip(b"=").decode("ascii"),
        "expires_at": "2020-01-02T03:04:05.123456789Z",
        "actor_device_id": None,
        "actor_session_id": None,
        "action": None,
        "operation_id": None,
        "target_device_id": None,
    }
    return (
        root,
        device,
        {
            "contract_version": "v2",
            "schema_version": 2,
            "platform": platform.value,
            "challenge": challenge,
            "account": account,
            "device_public_key_spki_b64": _b64(_spki(device)),
            "device_name": "Test device",
            "app_version": "1.2.3",
            "next_refresh_token_sha256": "cd" * 32,
            "binding_commit_id": "99999999-8888-7777-6666-555555555555",
        },
    )


def _wire(
    request: dict[str, Any],
    key: ec.EllipticCurvePrivateKey,
    purpose: PortableAccountPurposeV2 = PortableAccountPurposeV2.REGISTER,
    *,
    domain: bytes | None = None,
    signed_payload: bytes | None = None,
) -> bytes:
    # Explicit fixed test domains, independent of the implementation's domain map.
    if domain is None:
        domain = (
            b"autplay:portable-account:register:v2\n"
            if purpose is PortableAccountPurposeV2.REGISTER
            else b"autplay:portable-account:login:v2\n"
        )
    payload = rfc8785.dumps(cast(JsonValue, request)) if signed_payload is None else signed_payload
    proof = key.sign(domain + payload, ec.ECDSA(hashes.SHA256()))
    return json.dumps({"request": request, "proof": _b64(proof)}, separators=(",", ":")).encode()


def _stored(request: dict[str, Any]) -> bytes:
    return json.dumps(request["challenge"], indent=2).encode()


def _reject(raw: bytes, status: int = 400) -> PortableAccountV2Error:
    with pytest.raises(PortableAccountV2Error) as caught:
        parse_portable_account_v2(PortableAccountPurposeV2.REGISTER, raw)
    error = caught.value
    assert error.status == status
    assert error.__context__ is None and error.__cause__ is None
    assert error.args == ("device_contract_invalid",)
    return error


@pytest.mark.parametrize("purpose", list(PortableAccountPurposeV2))
@pytest.mark.parametrize("platform", list(PortableAccountPlatformV2))
def test_both_routes_and_platforms_full_message_root_and_receipt(
    purpose: PortableAccountPurposeV2,
    platform: PortableAccountPlatformV2,
) -> None:
    root, _, request = _case(purpose, platform)
    raw = _wire(request, root, purpose)
    parsed = parse_portable_account_v2(purpose, raw)
    verified = verify_portable_account_v2(parsed, _stored(request))
    evidence = verified.safe_evidence()
    assert evidence.purpose is purpose and evidence.platform is platform
    assert evidence.contract_version == "v2" and evidence.schema_version == 2
    assert evidence.account_id == UUID(request["account"]["account_id"])
    assert (
        evidence.request_sha256 == hashlib.sha256(rfc8785.dumps(cast(JsonValue, request))).digest()
    )
    outer = json.loads(raw)
    assert (
        evidence.original_document_sha256
        == hashlib.sha256(rfc8785.dumps(cast(JsonValue, outer))).digest()
    )
    assert evidence.original_document_sha256 != evidence.request_sha256
    original_der = base64.b64decode(outer["proof"], validate=True)
    assert evidence.root_proof_sha256 == hashlib.sha256(original_der).digest()
    assert evidence.root_proof_sha256 != hashlib.sha256(outer["proof"].encode("ascii")).digest()
    assert parsed.proof == original_der and parsed.proof_b64 == outer["proof"]
    assert evidence.root_spki == _spki(root) and evidence.root_spki != evidence.device_spki
    assert evidence.root_thumbprint_sha256 == hashlib.sha256(_spki(root)).digest()
    assert evidence.device_thumbprint_sha256 == hashlib.sha256(evidence.device_spki).digest()
    assert evidence.binding_commit_id == UUID(request["binding_commit_id"])
    assert evidence.next_refresh_token_sha256 == bytes.fromhex(request["next_refresh_token_sha256"])
    assert evidence.canonical_challenge == rfc8785.dumps(cast(JsonValue, request["challenge"]))
    assert evidence.challenge.nonce == request["challenge"]["nonce"]
    assert evidence.signature_algorithm == "ECDSA-P256-SHA256"
    assert evidence.signature_encoding == "DER/standard-padded-base64"
    expected_domain = (
        b"autplay:portable-account:register:v2\n"
        if purpose is PortableAccountPurposeV2.REGISTER
        else b"autplay:portable-account:login:v2\n"
    )
    assert evidence.domain == expected_domain
    # Old dates intentionally succeed here: service freshness is a separate gate.
    assert evidence.challenge.expires_at == "2020-01-02T03:04:05.123456789Z"
    assert not {"account", "passport", "raw", "request", "access_token"} & {
        field.name for field in fields(evidence)
    }


@pytest.mark.parametrize("purpose", list(PortableAccountPurposeV2))
@pytest.mark.parametrize("platform", list(PortableAccountPlatformV2))
def test_receipt_projection_has_only_allowlisted_fields_and_no_raw_root_proof_alias(
    purpose: PortableAccountPurposeV2,
    platform: PortableAccountPlatformV2,
) -> None:
    root, _, request = _case(purpose, platform)
    raw = _wire(request, root, purpose)
    parsed = parse_portable_account_v2(purpose, raw)
    verified = verify_portable_account_v2(parsed, _stored(request))
    evidence = verified.safe_evidence()
    projection = asdict(evidence)
    assert set(projection) == {
        "purpose",
        "contract_version",
        "schema_version",
        "platform",
        "domain",
        "signature_algorithm",
        "signature_encoding",
        "request_sha256",
        "original_document_sha256",
        "root_proof_sha256",
        "challenge",
        "canonical_challenge",
        "account_id",
        "public_id",
        "root_spki",
        "root_thumbprint_sha256",
        "device_spki",
        "device_thumbprint_sha256",
        "binding_commit_id",
        "next_refresh_token_sha256",
        "device_name",
        "app_version",
    }
    assert set(projection["challenge"]) == {
        "contract_version",
        "schema_version",
        "platform",
        "purpose",
        "server",
        "challenge_id",
        "account_id",
        "document_sha256",
        "nonce",
        "expires_at",
    }
    assert set(projection["challenge"]["server"]) == {
        "server_instance_id",
        "identity_epoch",
        "identity_thumbprint_sha256",
        "api_origin",
        "stream_origin",
    }

    def assert_no_authorization_copy(value: Any) -> None:
        if type(value) is dict:
            assert not {"proof", "proof_b64", "parsed", "body", "raw", "request", "account"} & set(
                value
            )
            for child in value.values():
                assert_no_authorization_copy(child)
        else:
            assert type(value) in {
                bytes,
                str,
                int,
                UUID,
                PortableAccountPurposeV2,
                PortableAccountPlatformV2,
            }
            assert value not in (
                parsed.proof,
                parsed.proof_b64,
                raw,
                rfc8785.dumps(cast(JsonValue, json.loads(raw))),
                rfc8785.dumps(cast(JsonValue, request)),
                parsed.canonical_passport_for_storage(),
            )

    assert_no_authorization_copy(projection)
    assert not hasattr(evidence, "__dict__")
    for alias in ("proof", "proof_b64", "parsed", "body", "raw", "request", "_canonical_request"):
        assert not hasattr(evidence, alias)


@pytest.mark.parametrize("purpose", list(PortableAccountPurposeV2))
@pytest.mark.parametrize("platform", list(PortableAccountPlatformV2))
def test_stored_receipt_or_hash_cannot_replace_incoming_root_authorization(
    purpose: PortableAccountPurposeV2,
    platform: PortableAccountPlatformV2,
) -> None:
    root, _, request = _case(purpose, platform)
    original = _wire(request, root, purpose)
    parsed = parse_portable_account_v2(purpose, original)
    evidence = verify_portable_account_v2(parsed, _stored(request)).safe_evidence()
    for stored_only in (
        evidence,
        evidence.request_sha256,
        evidence.original_document_sha256,
        evidence.root_proof_sha256,
    ):
        with pytest.raises(PortableAccountV2Error) as caught:
            verify_portable_account_v2(stored_only, _stored(request))  # type: ignore[arg-type]
        assert caught.value.status == 401
    for digest in (
        evidence.request_sha256,
        evidence.original_document_sha256,
        evidence.root_proof_sha256,
    ):
        outer = json.loads(original)
        outer["proof"] = _b64(digest)
        with pytest.raises(PortableAccountV2Error):
            candidate = parse_portable_account_v2(purpose, json.dumps(outer).encode())
            verify_portable_account_v2(candidate, _stored(request))
        # Even a real ROOT signature over a stored hash is not full-JCS authorization.
        candidate = parse_portable_account_v2(
            purpose,
            _wire(request, root, purpose, signed_payload=digest),
        )
        with pytest.raises(PortableAccountV2Error):
            verify_portable_account_v2(candidate, _stored(request))
    # Only the supplied complete original can produce matching verified replay evidence.
    replay = verify_portable_account_v2(
        parse_portable_account_v2(purpose, original),
        _stored(request),
    ).safe_evidence()
    assert replay.original_document_sha256 == evidence.original_document_sha256
    assert replay.root_proof_sha256 == evidence.root_proof_sha256


def test_passport_v1_validation_bytes_and_legacy_proof_are_unchanged() -> None:
    root, _, request = _case()
    passport = request["account"]
    account, login, spki = validate_account_document(passport)
    parsed = parse_portable_account_v2(PortableAccountPurposeV2.REGISTER, _wire(request, root))
    assert (parsed.account_id, parsed.public_id, parsed.root_spki) == (account, login, spki)
    assert parsed.canonical_passport_for_storage() == canonical_request(passport)
    assert parsed.canonical_passport_for_storage() == rfc8785.dumps(cast(JsonValue, passport))
    assert passport["version"] == 1 and "platform" not in passport
    legacy = {"challenge_id": request["challenge"]["challenge_id"], "nonce": "legacy-nonce"}
    signature = _b64(
        root.sign(
            b"autplay:portable-account:login:v1\n" + canonical_request(legacy),
            ec.ECDSA(hashes.SHA256()),
        )
    )
    assert (
        verify_account_proof(spki, "login", legacy, signature)
        == hashlib.sha256(
            canonical_request(legacy),
        ).digest()
    )


@pytest.mark.parametrize("signer", ["device", "unrelated-root"])
@pytest.mark.parametrize("purpose", list(PortableAccountPurposeV2))
@pytest.mark.parametrize("platform", list(PortableAccountPlatformV2))
def test_device_or_unrelated_root_never_has_root_authority(
    signer: str,
    purpose: PortableAccountPurposeV2,
    platform: PortableAccountPlatformV2,
) -> None:
    _, device, request = _case(purpose, platform)
    key = device if signer == "device" else ec.generate_private_key(ec.SECP256R1())
    parsed = parse_portable_account_v2(purpose, _wire(request, key, purpose))
    with pytest.raises(PortableAccountV2Error) as caught:
        verify_portable_account_v2(parsed, _stored(request))
    assert caught.value.status == 401
    assert caught.value.__context__ is None and caught.value.__cause__ is None


@pytest.mark.parametrize(
    "domain",
    [
        b"autplay:portable-account:register:v1\n",
        b"autplay:portable-account:login:v2\n",
        b"autplay:portable-account:register:v2\\n",
        b"autplay:device-compatibility:portable:register:v2\n",
    ],
)
def test_v1_cross_purpose_or_changed_domain_replay_fails(domain: bytes) -> None:
    root, _, request = _case()
    parsed = parse_portable_account_v2(
        PortableAccountPurposeV2.REGISTER,
        _wire(request, root, domain=domain),
    )
    with pytest.raises(PortableAccountV2Error, match="account_authentication_failed"):
        verify_portable_account_v2(parsed, _stored(request))


@pytest.mark.parametrize("digest_kind", ["request-raw", "request-hex", "outer", "pretty-json"])
def test_digest_or_noncanonical_json_is_not_the_signed_message(digest_kind: str) -> None:
    root, _, request = _case()
    digest = hashlib.sha256(rfc8785.dumps(cast(JsonValue, request))).digest()
    messages = {
        "request-raw": digest,
        "request-hex": digest.hex().encode(),
        "outer": hashlib.sha256(_wire(request, root)).digest(),
        "pretty-json": json.dumps(request, indent=2).encode(),
    }
    raw = _wire(request, root, signed_payload=messages[digest_kind])
    parsed = parse_portable_account_v2(PortableAccountPurposeV2.REGISTER, raw)
    with pytest.raises(PortableAccountV2Error):
        verify_portable_account_v2(parsed, _stored(request))


@pytest.mark.parametrize(
    "field,value",
    [
        ("device_name", "Different device"),
        ("app_version", "9"),
        ("binding_commit_id", "00000000-0000-0000-0000-000000000000"),
        ("next_refresh_token_sha256", "ef" * 32),
    ],
)
def test_tampering_with_any_binding_field_breaks_root_proof(field: str, value: str) -> None:
    root, _, request = _case()
    outer = json.loads(_wire(request, root))
    outer["request"][field] = value
    parsed = parse_portable_account_v2(
        PortableAccountPurposeV2.REGISTER, json.dumps(outer).encode()
    )
    with pytest.raises(PortableAccountV2Error):
        verify_portable_account_v2(parsed, _stored(request))


@pytest.mark.parametrize(
    "field,value",
    [
        ("platform", "ANDROID"),
        ("purpose", "LOGIN"),
        ("identity_epoch", 2),
        ("api_origin", "https://changed.example.test"),
        ("stream_origin", "https://changed.example.test"),
        ("identity_thumbprint_sha256", "ff" * 32),
        ("server_instance_id", "00000000-0000-0000-0000-000000000000"),
        ("challenge_id", "00000000-0000-0000-0000-000000000000"),
        ("account_id", "00000000-0000-0000-0000-000000000000"),
        ("document_sha256", "ff" * 32),
        ("nonce", base64.urlsafe_b64encode(bytes([1]) * 32).rstrip(b"=").decode()),
        ("expires_at", "2020-01-02T03:04:05.123456788Z"),
    ],
)
def test_independent_stored_challenge_requires_every_original_byte_after_jcs(
    field: str,
    value: Any,
) -> None:
    root, _, request = _case()
    parsed = parse_portable_account_v2(PortableAccountPurposeV2.REGISTER, _wire(request, root))
    stored = copy.deepcopy(request["challenge"])
    stored[field] = value
    with pytest.raises(PortableAccountV2Error) as caught:
        verify_portable_account_v2(parsed, json.dumps(stored).encode())
    assert caught.value.status == 401 and caught.value.__context__ is None


def test_stored_v1_or_malformed_challenge_is_never_evidence() -> None:
    root, _, request = _case()
    parsed = parse_portable_account_v2(PortableAccountPurposeV2.REGISTER, _wire(request, root))
    legacy = {
        name: value
        for name, value in request["challenge"].items()
        if name not in {"contract_version", "schema_version", "platform"}
    }
    for stored in (json.dumps(legacy).encode(), b"null", b"{}", b"{", b" " * 16_385):
        with pytest.raises(PortableAccountV2Error) as caught:
            verify_portable_account_v2(parsed, stored)
        assert caught.value.status == 401 and caught.value.__context__ is None


@pytest.mark.parametrize("level", ["outer", "request", "challenge", "passport"])
def test_duplicate_members_are_rejected_at_every_depth(level: str) -> None:
    root, _, request = _case()
    raw = _wire(request, root)
    markers = {
        "outer": (b'{"request":', b'{"request":null,"request":'),
        "request": (b'"request":{', b'"request":{"platform":"ANDROID",'),
        "challenge": (b'"challenge":{', b'"challenge":{"purpose":"LOGIN",'),
        "passport": (b'"account":{', b'"account":{"version":1,'),
    }
    original, replacement = markers[level]
    assert original in raw
    _reject(raw.replace(original, replacement, 1))


def test_escaped_duplicate_names_and_duplicate_stored_fields_are_rejected() -> None:
    root, _, request = _case()
    raw = _wire(request, root)
    _reject(raw.replace(b'"account":{', b'"account":{"ver\\u0073ion":1,', 1))
    parsed = parse_portable_account_v2(PortableAccountPurposeV2.REGISTER, raw)
    stored = _stored(request).replace(b"{", b'{"purpose":"REGISTER",', 1)
    with pytest.raises(PortableAccountV2Error):
        verify_portable_account_v2(parsed, stored)


@pytest.mark.parametrize(
    "scope,field,value",
    [
        ("request", "schema_version", True),
        ("challenge", "schema_version", True),
        ("challenge", "identity_epoch", True),
        ("passport", "version", True),
        ("passport", "iterations", True),
        ("request", "schema_version", 1),
        ("challenge", "identity_epoch", 0),
        ("challenge", "identity_epoch", 9_007_199_254_740_992),
        ("request", "contract_version", "v1"),
        ("challenge", "contract_version", "v1"),
        ("request", "platform", "OTHER"),
        ("request", "platform", "android"),
        ("challenge", "platform", "ANDROID"),
        ("challenge", "purpose", "CLAIM"),
        ("challenge", "purpose", "LOGIN"),
        ("challenge", "action", "REGISTER"),
        ("challenge", "actor_device_id", "00000000-0000-0000-0000-000000000000"),
        ("request", "binding_commit_id", "99999999888877776666555555555555"),
        ("challenge", "server_instance_id", "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"),
        ("passport", "account_id", "{12345678-1234-5678-9abc-123456789abc}"),
        ("challenge", "document_sha256", "00" * 32),
        ("challenge", "account_id", "00000000-0000-0000-0000-000000000000"),
        ("passport", "version", 2),
        ("passport", "public_id", "TEST_ACCOUNT"),
        ("passport", "iterations", 600_001),
        ("passport", "password_salt_b64", "AA=="),
        ("passport", "password_ciphertext_b64", _b64(bytes(79))),
        ("passport", "recovery_ciphertext_b64", _b64(bytes(513))),
        ("challenge", "nonce", "A" * 42 + "B"),
        ("challenge", "expires_at", "2026-02-30T00:00:00Z"),
        ("challenge", "expires_at", "2026-10-16T00:00:00+00:00"),
        ("challenge", "expires_at", "2026-10-16T00:00:00.1234567890Z"),
    ],
)
def test_strict_shape_identity_types_and_passport_constraints(
    scope: str,
    field: str,
    value: Any,
) -> None:
    root, _, request = _case()
    if scope == "challenge" and field == "identity_epoch" and value == 9_007_199_254_740_992:
        # Reach the parser with an unsafe integer; JCS rejects it during fixture signing.
        raw = _wire(request, root)
        marker = b'"identity_epoch":' + str(request["challenge"]["identity_epoch"]).encode("ascii")
        assert raw.count(marker) == 1
        _reject(raw.replace(marker, b'"identity_epoch":9007199254740992', 1))
        return
    target = request if scope == "request" else request["account" if scope == "passport" else scope]
    target[field] = value
    if scope == "passport":
        # Isolate passport validation rather than failing on an unrelated stale document hash.
        request["challenge"]["document_sha256"] = hashlib.sha256(
            rfc8785.dumps(cast(JsonValue, request["account"]))
        ).hexdigest()
    _reject(_wire(request, root))


@pytest.mark.parametrize("scope", ["outer", "request", "challenge", "passport"])
def test_unknown_and_missing_members_or_nulls_are_not_defaulted(scope: str) -> None:
    root, _, request = _case()
    outer = json.loads(_wire(request, root))
    target = (
        outer
        if scope == "outer"
        else outer["request"]
        if scope == "request"
        else outer["request"]["account" if scope == "passport" else scope]
    )
    for name in tuple(target):
        value = target.pop(name)
        _reject(json.dumps(outer).encode())
        target[name] = value
        if value is not None:
            target[name] = None
            _reject(json.dumps(outer).encode())
            target[name] = value
    target["unexpected"] = "do-not-accept"
    _reject(json.dumps(outer).encode())


@pytest.mark.parametrize(
    "member",
    [
        "replacement_proof",
        "device_signature_b64url",
        "proof_b64url",
        "proof_b64",
        "root_proof_sha256",
    ],
)
def test_no_second_proof_or_device_proof_member_is_accepted(member: str) -> None:
    root, _, request = _case()
    outer = json.loads(_wire(request, root))
    outer[member] = outer["proof"]
    _reject(json.dumps(outer).encode())
    del outer[member]
    outer["request"][member] = outer["proof"]
    _reject(json.dumps(outer).encode())


@pytest.mark.parametrize("token", [b"2.0", b"2e0", b"true", b'"2"', b"NaN", b"Infinity", b"-1"])
def test_integer_tokens_are_not_coerced(token: bytes) -> None:
    root, _, request = _case()
    raw = _wire(request, root)
    _reject(raw.replace(b'"schema_version":2', b'"schema_version":' + token, 1))


@pytest.mark.parametrize(
    "origin",
    [
        "https://api.example.test/",
        "https://user@api.example.test",
        "https://api.example.test?",
        "https://api.example.test#",
        "https://api.example.test/a",
        "https://api.example.test:0",
        "https://api.example.test:65536",
        "https://api.example.test:",
        "https://api.example.test:0443",
        "https://API.example.test",
        "https://api.example.test ",
        "https://api.example.test\t",
        "https://a\\b",
        "https://%61pi.example.test",
        "https://\u0430pi.example.test",
        "ftp://api.example.test",
        "https://api..example.test",
        "https://[not-ipv6]",
    ],
)
def test_origins_reject_paths_credentials_unicode_and_noncanonical_authorities(origin: str) -> None:
    root, _, request = _case()
    request["challenge"]["api_origin"] = origin
    _reject(_wire(request, root))


def test_unicode_and_json_lexical_spelling_preserve_exact_jcs_without_normalization() -> None:
    root, _, request = _case()
    request["device_name"] = " Cafe\u0301 \U0001f3b5 "
    raw = _wire(request, root)
    parsed = parse_portable_account_v2(PortableAccountPurposeV2.REGISTER, raw)
    verified = verify_portable_account_v2(parsed, _stored(request))
    assert verified.safe_evidence().device_name == " Cafe\u0301 \U0001f3b5 "
    # Whitespace, key ordering and escaped vs literal Unicode are JCS-equivalent.
    outer = json.loads(raw)
    spelling = json.dumps(
        dict(reversed(list(outer.items()))), ensure_ascii=False, indent=2
    ).encode()
    again = parse_portable_account_v2(PortableAccountPurposeV2.REGISTER, spelling)
    assert again.original_document_sha256 == parsed.original_document_sha256
    verify_portable_account_v2(again, _stored(request))
    outer["request"]["device_name"] = " Caf\u00e9 \U0001f3b5 "
    normalized = parse_portable_account_v2(
        PortableAccountPurposeV2.REGISTER, json.dumps(outer).encode()
    )
    assert normalized.request_sha256 != parsed.request_sha256
    with pytest.raises(PortableAccountV2Error):
        verify_portable_account_v2(normalized, _stored(request))


@pytest.mark.parametrize(
    "field,value",
    [
        ("device_name", ""),
        ("device_name", "a" * 121),
        ("device_name", "bad\x00name"),
        ("device_name", "bad\x7fname"),
        ("app_version", "a" * 33),
        ("app_version", "bad\nversion"),
    ],
)
def test_bounded_display_strings_reject_controls(field: str, value: str) -> None:
    root, _, request = _case()
    request[field] = value
    _reject(_wire(request, root))


def test_invalid_utf8_surrogates_depth_bom_trailing_json_and_raw_size() -> None:
    root, _, request = _case()
    raw = _wire(request, root)
    for invalid in (
        b"\xff",
        b"\xef\xbb\xbf" + raw,
        b"{",
        raw + b"{}",
        b"[[[[[0]]]]]",
        raw.replace(b'"Test device"', b'"\\ud800"'),
        raw.replace(b'"Test device"', b'"\\udfff"'),
        b"null",
        b"[]",
    ):
        _reject(invalid)
    padded = raw + b" " * (16_384 - len(raw))
    parse_portable_account_v2(PortableAccountPurposeV2.REGISTER, padded)
    _reject(padded + b" ", 413)


def test_p1363_noncanonical_der_and_base64_encodings_are_distinct() -> None:
    root, _, request = _case()
    outer = json.loads(_wire(request, root))
    der = base64.b64decode(outer["proof"])
    r, s = utils.decode_dss_signature(der)
    p1363 = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    # A standard-base64 P1363 signature is still not canonical DER.
    outer["proof"] = _b64(p1363)
    _reject(json.dumps(outer).encode())
    canonical_der = utils.encode_dss_signature(1, 1)
    for proof in (
        _b64(b"\x30\x07\x02\x02\x00\x01\x02\x01\x01"),
        _b64(canonical_der + b"\x00"),
        _b64(canonical_der).rstrip("="),
        _b64(canonical_der) + "=",
        " " + _b64(canonical_der),
        _b64(canonical_der) + "\n",
        "-" * 86,
        _b64(bytes(73)),
        # Nonzero unused pad bits must fail even when decoding yields the same bytes.
        "MAYCAQECAQF=",
    ):
        outer["proof"] = proof
        _reject(json.dumps(outer).encode())


def test_device_curve_and_root_spki_encoding_are_validated_without_key_substitution() -> None:
    root, _, request = _case()
    wrong_curve = ec.generate_private_key(ec.SECP384R1())
    request["device_public_key_spki_b64"] = _b64(_spki(wrong_curve))
    _reject(_wire(request, root))
    request["device_public_key_spki_b64"] = _b64(_spki(root)) + "="
    _reject(_wire(request, root))
    request["device_public_key_spki_b64"] = _b64(_spki(root))
    request["account"]["public_key_spki_b64"] += "="
    _reject(_wire(request, root))


def test_immutable_evidence_has_no_shared_mutable_account_platform_or_serializer() -> None:
    root, _, request = _case()
    raw = _wire(request, root)
    parsed = parse_portable_account_v2(PortableAccountPurposeV2.REGISTER, raw)
    verified = verify_portable_account_v2(parsed, _stored(request))
    evidence = verified.safe_evidence()
    passport_bytes = parsed.canonical_passport_for_storage()
    request["account"]["public_id"] = "changed"
    request["platform"] = "ANDROID"
    request["challenge"]["nonce"] = "changed"
    assert parsed.public_id == "test_account" and parsed.platform is PortableAccountPlatformV2.MACOS
    assert parsed.canonical_passport_for_storage() == passport_bytes
    assert evidence.challenge.nonce != "changed"
    for obj, member, replacement in (
        (parsed, "platform", PortableAccountPlatformV2.ANDROID),
        (parsed, "account_id", UUID(int=0)),
        (verified, "parsed", None),
        (evidence, "account_id", UUID(int=0)),
        (evidence.challenge, "platform", None),
        (evidence.challenge.server, "identity_epoch", 2),
    ):
        with pytest.raises((AttributeError, FrozenInstanceError)):
            setattr(obj, member, replacement)
        with pytest.raises((AttributeError, FrozenInstanceError)):
            delattr(obj, member)
    for obj in (parsed, verified):
        assert not hasattr(obj, "__dict__")
        assert not hasattr(obj, "to_dict") and not hasattr(obj, "body")
        with pytest.raises(TypeError):
            json.dumps(obj)
        with pytest.raises(TypeError, match="portable snapshot serialization forbidden"):
            pickle.dumps(obj)
    for kind in (ParsedPortableAccountV2, VerifiedPortableAccountV2):
        with pytest.raises(TypeError):
            kind()


def test_errors_reprs_and_logs_never_expose_request_passport_or_proof(caplog: Any) -> None:
    root, _, request = _case()
    request["device_name"] = "SENSITIVE_DEVICE_MARKER"
    raw = _wire(request, root)
    parsed = parse_portable_account_v2(PortableAccountPurposeV2.REGISTER, raw)
    verified = verify_portable_account_v2(parsed, _stored(request))
    evidence = verified.safe_evidence()
    renderings = " ".join(
        repr(obj)
        for obj in (
            parsed,
            verified,
            evidence,
            evidence.challenge,
            evidence.challenge.server,
        )
    )
    malformed = raw[:-1]  # JSONDecodeError would retain the complete sensitive body.
    error = _reject(malformed)
    with pytest.raises(PortableAccountV2Error) as caught:
        verify_portable_account_v2(parsed, malformed)
    for failure in (error, caught.value):
        renderings += str(failure) + repr(failure)
        assert failure.__context__ is None and failure.__cause__ is None
    for sensitive in (
        "SENSITIVE_DEVICE_MARKER",
        parsed.proof_b64,
        request["account"]["password_ciphertext_b64"],
        request["account"]["recovery_ciphertext_b64"],
        request["challenge"]["api_origin"],
    ):
        assert sensitive not in renderings and sensitive not in caplog.text
    assert caplog.records == []


def test_api_accepts_only_trusted_enum_and_raw_bytes() -> None:
    root, _, request = _case()
    raw = _wire(request, root)
    for purpose in ("REGISTER", "register", "LOGIN", None, True):
        with pytest.raises(PortableAccountV2Error):
            parse_portable_account_v2(purpose, raw)  # type: ignore[arg-type]
    for body in (json.loads(raw), raw.decode(), bytearray(raw), None):
        with pytest.raises(PortableAccountV2Error):
            parse_portable_account_v2(PortableAccountPurposeV2.REGISTER, body)  # type: ignore[arg-type]
    parsed = parse_portable_account_v2(PortableAccountPurposeV2.REGISTER, raw)
    for stored in (request["challenge"], _stored(request).decode(), None):
        with pytest.raises(PortableAccountV2Error):
            verify_portable_account_v2(parsed, stored)  # type: ignore[arg-type]
    with pytest.raises(PortableAccountV2Error):
        parse_portable_account_v2(PortableAccountPurposeV2.LOGIN, raw)
