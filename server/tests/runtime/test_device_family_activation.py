"""Production assembly must gate first effects independently of route discovery."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from autplay.domain.device_compatibility import DeviceCompatibilityError
from autplay.entrypoints import composition
from autplay.entrypoints.device_portable_v2_http import build_device_portable_v2_service
from autplay.runtime.settings import ApiSettings
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import SecretStr
from sqlalchemy import create_engine
from typed_assertions import present

FAMILIES = (
    "enrollment",
    "session",
    "portable",
    "admission",
    "trusted",
    "self",
    "recovery",
    "deletion",
)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("qualified", [None, frozenset(), *[frozenset({f}) for f in FAMILIES]])
def test_only_source_qualified_first_effects_open_and_android_v1_flags_survive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    enabled: bool,
    qualified: frozenset[str] | None,
) -> None:
    if qualified is None:
        # Exercise the shipped policy through real composition, without registry overrides.
        qualified = frozenset({"enrollment", "session", "portable"})
    else:
        monkeypatch.setattr(
            "autplay.domain.device_compatibility.IMPLEMENTED_AND_QUALIFIED_FAMILIES", qualified
        )
    pem = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode("ascii")
    )
    settings = ApiSettings(
        database_url=SecretStr("postgresql+psycopg://fixture:fixture@127.0.0.1:1/autplay"),
        auth_signing_secret=SecretStr("family-test-auth-secret-at-least-thirty-two-bytes"),
        public_access_source_hmac_secret=SecretStr("family-test-source-secret-at-least-32-bytes"),
        profile_identity_private_key_pem=SecretStr(pem),
        device_compatibility_v2_enabled=enabled,
        portable_accounts_enabled=True,
        self_device_pairing_enabled=True,
        account_recovery_enabled=True,
        account_deletion_enabled=True,
        privacy_ledger_path=tmp_path / "privacy.sqlite3",
        privacy_ledger_key=SecretStr("family-test-ledger-secret-at-least-thirty-two-bytes"),
        privacy_ledger_key_id="fixture",
    )
    engine = create_engine("sqlite://")
    try:
        identity = present(composition.build_profile_pairing_service(settings, engine))
        profile = present(
            composition.build_device_profile_v2_service(settings, engine, identity=identity)
        )
        admission = present(
            composition.build_device_admission_v2_service(settings, engine, identity=identity)
        )
        portable = present(build_device_portable_v2_service(settings, engine, identity=identity))
        self_pairing = present(
            composition.build_device_self_v2_service(settings, engine, identity=identity)
        )
        recovery = present(
            composition.build_device_recovery_v2_service(settings, engine, identity=identity)
        )
        deletion = present(
            composition.build_device_deletion_v2_service(settings, engine, identity=identity)
        )
        # This test isolates admission from the independently tested durable ledger.
        monkeypatch.setattr(deletion, "_evidence_ready", lambda: True)
        gates: dict[str, Callable[[], None]] = {
            "enrollment": lambda: profile._require_enabled("enrollment"),
            "session": lambda: profile._require_enabled("session"),
            "admission": lambda: admission._require_enabled("admission"),
            "trusted": lambda: admission._require_enabled("trusted"),
            "portable": portable._require_enabled,
            "self": self_pairing._gate,
            "recovery": recovery._gate,
            "deletion": lambda: deletion._ready(first_request=True),
        }
        for family, gate in gates.items():
            if enabled and family in qualified:
                gate()
            else:
                with pytest.raises(DeviceCompatibilityError, match="device_contract_unavailable"):
                    gate()
        # Web approval is another first effect for admission, outside the v2 router.
        assert identity._device_v2_enabled() is (enabled and "admission" in qualified)
        assert identity._self_device_pairing_enabled is True
        assert identity._account_recovery_enabled is True
        assert identity._account_deletion_enabled is True
        assert identity._portable_accounts_enabled is True
        # A mounted terminal handler remains usable when first effects are closed.
        deletion._ready()
    finally:
        engine.dispose()
