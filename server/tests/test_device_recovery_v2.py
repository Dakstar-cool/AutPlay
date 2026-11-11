"""Future pure D1 recovery tests: SOURCE ONLY, never executed for this change.

All key/hash/signature production occurs inside future fixtures/test invocation.
No computed vectors are constants. Service/storage/current-authority gates remain
separate UNVERIFIED work; these tests do not claim tokens, recovery or persistence.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import MutableMapping
from dataclasses import FrozenInstanceError
from typing import Any, cast
from uuid import uuid4

import autplay.domain.device_recovery_v2 as policy
import pytest
import rfc8785
from autplay.domain.device_recovery_v2 import (
    DeviceKeyEvidence,
    DevicePlatform,
    KeyEvidenceSource,
    RecoveryV2Error,
    VerifiedRecoveryProof,
    parse_recovery_request,
    verify_recovery_proof,
)
from autplay.domain.device_recovery_v2 import (
    RecoveryOperation as Op,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from typed_assertions import replaced, require_instance


def _spki(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _json(doc: dict[str, Any]) -> bytes:
    return json.dumps(doc, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _sign(doc: dict[str, Any], key: ec.EllipticCurvePrivateKey, domain: bytes) -> dict[str, Any]:
    """Independent producer; do not reuse policy hash/signature functions."""
    result = dict(doc)
    unsigned = {
        k: v for k, v in result.items() if k not in {"request_sha256", "device_signature_b64url"}
    }
    digest = hashlib.sha256(rfc8785.dumps(unsigned)).digest()
    result["request_sha256"] = digest.hex()
    r, s = utils.decode_dss_signature(key.sign(domain + digest, ec.ECDSA(hashes.SHA256())))
    result["device_signature_b64url"] = (
        base64.urlsafe_b64encode(
            r.to_bytes(32, "big") + s.to_bytes(32, "big"),
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    return result


def _domain(op: Op) -> bytes:
    return (
        b"autplay:device-compatibility:recovery:preview:v2\n"
        if op is Op.PREVIEW
        else b"autplay:device-compatibility:recovery:recover:v2\n"
    )


@pytest.fixture
def key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(params=list(DevicePlatform))
def platform(request: pytest.FixtureRequest) -> DevicePlatform:
    return require_instance(request.param, DevicePlatform)


@pytest.fixture
def documents(
    key: ec.EllipticCurvePrivateKey, platform: DevicePlatform
) -> dict[Op, dict[str, Any]]:
    common = {
        "contract_version": "v2",
        "schema_version": 2,
        "platform": platform.value,
        "operation_id": str(uuid4()),
        "account_id": str(uuid4()),
        "requested_at": "2026-10-16T12:00:00.123456789Z",
        "expected_server_instance_id": str(uuid4()),
        "expected_identity_epoch": 2,
        "expected_identity_thumbprint_sha256": "a" * 64,
        "expected_api_origin": "https://API.test.invalid:443",
        "expected_stream_origin": "https://stream.test.invalid",
        "device_public_key_spki_b64": base64.b64encode(_spki(key)).decode("ascii"),
        "device_key_thumbprint_sha256": hashlib.sha256(_spki(key)).hexdigest(),
        "device_name": "Recovery e\u0301 \U0001f3b5",
        "app_version": "future-v2",
    }
    commit = _sign(
        {
            **common,
            "confirmed_account_id": common["account_id"],
            "expected_code_generation": 1,
            "next_code_verifier_sha256": "b" * 64,
            "next_refresh_token_sha256": "c" * 64,
            "binding_commit_id": str(uuid4()),
        },
        key,
        _domain(Op.RECOVER),
    )
    return {
        Op.PREVIEW: _sign(common, key, _domain(Op.PREVIEW)),
        Op.RECOVER: commit,
        Op.OUTCOME: dict(commit),
    }


def _evidence(
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
    source: KeyEvidenceSource = KeyEvidenceSource.CLIENT_SUPPLIED,
) -> DeviceKeyEvidence:
    return DeviceKeyEvidence(_spki(key), platform, source)


def _verify(
    op: Op, doc: dict[str, Any], key: ec.EllipticCurvePrivateKey, platform: DevicePlatform
) -> VerifiedRecoveryProof:
    parsed = parse_recovery_request(op, _json(doc))
    original = parse_recovery_request(Op.RECOVER, _json(doc)) if op is Op.OUTCOME else None
    source = (
        KeyEvidenceSource.HISTORICAL_APPROVED
        if op is Op.OUTCOME
        else KeyEvidenceSource.CLIENT_SUPPLIED
    )
    return verify_recovery_proof(
        parsed,
        key_evidence=_evidence(key, platform, source),
        expected_trust=parsed.trust,
        retained_original=original,
    )


@pytest.mark.parametrize("op", list(Op))
def test_every_recovery_operation_both_platforms_retains_full_exact_proof(
    op: Op,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
) -> None:
    doc = documents[op]
    proof = _verify(op, doc, key, platform)
    parsed = proof.request
    canonical = rfc8785.dumps(doc)
    assert parsed.canonical_document == canonical
    assert parsed.original_document_sha256 == hashlib.sha256(canonical).digest()
    assert parsed.request_sha256.hex() == doc["request_sha256"]
    assert parsed.signature == base64.urlsafe_b64decode(doc["device_signature_b64url"] + "==")
    assert dict(parsed.document) == doc
    assert parsed.platform is platform
    assert proof.key_evidence.public_key_spki == _spki(key)
    assert proof.trust_evidence == parsed.trust
    assert proof.proof_domain.encode("ascii") == _domain(op)
    assert proof.signature_algorithm == "ES256-P1363"
    assert parsed.document["requested_at"].endswith("123456789Z")
    assert parsed.document["device_name"] == "Recovery e\u0301 \U0001f3b5"


def test_outcome_reuses_original_commit_without_freshness_or_new_signature(
    documents: dict[Op, dict[str, Any]], key: ec.EllipticCurvePrivateKey, platform: DevicePlatform
) -> None:
    doc = {**documents[Op.RECOVER], "requested_at": "2000-02-29T00:00:00Z"}
    doc = _sign(doc, key, _domain(Op.RECOVER))
    original = parse_recovery_request(Op.RECOVER, _json(doc))
    outcome = parse_recovery_request(Op.OUTCOME, _json(doc))
    proof = verify_recovery_proof(
        outcome,
        key_evidence=_evidence(key, platform, KeyEvidenceSource.LOCKED_APPROVED),
        expected_trust=original.trust,
        retained_original=original,
    )
    assert proof.request.canonical_document == original.canonical_document
    with pytest.raises(RecoveryV2Error):
        verify_recovery_proof(
            outcome, key_evidence=proof.key_evidence, expected_trust=original.trust
        )
    with pytest.raises(RecoveryV2Error):
        verify_recovery_proof(
            outcome,
            key_evidence=_evidence(key, platform),
            expected_trust=original.trust,
            retained_original=original,
        )


@pytest.mark.parametrize("op", list(Op))
@pytest.mark.parametrize(
    "domain",
    [
        b"autplay:account-recovery:recover:v1\n",
        b"autplay:device-compatibility:recovery:outcome:v2\n",
        b"autplay:device-compatibility:deletion:preview:v2\n",
        b"autplay:device-compatibility:recovery:recover:v1\n",
        b"autplay:device-compatibility:recovery:recover:v2\\n",
    ],
)
def test_wrong_domains_reach_signature_guard(
    op: Op,
    domain: bytes,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
) -> None:
    doc = _sign(documents[op], key, domain)
    parse_recovery_request(op, _json(doc))  # Hash/model valid before intended proof failure.
    with pytest.raises(RecoveryV2Error) as caught:
        _verify(op, doc, key, platform)
    assert caught.value.http_status == 401


@pytest.mark.parametrize("op", list(Op))
def test_message_recipe_rejects_prehash_and_hex_digest(
    op: Op,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
) -> None:
    for mode in ("prehashed", "hex"):
        doc = dict(documents[op])
        digest = bytes.fromhex(doc["request_sha256"])
        message = digest if mode == "prehashed" else _domain(op) + digest.hex().encode("ascii")
        algorithm = (
            ec.ECDSA(utils.Prehashed(hashes.SHA256()))
            if mode == "prehashed"
            else ec.ECDSA(hashes.SHA256())
        )
        r, s = utils.decode_dss_signature(key.sign(message, algorithm))
        doc["device_signature_b64url"] = (
            base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
            .rstrip(b"=")
            .decode("ascii")
        )
        with pytest.raises(RecoveryV2Error) as caught:
            _verify(op, doc, key, platform)
        assert caught.value.http_status == 401


@pytest.mark.parametrize(
    "field,value",
    [
        ("contract_version", "v1"),
        ("schema_version", 1),
        ("schema_version", True),
        ("schema_version", None),
        ("platform", "OTHER"),
        ("platform", "WEB"),
        ("platform", "macos"),
        ("expected_identity_epoch", True),
        ("expected_identity_epoch", 0),
        ("expected_identity_epoch", 9007199254740992),
        ("expected_identity_epoch", "2"),
        ("account_id", "A" * 36),
        ("expected_identity_thumbprint_sha256", "A" * 64),
        ("requested_at", "2026-02-29T12:00:00Z"),
        ("requested_at", "2026-01-01T24:00:00Z"),
        ("requested_at", "2026-01-01T12:00:60Z"),
        ("requested_at", "0000-01-01T00:00:00Z"),
        ("requested_at", "2026-01-01T00:00:00.1234567890Z"),
        ("requested_at", "2026-01-01T00:00:00+00:00"),
        ("expected_api_origin", "https://user@host.invalid"),
        ("expected_api_origin", "https://host.invalid/"),
        ("expected_api_origin", "https://host.invalid:0"),
        ("expected_stream_origin", "https://h\u00e9.invalid"),
        ("device_name", ""),
        ("device_name", "x" * 121),
        ("device_name", "x\u007f"),
        ("app_version", "x" * 33),
        ("app_version", "x\n"),
        ("device_public_key_spki_b64", "=" * 124),
        ("device_signature_b64url", "A" * 85),
        ("device_signature_b64url", "A" * 86 + "=="),
    ],
)
def test_invalid_members_reach_model_guard_before_hash(
    field: str, value: object, documents: dict[Op, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = {**documents[Op.PREVIEW], field: value}

    def unexpected_hash(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid member reached hash")

    monkeypatch.setattr(policy, "canonical_sha256", unexpected_hash)
    with pytest.raises(RecoveryV2Error) as caught:
        parse_recovery_request(Op.PREVIEW, _json(doc))
    assert caught.value.http_status == 400


@pytest.mark.parametrize("op", list(Op))
def test_no_missing_null_extra_or_secret_members(
    op: Op, documents: dict[Op, dict[str, Any]]
) -> None:
    for field in documents[op]:
        missing = dict(documents[op])
        del missing[field]
        for invalid in (missing, {**documents[op], field: None}):
            with pytest.raises(RecoveryV2Error):
                parse_recovery_request(op, _json(invalid))
    for field in (
        "code",
        "refresh",
        "recovery_code",
        "current_refresh_token",
        "proof_b64url",
        "domain",
        "kind",
    ):
        with pytest.raises(RecoveryV2Error):
            parse_recovery_request(op, _json({**documents[op], field: "do-not-retain"}))


@pytest.mark.parametrize("op", list(Op))
def test_raw_duplicate_tokens_unicode_and_bounds(
    op: Op, documents: dict[Op, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _json(documents[op])
    invalid = [
        b'{"schema_version":2,' + raw[1:],
        b'{"unknown":{"x":1,"x":2},' + raw[1:],
        raw.replace(b'"schema_version":2', b'"schema_version":2.0'),
        raw.replace(b'"schema_version":2', b'"schema_version":2e0'),
        raw.replace(b'"schema_version":2', b'"schema_version":NaN'),
        raw.replace(b'"schema_version":2', b'"schema_version":Infinity'),
        raw.replace(b'"schema_version":2', b'"schema_version":-1'),
        _json({**documents[op], "device_name": "\ud800"}),
        b"\xff",
        b"[]",
        b"null",
        raw + b"{}",
    ]
    for item in invalid:
        assert item != raw
        with pytest.raises(RecoveryV2Error) as caught:
            parse_recovery_request(op, item)
        assert caught.value.http_status == 400
    # Exact raw limit is inclusive; whitespace is transport-only.
    padded = raw + b" " * (policy.MAX_BODY_BYTES - len(raw))
    assert parse_recovery_request(op, padded).canonical_document == rfc8785.dumps(documents[op])
    with pytest.raises(RecoveryV2Error) as caught:
        parse_recovery_request(op, padded + b" ")
    assert caught.value.http_status == 413
    monkeypatch.setattr(rfc8785, "dumps", lambda value: b"x" * (policy.MAX_BODY_BYTES + 1))
    with pytest.raises(RecoveryV2Error) as caught:
        parse_recovery_request(op, raw)
    assert caught.value.http_status == 413


@pytest.mark.parametrize("op", list(Op))
def test_hash_platform_key_and_trust_tampering(
    op: Op,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
) -> None:
    doc = documents[op]
    for changed in (
        {**doc, "request_sha256": "0" * 64},
        {**doc, "platform": "MACOS" if platform is DevicePlatform.ANDROID else "ANDROID"},
    ):
        with pytest.raises(RecoveryV2Error) as caught:
            parse_recovery_request(op, _json(changed))
        assert caught.value.http_status == 400
    parsed = parse_recovery_request(op, _json(doc))
    original = parse_recovery_request(Op.RECOVER, _json(doc)) if op is Op.OUTCOME else None
    source = KeyEvidenceSource.LOCKED_APPROVED
    other = ec.generate_private_key(ec.SECP256R1())
    for evidence in (
        _evidence(other, platform, source),
        _evidence(
            key,
            DevicePlatform.MACOS if platform is DevicePlatform.ANDROID else DevicePlatform.ANDROID,
            source,
        ),
        DeviceKeyEvidence(_spki(key), cast(DevicePlatform, "ANDROID"), source),
        DeviceKeyEvidence(_spki(key), platform, cast(KeyEvidenceSource, "LOCKED_APPROVED")),
    ):
        with pytest.raises(RecoveryV2Error):
            verify_recovery_proof(
                parsed,
                key_evidence=evidence,
                expected_trust=parsed.trust,
                retained_original=original,
            )
    for field, value in (
        ("server_instance_id", str(uuid4())),
        ("identity_epoch", 3),
        ("identity_thumbprint_sha256", "b" * 64),
        ("api_origin", "https://api.test.invalid:443"),
        ("stream_origin", "https://different.invalid"),
        ("identity_epoch", True),
    ):
        with pytest.raises(RecoveryV2Error):
            verify_recovery_proof(
                parsed,
                key_evidence=_evidence(key, platform, source),
                expected_trust=replaced(parsed.trust, **{field: value}),
                retained_original=original,
            )


def test_confirmation_generation_and_key_encoding_guards(
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A stale request digest must never satisfy a structural rejection.
    def unexpected_hash(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid confirmation, generation or key encoding reached digest")

    monkeypatch.setattr(policy, "canonical_sha256", unexpected_hash)
    for field, value in (
        ("confirmed_account_id", str(uuid4())),
        ("expected_code_generation", 0),
        ("expected_code_generation", True),
        ("expected_code_generation", 9007199254740992),
        ("next_code_verifier_sha256", None),
        ("binding_commit_id", None),
    ):
        with pytest.raises(RecoveryV2Error):
            parse_recovery_request(Op.RECOVER, _json({**documents[Op.RECOVER], field: value}))
    base = documents[Op.PREVIEW]
    wrong_curve = ec.generate_private_key(ec.SECP384R1())
    for spki in (_spki(wrong_curve), _spki(key) + b"\x00", b"malformed DER" * 8):
        doc = {
            **base,
            "device_public_key_spki_b64": base64.b64encode(spki).decode("ascii"),
            "device_key_thumbprint_sha256": hashlib.sha256(spki).hexdigest(),
        }
        with pytest.raises(RecoveryV2Error):
            parse_recovery_request(Op.PREVIEW, _json(doc))
    doc = {**base, "device_key_thumbprint_sha256": "0" * 64}
    with pytest.raises(RecoveryV2Error):
        parse_recovery_request(Op.PREVIEW, _json(doc))
    for sig in ("A" * 85 + "B", base["device_signature_b64url"] + "=", "-" * 86):
        with pytest.raises(RecoveryV2Error):
            parse_recovery_request(Op.PREVIEW, _json({**base, "device_signature_b64url": sig}))


def test_invalid_p1363_scalars_and_der_signature(
    documents: dict[Op, dict[str, Any]], key: ec.EllipticCurvePrivateKey, platform: DevicePlatform
) -> None:
    base = documents[Op.PREVIEW]
    for raw in (bytes(64), bytes([255]) * 64):
        sig = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        doc = {**base, "device_signature_b64url": sig}
        parse_recovery_request(Op.PREVIEW, _json(doc))
        with pytest.raises(RecoveryV2Error) as caught:
            _verify(Op.PREVIEW, doc, key, platform)
        assert caught.value.http_status == 401
    der = key.sign(
        _domain(Op.PREVIEW) + bytes.fromhex(base["request_sha256"]), ec.ECDSA(hashes.SHA256())
    )
    with pytest.raises(RecoveryV2Error):
        parse_recovery_request(
            Op.PREVIEW,
            _json(
                {
                    **base,
                    "device_signature_b64url": base64.urlsafe_b64encode(der)
                    .rstrip(b"=")
                    .decode("ascii"),
                }
            ),
        )


def test_original_outcome_changes_and_redaction_immutability(
    documents: dict[Op, dict[str, Any]], key: ec.EllipticCurvePrivateKey, platform: DevicePlatform
) -> None:
    doc = documents[Op.RECOVER]
    original = parse_recovery_request(Op.RECOVER, _json(doc))
    for field, value in (
        ("operation_id", str(uuid4())),
        ("binding_commit_id", str(uuid4())),
        ("requested_at", "2026-10-16T12:00:01Z"),
        ("next_refresh_token_sha256", "d" * 64),
    ):
        changed = _sign({**doc, field: value}, key, _domain(Op.RECOVER))
        outcome = parse_recovery_request(Op.OUTCOME, _json(changed))
        with pytest.raises(RecoveryV2Error):
            verify_recovery_proof(
                outcome,
                key_evidence=_evidence(key, platform, KeyEvidenceSource.HISTORICAL_APPROVED),
                expected_trust=original.trust,
                retained_original=original,
            )
    # The valid (r, n-s) twin has identical D but distinct original proof bytes.
    order = int("ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16)
    raw = original.signature
    twin = raw[:32] + (order - int.from_bytes(raw[32:], "big")).to_bytes(32, "big")
    changed = {
        **doc,
        "device_signature_b64url": base64.urlsafe_b64encode(twin).rstrip(b"=").decode("ascii"),
    }
    _verify(Op.OUTCOME, changed, key, platform)  # Established valid high-S acceptance retained.
    with pytest.raises(RecoveryV2Error):
        verify_recovery_proof(
            parse_recovery_request(Op.OUTCOME, _json(changed)),
            key_evidence=_evidence(key, platform, KeyEvidenceSource.LOCKED_APPROVED),
            expected_trust=original.trust,
            retained_original=original,
        )
    proof = _verify(Op.RECOVER, doc, key, platform)
    for obj, immutable_field, immutable_value in (
        (proof, "request", original),
        (proof.request, "canonical_document", b"changed"),
        (proof.key_evidence, "platform", DevicePlatform.ANDROID),
        (proof.trust_evidence, "api_origin", "changed"),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, immutable_field, immutable_value)
        assert "<redacted>" in repr(obj)
        assert doc["account_id"] not in repr(obj)
    with pytest.raises(TypeError):
        cast(MutableMapping[str, object], proof.request.document)["device_name"] = "changed"
    doc["device_name"] = "mutated input"
    assert proof.request.document["device_name"] != doc["device_name"]
    assert "<redacted>" in repr(proof.request.document)
    with pytest.raises(RecoveryV2Error) as caught:
        parse_recovery_request(Op.RECOVER, b'{"secret":"do-not-log"}')
    assert "do-not-log" not in repr(caught.value) + str(caught.value)
    assert caught.value.__suppress_context__


def test_closed_operations_and_unmodified_unicode(
    documents: dict[Op, dict[str, Any]], key: ec.EllipticCurvePrivateKey, platform: DevicePlatform
) -> None:
    for operation in ("recovery.preview", "preview", "configure", None):
        with pytest.raises(RecoveryV2Error):
            parse_recovery_request(cast(Op, operation), _json(documents[Op.PREVIEW]))
    first = documents[Op.PREVIEW]
    second = _sign({**first, "device_name": "Recovery \u00e9 \U0001f3b5"}, key, _domain(Op.PREVIEW))
    assert (
        _verify(Op.PREVIEW, first, key, platform).request.request_sha256
        != _verify(Op.PREVIEW, second, key, platform).request.request_sha256
    )


@pytest.mark.parametrize("op", list(Op))
def test_validly_signed_platform_and_trust_changes_use_explicit_original_scope(
    op: Op,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
) -> None:
    original = parse_recovery_request(Op.RECOVER if op is Op.OUTCOME else op, _json(documents[op]))
    for name, value in (
        ("platform", "ANDROID" if platform is DevicePlatform.MACOS else "MACOS"),
        ("expected_server_instance_id", str(uuid4())),
        ("expected_identity_epoch", 3),
        ("expected_identity_thumbprint_sha256", "d" * 64),
        ("expected_api_origin", "https://changed.invalid"),
        ("expected_stream_origin", "https://changed.invalid"),
    ):
        changed = _sign({**documents[op], name: value}, key, _domain(op))
        parsed = parse_recovery_request(op, _json(changed))
        _verify(op, changed, key, parsed.platform)  # Valid model and signature in its own scope.
        with pytest.raises(RecoveryV2Error) as caught:
            verify_recovery_proof(
                parsed,
                key_evidence=_evidence(key, platform, KeyEvidenceSource.LOCKED_APPROVED),
                expected_trust=original.trust,
                retained_original=original if op is Op.OUTCOME else None,
            )
        assert caught.value.http_status == 401


def test_noncanonical_spki_base64_and_wrong_signer_reach_intended_guards(
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = documents[Op.PREVIEW]
    encoded = doc["device_public_key_spki_b64"]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    assert encoded.endswith("==")
    # Change only unused pad bits; decoded DER remains the same.
    index = alphabet.index(encoded[-3])
    noncanonical = encoded[:-3] + alphabet[index | 1] + "=="
    assert base64.b64decode(noncanonical) == _spki(key)
    with monkeypatch.context() as context:

        def unexpected_hash(*args: object, **kwargs: object) -> None:
            raise AssertionError("noncanonical SPKI reached digest")

        context.setattr(policy, "canonical_sha256", unexpected_hash)
        for value in (noncanonical, encoded.rstrip("="), encoded + "\n"):
            with pytest.raises(RecoveryV2Error):
                parse_recovery_request(
                    Op.PREVIEW, _json({**doc, "device_public_key_spki_b64": value})
                )
    other = ec.generate_private_key(ec.SECP256R1())
    changed = _sign(doc, other, _domain(Op.PREVIEW))
    parse_recovery_request(Op.PREVIEW, _json(changed))
    with pytest.raises(RecoveryV2Error) as caught:
        _verify(Op.PREVIEW, changed, key, platform)
    assert caught.value.http_status == 401


def test_safe_integer_maximum_real_leap_day_and_factory_only_results(
    documents: dict[Op, dict[str, Any]], key: ec.EllipticCurvePrivateKey, platform: DevicePlatform
) -> None:
    doc = _sign(
        {
            **documents[Op.RECOVER],
            "expected_identity_epoch": 9007199254740991,
            "expected_code_generation": 9007199254740991,
            "requested_at": "2024-02-29T23:59:59.123456789Z",
        },
        key,
        _domain(Op.RECOVER),
    )
    proof = _verify(Op.RECOVER, doc, key, platform)
    assert proof.request.document["expected_code_generation"] == 9007199254740991
    for result_type in (policy.ParsedRecoveryRequest, policy.VerifiedRecoveryProof):
        with pytest.raises(TypeError):
            result_type()


@pytest.mark.parametrize("op", list(Op))
def test_recovery_domains_are_disjoint_with_valid_model_and_digest(
    op: Op,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
) -> None:
    other = Op.RECOVER if op is Op.PREVIEW else Op.PREVIEW
    changed = _sign(documents[op], key, _domain(other))
    parse_recovery_request(op, _json(changed))
    with pytest.raises(RecoveryV2Error) as caught:
        _verify(op, changed, key, platform)
    assert caught.value.http_status == 401


@pytest.mark.parametrize("op,kind", [(Op.PREVIEW, "preview"), (Op.RECOVER, "recover")])
def test_v1_android_proof_remains_its_own_format(
    op: Op, kind: str, documents: dict[Op, dict[str, Any]], key: ec.EllipticCurvePrivateKey
) -> None:
    from autplay.domain.account_recovery import AccountRecoveryError, parse_request, verify_device

    legacy = _sign(
        {
            **documents[op],
            "contract_version": "v1",
            "schema_version": 1,
            "platform": "ANDROID",
        },
        key,
        b"autplay:account-recovery:" + kind.encode("ascii") + b":v1\n",
    )
    assert verify_device(kind, parse_request(kind, legacy)) == _spki(key)
    with pytest.raises(RecoveryV2Error):
        parse_recovery_request(op, _json(legacy))
    with pytest.raises(AccountRecoveryError):
        parse_request(kind, documents[op])
