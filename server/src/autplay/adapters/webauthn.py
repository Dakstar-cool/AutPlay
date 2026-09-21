"""Bounded WebAuthn verification using the pinned Duo implementation."""

from __future__ import annotations

import base64
import json
from urllib.parse import urlsplit

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import parse_attestation_object
from webauthn.helpers.cose import COSEAlgorithmIdentifier
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from autplay.domain.web_admin import WebAdminError
from autplay.domain.web_passkeys import VerifiedPasskey, VerifiedPasskeyAssertion

MAX_CREDENTIAL_BYTES = 16_384
_ALGORITHMS = [
    COSEAlgorithmIdentifier.ECDSA_SHA_256,
    COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256,
]


class DuoWebPasskeyVerifier:
    def __init__(self, origin: str) -> None:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.hostname != parsed.hostname.lower()
            or parsed.port == 443
        ):
            raise ValueError("passkeys require a canonical HTTPS origin")
        self.origin = origin
        self.rp_id = parsed.hostname

    def registration_options(
        self, challenge: bytes, user_handle: bytes, existing: tuple[bytes, ...]
    ) -> str:
        return options_to_json(
            generate_registration_options(
                rp_id=self.rp_id,
                rp_name="AutPlay Admin",
                user_name="AutPlay Admin",
                user_id=user_handle,
                challenge=challenge,
                timeout=60_000,
                authenticator_selection=AuthenticatorSelectionCriteria(
                    resident_key=ResidentKeyRequirement.REQUIRED,
                    require_resident_key=True,
                    user_verification=UserVerificationRequirement.REQUIRED,
                ),
                exclude_credentials=[PublicKeyCredentialDescriptor(id=value) for value in existing],
                supported_pub_key_algs=_ALGORITHMS,
            )
        )

    def authentication_options(self, challenge: bytes) -> str:
        return options_to_json(
            generate_authentication_options(
                rp_id=self.rp_id,
                challenge=challenge,
                timeout=60_000,
                user_verification=UserVerificationRequirement.REQUIRED,
            )
        )

    def credential_id(self, document: str) -> bytes:
        try:
            credential = _credential(document)
            return _decode(credential["rawId"], maximum=1024)
        except Exception:
            raise WebAdminError("passkey_invalid") from None

    def verify_registration(self, document: str, challenge: bytes) -> VerifiedPasskey:
        try:
            credential = _credential(document)
            response = _response(credential)
            _same_origin_client(response)
            attestation = parse_attestation_object(
                _decode(response["attestationObject"], maximum=8192)
            )
            if attestation.fmt != "none":
                raise ValueError("attestation not accepted")
            result = verify_registration_response(
                credential=document,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                require_user_presence=True,
                require_user_verification=True,
                supported_pub_key_algs=_ALGORITHMS,
            )
            if (
                not 1 <= len(result.credential_id) <= 1024
                or len(result.credential_public_key) > 2048
            ):
                raise ValueError("credential bounds")
            return VerifiedPasskey(
                result.credential_id,
                result.credential_public_key,
                result.sign_count,
                result.credential_device_type == CredentialDeviceType.MULTI_DEVICE,
                result.credential_backed_up,
            )
        except Exception:
            raise WebAdminError("passkey_invalid") from None

    def verify_authentication(
        self,
        document: str,
        challenge: bytes,
        *,
        credential_id: bytes,
        public_key: bytes,
        user_handle: bytes,
        sign_count: int,
    ) -> VerifiedPasskeyAssertion:
        try:
            credential = _credential(document)
            response = _response(credential)
            _same_origin_client(response)
            if (
                _decode(credential["rawId"], maximum=1024) != credential_id
                or _decode(response["userHandle"], maximum=64) != user_handle
            ):
                raise ValueError("credential account mismatch")
            result = verify_authentication_response(
                credential=document,
                expected_challenge=challenge,
                expected_rp_id=self.rp_id,
                expected_origin=self.origin,
                credential_public_key=public_key,
                credential_current_sign_count=sign_count,
                require_user_verification=True,
            )
            return VerifiedPasskeyAssertion(
                result.new_sign_count,
                result.credential_device_type == CredentialDeviceType.MULTI_DEVICE,
                result.credential_backed_up,
            )
        except Exception:
            raise WebAdminError("passkey_invalid") from None


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _object(document: str | bytes) -> dict[str, object]:
    parsed: object = json.loads(document, object_pairs_hook=_unique_object)
    if not isinstance(parsed, dict):
        raise ValueError("JSON object required")
    return parsed


def _credential(document: str) -> dict[str, object]:
    if not 1 <= len(document.encode("utf-8")) <= MAX_CREDENTIAL_BYTES:
        raise ValueError("credential bounds")
    credential = _object(document)
    if credential.get("type") != "public-key":
        raise ValueError("credential type")
    raw_id = _decode(credential["rawId"], maximum=1024)
    if base64.urlsafe_b64encode(raw_id).decode().rstrip("=") != credential["id"]:
        raise ValueError("credential ID mismatch")
    return credential


def _response(credential: dict[str, object]) -> dict[str, object]:
    response = credential.get("response")
    if not isinstance(response, dict):
        raise ValueError("response required")
    return response


def _same_origin_client(response: dict[str, object]) -> None:
    client = _object(_decode(response["clientDataJSON"], maximum=2048))
    if client.get("crossOrigin", False) is not False or "topOrigin" in client:
        raise ValueError("embedded ceremony forbidden")


def _decode(value: object, *, maximum: int) -> bytes:
    if not isinstance(value, str) or not 1 <= len(value) <= (maximum + 2) * 4 // 3:
        raise ValueError("base64url bounds")
    decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    if not 1 <= len(decoded) <= maximum:
        raise ValueError("decoded bounds")
    return decoded
