"""Authenticated S1C social HTTP surface with deliberately coarse views."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from autplay.application.social import SocialError, SocialService
from autplay.domain.auth import Principal
from autplay.runtime.http import ApiError

_PROFILE_STATISTICS_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Vary": "Authorization",
}


class _ProfileStatisticsRoute(APIRoute):
    """Apply the S2 private response policy to success, auth and validation outcomes."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        route_handler = super().get_route_handler()

        async def private_route_handler(request: Request) -> Response:
            try:
                response = await route_handler(request)
            except RequestValidationError as error:
                raise ApiError(
                    "request_validation_failed",
                    "The request is invalid.",
                    422,
                    headers=dict(_PROFILE_STATISTICS_PRIVATE_HEADERS),
                ) from error
            except ApiError as error:
                headers = dict(error.headers or {})
                headers.update(_PROFILE_STATISTICS_PRIVATE_HEADERS)
                raise ApiError(
                    error.code,
                    error.message,
                    error.status_code,
                    retryable=error.retryable,
                    headers=headers,
                    details=error.details,
                ) from error
            response.headers.update(_PROFILE_STATISTICS_PRIVATE_HEADERS)
            return response

        return private_route_handler


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContactCardBody(StrictBody):
    server_instance_id: UUID
    account_id: UUID
    display_name_hint: str = Field(min_length=1, max_length=120)
    issued_at: datetime
    expires_at: datetime
    signature_b64url: str = Field(pattern="^[A-Za-z0-9_-]{86}$")


class FriendshipCommandBody(StrictBody):
    operation_id: UUID
    action: str = Field(
        pattern="^(SEND_REQUEST|ACCEPT_REQUEST|DECLINE_REQUEST|CANCEL_REQUEST|REMOVE_FRIEND|BLOCK_USER|UNBLOCK_USER)$"
    )
    target_account_id: UUID | None = None
    contact_card: ContactCardBody | None = None


class PresenceSettingsBody(StrictBody):
    operation_id: UUID
    friend_presence_visibility_enabled: bool
    room_activity_sharing_enabled: bool
    invite_availability_enabled: bool


class PresenceHeartbeatBody(StrictBody):
    operation_id: UUID


class ProfileStatisticsSettingsBody(StrictBody):
    operation_id: UUID
    expected_revision: Annotated[StrictInt, Field(ge=0, le=9_223_372_036_854_775_807)]
    friends_can_view_statistics: StrictBool


class RoomInvitationBody(StrictBody):
    operation_id: UUID
    room_id: UUID
    target_account_id: UUID


class AcceptInvitationBody(StrictBody):
    operation_id: UUID


class CancelInvitationBody(StrictBody):
    operation_id: UUID


def create_social_router(
    service: SocialService, *, authenticated: Callable[[Request], None]
) -> APIRouter:
    router = APIRouter(prefix="/social", dependencies=[Depends(authenticated)])
    statistics_router = APIRouter(
        dependencies=[Depends(authenticated)], route_class=_ProfileStatisticsRoute
    )

    def p(request: Request) -> Principal:
        value = request.state.principal
        if not isinstance(value, Principal):
            raise RuntimeError("principal missing")
        return value

    def n() -> datetime:
        return datetime.now(UTC)

    def call(fn: Callable[[], object]) -> object:
        try:
            return fn()
        except SocialError as error:
            status = (
                429
                if error.code == "rate_limited"
                else (
                    404
                    if error.code
                    in {
                        "presence_private",
                        "profile_statistics_unavailable",
                        "room_invitation_unavailable",
                    }
                    else (401 if error.code == "auth_attention_required" else 409)
                )
            )
            raise ApiError(
                error.code, "Social operation is unavailable.", status, details=error.details
            ) from error
        except (ValueError, KeyError) as error:
            raise ApiError(
                "friend_request_unavailable", "Social request is invalid.", 422
            ) from error

    def private(response: Response) -> None:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

    @router.get("/contact-card")
    def contact_card(request: Request, response: Response) -> object:
        private(response)
        return call(lambda: service.contact_card(p(request), n()))

    @router.get("/snapshot")
    def snapshot(request: Request, response: Response) -> object:
        private(response)
        return call(lambda: service.snapshot(p(request), n()))

    @router.post("/friendships/commands")
    def command(request: Request, body: FriendshipCommandBody) -> object:
        return call(lambda: service.command(p(request), body.model_dump(mode="json"), n()))

    @router.get("/presence/settings")
    def settings_get(request: Request, response: Response) -> object:
        private(response)
        return call(lambda: service.get_settings(p(request), n()))

    @router.put("/presence/settings")
    def settings_put(request: Request, body: PresenceSettingsBody) -> object:
        return call(lambda: service.set_settings(p(request), body.model_dump(mode="json"), n()))

    @router.post("/presence/heartbeat", status_code=204)
    def heartbeat(request: Request, body: PresenceHeartbeatBody) -> None:
        call(lambda: service.heartbeat(p(request), body.operation_id, n()))

    @router.get("/friends/presence")
    def presences(request: Request, response: Response) -> object:
        private(response)
        return call(lambda: service.presence_page(p(request), n()))

    @router.get("/friends/{account_id}/presence")
    def presence(request: Request, account_id: UUID, response: Response) -> object:
        private(response)
        return call(lambda: service.presence(p(request), account_id, n()))

    @router.post("/room-invitations", status_code=201)
    def create_invitation(request: Request, body: RoomInvitationBody) -> object:
        return call(
            lambda: service.create_invitation(
                p(request), body.room_id, body.target_account_id, body.operation_id, n()
            )
        )

    @router.post("/room-invitations/{invitation_id}/accept")
    def accept_invitation(
        request: Request, invitation_id: UUID, body: AcceptInvitationBody
    ) -> object:
        return call(
            lambda: service.accept_invitation(p(request), invitation_id, body.operation_id, n())
        )

    @router.post("/room-invitations/{invitation_id}/cancel")
    def cancel_invitation(
        request: Request, invitation_id: UUID, body: CancelInvitationBody
    ) -> object:
        return call(
            lambda: service.cancel_invitation(p(request), invitation_id, body.operation_id, n())
        )

    @statistics_router.get("/profile-statistics/settings")
    def profile_statistics_settings_get(request: Request) -> object:
        return call(lambda: service.get_profile_statistics_settings(p(request), n()))

    @statistics_router.put("/profile-statistics/settings")
    def profile_statistics_settings_put(
        request: Request, body: ProfileStatisticsSettingsBody
    ) -> object:
        return call(
            lambda: service.set_profile_statistics_settings(
                p(request), body.model_dump(mode="json"), n()
            )
        )

    @statistics_router.get("/friends/{account_id}/profile-statistics")
    def friend_profile_statistics(request: Request, account_id: UUID) -> object:
        return call(lambda: service.friend_profile_statistics(p(request), account_id, n()))

    router.include_router(statistics_router)

    return router


__all__ = ("create_social_router",)
