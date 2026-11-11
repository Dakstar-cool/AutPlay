"""Future D1 pure-policy tests. SOURCE ONLY: not run or qualified.

Keys, hashes and signatures below are generated only on a future test invocation.
No computed v2 vectors or expected signature/hash/SAS constants are claimed here.
Manual contract candidates are unsigned recipes, never proof-acceptance evidence.
Service authority, storage, transport and concurrency need separate future gates.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any
from uuid import uuid4

import autplay.domain.device_self_v2 as policy
import pytest
import rfc8785
from autplay.domain.device_self_v2 import (
    MAX_BODY_BYTES,
    ParsedSelfRequest,
    SelfOperation,
    SelfPlatform,
    SelfPlatformRole,
    SelfV2Error,
    VerifiedRecipientProof,
    comparison_code,
    parse_self_request,
    verify_recipient_proof,
)
from autplay.domain.profile_pairing import canonical_sha256
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

_RECIPIENTS = (SelfOperation.CLAIM, SelfOperation.POLL, SelfOperation.EXCHANGE)
_SOURCES = (SelfOperation.START, SelfOperation.DECISION)


def _spki(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _json(document: dict[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _signature(key: ec.EllipticCurvePrivateKey, message: bytes) -> str:
    r, s = utils.decode_dss_signature(key.sign(message, ec.ECDSA(hashes.SHA256())))
    return _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def _wire(
    operation: SelfOperation,
    document: dict[str, Any],
    key: ec.EllipticCurvePrivateKey,
    *,
    domain: bytes | None = None,
) -> dict[str, Any]:
    """Independent future producer, not the policy's signing/hash implementation."""
    result = {
        name: value
        for name, value in document.items()
        if name not in {"request_sha256", "device_signature_b64url"}
    }
    digest = hashlib.sha256(rfc8785.dumps(result)).digest()
    result["request_sha256"] = digest.hex()
    if operation in _RECIPIENTS:
        prefix = (
            domain
            if domain is not None
            else {
                SelfOperation.CLAIM: b"autplay:device-compatibility:self:claim:v2\n",
                SelfOperation.POLL: b"autplay:device-compatibility:self:poll:v2\n",
                SelfOperation.EXCHANGE: b"autplay:device-compatibility:self:exchange:v2\n",
            }[operation]
        )
        result["device_signature_b64url"] = _signature(key, prefix + digest)
    return result


@pytest.fixture
def key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def models(key: ec.EllipticCurvePrivateKey) -> dict[SelfOperation, dict[str, Any]]:
    common: dict[str, Any] = {
        "contract_version": "v2",
        "schema_version": 2,
        "platform": "MACOS",
        "ceremony_id": str(uuid4()),
        "requested_at": "2026-10-16T12:00:00.123456789Z",
        "expected_server_instance_id": str(uuid4()),
        "expected_identity_epoch": 2,
        "expected_identity_thumbprint_sha256": "a" * 64,
        "expected_api_origin": "https://API.test.invalid:443",
        "expected_stream_origin": "https://stream.test.invalid",
    }
    claim = _wire(
        SelfOperation.CLAIM,
        {
            **common,
            "claim_id": str(uuid4()),
            "poll_secret_sha256": "b" * 64,
            "device_public_key_spki_b64": base64.b64encode(_spki(key)).decode("ascii"),
            "device_key_thumbprint_sha256": hashlib.sha256(_spki(key)).hexdigest(),
            "device_name": "Recipient \u00e9",
            "app_version": "test-v2",
        },
        key,
    )
    reference = {
        "claim_id": claim["claim_id"],
        "claim_request_sha256": claim["request_sha256"],
    }
    return {
        SelfOperation.START: _wire(
            SelfOperation.START,
            {
                **common,
                "operation_id": str(uuid4()),
                "rendezvous_secret_sha256": "c" * 64,
            },
            key,
        ),
        SelfOperation.CLAIM: claim,
        SelfOperation.POLL: _wire(SelfOperation.POLL, {**common, **reference}, key),
        SelfOperation.DECISION: _wire(
            SelfOperation.DECISION,
            {
                **common,
                "operation_id": str(uuid4()),
                "action": "CANCEL",
                "expected_revision": 1,
                "claim_id": None,
                "claim_request_sha256": None,
                "comparison_code": None,
            },
            key,
        ),
        SelfOperation.EXCHANGE: _wire(
            SelfOperation.EXCHANGE,
            {
                **common,
                **reference,
                "exchange_id": str(uuid4()),
                "binding_commit_id": str(uuid4()),
                "approval_operation_id": str(uuid4()),
                "confirmed_account_id": str(uuid4()),
                "next_refresh_token_sha256": "d" * 64,
            },
            key,
        ),
    }


def _parsed(
    operation: SelfOperation, models: dict[SelfOperation, dict[str, Any]]
) -> ParsedSelfRequest:
    return parse_self_request(operation, _json(models[operation]))


def _verified(
    request: ParsedSelfRequest, key: ec.EllipticCurvePrivateKey
) -> VerifiedRecipientProof:
    return verify_recipient_proof(
        request, approved_public_key_spki=_spki(key), approved_platform=request.platform
    )


@pytest.mark.parametrize("operation", tuple(SelfOperation))
@pytest.mark.parametrize("platform", tuple(SelfPlatform))
def test_exact_models_both_platforms_and_separate_roles(
    operation: SelfOperation,
    platform: SelfPlatform,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    document = _wire(operation, {**models[operation], "platform": platform.value}, key)
    parsed = parse_self_request(operation, _json(document))
    assert dict(parsed.document) == document
    assert parsed.platform is platform
    assert parsed.canonical_document == rfc8785.dumps(document)
    assert parsed.original_document_sha256 == hashlib.sha256(parsed.canonical_document).digest()
    assert parsed.request_sha256.hex() == document["request_sha256"]
    if operation in _RECIPIENTS:
        verified = _verified(parsed, key)
        assert parsed.platform_role is SelfPlatformRole.RECIPIENT
        assert verified.request is parsed
        assert verified.platform is platform
        assert verified.signature_algorithm == "ES256-P1363"
        assert parsed.signature == base64.urlsafe_b64decode(
            document["device_signature_b64url"] + "=="
        )
    else:
        assert parsed.platform_role is SelfPlatformRole.SOURCE
        assert parsed.signature is None
        with pytest.raises(SelfV2Error):
            _verified(parsed, key)


@pytest.mark.parametrize("operation", _SOURCES)
def test_source_never_calls_device_verifier(
    operation: SelfOperation,
    models: dict[SelfOperation, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("source parsing or rejection must not verify a device proof")

    monkeypatch.setattr(policy, "verify_p1363", forbidden)
    parsed = _parsed(operation, models)
    assert type(parsed) is ParsedSelfRequest
    with pytest.raises(SelfV2Error):
        verify_recipient_proof(
            parsed, approved_public_key_spki=b"unused", approved_platform=SelfPlatform.MACOS
        )
    for member in ("device_signature_b64url", "proof_b64url", "signature_algorithm"):
        with pytest.raises(SelfV2Error):
            parse_self_request(operation, _json({**models[operation], member: "unexpected"}))


@pytest.mark.parametrize("selection", ["self.start", "self.status", "self.sas", "start", None])
def test_no_string_dispatch_or_status_body(selection: Any) -> None:
    with pytest.raises(SelfV2Error):
        parse_self_request(selection, b"{}")
    assert {item.value for item in SelfOperation} == {
        "self.start",
        "self.claim",
        "self.poll",
        "self.decision",
        "self.exchange",
    }


@pytest.mark.parametrize("operation", tuple(SelfOperation))
def test_every_member_required_null_exact_and_unknown_rejected(
    operation: SelfOperation,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    original = models[operation]
    for member, value in original.items():
        missing = dict(original)
        del missing[member]
        with pytest.raises(SelfV2Error):
            parse_self_request(operation, _json(missing))
        if value is not None:
            with pytest.raises(SelfV2Error):
                parse_self_request(operation, _json({**original, member: None}))
    for member in ("family", "domain", "rendezvous_secret", "poll_secret", "Authorization"):
        changed = _wire(operation, {**original, member: "synthetic-marker"}, key)
        with pytest.raises(SelfV2Error):
            parse_self_request(operation, _json(changed))


@pytest.mark.parametrize(
    "action,has_claim,has_code,valid",
    [
        ("APPROVE", True, True, True),
        ("APPROVE", False, True, False),
        ("APPROVE", True, False, False),
        ("REJECT", True, False, True),
        ("REJECT", False, False, False),
        ("REJECT", True, True, False),
        ("CANCEL", False, False, True),
        ("CANCEL", True, False, True),
        ("CANCEL", True, True, False),
        ("AUTOMATIC", True, False, False),
    ],
)
def test_decision_conditions_without_source_authority(
    action: str,
    has_claim: bool,
    has_code: bool,
    valid: bool,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    claim = _verified(_parsed(SelfOperation.CLAIM, models), key)
    code = comparison_code(_parsed(SelfOperation.START, models), claim)
    document = _wire(
        SelfOperation.DECISION,
        {
            **models[SelfOperation.DECISION],
            "action": action,
            "claim_id": claim.request.document["claim_id"] if has_claim else None,
            "claim_request_sha256": claim.request.request_sha256.hex() if has_claim else None,
            "comparison_code": code if has_code else None,
        },
        key,
    )
    if valid:
        parsed = parse_self_request(SelfOperation.DECISION, _json(document))
        assert parsed.platform_role is SelfPlatformRole.SOURCE
        assert parsed.signature is None
    else:
        with pytest.raises(SelfV2Error):
            parse_self_request(SelfOperation.DECISION, _json(document))


@pytest.mark.parametrize("member", ["claim_id", "claim_request_sha256"])
def test_cancel_claim_pair_cannot_be_half_null(
    member: str,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    operation = SelfOperation.DECISION
    value = models[SelfOperation.CLAIM]["claim_id" if member == "claim_id" else "request_sha256"]
    document = _wire(operation, {**models[operation], member: value}, key)
    with pytest.raises(SelfV2Error):
        parse_self_request(operation, _json(document))


@pytest.mark.parametrize(
    "token", ["true", "false", "2.0", "2e0", '"2"', "0", "-1", "9007199254740992"]
)
@pytest.mark.parametrize("member", ["schema_version", "expected_identity_epoch"])
def test_integer_tokens_never_coerced(
    token: str,
    member: str,
    models: dict[SelfOperation, dict[str, Any]],
) -> None:
    raw = _json(models[SelfOperation.START])
    raw = raw.replace(f'"{member}":2'.encode(), f'"{member}":{token}'.encode())
    with pytest.raises(SelfV2Error):
        parse_self_request(SelfOperation.START, raw)


def test_safe_integer_upper_bound_preserved(
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    operation = SelfOperation.DECISION
    document = _wire(
        operation,
        {
            **models[operation],
            "expected_identity_epoch": 9007199254740991,
            "expected_revision": 9007199254740991,
        },
        key,
    )
    assert (
        parse_self_request(operation, _json(document)).document["expected_revision"]
        == 9007199254740991
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"[]",
        b"null",
        b"{} {}",
        b"\xef\xbb\xbf{}",
        b"\xff",
        b'{"x":"\xed\xa0\x80"}',
        b'{"x":"\\ud800"}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b'{"x":1.1}',
        b'{"x":1,"x":2}',
        b'{"x":{"a":1,"a":2}}',
        b'{"x":[{"a":1,"\\u0061":2}]}',
        b'{"x":"literal\x00"}',
    ],
)
def test_malformed_raw_json_is_redacted(raw: bytes) -> None:
    with pytest.raises(SelfV2Error) as caught:
        parse_self_request(SelfOperation.START, raw)
    assert str(caught.value) == "device_contract_invalid"
    assert repr(caught.value) == "SelfV2Error(<redacted>)"
    assert caught.value.__suppress_context__


def test_duplicate_known_member_and_valid_member_surrogate_rejected(
    models: dict[SelfOperation, dict[str, Any]],
) -> None:
    raw = _json(models[SelfOperation.START])
    for tail in (b',"schema_version":2}', b',"\\u0073chema_version":2}'):
        with pytest.raises(SelfV2Error):
            parse_self_request(SelfOperation.START, raw[:-1] + tail)
    raw = _json(models[SelfOperation.CLAIM])
    raw = re.sub(rb'"device_name":"[^"]*"', b'"device_name":"marker"', raw)
    raw = raw.replace(b'"marker"', b'"\\udfff"')
    with pytest.raises(SelfV2Error):
        parse_self_request(SelfOperation.CLAIM, raw)


@pytest.mark.parametrize(
    "member,value",
    [
        ("contract_version", "v1"),
        ("schema_version", 1),
        ("platform", "OTHER"),
        ("platform", "macos"),
        ("ceremony_id", "{10000000-0000-4000-8000-000000000002}"),
        ("ceremony_id", "ABCDEFAB-0000-4000-8000-000000000002"),
        ("requested_at", "2026-02-29T12:00:00Z"),
        ("requested_at", "2024-01-01T24:00:00Z"),
        ("requested_at", "2024-01-01T12:00:60Z"),
        ("requested_at", "0000-01-01T12:00:00Z"),
        ("requested_at", "2026-10-16T12:00:00+00:00"),
        ("requested_at", "2026-10-16T12:00:00.1234567890Z"),
        ("requested_at", "2026-10-16T12:00:00\uff11Z"),
        ("expected_api_origin", "https://host/"),
        ("expected_api_origin", "https://user@host"),
        ("expected_api_origin", "https://host?x"),
        ("expected_api_origin", "https://host#x"),
        ("expected_api_origin", "https://ho st"),
        ("expected_api_origin", "https://host\\path"),
        ("expected_api_origin", "https://\u00e9.invalid"),
        ("expected_api_origin", "https://host:65536"),
        ("expected_api_origin", "https://host:"),
        ("expected_api_origin", "https://-bad.invalid"),
        ("expected_api_origin", "https://[::gg]"),
        ("expected_api_origin", "https://[fe80::1%25eth0]"),
        ("rendezvous_secret_sha256", "A" * 64),
        ("rendezvous_secret_sha256", "a" * 63),
    ],
)
def test_invalid_semantics_even_with_recomputed_own_hash(
    member: str,
    value: Any,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    operation = SelfOperation.START
    document = _wire(operation, {**models[operation], member: value}, key)
    with pytest.raises(SelfV2Error):
        parse_self_request(operation, _json(document))


@pytest.mark.parametrize(
    "origin",
    [
        "https://API.test.invalid:0443",
        "https://[2001:DB8::1]:443",
        "https://localhost.",
        "http://127.0.0.1:8080",
    ],
)
def test_origin_syntax_preserves_text_and_does_not_grant_transport_trust(
    origin: str,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    operation = SelfOperation.START
    document = _wire(operation, {**models[operation], "expected_api_origin": origin}, key)
    assert parse_self_request(operation, _json(document)).document["expected_api_origin"] == origin


@pytest.mark.parametrize("member,limit", [("device_name", 120), ("app_version", 32)])
@pytest.mark.parametrize("bad", ["", "\x00", "\x1f", "\x7f", None, {"nested": "value"}])
def test_claim_name_and_version_invalid(
    member: str,
    limit: int,
    bad: Any,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    operation = SelfOperation.CLAIM
    for value in (bad, "x" * (limit + 1)):
        document = _wire(operation, {**models[operation], member: value}, key)
        with pytest.raises(SelfV2Error):
            parse_self_request(operation, _json(document))


def test_unicode_names_never_normalized_or_trimmed(
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    requests = []
    for name in (" \u00e9 ", " e\u0301 ", "\U0001f3b5"):
        operation = SelfOperation.CLAIM
        document = _wire(operation, {**models[operation], "device_name": name}, key)
        parsed = parse_self_request(operation, _json(document))
        assert parsed.document["device_name"] == name
        requests.append(parsed.request_sha256)
    assert len(set(requests)) == len(requests)


@pytest.mark.parametrize("operation", _RECIPIENTS)
def test_wrong_approved_key_platform_or_missing_evidence_never_falls_back(
    operation: SelfOperation,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    request = _parsed(operation, models)
    another = ec.generate_private_key(ec.SECP256R1())
    cases: tuple[tuple[Any, Any], ...] = (
        (_spki(another), SelfPlatform.MACOS),
        (_spki(key), SelfPlatform.ANDROID),
        (b"", SelfPlatform.MACOS),
        (None, SelfPlatform.MACOS),
        (_spki(key), None),
        (_spki(key), "MACOS"),
    )
    for spki, platform in cases:
        with pytest.raises(SelfV2Error) as caught:
            verify_recipient_proof(
                request, approved_public_key_spki=spki, approved_platform=platform
            )
        assert caught.value.code == "self_pairing_unavailable"


def test_claim_key_mismatch_rejected_before_device_verification(
    models: dict[SelfOperation, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("mismatched supplied claim key must fail before verification")

    monkeypatch.setattr(policy, "verify_p1363", forbidden)
    request = _parsed(SelfOperation.CLAIM, models)
    with pytest.raises(SelfV2Error):
        verify_recipient_proof(
            request,
            approved_public_key_spki=_spki(ec.generate_private_key(ec.SECP256R1())),
            approved_platform=SelfPlatform.MACOS,
        )


@pytest.mark.parametrize("operation", _RECIPIENTS)
@pytest.mark.parametrize(
    "member,value",
    [
        ("platform", "ANDROID"),
        ("requested_at", "2026-10-16T12:00:01Z"),
        ("expected_api_origin", "https://different.test.invalid"),
        ("expected_identity_epoch", 3),
        ("ceremony_id", "10000000-0000-4000-8000-000000000099"),
    ],
)
def test_changed_signed_field_requires_both_new_hash_and_new_proof(
    operation: SelfOperation,
    member: str,
    value: Any,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    changed = {**models[operation], member: value}
    with pytest.raises(SelfV2Error):
        parse_self_request(operation, _json(changed))
    recomputed = _wire(operation, changed, key)
    recomputed["device_signature_b64url"] = models[operation]["device_signature_b64url"]
    parsed = parse_self_request(operation, _json(recomputed))
    with pytest.raises(SelfV2Error):
        _verified(parsed, key)


@pytest.mark.parametrize("operation", (SelfOperation.POLL, SelfOperation.EXCHANGE))
def test_original_claim_reference_is_signed_not_omitted(
    operation: SelfOperation,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    changed = _wire(operation, {**models[operation], "claim_request_sha256": "e" * 64}, key)
    assert changed["request_sha256"] != models[operation]["request_sha256"]
    changed["device_signature_b64url"] = models[operation]["device_signature_b64url"]
    with pytest.raises(SelfV2Error):
        _verified(parse_self_request(operation, _json(changed)), key)


@pytest.mark.parametrize(
    "domain",
    [
        b"autplay:self-device-pairing:claim:v1\n",
        b"autplay:device-compatibility:self:poll:v2\n",
        b"autplay:device-compatibility:admission:claim:v2\n",
        b"autplay:device-compatibility:self:claim:v2\\n",
        b"autplay:device-compatibility:self:claim:v2",
    ],
)
def test_signature_under_another_domain_is_rejected(
    domain: bytes,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    operation = SelfOperation.CLAIM
    request = parse_self_request(
        operation, _json(_wire(operation, models[operation], key, domain=domain))
    )
    with pytest.raises(SelfV2Error):
        _verified(request, key)


@pytest.mark.parametrize("mode", ["hex", "prehashed", "der", "zero-r", "order-r"])
def test_wrong_message_api_or_signature_representation_fails(
    mode: str,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    operation = SelfOperation.CLAIM
    document = dict(models[operation])
    digest = bytes.fromhex(document["request_sha256"])
    prefix = b"autplay:device-compatibility:self:claim:v2\n"
    if mode == "hex":
        signature = _signature(key, prefix + digest.hex().encode("ascii"))
    elif mode == "prehashed":
        r, s = utils.decode_dss_signature(
            key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        )
        signature = _b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    elif mode == "der":
        signature = _b64url(key.sign(prefix + digest, ec.ECDSA(hashes.SHA256())))
    else:
        r = (
            0
            if mode == "zero-r"
            else 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
        )
        signature = _b64url(r.to_bytes(32, "big") + (1).to_bytes(32, "big"))
    document["device_signature_b64url"] = signature
    with pytest.raises(SelfV2Error):
        _verified(parse_self_request(operation, _json(document)), key)


def test_valid_high_s_is_not_given_new_low_s_requirement(
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    operation = SelfOperation.CLAIM
    document = dict(models[operation])
    raw = base64.urlsafe_b64decode(document["device_signature_b64url"] + "==")
    order = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
    s = int.from_bytes(raw[32:], "big")
    document["device_signature_b64url"] = _b64url(raw[:32] + max(s, order - s).to_bytes(32, "big"))
    _verified(parse_self_request(operation, _json(document)), key)


def test_noncanonical_base64_padding_bits_rejected(
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    operation = SelfOperation.CLAIM
    original = models[operation]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    sig = original["device_signature_b64url"]
    bad_tail = alphabet[alphabet.index(sig[-1]) | 1]
    for signature in (sig + "=", sig[:-1] + bad_tail, sig[:-1], "!" + sig[1:]):
        with pytest.raises(SelfV2Error):
            parse_self_request(operation, _json({**original, "device_signature_b64url": signature}))
    spki = original["device_public_key_spki_b64"]
    standard = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    pad_index = len(spki.rstrip("=")) - 1
    noncanonical = (
        spki[:pad_index] + standard[standard.index(spki[pad_index]) | 1] + spki[pad_index + 1 :]
    )
    for encoded in (spki.rstrip("="), spki + "=", spki + "\n", noncanonical):
        document = _wire(operation, {**original, "device_public_key_spki_b64": encoded}, key)
        with pytest.raises(SelfV2Error):
            parse_self_request(operation, _json(document))


def test_wrong_curve_trailing_der_and_thumbprint_fail(
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    operation = SelfOperation.CLAIM
    bad_curve = ec.generate_private_key(ec.SECP384R1())
    for raw in (_spki(bad_curve), _spki(key) + b"\x00", b"not-der" * 6):
        document = _wire(
            operation,
            {
                **models[operation],
                "device_public_key_spki_b64": base64.b64encode(raw).decode("ascii"),
                "device_key_thumbprint_sha256": hashlib.sha256(raw).hexdigest(),
            },
            key,
        )
        with pytest.raises(SelfV2Error):
            parse_self_request(operation, _json(document))
    document = _wire(
        operation, {**models[operation], "device_key_thumbprint_sha256": "f" * 64}, key
    )
    with pytest.raises(SelfV2Error):
        parse_self_request(operation, _json(document))


def test_raw_byte_bound_before_decoding_and_exact_boundary(
    models: dict[SelfOperation, dict[str, Any]],
) -> None:
    operation = SelfOperation.START
    raw = _json(models[operation])
    at_bound = raw + b" " * (MAX_BODY_BYTES - len(raw))
    parse_self_request(operation, at_bound)
    for oversized in (at_bound + b" ", b"\xff" * (MAX_BODY_BYTES + 1)):
        with pytest.raises(SelfV2Error) as caught:
            parse_self_request(operation, oversized)
        assert caught.value.http_status == 413


def test_additional_canonical_bound_precedes_hashing(
    models: dict[SelfOperation, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Exercise the second boundary independently; no real legal self field can be
    # filled with arbitrary bytes just to inflate a canonical payload.
    monkeypatch.setattr(rfc8785, "dumps", lambda value: b" " * (MAX_BODY_BYTES + 1))

    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("canonical oversized payload must fail before hashing")

    monkeypatch.setattr(policy, "canonical_sha256", forbidden)
    with pytest.raises(SelfV2Error) as caught:
        _parsed(SelfOperation.START, models)
    assert caught.value.http_status == 413


def test_snapshot_and_verified_evidence_cannot_be_mutated(
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    parsed = _parsed(SelfOperation.CLAIM, models)
    verified = _verified(parsed, key)
    before = (parsed.canonical_document, parsed.request_sha256, parsed.original_document_sha256)
    for member, value in (
        ("platform", "ANDROID"),
        ("request_sha256", "e" * 64),
        ("device_public_key_spki_b64", "replacement"),
    ):
        with pytest.raises(TypeError):
            parsed.document[member] = value  # type: ignore[index]
    for target, attribute, replacement in (
        (parsed, "request_sha256", b"replacement"),
        (parsed, "_items", ()),
        (verified, "platform", SelfPlatform.ANDROID),
        (verified, "approved_public_key_spki", b"replacement"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(target, attribute, replacement)
    copy = dict(parsed.document)
    copy["platform"] = "ANDROID"
    models[SelfOperation.CLAIM]["device_name"] = "changed external source"
    assert (
        parsed.canonical_document,
        parsed.request_sha256,
        parsed.original_document_sha256,
    ) == before
    assert verified.platform is SelfPlatform.MACOS
    assert verified.approved_public_key_spki == _spki(key)
    assert repr(parsed) == "ParsedSelfRequest(<redacted>)"
    assert repr(parsed.document) == "SelfDocument(<redacted>)"
    assert repr(verified) == "VerifiedRecipientProof(<redacted>)"


def test_full_original_digest_and_exact_signature_are_separate_replay_evidence(
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    operation = SelfOperation.CLAIM
    first = _parsed(operation, models)
    document = dict(models[operation])
    raw = base64.urlsafe_b64decode(document["device_signature_b64url"] + "==")
    order = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
    document["device_signature_b64url"] = _b64url(
        raw[:32] + (order - int.from_bytes(raw[32:], "big")).to_bytes(32, "big")
    )
    second = parse_self_request(operation, _json(document))
    _verified(second, key)
    assert first.request_sha256 == second.request_sha256
    assert first.original_document_sha256 != second.original_document_sha256
    assert first.signature != second.signature
    pretty = json.dumps(document, indent=2).encode("utf-8")
    assert parse_self_request(operation, pretty).canonical_document == second.canonical_document
    redacted = dict(document)
    del redacted["device_signature_b64url"]
    with pytest.raises(SelfV2Error):
        parse_self_request(operation, _json(redacted))


@pytest.mark.parametrize("source", tuple(SelfPlatform))
@pytest.mark.parametrize("recipient", tuple(SelfPlatform))
def test_sas_bounded_deterministic_and_cross_platform(
    source: SelfPlatform,
    recipient: SelfPlatform,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    start = parse_self_request(
        SelfOperation.START,
        _json(
            _wire(
                SelfOperation.START,
                {
                    **models[SelfOperation.START],
                    "platform": source.value,
                },
                key,
            )
        ),
    )
    claim = _verified(
        parse_self_request(
            SelfOperation.CLAIM,
            _json(
                _wire(
                    SelfOperation.CLAIM,
                    {
                        **models[SelfOperation.CLAIM],
                        "platform": recipient.value,
                    },
                    key,
                )
            ),
        ),
        key,
    )
    code = comparison_code(start, claim)
    assert re.fullmatch(r"[0-9]{12}", code)
    assert 0 <= int(code) < 10**12
    assert comparison_code(start, claim) == code


def test_sas_preserves_exact_claim_digest_and_new_domain(
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = _parsed(SelfOperation.START, models)
    claim = _verified(_parsed(SelfOperation.CLAIM, models), key)
    canonical_hash = canonical_sha256
    sha256 = hashlib.sha256
    payloads: list[dict[str, Any]] = []
    messages: list[bytes] = []

    def capture_payload(value: Any, **kwargs: Any) -> bytes:
        payloads.append(dict(value))
        return canonical_hash(value, **kwargs)

    def capture_message(value: bytes = b"") -> Any:
        messages.append(value)
        return sha256(value)

    monkeypatch.setattr(policy, "canonical_sha256", capture_payload)
    monkeypatch.setattr(hashlib, "sha256", capture_message)
    comparison_code(start, claim)
    assert payloads == [
        {
            "server_instance_id": start.document["expected_server_instance_id"],
            "identity_epoch": start.document["expected_identity_epoch"],
            "ceremony_id": start.document["ceremony_id"],
            "claim_request_sha256": claim.request.request_sha256.hex(),
            "key_thumbprint": claim.approved_key_thumbprint_sha256.hex(),
        }
    ]
    # One JCS hash followed by one domain-separated hash of exactly 32 raw bytes.
    assert len(messages) == 2
    assert messages[0] == rfc8785.dumps(payloads[0])
    assert (
        messages[1] == b"autplay:device-compatibility:self:sas:v2\n" + sha256(messages[0]).digest()
    )


def test_changed_platform_and_app_version_flow_through_original_claim_digest(
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    start = _parsed(SelfOperation.START, models)
    first = _verified(_parsed(SelfOperation.CLAIM, models), key)
    for field, value in (("platform", "ANDROID"), ("app_version", "next-version")):
        changed = _wire(SelfOperation.CLAIM, {**models[SelfOperation.CLAIM], field: value}, key)
        second = _verified(parse_self_request(SelfOperation.CLAIM, _json(changed)), key)
        assert first.request.request_sha256 != second.request.request_sha256
        # Different codes are overwhelmingly likely but are not guaranteed by a
        # 12-digit truncation; assert binding input changes rather than no collision.
        assert re.fullmatch(r"[0-9]{12}", comparison_code(start, second))


@pytest.mark.parametrize(
    "member,value",
    [
        ("ceremony_id", "10000000-0000-4000-8000-000000000099"),
        ("expected_api_origin", "https://other.test.invalid"),
        ("expected_identity_epoch", 3),
    ],
)
def test_sas_rejects_mixed_ceremony_or_trust(
    member: str,
    value: Any,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    start = _parsed(SelfOperation.START, models)
    changed = _wire(SelfOperation.CLAIM, {**models[SelfOperation.CLAIM], member: value}, key)
    claim = _verified(parse_self_request(SelfOperation.CLAIM, _json(changed)), key)
    with pytest.raises(SelfV2Error):
        comparison_code(start, claim)


def test_sas_requires_start_and_verified_original_claim(
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    start = _parsed(SelfOperation.START, models)
    claim = _parsed(SelfOperation.CLAIM, models)
    for source, recipient in (
        (start, claim),
        (_parsed(SelfOperation.DECISION, models), _verified(claim, key)),
        (start, _verified(_parsed(SelfOperation.POLL, models), key)),
    ):
        with pytest.raises(SelfV2Error):
            comparison_code(source, recipient)  # type: ignore[arg-type]


def test_unsigned_manual_source_candidates_are_not_signature_vectors(
    key: ec.EllipticCurvePrivateKey,
) -> None:
    path = Path(__file__).resolve().parents[2] / "contracts/device-compatibility/v2/vectors.json"
    fixture = json.loads(path.read_text("utf-8"))
    assert fixture["status"] == "MANUAL_UNSIGNED_UNVERIFIED"
    selected = {
        "self-start-macos": SelfOperation.START,
        "self-cancel-before-claim": SelfOperation.DECISION,
    }
    seen = set()
    for candidate in fixture["canonical_candidates"]:
        if candidate["id"] not in selected:
            continue
        seen.add(candidate["id"])
        assert candidate["computed_digest"] is None
        unsigned = candidate["canonical_unsigned_utf8"].encode("utf-8")
        document = json.loads(unsigned)
        # Check the hand-authored JCS candidate only when execution is authorized.
        assert rfc8785.dumps(document) == unsigned
        operation = selected[candidate["id"]]
        parsed = parse_self_request(operation, _json(_wire(operation, document, key)))
        assert parsed.signature is None
    assert seen == set(selected)


@pytest.mark.parametrize("operation", tuple(SelfOperation))
def test_each_body_checks_its_own_hash_including_source_requests(
    operation: SelfOperation,
    models: dict[SelfOperation, dict[str, Any]],
) -> None:
    original = models[operation]
    digest = original["request_sha256"]
    altered = ("0" if digest[0] != "0" else "1") + digest[1:]
    for changes in (
        {"request_sha256": altered},
        {"requested_at": "2026-10-16T12:00:01Z"},
    ):
        with pytest.raises(SelfV2Error):
            parse_self_request(operation, _json({**original, **changes}))


def test_raw_utf8_and_escaped_unicode_have_the_same_immutable_evidence(
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    operation = SelfOperation.CLAIM
    document = _wire(
        operation,
        {
            **models[operation],
            "device_name": "\U0001f3b5 \u00e9",
            "app_version": "v" * 32,
        },
        key,
    )
    utf8 = json.dumps(document, ensure_ascii=False).encode("utf-8")
    escaped = _json(document)
    first, second = parse_self_request(operation, utf8), parse_self_request(operation, escaped)
    assert first.canonical_document == second.canonical_document
    assert first.original_document_sha256 == second.original_document_sha256
    _verified(first, key)


@pytest.mark.parametrize("operation", tuple(SelfOperation))
def test_old_timestamp_still_needs_service_freshness_and_current_authority(
    operation: SelfOperation,
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
) -> None:
    # Historical timestamps may be required for exact committed replay. Pure
    # parsing/proof checking cannot decide whether this is a live first effect.
    document = _wire(
        operation,
        {
            **models[operation],
            "requested_at": "2000-02-29T00:00:00.000000001Z",
        },
        key,
    )
    parsed = parse_self_request(operation, _json(document))
    assert parsed.document["requested_at"] == document["requested_at"]
    if operation in _RECIPIENTS:
        _verified(parsed, key)


def test_sas_first_eight_bytes_unsigned_padding_and_modular_boundaries(
    models: dict[SelfOperation, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = _parsed(SelfOperation.START, models)
    claim = _verified(_parsed(SelfOperation.CLAIM, models), key)
    controlled = bytes(32)

    class ControlledDigest:
        def digest(self) -> bytes:
            return controlled

    def controlled_sha256(value: bytes = b"") -> ControlledDigest:
        return ControlledDigest()

    monkeypatch.setattr(hashlib, "sha256", controlled_sha256)

    def code_for(first_eight: int, suffix: bytes = bytes(24)) -> str:
        nonlocal controlled
        controlled = first_eight.to_bytes(8, "big", signed=False) + suffix
        result = comparison_code(start, claim)
        assert len(result) == 12 and result.isascii() and result.isdecimal()
        assert 0 <= int(result) < 10**12
        return result

    # Synthetic arithmetic probes, not cryptographic vectors or computed codes.
    # Values below the modulus survive; adding a full modulus preserves the code.
    for value in (0, 1, 10**12 - 1):
        assert int(code_for(value)) == value
        assert code_for(value) == code_for(value + 10**12)
    # The top bit must not trigger signed conversion, and the remaining digest
    # bytes must have no influence. No probabilistic no-collision assertion.
    value = 2**63
    assert code_for(value) == code_for(value + 10**12)
    assert code_for(value) == code_for(value % 10**12)
    assert code_for(2**64 - 1) == code_for(2**64 - 1, b"\xff" * 24)
