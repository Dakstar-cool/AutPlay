"""M5B pure crypto, canonicalization, and recovery-output evidence."""

from __future__ import annotations

import hashlib
import json
from io import StringIO
from typing import Any, cast
from uuid import uuid4

import pytest
from autplay.domain.profile_pairing import (
    ProfilePairingError,
    canonical_sha256,
    public_key_thumbprint,
    public_spki,
    sign_p1363,
    verify_p1363,
)
from autplay.entrypoints.admin import run_recovery_invitation
from cryptography.hazmat.primitives.asymmetric import ec


def test_canonical_hash_is_rfc8785_and_omits_only_contract_fields() -> None:
    left = {
        "z": [3, 2, 1],
        "number": 1,
        "request_sha256": "ignored",
        "device_signature_b64url": "x",
    }
    right = {
        "number": 1.0,
        "z": [3, 2, 1],
        "device_signature_b64url": "different",
        "request_sha256": "other",
    }

    omitted = frozenset({"request_sha256", "device_signature_b64url"})
    assert canonical_sha256(left, omit=omitted) == canonical_sha256(right, omit=omitted)
    assert canonical_sha256(left) != canonical_sha256(right)


def test_p256_proof_rejects_wrong_length_non_p256_and_domain_substitution() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    digest = hashlib.sha256(b"M5B exact request").digest()
    signature = sign_p1363(key, "AutPlay enrollment exchange v1\n", digest)
    spki = public_spki(key)

    verify_p1363(spki, "AutPlay enrollment exchange v1\n", digest, signature)
    assert public_key_thumbprint(spki) == hashlib.sha256(spki).digest()
    with pytest.raises(ProfilePairingError, match="enrollment_invitation_unavailable"):
        verify_p1363(spki, "AutPlay session rotation v1\n", digest, signature)
    with pytest.raises(ProfilePairingError, match="enrollment_invitation_unavailable"):
        verify_p1363(spki, "AutPlay enrollment exchange v1\n", digest, "A" * 85)
    wrong_curve = ec.generate_private_key(ec.SECP384R1())
    with pytest.raises(ProfilePairingError, match="enrollment_invitation_unavailable"):
        verify_p1363(
            public_spki(wrong_curve), "AutPlay enrollment exchange v1\n", digest, signature
        )


class _RecoveryService:
    def issue_recovery_invitation(
        self, user_id: object, operation_id: object, ttl: int
    ) -> dict[str, object]:
        return {
            "invitation_id": str(operation_id),
            "user_id": str(user_id),
            "expires_in_seconds": ttl,
            "invitation_secret": "recoverable-secret-must-not-leak-to-errors",
            "api_origin": "https://private.example.invalid",
        }


def test_recovery_cli_writes_secret_only_to_selected_stdout_and_never_error_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    user_id, operation_id = uuid4(), uuid4()
    stdout, stderr = StringIO(), StringIO()

    assert (
        run_recovery_invitation(
            cast(Any, _RecoveryService()),
            (
                "issue-recovery-invitation",
                "--user-id",
                str(user_id),
                "--operation-id",
                str(operation_id),
                "--expires-in-seconds",
                "60",
            ),
            stdout=stdout,
            stderr=stderr,
        )
        == 0
    )
    document = json.loads(stdout.getvalue())
    assert document["invitation_secret"] == "recoverable-secret-must-not-leak-to-errors"
    assert stderr.getvalue() == ""
    assert "recoverable-secret" not in caplog.text

    failed_stdout, failed_stderr = StringIO(), StringIO()
    assert (
        run_recovery_invitation(
            cast(Any, _RecoveryService()),
            (
                "issue-recovery-invitation",
                "--user-id",
                "not-a-uuid",
                "--operation-id",
                str(operation_id),
            ),
            stdout=failed_stdout,
            stderr=failed_stderr,
        )
        == 4
    )
    assert failed_stdout.getvalue() == ""
    assert "recoverable-secret" not in failed_stderr.getvalue() + caplog.text
