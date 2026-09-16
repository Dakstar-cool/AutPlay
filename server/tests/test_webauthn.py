"""Real P-256 attestation/assertion vectors at the WebAuthn trust boundary."""

from __future__ import annotations

import base64
import json
from hashlib import sha256

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from autplay.adapters.webauthn import DuoWebPasskeyVerifier
from autplay.domain.web_admin import WebAdminError

ORIGIN = "https://admin.example.test"
CHALLENGE = b"challenge" * 4
HANDLE = b"opaque account handle" * 2
CREDENTIAL_ID = b"credential id" * 2


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _vector(
    key: ec.EllipticCurvePrivateKey,
    *,
    registration: bool,
    origin: str = ORIGIN,
    challenge: bytes = CHALLENGE,
    handle: bytes = HANDLE,
    flags: int | None = None,
    rp_id: str = "admin.example.test",
    counter: int = 1,
    client_extra: dict[str, object] | None = None,
) -> str:
    client_data = json.dumps(
        {
            "type": "webauthn.create" if registration else "webauthn.get",
            "challenge": _b64(challenge),
            "origin": origin,
            "crossOrigin": False,
            **(client_extra or {}),
        },
        separators=(",", ":"),
    ).encode()
    flags = (0x45 if registration else 0x05) if flags is None else flags
    auth_data = sha256(rp_id.encode()).digest() + bytes([flags]) + counter.to_bytes(4, "big")
    response: dict[str, object] = {"clientDataJSON": _b64(client_data)}
    if registration:
        public = key.public_key().public_numbers()
        cose_key = cbor2.dumps(
            {1: 2, 3: -7, -1: 1, -2: public.x.to_bytes(32, "big"), -3: public.y.to_bytes(32, "big")}
        )
        auth_data += b"\0" * 16 + len(CREDENTIAL_ID).to_bytes(2, "big") + CREDENTIAL_ID + cose_key
        response["attestationObject"] = _b64(
            cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        )
    else:
        response.update(
            {
                "authenticatorData": _b64(auth_data),
                "signature": _b64(
                    key.sign(auth_data + sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
                ),
                "userHandle": _b64(handle),
            }
        )
    return json.dumps(
        {
            "id": _b64(CREDENTIAL_ID),
            "rawId": _b64(CREDENTIAL_ID),
            "type": "public-key",
            "response": response,
        }
    )


def test_real_registration_and_assertion_preserve_account_binding() -> None:
    verifier = DuoWebPasskeyVerifier(ORIGIN)
    key = ec.generate_private_key(ec.SECP256R1())
    registered = verifier.verify_registration(_vector(key, registration=True), CHALLENGE)
    assert registered.credential_id == CREDENTIAL_ID
    assertion = verifier.verify_authentication(
        _vector(key, registration=False, counter=2),
        CHALLENGE,
        credential_id=CREDENTIAL_ID,
        public_key=registered.public_key,
        user_handle=HANDLE,
        sign_count=1,
    )
    assert assertion.sign_count == 2
    assert not assertion.backup_eligible


@pytest.mark.parametrize(
    "change",
    [
        {"origin": "https://wrong.example.test"},
        {"challenge": b"different challenge"},
        {"handle": b"different account"},
        {"rp_id": "wrong.example.test"},
        {"flags": 0x01},
        {"flags": 0x04},
        {"counter": 1},
        {"client_extra": {"crossOrigin": True}},
        {"client_extra": {"crossOrigin": "false"}},
        {"client_extra": {"topOrigin": ORIGIN}},
    ],
)
def test_signed_assertion_rejects_wrong_authority(change: dict[str, object]) -> None:
    verifier = DuoWebPasskeyVerifier(ORIGIN)
    key = ec.generate_private_key(ec.SECP256R1())
    registered = verifier.verify_registration(_vector(key, registration=True), CHALLENGE)
    arguments = {"registration": False, "counter": 2, **change}
    document = _vector(key, **arguments)  # type: ignore[arg-type]
    with pytest.raises(WebAdminError, match=r"^passkey_invalid$"):
        verifier.verify_authentication(
            document,
            CHALLENGE,
            credential_id=CREDENTIAL_ID,
            public_key=registered.public_key,
            user_handle=HANDLE,
            sign_count=1,
        )


def test_signature_from_another_key_cannot_authenticate() -> None:
    verifier = DuoWebPasskeyVerifier(ORIGIN)
    key = ec.generate_private_key(ec.SECP256R1())
    registered = verifier.verify_registration(_vector(key, registration=True), CHALLENGE)
    attacker = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(WebAdminError, match=r"^passkey_invalid$"):
        verifier.verify_authentication(
            _vector(attacker, registration=False, counter=2),
            CHALLENGE,
            credential_id=CREDENTIAL_ID,
            public_key=registered.public_key,
            user_handle=HANDLE,
            sign_count=1,
        )


@pytest.mark.parametrize("flags", [0x41, 0x44])
def test_registration_requires_presence_and_verification(flags: int) -> None:
    verifier = DuoWebPasskeyVerifier(ORIGIN)
    key = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(WebAdminError, match=r"^passkey_invalid$"):
        verifier.verify_registration(_vector(key, registration=True, flags=flags), CHALLENGE)


@pytest.mark.parametrize("document", ["{}", "[]", "{" * 16000, "x" * 17000])
def test_untrusted_payload_is_bounded_and_errors_are_sanitized(document: str) -> None:
    verifier = DuoWebPasskeyVerifier(ORIGIN)
    with pytest.raises(WebAdminError, match=r"^passkey_invalid$") as error:
        verifier.verify_registration(document, CHALLENGE)
    assert error.value.__suppress_context__


def test_duplicate_client_keys_are_rejected() -> None:
    verifier = DuoWebPasskeyVerifier(ORIGIN)
    key = ec.generate_private_key(ec.SECP256R1())
    credential = json.loads(_vector(key, registration=True))
    credential["response"]["clientDataJSON"] = _b64(b'{"crossOrigin":true,"crossOrigin":false}')
    with pytest.raises(WebAdminError, match=r"^passkey_invalid$"):
        verifier.verify_registration(json.dumps(credential), CHALLENGE)


def test_options_require_uv_resident_keys_and_no_account_enumeration() -> None:
    verifier = DuoWebPasskeyVerifier(ORIGIN)
    registration = json.loads(verifier.registration_options(CHALLENGE, HANDLE, (CREDENTIAL_ID,)))
    assert registration["authenticatorSelection"]["userVerification"] == "required"
    assert registration["authenticatorSelection"]["residentKey"] == "required"
    assert registration["attestation"] == "none"
    login = json.loads(verifier.authentication_options(CHALLENGE))
    assert not login.get("allowCredentials")
    assert login["userVerification"] == "required"
