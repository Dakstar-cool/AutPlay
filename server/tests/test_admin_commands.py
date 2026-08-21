"""Focused application facade coverage for M6-C browser administration."""

from __future__ import annotations

from uuid import uuid4

import pytest
from autplay.application.admin_commands import AdminCommandService
from autplay.domain.admin_commands import AdminCommand
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor


class _Repository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def execute(self, command: AdminCommand, *, action: str, target_type: str) -> dict[str, object]:
        self.calls.append((action, target_type))
        return {"operation_id": str(command.operation_id), "outcome": "APPLIED"}


def _command() -> AdminCommand:
    return AdminCommand(
        WebActor(uuid4(), uuid4(), uuid4(), AccountRole.OWNER, 0), uuid4(), uuid4(), b"r" * 32
    )


@pytest.mark.parametrize(
    ("method", "expected"),
    [
        (
            "cancel_enrollment_invitation",
            ("web.enrollment_invitation_cancelled", "ENROLLMENT_INVITATION"),
        ),
        ("revoke_android_device", ("web.android_device_revoked", "DEVICE")),
        ("revoke_android_session", ("web.android_session_revoked", "USER_SESSION")),
    ],
)
def test_commands_route_only_to_fixed_actions(method: str, expected: tuple[str, str]) -> None:
    repository = _Repository()
    result = getattr(AdminCommandService(repository), method)(_command())

    assert repository.calls == [expected]
    assert result["outcome"] == "APPLIED"


def test_command_rejects_non_sha256_request_hash() -> None:
    actor = WebActor(uuid4(), uuid4(), uuid4(), AccountRole.ADMIN, 1)
    with pytest.raises(ValueError, match="32 bytes"):
        AdminCommand(actor, uuid4(), uuid4(), b"short")
