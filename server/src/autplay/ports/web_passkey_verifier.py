"""WebAuthn library boundary; no cryptographic implementation in use cases."""

from typing import Protocol

from autplay.domain.web_passkeys import VerifiedPasskey, VerifiedPasskeyAssertion


class WebPasskeyVerifier(Protocol):
    origin: str
    rp_id: str

    def registration_options(
        self, challenge: bytes, user_handle: bytes, existing: tuple[bytes, ...]
    ) -> str: ...

    def authentication_options(self, challenge: bytes) -> str: ...

    def credential_id(self, document: str) -> bytes: ...

    def verify_registration(self, document: str, challenge: bytes) -> VerifiedPasskey: ...

    def verify_authentication(
        self,
        document: str,
        challenge: bytes,
        *,
        credential_id: bytes,
        public_key: bytes,
        user_handle: bytes,
        sign_count: int,
    ) -> VerifiedPasskeyAssertion: ...
