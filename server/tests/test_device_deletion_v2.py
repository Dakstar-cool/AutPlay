"""Future D1 deletion pure-proof tests; SOURCE ONLY, UNVERIFIED and unrun.

No key, digest or signature is computed at import time. Historical proof tests
assert no active-session prerequisite, never successful service authorization.
Ledger/state/secret/header/storage/lifecycle behavior needs separate future gates.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import MutableMapping
from dataclasses import FrozenInstanceError, replace
from typing import Any, cast
from uuid import uuid4

import autplay.domain.device_recovery_v2 as shared
import pytest
import rfc8785
from autplay.domain.device_deletion_v2 import (
    DeletionOperation as Op,
)
from autplay.domain.device_deletion_v2 import (
    DeletionV2Error,
    DeviceKeyEvidence,
    DevicePlatform,
    KeyEvidenceSource,
    ParsedDeletionRequest,
    VerifiedDeletionProof,
    VerifiedDeletionResolutionCandidate,
    parse_deletion_request,
    verify_deletion_proof,
    verify_deletion_resolution_candidate,
)
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from typed_assertions import replaced, require_instance, set_field


def _spki(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def _json(doc: dict[str, Any]) -> bytes:
    return json.dumps(doc, ensure_ascii=True, separators=(",", ":")).encode("utf-8")


def _base(op: Op) -> Op:
    return {
        Op.REQUEST_RECEIPT: Op.REQUEST,
        Op.REQUEST_RESOLVE: Op.REQUEST,
        Op.CANCEL_OUTCOME: Op.CANCEL,
    }.get(op, op)


def _domain(op: Op) -> bytes:
    return {
        Op.REQUEST: b"autplay:device-compatibility:deletion:request:v2\n",
        Op.PREVIEW: b"autplay:device-compatibility:deletion:preview:v2\n",
        Op.CANCEL: b"autplay:device-compatibility:deletion:cancel:v2\n",
    }[_base(op)]


def _sign(doc: dict[str, Any], key: ec.EllipticCurvePrivateKey, domain: bytes) -> dict[str, Any]:
    result = dict(doc)
    unsigned = {
        k: v for k, v in result.items() if k not in {"request_sha256", "device_signature_b64url"}
    }
    digest = hashlib.sha256(rfc8785.dumps(unsigned)).digest()
    result["request_sha256"] = digest.hex()
    r, s = utils.decode_dss_signature(key.sign(domain + digest, ec.ECDSA(hashes.SHA256())))
    result["device_signature_b64url"] = (
        base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        .rstrip(b"=")
        .decode("ascii")
    )
    return result


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
        "device_name": "Deletion e\u0301 \U0001f3b5",
        "app_version": "future-v2",
    }
    request_doc = _sign(
        {
            **common,
            "confirmed_account_id": common["account_id"],
            "expected_code_generation": 1,
            "expected_authority_generation": 2,
        },
        key,
        _domain(Op.REQUEST),
    )
    cancel_doc = _sign(
        {
            **common,
            "operation_id": str(uuid4()),
            "confirmed_account_id": common["account_id"],
            "expected_code_generation": 1,
            "next_code_verifier_sha256": "b" * 64,
            "next_refresh_token_sha256": "c" * 64,
            "binding_commit_id": str(uuid4()),
            "deletion_request_id": request_doc["operation_id"],
            "expected_revision": 1,
        },
        key,
        _domain(Op.CANCEL),
    )
    models = {
        Op.REQUEST: request_doc,
        Op.PREVIEW: _sign(common, key, _domain(Op.PREVIEW)),
        Op.CANCEL: cancel_doc,
    }
    return {op: dict(models[_base(op)]) for op in Op}


def _evidence(
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
    source: KeyEvidenceSource = KeyEvidenceSource.LOCKED_APPROVED,
) -> DeviceKeyEvidence:
    return DeviceKeyEvidence(_spki(key), platform, source)


def _verify(
    op: Op, doc: dict[str, Any], key: ec.EllipticCurvePrivateKey, platform: DevicePlatform
) -> VerifiedDeletionProof:
    parsed = parse_deletion_request(op, _json(doc))
    historical = _base(op) is not op
    original = parse_deletion_request(_base(op), _json(doc)) if historical else None
    return verify_deletion_proof(
        parsed,
        key_evidence=_evidence(
            key,
            platform,
            KeyEvidenceSource.HISTORICAL_APPROVED
            if historical
            else KeyEvidenceSource.LOCKED_APPROVED,
        ),
        expected_trust=parsed.trust,
        retained_original=original,
    )


@pytest.mark.parametrize("op", list(Op))
def test_all_deletion_operations_both_platforms_full_original_evidence(
    op: Op,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
) -> None:
    doc = documents[op]
    proof = _verify(op, doc, key, platform)
    parsed = proof.request
    assert parsed.canonical_document == rfc8785.dumps(doc)
    assert parsed.original_document_sha256 == hashlib.sha256(rfc8785.dumps(doc)).digest()
    assert parsed.signature == base64.urlsafe_b64decode(doc["device_signature_b64url"] + "==")
    assert parsed.request_sha256.hex() == doc["request_sha256"]
    assert dict(parsed.document) == doc
    assert parsed.platform is platform
    assert proof.key_evidence.public_key_spki == _spki(key)
    assert proof.trust_evidence == parsed.trust
    assert proof.proof_domain.encode("ascii") == _domain(op)
    assert proof.signature_algorithm == "ES256-P1363"
    assert parsed.document["requested_at"].endswith("123456789Z")
    assert parsed.document["device_name"] == "Deletion e\u0301 \U0001f3b5"
    assert "code" not in parsed.document and "refresh" not in parsed.document


@pytest.mark.parametrize("op", [Op.REQUEST_RECEIPT, Op.REQUEST_RESOLVE, Op.CANCEL_OUTCOME])
def test_continuations_verify_original_without_session_or_freshness(
    op: Op,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
) -> None:
    doc = _sign({**documents[op], "requested_at": "2000-02-29T00:00:00Z"}, key, _domain(op))
    parsed = parse_deletion_request(op, _json(doc))
    original = parse_deletion_request(_base(op), _json(doc))
    proof = verify_deletion_proof(
        parsed,
        key_evidence=_evidence(key, platform, KeyEvidenceSource.HISTORICAL_APPROVED),
        expected_trust=original.trust,
        retained_original=original,
    )
    assert proof.request.signature == original.signature
    assert proof.request.canonical_document == original.canonical_document
    assert not hasattr(proof, "access_token") and not hasattr(proof, "authorized")
    for retained, source in (
        (None, KeyEvidenceSource.LOCKED_APPROVED),
        (original, KeyEvidenceSource.CLIENT_SUPPLIED),
    ):
        with pytest.raises(DeletionV2Error) as caught:
            verify_deletion_proof(
                parsed,
                key_evidence=_evidence(key, platform, source),
                expected_trust=original.trust,
                retained_original=retained,
            )
        assert caught.value.http_status == 401
    # Historical identity is supplied explicitly; current identity is not guessed.
    with pytest.raises(DeletionV2Error):
        verify_deletion_proof(
            parsed,
            key_evidence=proof.key_evidence,
            expected_trust=replace(original.trust, identity_epoch=3),
            retained_original=original,
        )


@pytest.mark.parametrize("op", list(Op))
@pytest.mark.parametrize(
    "domain",
    [
        b"autplay:account-deletion:request:v1\n",
        b"autplay:device-compatibility:deletion:request-receipt:v2\n",
        b"autplay:device-compatibility:deletion:request-resolve:v2\n",
        b"autplay:device-compatibility:deletion:cancel-outcome:v2\n",
        b"autplay:device-compatibility:recovery:preview:v2\n",
        b"autplay:device-compatibility:recovery:recover:v2\n",
    ],
)
def test_wrong_domain_valid_hash_reaches_signature_guard(
    op: Op,
    domain: bytes,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
) -> None:
    doc = _sign(documents[op], key, domain)
    parse_deletion_request(op, _json(doc))
    with pytest.raises(DeletionV2Error) as caught:
        _verify(op, doc, key, platform)
    assert caught.value.http_status == 401


@pytest.mark.parametrize("op", list(Op))
def test_models_cannot_be_interchanged_and_operations_are_closed(
    op: Op, documents: dict[Op, dict[str, Any]]
) -> None:
    for other in Op:
        if _base(other) is _base(op):
            continue
        with pytest.raises(DeletionV2Error):
            parse_deletion_request(op, _json(documents[other]))
    for operation in (op.value, "request", "DELETE_CANCEL", None):
        with pytest.raises(DeletionV2Error):
            parse_deletion_request(cast(Op, operation), _json(documents[op]))


@pytest.mark.parametrize("op", list(Op))
def test_members_no_defaults_null_secrets_or_hidden_domains(
    op: Op, documents: dict[Op, dict[str, Any]]
) -> None:
    for name in documents[op]:
        missing = dict(documents[op])
        del missing[name]
        for doc in (missing, {**documents[op], name: None}):
            with pytest.raises(DeletionV2Error):
                parse_deletion_request(op, _json(doc))
    for name in (
        "code",
        "recovery_code",
        "refresh",
        "current_refresh_token",
        "domain",
        "proof_b64url",
        "kind",
    ):
        with pytest.raises(DeletionV2Error):
            parse_deletion_request(op, _json({**documents[op], name: "do-not-store"}))


@pytest.mark.parametrize("op", list(Op))
def test_raw_json_and_structural_guards_before_digest(
    op: Op, documents: dict[Op, dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _json(documents[op])
    invalid = [
        b'{"schema_version":2,' + raw[1:],
        b'{"extra":{"x":1,"x":2},' + raw[1:],
        raw.replace(b'"schema_version":2', b'"schema_version":2.0'),
        raw.replace(b'"schema_version":2', b'"schema_version":2e0'),
        raw.replace(b'"schema_version":2', b'"schema_version":NaN'),
        raw.replace(b'"schema_version":2', b'"schema_version":Infinity'),
        raw.replace(b'"schema_version":2', b'"schema_version":-1'),
        _json({**documents[op], "device_name": "\ud800"}),
        b"\xff",
        b"null",
        b"[]",
    ]
    for name, value in (
        ("schema_version", True),
        ("schema_version", 1),
        ("contract_version", "v1"),
        ("platform", "OTHER"),
        ("platform", "WEB"),
        ("platform", "macos"),
        ("expected_identity_epoch", True),
        ("expected_identity_epoch", 0),
        ("expected_identity_epoch", 9007199254740992),
        ("expected_identity_epoch", "2"),
        ("account_id", "A" * 36),
        ("requested_at", "2026-02-29T00:00:00Z"),
        ("requested_at", "2026-01-01T24:00:00Z"),
        ("requested_at", "2026-01-01T00:00:60Z"),
        ("requested_at", "2026-01-01T00:00:00.1234567890Z"),
        ("expected_api_origin", "https://host.invalid/"),
        ("expected_api_origin", "https://user@host.invalid"),
        ("expected_stream_origin", "https://host.invalid?q=secret"),
        ("device_name", "x" * 121),
        ("device_name", "x\n"),
        ("app_version", "x\u007f"),
        ("device_public_key_spki_b64", "=" * 124),
        ("device_signature_b64url", "A" * 85 + "B"),
    ):
        invalid.append(_json({**documents[op], name: value}))

    def unexpected_hash(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid model reached digest")

    monkeypatch.setattr(shared, "canonical_sha256", unexpected_hash)
    for item in invalid:
        assert item != raw
        with pytest.raises(DeletionV2Error) as caught:
            parse_deletion_request(op, item)
        assert caught.value.http_status == 400


@pytest.mark.parametrize("op", list(Op))
def test_bounded_raw_and_canonical_and_immutable_redacted_evidence(
    op: Op,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _json(documents[op])
    padded = raw + b" " * (shared.MAX_BODY_BYTES - len(raw))
    assert parse_deletion_request(op, padded).canonical_document == rfc8785.dumps(documents[op])
    with pytest.raises(DeletionV2Error) as caught:
        parse_deletion_request(op, padded + b" ")
    assert caught.value.http_status == 413
    proof = _verify(op, documents[op], key, platform)
    for obj, name, value in (
        (proof, "request", None),
        (proof.request, "signature", bytes(64)),
        (proof.key_evidence, "platform", "ANDROID"),
        (proof.trust_evidence, "identity_epoch", 99),
    ):
        with pytest.raises(FrozenInstanceError):
            setattr(obj, name, value)
        assert "<redacted>" in repr(obj)
        assert documents[op]["account_id"] not in repr(obj)
    with pytest.raises(TypeError):
        cast(MutableMapping[str, object], proof.request.document)["device_name"] = "changed"
    documents[op]["device_name"] = "mutated caller copy"
    assert proof.request.document["device_name"] != documents[op]["device_name"]
    monkeypatch.setattr(rfc8785, "dumps", lambda value: b"x" * (shared.MAX_BODY_BYTES + 1))
    with pytest.raises(DeletionV2Error) as caught:
        parse_deletion_request(op, raw)
    assert caught.value.http_status == 413


@pytest.mark.parametrize("op", list(Op))
def test_explicit_key_platform_and_five_trust_fields(
    op: Op,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
) -> None:
    parsed = parse_deletion_request(op, _json(documents[op]))
    original = (
        parse_deletion_request(_base(op), _json(documents[op])) if _base(op) is not op else None
    )
    other = ec.generate_private_key(ec.SECP256R1())
    for evidence in (
        _evidence(other, platform),
        _evidence(
            key,
            DevicePlatform.MACOS if platform is DevicePlatform.ANDROID else DevicePlatform.ANDROID,
        ),
        DeviceKeyEvidence(
            _spki(key), cast(DevicePlatform, "ANDROID"), KeyEvidenceSource.LOCKED_APPROVED
        ),
        DeviceKeyEvidence(_spki(key), platform, cast(KeyEvidenceSource, "LOCKED_APPROVED")),
    ):
        with pytest.raises(DeletionV2Error):
            verify_deletion_proof(
                parsed,
                key_evidence=evidence,
                expected_trust=parsed.trust,
                retained_original=original,
            )
    for name, value in (
        ("server_instance_id", str(uuid4())),
        ("identity_epoch", 3),
        ("identity_thumbprint_sha256", "b" * 64),
        ("api_origin", "https://api.test.invalid:443"),
        ("stream_origin", "https://different.invalid"),
    ):
        with pytest.raises(DeletionV2Error):
            verify_deletion_proof(
                parsed,
                key_evidence=_evidence(key, platform),
                expected_trust=replaced(parsed.trust, **{name: value}),
                retained_original=original,
            )


def test_historical_evidence_cannot_be_fresh_actor_or_recipient(
    documents: dict[Op, dict[str, Any]], key: ec.EllipticCurvePrivateKey, platform: DevicePlatform
) -> None:
    for op in (Op.REQUEST, Op.PREVIEW, Op.CANCEL):
        parsed = parse_deletion_request(op, _json(documents[op]))
        with pytest.raises(DeletionV2Error):
            verify_deletion_proof(
                parsed,
                key_evidence=_evidence(key, platform, KeyEvidenceSource.HISTORICAL_APPROVED),
                expected_trust=parsed.trust,
            )
    parsed = parse_deletion_request(Op.REQUEST, _json(documents[Op.REQUEST]))
    with pytest.raises(DeletionV2Error):
        verify_deletion_proof(
            parsed,
            key_evidence=_evidence(key, platform, KeyEvidenceSource.CLIENT_SUPPLIED),
            expected_trust=parsed.trust,
        )
    for op in (Op.PREVIEW, Op.CANCEL):
        parsed = parse_deletion_request(op, _json(documents[op]))
        verify_deletion_proof(
            parsed,
            key_evidence=_evidence(key, platform, KeyEvidenceSource.CLIENT_SUPPLIED),
            expected_trust=parsed.trust,
        )


@pytest.mark.parametrize("op", [Op.REQUEST_RECEIPT, Op.REQUEST_RESOLVE, Op.CANCEL_OUTCOME])
def test_retained_original_rejects_validly_signed_changes_and_different_proof(
    op: Op,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
) -> None:
    doc = documents[op]
    original = parse_deletion_request(_base(op), _json(doc))
    changes: list[tuple[str, str | int]] = [
        ("operation_id", str(uuid4())),
        ("requested_at", "2026-10-16T12:01:00Z"),
    ]
    if op is Op.CANCEL_OUTCOME:
        changes.extend(
            [
                ("binding_commit_id", str(uuid4())),
                ("deletion_request_id", str(uuid4())),
                ("expected_revision", 2),
                ("next_refresh_token_sha256", "d" * 64),
            ]
        )
    for name, value in changes:
        changed = _sign({**doc, name: value}, key, _domain(op))
        _verify(op, changed, key, platform)  # Independent valid new proof, not stale hash.
        with pytest.raises(DeletionV2Error):
            verify_deletion_proof(
                parse_deletion_request(op, _json(changed)),
                key_evidence=_evidence(key, platform),
                expected_trust=original.trust,
                retained_original=original,
            )
    order = int("ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", 16)
    raw = original.signature
    twin = raw[:32] + (order - int.from_bytes(raw[32:], "big")).to_bytes(32, "big")
    changed = {
        **doc,
        "device_signature_b64url": base64.urlsafe_b64encode(twin).rstrip(b"=").decode("ascii"),
    }
    _verify(op, changed, key, platform)
    with pytest.raises(DeletionV2Error):
        verify_deletion_proof(
            parse_deletion_request(op, _json(changed)),
            key_evidence=_evidence(key, platform),
            expected_trust=original.trust,
            retained_original=original,
        )


def test_confirmation_generations_revision_and_spki_p1363_guards(
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = documents[Op.PREVIEW]
    # A stale request digest must never satisfy a structural rejection.
    with monkeypatch.context() as context:

        def unexpected_hash(*args: object, **kwargs: object) -> None:
            raise AssertionError(
                "invalid confirmation, generation, revision or SPKI reached digest"
            )

        context.setattr(shared, "canonical_sha256", unexpected_hash)
        for op in (Op.REQUEST, Op.CANCEL):
            for name in (
                "expected_code_generation",
                "expected_authority_generation",
                "expected_revision",
            ):
                if name not in documents[op]:
                    continue
                for value in (True, 0, -1, "1", 9007199254740992):
                    with pytest.raises(DeletionV2Error):
                        parse_deletion_request(op, _json({**documents[op], name: value}))
            with pytest.raises(DeletionV2Error):
                parse_deletion_request(
                    op, _json({**documents[op], "confirmed_account_id": str(uuid4())})
                )
        for spki in (
            _spki(ec.generate_private_key(ec.SECP384R1())),
            _spki(key) + b"\x00",
            bytes(91),
        ):
            changed = {
                **doc,
                "device_public_key_spki_b64": base64.b64encode(spki).decode("ascii"),
                "device_key_thumbprint_sha256": hashlib.sha256(spki).hexdigest(),
            }
            with pytest.raises(DeletionV2Error):
                parse_deletion_request(Op.PREVIEW, _json(changed))
        with pytest.raises(DeletionV2Error):
            parse_deletion_request(
                Op.PREVIEW, _json({**doc, "device_key_thumbprint_sha256": "0" * 64})
            )
    for raw in (bytes(64), bytes([255]) * 64):
        changed = {
            **doc,
            "device_signature_b64url": base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
        }
        parse_deletion_request(Op.PREVIEW, _json(changed))
        with pytest.raises(DeletionV2Error) as caught:
            _verify(Op.PREVIEW, changed, key, platform)
        assert caught.value.http_status == 401


def test_no_unicode_normalization_and_payload_free_errors(
    documents: dict[Op, dict[str, Any]], key: ec.EllipticCurvePrivateKey, platform: DevicePlatform
) -> None:
    doc = documents[Op.PREVIEW]
    composed = _sign({**doc, "device_name": "Deletion \u00e9 \U0001f3b5"}, key, _domain(Op.PREVIEW))
    first = _verify(Op.PREVIEW, doc, key, platform)
    second = _verify(Op.PREVIEW, composed, key, platform)
    assert first.request.request_sha256 != second.request.request_sha256
    with pytest.raises(DeletionV2Error) as caught:
        parse_deletion_request(Op.REQUEST, b'{"code":"do-not-log"}')
    assert "do-not-log" not in str(caught.value) + repr(caught.value)
    assert caught.value.__suppress_context__


def test_first_resolution_candidate_is_not_a_terminal_result(
    documents: dict[Op, dict[str, Any]], key: ec.EllipticCurvePrivateKey, platform: DevicePlatform
) -> None:
    parsed = parse_deletion_request(Op.REQUEST_RESOLVE, _json(documents[Op.REQUEST]))
    candidate = verify_deletion_resolution_candidate(
        parsed,
        key_evidence=_evidence(key, platform),
        expected_trust=parsed.trust,
    )
    assert type(candidate) is VerifiedDeletionResolutionCandidate
    assert not isinstance(candidate, VerifiedDeletionProof)
    assert candidate.request is parsed
    assert candidate.proof_domain.encode("ascii") == _domain(Op.REQUEST)
    assert candidate.signature_algorithm == "ES256-P1363"
    assert "<redacted>" in repr(candidate)
    assert not hasattr(candidate, "state") and not hasattr(candidate, "authorized")
    with pytest.raises(FrozenInstanceError):
        set_field(candidate, "request", None)
    for source in (KeyEvidenceSource.CLIENT_SUPPLIED, KeyEvidenceSource.HISTORICAL_APPROVED):
        with pytest.raises(DeletionV2Error):
            verify_deletion_resolution_candidate(
                parsed, key_evidence=_evidence(key, platform, source), expected_trust=parsed.trust
            )
    for op in (Op.REQUEST, Op.REQUEST_RECEIPT, Op.PREVIEW, Op.CANCEL, Op.CANCEL_OUTCOME):
        request = parse_deletion_request(op, _json(documents[op]))
        with pytest.raises(DeletionV2Error):
            verify_deletion_resolution_candidate(
                request, key_evidence=_evidence(key, platform), expected_trust=request.trust
            )
    for result_type in (
        ParsedDeletionRequest,
        VerifiedDeletionProof,
        VerifiedDeletionResolutionCandidate,
    ):
        with pytest.raises(TypeError):
            result_type()


@pytest.mark.parametrize("op", list(Op))
def test_signed_platform_and_trust_tampering_not_stale_hash(
    op: Op,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
) -> None:
    original = parse_deletion_request(_base(op), _json(documents[op]))
    for name, value in (
        ("platform", "ANDROID" if platform is DevicePlatform.MACOS else "MACOS"),
        ("expected_server_instance_id", str(uuid4())),
        ("expected_identity_epoch", 3),
        ("expected_identity_thumbprint_sha256", "d" * 64),
        ("expected_api_origin", "https://changed.invalid"),
        ("expected_stream_origin", "https://changed.invalid"),
    ):
        changed = _sign({**documents[op], name: value}, key, _domain(op))
        parsed = parse_deletion_request(op, _json(changed))
        _verify(op, changed, key, parsed.platform)
        with pytest.raises(DeletionV2Error) as caught:
            verify_deletion_proof(
                parsed,
                key_evidence=_evidence(key, platform),
                expected_trust=original.trust,
                retained_original=original if _base(op) is not op else None,
            )
        assert caught.value.http_status == 401


def test_noncanonical_spki_prehash_der_and_wrong_signer(
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = documents[Op.PREVIEW]
    encoded = doc["device_public_key_spki_b64"]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    assert encoded.endswith("==")
    index = alphabet.index(encoded[-3])
    noncanonical = encoded[:-3] + alphabet[index | 1] + "=="
    assert base64.b64decode(noncanonical) == _spki(key)
    with monkeypatch.context() as context:

        def unexpected_hash(*args: object, **kwargs: object) -> None:
            raise AssertionError("noncanonical SPKI reached digest")

        context.setattr(shared, "canonical_sha256", unexpected_hash)
        for value in (noncanonical, encoded.rstrip("="), encoded + "\n"):
            with pytest.raises(DeletionV2Error):
                parse_deletion_request(
                    Op.PREVIEW, _json({**doc, "device_public_key_spki_b64": value})
                )
    digest = bytes.fromhex(doc["request_sha256"])
    for message, algorithm in (
        (digest, ec.ECDSA(utils.Prehashed(hashes.SHA256()))),
        (_domain(Op.PREVIEW) + digest.hex().encode("ascii"), ec.ECDSA(hashes.SHA256())),
    ):
        r, s = utils.decode_dss_signature(key.sign(message, algorithm))
        sig = (
            base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
            .rstrip(b"=")
            .decode("ascii")
        )
        with pytest.raises(DeletionV2Error) as caught:
            _verify(Op.PREVIEW, {**doc, "device_signature_b64url": sig}, key, platform)
        assert caught.value.http_status == 401
    der = key.sign(_domain(Op.PREVIEW) + digest, ec.ECDSA(hashes.SHA256()))
    with pytest.raises(DeletionV2Error):
        parse_deletion_request(
            Op.PREVIEW,
            _json(
                {
                    **doc,
                    "device_signature_b64url": base64.urlsafe_b64encode(der)
                    .rstrip(b"=")
                    .decode("ascii"),
                }
            ),
        )
    other = ec.generate_private_key(ec.SECP256R1())
    changed = _sign(doc, other, _domain(Op.PREVIEW))
    parse_deletion_request(Op.PREVIEW, _json(changed))
    with pytest.raises(DeletionV2Error) as caught:
        _verify(Op.PREVIEW, changed, key, platform)
    assert caught.value.http_status == 401


@pytest.mark.parametrize("op", list(Op))
def test_deletion_domains_are_disjoint_with_valid_models(
    op: Op,
    documents: dict[Op, dict[str, Any]],
    key: ec.EllipticCurvePrivateKey,
    platform: DevicePlatform,
) -> None:
    for other in (Op.REQUEST, Op.PREVIEW, Op.CANCEL):
        if _base(op) is other:
            continue
        changed = _sign(documents[op], key, _domain(other))
        parse_deletion_request(op, _json(changed))
        with pytest.raises(DeletionV2Error) as caught:
            _verify(op, changed, key, platform)
        assert caught.value.http_status == 401


@pytest.mark.parametrize(
    "op,kind", [(Op.REQUEST, "request"), (Op.PREVIEW, "preview"), (Op.CANCEL, "cancel")]
)
def test_v1_android_deletion_remains_a_separate_original_format(
    op: Op, kind: str, documents: dict[Op, dict[str, Any]], key: ec.EllipticCurvePrivateKey
) -> None:
    from autplay.domain.account_deletion import AccountDeletionError, parse_request, verify_device

    legacy = _sign(
        {
            **documents[op],
            "contract_version": "v1",
            "schema_version": 1,
            "platform": "ANDROID",
        },
        key,
        b"autplay:account-deletion:" + kind.encode("ascii") + b":v1\n",
    )
    assert verify_device(kind, parse_request(kind, legacy)) == _spki(key)
    with pytest.raises(DeletionV2Error):
        parse_deletion_request(op, _json(legacy))
    with pytest.raises(AccountDeletionError):
        parse_request(kind, documents[op])
