"""M6 presentation adapter for S1B; no key or locator leaves the service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar, cast
from uuid import UUID

from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.auth import Principal
from autplay.domain.profile_pairing import ProfilePairingError
from autplay.domain.web_admin import WebActor, WebAdminError
from autplay.entrypoints.admin_web_http import DeviceAdmissionReview, TrustedDeviceWebItem

_T = TypeVar("_T")


class DeviceAdmissionWebAdapter:
    """Adapt the exact M6 actor/session boundary to S1B application commands."""

    def __init__(self, service: ProfilePairingService) -> None:
        self._service = service

    def resolve_review_locator(
        self, actor: WebActor, locator: str, operation_id: UUID, request_sha256: bytes
    ) -> None:
        self._call(
            lambda: self._service.bind_device_admission_review(
                actor=actor,
                web_session_id=actor.web_session_id,
                locator=locator,
                operation_id=operation_id,
                request_sha256=request_sha256,
            )
        )

    def review(self, actor: WebActor) -> DeviceAdmissionReview:
        data = self._call(
            lambda: self._service.reviewed_device_admission(
                actor=actor, web_session_id=actor.web_session_id
            )
        )
        sas = str(data["sas_decimal_12"])
        return DeviceAdmissionReview(
            request_id=UUID(str(data["request_id"])),
            device_label=str(data["nickname"]),
            platform=str(data["platform"]),
            app_version=str(data["app_version"]),
            device_model_hint=(
                str(data["device_model_hint"])
                if data.get("device_model_hint") is not None
                else None
            ),
            api_major=int(str(data["api_major"])),
            requested_at=datetime.fromisoformat(str(data["requested_at"]).replace("Z", "+00:00")),
            expires_at=datetime.fromisoformat(str(data["expires_at"]).replace("Z", "+00:00")),
            sas_3x4=(sas[:4], sas[4:8], sas[8:]),
        )

    def decide_review(
        self,
        actor: WebActor,
        request_id: UUID,
        action: str,
        operation_id: UUID,
        request_sha256: bytes,
    ) -> None:
        self._call(
            lambda: self._service.decide_device_admission(
                actor,
                request_id,
                action,
                operation_id,
                request_sha256,
                web_session_id=actor.web_session_id,
            )
        )

    def trusted_devices(self, actor: WebActor) -> tuple[TrustedDeviceWebItem, ...]:
        data = self._call(lambda: self._service.list_trusted_device_keys(actor))
        rows = cast(list[dict[str, Any]], data["trusted_keys"])
        return tuple(
            TrustedDeviceWebItem(
                UUID(str(row["key_reference"])),
                str(row["device_label"]),
                str(row["platform"]),
                "REMOVED" if row["removed_at"] else "ACTIVE",
                int(row["active_session_count"]),
            )
            for row in rows
        )

    def manage_trusted_device(
        self,
        actor: WebActor,
        key_reference: UUID,
        action: str,
        operation_id: UUID,
        request_sha256: bytes,
    ) -> None:
        self._call(
            lambda: self._service.manage_trusted_key_reference(
                principal=actor,
                key_reference=key_reference,
                action=action,
                operation_id=operation_id,
                request_sha256=request_sha256,
            )
        )

    @staticmethod
    def _principal(actor: WebActor) -> Principal:
        return Principal(actor.user_id, UUID(int=0), UUID(int=0), actor.role)

    @staticmethod
    def _call(command: Callable[[], _T]) -> _T:
        try:
            return command()
        except ProfilePairingError as error:
            raise WebAdminError(error.code) from error


__all__ = ("DeviceAdmissionWebAdapter",)
