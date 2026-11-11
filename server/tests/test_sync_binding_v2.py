"""Future pure B1 fixtures. SOURCE ONLY / UNVERIFIED; no precomputed crypto outputs.

No service/claims/history/DB qualification is implied by these codec tests.
Signing, hashing and key construction below run only after execution is resumed.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import autplay.domain.sync_binding_v2 as binding
import pytest
import rfc8785
from autplay.domain.canonical_json import JsonValue
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from typed_assertions import require_instance, set_field

NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)
USER = UUID("11111111-1111-4111-8111-111111111111")
DEVICE = UUID("22222222-2222-4222-8222-222222222222")
INSTANCE = UUID("33333333-3333-4333-8333-333333333333")
PROFILE = "44444444-4444-4444-8444-444444444444"
EPOCH = "55555555-5555-4555-8555-555555555555"


def _trust(scalar: int = 7) -> tuple[ec.EllipticCurvePrivateKey, binding.TrustedBindingIdentity]:
    key = ec.derive_private_key(scalar, ec.SECP256R1())
    spki = key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return key, binding.TrustedBindingIdentity(
        INSTANCE,
        1,
        spki,
        hashlib.sha256(spki).hexdigest(),
        "https://api.test.invalid",
        "https://stream.test.invalid",
    )


def _request(trust: binding.TrustedBindingIdentity) -> dict[str, object]:
    return {
        "contract_version": "v2",
        "schema_version": 2,
        "binding_contract_version": 2,
        "protocol_version": 1,
        "user_id": str(USER),
        "device_id": str(DEVICE),
        "server_profile_id": PROFILE,
        "journal_epoch": EPOCH,
        "supported_event_schema_versions": [1],
        "expected_capability_revision": 1,
        **{"expected_" + key: value for key, value in trust.fields().items()},
    }


def _payload(
    trust: binding.TrustedBindingIdentity,
    kind: binding.NegotiationKind,
    platform: str = "MACOS",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "contract_version": "v2",
        "schema_version": 2,
        "binding_contract_version": 2,
        "protocol_version": 1,
        **trust.fields(),
        "api_major": 1,
        "identity_public_key_spki_b64": base64.b64encode(trust.public_spki).decode("ascii"),
        "binding_available": False,
        "platforms": [],
        "issued_at": "2026-10-16T12:00:00Z",
        "expires_at": "2026-10-16T12:10:00Z",
    }
    if kind is binding.NegotiationKind.CAPABILITIES:
        payload.update(
            user_id=str(USER), device_id=str(DEVICE), platform=platform, capability_revision=1
        )
        payload["expires_at"] = "2026-10-16T13:00:00Z"
    return payload


def _oracle_envelope(
    key: ec.EllipticCurvePrivateKey,
    payload: dict[str, object],
    kind: binding.NegotiationKind,
    *,
    domain: str | None = None,
    recipe: str = "message",
) -> dict[str, object]:
    # Independent oracle: do NOT call production hash/sign/verify helpers.
    digest = hashlib.sha256(rfc8785.dumps(cast(JsonValue, payload))).digest()
    if domain is None:
        domain = (
            "autplay:sync-binding:negotiation:discovery:v2\n"
            if kind is binding.NegotiationKind.DISCOVERY
            else "autplay:sync-binding:negotiation:capabilities:v2\n"
        )
    if recipe == "prehashed":
        der = key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    else:
        message = domain.encode("ascii") + (
            digest.hex().encode("ascii") if recipe == "hex" else digest
        )
        der = key.sign(message, ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der)
    signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return {
        "contract_version": "v2",
        "schema_version": 2,
        "payload": payload,
        "payload_sha256": digest.hex(),
        "signature_algorithm": "ES256-P1363",
        "signature_b64url": base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii"),
    }


@pytest.mark.parametrize("platform", list(binding.BindingPlatform))
@pytest.mark.parametrize("kind", list(binding.NegotiationKind))
@pytest.mark.parametrize("available", [False, True])
def test_valid_independent_envelope_and_both_enrolled_platforms(
    platform: binding.BindingPlatform,
    kind: binding.NegotiationKind,
    available: bool,
) -> None:
    key, trust = _trust()
    payload = _payload(trust, kind, platform.value)
    payload.update(binding_available=available, platforms=["ANDROID", "MACOS"] if available else [])
    envelope = _oracle_envelope(key, payload, kind)
    parsed = binding.parse_negotiation(kind, rfc8785.dumps(cast(JsonValue, envelope)))
    actor = (
        binding.ExpectedBindingActor(USER, DEVICE, platform)
        if kind is binding.NegotiationKind.CAPABILITIES
        else None
    )
    verified = binding.verify_negotiation(
        parsed, trusted_identity=trust, trusted_now=NOW, expected_actor=actor
    )
    assert verified.payload == payload
    # Independently verify raw32 MESSAGE and both fixed domain literal bytes.
    domain = (
        binding.DISCOVERY_DOMAIN
        if kind is binding.NegotiationKind.DISCOVERY
        else binding.CAPABILITIES_DOMAIN
    )
    assert domain.endswith("\n") and not domain.endswith("\\n")
    raw = base64.urlsafe_b64decode(cast(str, envelope["signature_b64url"]) + "==")
    der = utils.encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    key.public_key().verify(
        der,
        domain.encode("ascii") + hashlib.sha256(rfc8785.dumps(cast(JsonValue, payload))).digest(),
        ec.ECDSA(hashes.SHA256()),
    )
    assert len(parsed.canonical_document) <= (
        16384 if kind is binding.NegotiationKind.DISCOVERY else 65536
    )


@pytest.mark.parametrize("platform", list(binding.BindingPlatform))
def test_bind_parser_is_bearer_independent_and_exact_result_checks_captured_actor(
    platform: binding.BindingPlatform,
) -> None:
    _, trust = _trust()
    body = _request(trust)
    # A different well-formed claimed owner still parses: actual claims are a service obligation.
    other = {**body, "user_id": str(INSTANCE)}
    assert binding.parse_binding_request(rfc8785.dumps(cast(JsonValue, other))).document[
        "user_id"
    ] == str(INSTANCE)
    at_max = {**body, "expected_capability_revision": 9007199254740991}
    assert (
        binding.parse_binding_request(rfc8785.dumps(cast(JsonValue, at_max))).document[
            "expected_capability_revision"
        ]
        == 9007199254740991
    )
    request = binding.parse_binding_request(rfc8785.dumps(cast(JsonValue, body)))
    response = {key: value for key, value in body.items() if not key.startswith("expected_")}
    response.update(trust.fields(), state="BOUND", platform=platform.value)
    result = binding.parse_binding_result(rfc8785.dumps(cast(JsonValue, response)))
    actor = binding.ExpectedBindingActor(USER, DEVICE, platform)
    assert (
        binding.verify_binding_result(
            result, request=request, trusted_identity=trust, expected_actor=actor
        )
        is result
    )
    changed = result.document
    cast(list[int], changed["supported_event_schema_versions"]).append(2)
    changed["user_id"] = "private marker"
    assert result.document == response
    with pytest.raises(FrozenInstanceError):
        set_field(request, "canonical_document", b"{}")
    assert str(USER) not in repr(request) + repr(result)
    for field, value in (
        ("user_id", str(INSTANCE)),
        ("journal_epoch", PROFILE),
        ("server_profile_id", EPOCH),
        ("platform", "MACOS" if platform.value == "ANDROID" else "ANDROID"),
        ("api_origin", "https://other.test.invalid"),
    ):
        wrong = binding.parse_binding_result(
            rfc8785.dumps(cast(JsonValue, {**response, field: value}))
        )
        with pytest.raises(binding.SyncBindingV2Error):
            binding.verify_binding_result(
                wrong, request=request, trusted_identity=trust, expected_actor=actor
            )


@pytest.mark.parametrize(
    "field,value",
    [
        ("platform", "MACOS"),
        ("device_public_key_spki_b64", "forbidden"),
        ("operation_id", PROFILE),
        ("proof", "forbidden"),
        ("metadata", {}),
        ("reset", False),
        ("schema_version", True),
        ("protocol_version", 2),
        ("binding_contract_version", 1),
        ("contract_version", "v1"),
        ("expected_identity_epoch", 0),
        ("expected_capability_revision", 9007199254740992),
        ("journal_epoch", "55555555555545558555555555555555"),
        ("user_id", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
        ("expected_identity_thumbprint_sha256", "A" * 64),
        ("expected_api_origin", "https://api.test.invalid/"),
        ("expected_api_origin", "https://user:pass@api.test.invalid"),
        ("expected_api_origin", "https://api.test.invalid?"),
        ("expected_api_origin", "https://api.test.invalid:65536"),
        ("expected_stream_origin", "https://bad_host.invalid"),
        ("supported_event_schema_versions", [True]),
        ("supported_event_schema_versions", [1, 1]),
        ("supported_event_schema_versions", []),
        ("expected_stream_origin", None),
    ],
)
def test_closed_request_negative_types_fields_versions_and_origins(
    field: str, value: object
) -> None:
    _, trust = _trust()
    with pytest.raises(binding.SyncBindingV2Error) as caught:
        binding.parse_binding_request(json.dumps({**_request(trust), field: value}).encode("utf-8"))
    assert caught.value.code == "sync_binding_invalid"
    assert caught.value.__suppress_context__


def test_raw_token_unicode_duplicates_missing_depth_and_size_refuse_without_coercion() -> None:
    _, trust = _trust()
    raw = rfc8785.dumps(cast(JsonValue, _request(trust)))
    for invalid in (
        raw.replace(b'"schema_version":2', b'"schema_version":2.0'),
        raw.replace(b'"schema_version":2', b'"schema_version":2e0'),
        raw.replace(b'"schema_version":2', b'"schema_version":NaN'),
        raw.replace(b'"schema_version":2', b'"schema_version":Infinity'),
        raw.replace(b'"schema_version":2', b'"schema_version":-0'),
        raw.replace(b'"schema_version":2', b'"schema_version":9007199254740992'),
        raw.replace(
            b'"supported_event_schema_versions":[1]', b'"supported_event_schema_versions":[1.0]'
        ),
        raw.replace(b'"schema_version":2', b'"schema_version":2,"schema_versio\\u006e":2'),
        raw.replace(b'"contract_version":"v2"', b'"contract_version":"\\ud800"'),
        raw.replace(b'"contract_version":"v2"', b'"contract_version":"\xff"'),
        raw.replace(b'"schema_version":2,', b""),
        raw + b"{}",
        b"\xef\xbb\xbf" + raw,
        b'{"hidden":{"a":{"b":{"c":{"d":1}}}}}',
        b'{"extra":{"same":1,"sa\\u006de":1}}',
        b"null",
    ):
        with pytest.raises(binding.SyncBindingV2Error) as caught:
            binding.parse_binding_request(invalid)
        assert caught.value.code == "sync_binding_invalid"
    with pytest.raises(binding.SyncBindingV2Error) as caught:
        binding.parse_binding_request(b" " * 16385)
    assert (caught.value.code, caught.value.status) == ("request_body_too_large", 413)


@pytest.mark.parametrize("kind", list(binding.NegotiationKind))
def test_trust_actor_clock_and_kind_are_explicit(kind: binding.NegotiationKind) -> None:
    key, trust = _trust()
    payload = _payload(trust, kind)
    parsed = binding.parse_negotiation(
        kind, rfc8785.dumps(cast(JsonValue, _oracle_envelope(key, payload, kind)))
    )
    actor = (
        binding.ExpectedBindingActor(USER, DEVICE, binding.BindingPlatform.MACOS)
        if kind is binding.NegotiationKind.CAPABILITIES
        else None
    )
    _, wrong_trust = _trust(11)
    for current_trust, current_time, current_actor in (
        (wrong_trust, NOW, actor),
        (trust, NOW + timedelta(hours=2), actor),
        (trust, NOW - timedelta(seconds=121), actor),
        (trust, NOW.replace(tzinfo=None), actor),
        (
            trust,
            NOW,
            None
            if actor
            else binding.ExpectedBindingActor(USER, DEVICE, binding.BindingPlatform.MACOS),
        ),
    ):
        with pytest.raises(binding.SyncBindingV2Error):
            binding.verify_negotiation(
                parsed,
                trusted_identity=current_trust,
                trusted_now=current_time,
                expected_actor=current_actor,
            )
    if actor:
        for wrong in (
            binding.ExpectedBindingActor(INSTANCE, DEVICE, binding.BindingPlatform.MACOS),
            binding.ExpectedBindingActor(USER, INSTANCE, binding.BindingPlatform.MACOS),
            binding.ExpectedBindingActor(USER, DEVICE, binding.BindingPlatform.ANDROID),
        ):
            with pytest.raises(binding.SyncBindingV2Error):
                binding.verify_negotiation(
                    parsed, trusted_identity=trust, trusted_now=NOW, expected_actor=wrong
                )
    opposite = (
        binding.NegotiationKind.CAPABILITIES
        if kind is binding.NegotiationKind.DISCOVERY
        else binding.NegotiationKind.DISCOVERY
    )
    with pytest.raises(binding.SyncBindingV2Error):
        binding.parse_negotiation(opposite, parsed.canonical_document)
    # Exactly120s future issue is permitted, unlike121s.
    binding.verify_negotiation(
        parsed,
        trusted_identity=trust,
        trusted_now=NOW - timedelta(seconds=120),
        expected_actor=actor,
    )


@pytest.mark.parametrize("kind", list(binding.NegotiationKind))
def test_structural_envelope_negatives_have_fresh_valid_digest_and_signature(
    kind: binding.NegotiationKind,
) -> None:
    key, trust = _trust()
    payload = _payload(trust, kind)
    for field, value in (
        ("schema_version", True),
        ("api_major", True),
        ("binding_available", 1),
        ("binding_available", True),
        ("platforms", ["MACOS", "ANDROID"]),
        ("platforms", None),
        ("unexpected", "forbidden"),
        ("identity_public_key_spki_b64", "AAAA"),
        ("identity_public_key_spki_b64", base64.b64encode(trust.public_spki).decode("ascii") + "="),
        ("identity_thumbprint_sha256", "0" * 64),
        ("identity_epoch", True),
        ("issued_at", "2026-02-30T12:00:00Z"),
        ("issued_at", "2026-10-16T12:00:00+00:00"),
        ("issued_at", "2026-10-16T12:00:00.1234567890Z"),
        ("expires_at", "2026-10-16T12:00:00Z"),
    ):
        invalid = _oracle_envelope(key, {**payload, field: value}, kind)
        # No stale hash can hide the structural rejection: oracle re-signs every mutation.
        assert (
            invalid["payload_sha256"]
            == hashlib.sha256(rfc8785.dumps(cast(JsonValue, invalid["payload"]))).hexdigest()
        )
        with pytest.raises(binding.SyncBindingV2Error):
            binding.parse_negotiation(kind, rfc8785.dumps(cast(JsonValue, invalid)))
    valid = _oracle_envelope(key, payload, kind)
    for field, value in (
        ("unexpected", 1),
        ("signature_algorithm", "ES256"),
        ("signature_b64url", require_instance(valid["signature_b64url"], str) + "="),
    ):
        with pytest.raises(binding.SyncBindingV2Error):
            binding.parse_negotiation(kind, rfc8785.dumps(cast(JsonValue, {**valid, field: value})))
    # The same decoded signature with nonzero padding bits is not canonical base64url.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    signature = require_instance(valid["signature_b64url"], str)
    noncanonical = signature[:-1] + alphabet[alphabet.index(signature[-1]) | 1]
    with pytest.raises(binding.SyncBindingV2Error):
        binding.parse_negotiation(
            kind, rfc8785.dumps(cast(JsonValue, {**valid, "signature_b64url": noncanonical}))
        )
    raw = rfc8785.dumps(cast(JsonValue, valid)).replace(
        b'"api_major":1', b'"api_major":1,"api_majo\\u0072":1'
    )
    with pytest.raises(binding.SyncBindingV2Error):
        binding.parse_negotiation(kind, raw)
    limit = 16384 if kind is binding.NegotiationKind.DISCOVERY else 65536
    with pytest.raises(binding.SyncBindingV2Error):
        binding.parse_negotiation(kind, b" " * (limit + 1))


@pytest.mark.parametrize("kind", list(binding.NegotiationKind))
def test_nanosecond_ttl_and_expiry_boundaries_are_not_truncated(
    kind: binding.NegotiationKind,
) -> None:
    key, trust = _trust()
    payload = _payload(trust, kind)
    expires = (
        "2026-10-16T12:10:00"
        if kind is binding.NegotiationKind.DISCOVERY
        else "2026-10-16T13:00:00"
    )
    too_long = {**payload, "expires_at": expires + ".000000001Z"}
    with pytest.raises(binding.SyncBindingV2Error):
        binding.parse_negotiation(
            kind, rfc8785.dumps(cast(JsonValue, _oracle_envelope(key, too_long, kind)))
        )
    exact = {**too_long, "issued_at": "2026-10-16T12:00:00.000000001Z"}
    parsed = binding.parse_negotiation(
        kind, rfc8785.dumps(cast(JsonValue, _oracle_envelope(key, exact, kind)))
    )
    actor = (
        binding.ExpectedBindingActor(USER, DEVICE, binding.BindingPlatform.MACOS)
        if kind is binding.NegotiationKind.CAPABILITIES
        else None
    )
    binding.verify_negotiation(
        parsed, trusted_identity=trust, trusted_now=NOW, expected_actor=actor
    )
    ordinary = binding.parse_negotiation(
        kind, rfc8785.dumps(cast(JsonValue, _oracle_envelope(key, payload, kind)))
    )
    expiration = NOW + timedelta(seconds=600 if kind is binding.NegotiationKind.DISCOVERY else 3600)
    with pytest.raises(binding.SyncBindingV2Error):
        binding.verify_negotiation(
            ordinary, trusted_identity=trust, trusted_now=expiration, expected_actor=actor
        )


@pytest.mark.parametrize("kind", list(binding.NegotiationKind))
def test_domain_message_digest_and_high_low_s_oracles(kind: binding.NegotiationKind) -> None:
    key, trust = _trust()
    payload = _payload(trust, kind)
    actor = (
        binding.ExpectedBindingActor(USER, DEVICE, binding.BindingPlatform.MACOS)
        if kind is binding.NegotiationKind.CAPABILITIES
        else None
    )
    opposite = (
        binding.CAPABILITIES_DOMAIN
        if kind is binding.NegotiationKind.DISCOVERY
        else binding.DISCOVERY_DOMAIN
    )
    selected = (
        binding.DISCOVERY_DOMAIN
        if kind is binding.NegotiationKind.DISCOVERY
        else binding.CAPABILITIES_DOMAIN
    )
    for domain, recipe in (
        (opposite, "message"),
        (selected.replace("\n", "\\n"), "message"),
        ("autplay:device-compatibility:negotiation:discovery:v2\n", "message"),
        ("AutPlay capabilities v1\n", "message"),
        (selected, "hex"),
        (selected, "prehashed"),
    ):
        parsed = binding.parse_negotiation(
            kind,
            rfc8785.dumps(
                cast(JsonValue, _oracle_envelope(key, payload, kind, domain=domain, recipe=recipe))
            ),
        )
        with pytest.raises(binding.SyncBindingV2Error):
            binding.verify_negotiation(
                parsed, trusted_identity=trust, trusted_now=NOW, expected_actor=actor
            )
    envelope = _oracle_envelope(key, payload, kind)
    raw = base64.urlsafe_b64decode(cast(str, envelope["signature_b64url"]) + "==")
    r, s = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    order = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
    for equivalent_s in (min(s, order - s), max(s, order - s)):
        encoded = (
            base64.urlsafe_b64encode(r.to_bytes(32, "big") + equivalent_s.to_bytes(32, "big"))
            .rstrip(b"=")
            .decode("ascii")
        )
        parsed = binding.parse_negotiation(
            kind, rfc8785.dumps(cast(JsonValue, {**envelope, "signature_b64url": encoded}))
        )
        binding.verify_negotiation(
            parsed, trusted_identity=trust, trusted_now=NOW, expected_actor=actor
        )
    wrong_hash = binding.parse_negotiation(
        kind, rfc8785.dumps(cast(JsonValue, {**envelope, "payload_sha256": "0" * 64}))
    )
    with pytest.raises(binding.SyncBindingV2Error):
        binding.verify_negotiation(
            wrong_hash, trusted_identity=trust, trusted_now=NOW, expected_actor=actor
        )


def test_default_off_and_independent_empty_qualification_are_source_only() -> None:
    assert binding.SYNC_BINDING_V2_ENABLED_DEFAULT is False
    assert frozenset() == binding.IMPLEMENTED_AND_QUALIFIED_PLATFORMS
    # No flags, claims, history, cursor, revision publication or bootstrap service is implemented
    # here.
