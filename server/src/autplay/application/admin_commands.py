"""Application facade for the three accepted M6-C administrative mutations."""

from __future__ import annotations

from autplay.domain.admin_commands import AdminCommand
from autplay.ports.admin_commands import AdminCommandRepository


class AdminCommandService:
    def __init__(self, repository: AdminCommandRepository) -> None:
        self._repository = repository

    def cancel_enrollment_invitation(self, command: AdminCommand) -> dict[str, object]:
        return self._repository.execute(
            command,
            action="web.enrollment_invitation_cancelled",
            target_type="ENROLLMENT_INVITATION",
        )

    def revoke_android_device(self, command: AdminCommand) -> dict[str, object]:
        return self._repository.execute(
            command, action="web.android_device_revoked", target_type="DEVICE"
        )

    def revoke_android_session(self, command: AdminCommand) -> dict[str, object]:
        return self._repository.execute(
            command, action="web.android_session_revoked", target_type="USER_SESSION"
        )


__all__ = ("AdminCommandService",)
