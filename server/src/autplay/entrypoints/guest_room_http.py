"""S1D host issuance and separately authenticated guest Wave HTTP/WS surface."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict, Field

from autplay.application.guest_room import (
    DEFAULT_GUEST_TTL_SECONDS,
    GuestRoomError,
    GuestRoomService,
)
from autplay.domain.auth import Principal
from autplay.domain.wave import Availability, WaveRoom
from autplay.entrypoints.wave_http import WaveBroadcaster
from autplay.runtime.http import ApiError
from autplay.runtime.web_security import source_rate_key

NO_STORE_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}
GUEST_HEADER = "X-AutPlay-Guest-Capability"


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IssueGuestDocumentBody(StrictBody):
    operation_id: UUID
    room_id: UUID
    document_bearer: str = Field(min_length=43, max_length=43, repr=False)
    ttl_seconds: int = Field(default=DEFAULT_GUEST_TTL_SECONDS, ge=60, le=21_600)
    max_uses: int = Field(default=1, ge=1, le=8)


class OperationBody(StrictBody):
    operation_id: UUID


class RedeemGuestDocumentBody(StrictBody):
    operation_id: UUID
    invitation_id: UUID
    room_id: UUID
    document_bearer: str = Field(min_length=43, max_length=43, repr=False)
    session_bearer: str = Field(min_length=43, max_length=43, repr=False)
    display_name: str = Field(min_length=1, max_length=80)


class GuestAvailabilityBody(StrictBody):
    queue_entry_id: UUID
    recording_id: UUID
    queue_version: int = Field(ge=1)
    availability: Availability
    final_ready: bool = False


class GuestTimingBody(StrictBody):
    command_sequence: int = Field(ge=0)
    rtt_ms: int = Field(ge=0, le=1_000)
    offset_ms: int = Field(ge=-86_400_000, le=86_400_000)
    uncertainty_ms: int = Field(ge=0, le=100)
    start_skew_ms: int | None = Field(default=None, ge=-60_000, le=60_000)
    drift_ms: int | None = Field(default=None, ge=-60_000, le=60_000)


class GuestClockBody(StrictBody):
    room_id: UUID


def create_guest_room_router(
    service: GuestRoomService,
    *,
    authenticated: Callable[[Request], None],
    source_secret: bytes,
    broadcaster: WaveBroadcaster,
) -> APIRouter:
    """Build distinct host and guest routers without sharing account authentication."""
    if len(source_secret) < 32:
        raise ValueError("guest source HMAC secret must be at least 32 bytes")
    router = APIRouter()
    host = APIRouter(prefix="/social/guest-documents", dependencies=[Depends(authenticated)])
    guest = APIRouter(prefix="/wave/guest")

    def now() -> datetime:
        return datetime.now(UTC)

    def principal(request: Request) -> Principal:
        value = request.state.principal
        if not isinstance(value, Principal):
            raise RuntimeError("principal missing")
        return value

    def token(request: Request) -> str:
        values = request.headers.getlist(GUEST_HEADER)
        if len(values) != 1 or not values[0]:
            raise ApiError("guest_unavailable", "Guest access is unavailable.", 401)
        return values[0]

    def call(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except GuestRoomError as error:
            raise _api_error(error) from error

    @host.post("", status_code=201)
    def issue(
        request: Request, body: IssueGuestDocumentBody, response: Response
    ) -> dict[str, object]:
        response.headers.update(NO_STORE_HEADERS)
        return cast(
            dict[str, object],
            call(
                lambda: service.issue(
                    principal(request),
                    body.room_id,
                    body.operation_id,
                    body.document_bearer,
                    body.ttl_seconds,
                    body.max_uses,
                    now(),
                )
            ),
        )

    @host.post("/{invitation_id}/revoke")
    def revoke(
        request: Request,
        invitation_id: UUID,
        body: OperationBody,
        response: Response,
    ) -> dict[str, object]:
        response.headers.update(NO_STORE_HEADERS)
        result = cast(
            dict[str, object],
            call(
                lambda: service.revoke(principal(request), invitation_id, body.operation_id, now())
            ),
        )
        room_id = UUID(str(result["room_id"]))
        broadcaster.publish(room_id, {"type": "guest_access_changed", "room_id": str(room_id)})
        return result

    @guest.post("/redeem")
    def redeem(
        request: Request, body: RedeemGuestDocumentBody, response: Response
    ) -> dict[str, object]:
        response.headers.update(NO_STORE_HEADERS)
        source = None if request.client is None else request.client.host
        return cast(
            dict[str, object],
            call(
                lambda: service.redeem(
                    invitation_id=body.invitation_id,
                    room_id=body.room_id,
                    operation_id=body.operation_id,
                    document_bearer=body.document_bearer,
                    session_bearer=body.session_bearer,
                    display_name=body.display_name,
                    source_rate_key=source_rate_key(source_secret, source),
                    now=now(),
                )
            ),
        )

    @guest.get("/rooms/{room_id}/snapshot")
    def snapshot(request: Request, room_id: UUID, response: Response) -> dict[str, object]:
        response.headers.update(NO_STORE_HEADERS)
        room = cast(WaveRoom, call(lambda: service.snapshot(token(request), room_id, now())))
        return _room_view(room)

    @guest.post("/rooms/{room_id}/presence", status_code=204)
    def presence(request: Request, room_id: UUID, response: Response) -> None:
        response.headers.update(NO_STORE_HEADERS)
        call(lambda: service.presence(token(request), room_id, now()))

    @guest.post("/rooms/{room_id}/preflight", status_code=204)
    def preflight(
        request: Request,
        room_id: UUID,
        body: GuestAvailabilityBody,
        response: Response,
    ) -> None:
        response.headers.update(NO_STORE_HEADERS)
        call(
            lambda: service.preflight(
                token(request),
                room_id,
                body.queue_entry_id,
                body.recording_id,
                body.queue_version,
                body.availability,
                body.final_ready,
                now(),
            )
        )

    @guest.post("/rooms/{room_id}/timing", status_code=204)
    def timing(request: Request, room_id: UUID, body: GuestTimingBody, response: Response) -> None:
        response.headers.update(NO_STORE_HEADERS)
        call(
            lambda: service.timing(
                token(request),
                room_id,
                body.command_sequence,
                body.rtt_ms,
                body.offset_ms,
                body.uncertainty_ms,
                now(),
                start_skew_ms=body.start_skew_ms,
                drift_ms=body.drift_ms,
            )
        )

    @guest.post("/clock")
    def clock(request: Request, body: GuestClockBody, response: Response) -> dict[str, int]:
        response.headers.update(NO_STORE_HEADERS)
        call(lambda: service.authenticate(token(request), body.room_id, "ROOM_TIMING", now()))
        received = now()
        sent = now()
        return {
            "server_receive_epoch_ms": int(received.timestamp() * 1_000),
            "server_send_epoch_ms": int(sent.timestamp() * 1_000),
        }

    @guest.post("/rooms/{room_id}/leave")
    def leave(
        request: Request, room_id: UUID, body: OperationBody, response: Response
    ) -> dict[str, object]:
        response.headers.update(NO_STORE_HEADERS)
        result = cast(
            dict[str, object],
            call(lambda: service.leave(token(request), room_id, body.operation_id, now())),
        )
        broadcaster.publish(room_id, {"type": "guest_access_changed", "room_id": str(room_id)})
        return result

    @guest.websocket("/rooms/{room_id}/events")
    async def events(websocket: WebSocket, room_id: UUID) -> None:
        # The capability is header-only; path/query values are deliberately ignored.
        values = websocket.headers.getlist(GUEST_HEADER)
        if len(values) != 1 or not values[0]:
            await websocket.close(code=4401)
            return
        capability = values[0]
        try:
            principal_value = service.authenticate(capability, room_id, "ROOM_EVENTS", now())
            room = service.snapshot(capability, room_id, now())
        except GuestRoomError:
            await websocket.close(code=4401)
            return
        await websocket.accept(
            headers=[(key.encode(), value.encode()) for key, value in NO_STORE_HEADERS.items()]
        )
        subscription = broadcaster.subscribe(room_id)
        try:
            hello = await asyncio.wait_for(websocket.receive_json(), timeout=10)
            if not isinstance(hello, dict) or hello.get("type") != "hello":
                await websocket.close(code=4400)
                return
            after = hello.get("after_sequence", 0)
            if not isinstance(after, int) or after < 0:
                await websocket.close(code=4400)
                return
            hello_epoch = hello.get("room_epoch")
            if hello_epoch is not None and str(hello_epoch) != str(room.room_epoch):
                await websocket.send_json({"protocol_version": 1, "type": "snapshot_required"})
                return
            for event in service.catch_up(capability, room_id, after, now()):
                await websocket.send_json(
                    {"protocol_version": 1, "type": "event", "epoch": str(room.room_epoch), **event}
                )
            await websocket.send_json(
                {
                    "protocol_version": 1,
                    "type": "hello",
                    "room_id": str(room_id),
                    "room_epoch": str(room.room_epoch),
                    "latest_sequence": room.command_sequence,
                    "guest_session_id": str(principal_value.guest_session_id),
                }
            )
            while True:
                receive_task = asyncio.create_task(websocket.receive_json())
                event_task = asyncio.create_task(subscription.get())
                done, pending = await asyncio.wait(
                    {receive_task, event_task}, timeout=10, return_when=asyncio.FIRST_COMPLETED
                )
                # Revalidate before every delivery as well as every idle heartbeat.
                service.authenticate(capability, room_id, "ROOM_EVENTS", now())
                if not done:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    await websocket.send_json(
                        {
                            "protocol_version": 1,
                            "type": "ping",
                            "server_time_ms": int(now().timestamp() * 1_000),
                        }
                    )
                    continue
                if event_task in done:
                    receive_task.cancel()
                    await asyncio.gather(receive_task, return_exceptions=True)
                    event_result = event_task.result()
                    await websocket.send_json(event_result)
                    continue
                event_task.cancel()
                await asyncio.gather(event_task, return_exceptions=True)
                receive_result = receive_task.result()
                if not isinstance(receive_result, dict) or receive_result.get("type") != "ping":
                    await websocket.close(code=4400)
                    return
                await websocket.send_json(
                    {"protocol_version": 1, "type": "pong", "server_time": now().isoformat()}
                )
        except GuestRoomError:
            await websocket.close(code=4401)
        except WebSocketDisconnect, asyncio.CancelledError:
            return
        finally:
            broadcaster.unsubscribe(room_id, subscription)

    router.include_router(host)
    router.include_router(guest)
    return router


def _room_view(value: WaveRoom) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "room_id": str(value.room_id),
        "host_user_id": str(value.host_user_id),
        "room_epoch": str(value.room_epoch),
        "state": value.state,
        "playback_state": value.playback_state,
        "role": "GUEST",
        "queue_version": value.version,
        "latest_sequence": value.command_sequence,
        "sequence": value.command_sequence,
        "expires_at": value.expires_at.isoformat(),
        "media_boundary": "INDEPENDENT_DEVICE_AUTHORIZATION_ONLY",
        "queue": [
            {
                "queue_entry_id": str(item.queue_entry_id),
                "recording_id": str(item.recording_id),
                "position": item.position,
            }
            for item in value.queue
        ],
        "preflight": {
            str(queue_entry_id): availability.value
            for queue_entry_id, availability in value.self_preflight.items()
        },
    }


def _api_error(error: GuestRoomError) -> ApiError:
    code = error.code
    if code == "rate_limited":
        return ApiError(code, "Guest access is temporarily rate limited.", 429, retryable=True)
    if code == "operation_conflict":
        return ApiError(code, "The operation conflicts with an earlier request.", 409)
    if code == "room_full":
        return ApiError(code, "The Room is full.", 409)
    if code == "room_changed":
        return ApiError(code, "The Room has changed.", 409)
    if code == "guest_scope_denied":
        return ApiError(code, "The guest capability does not allow this action.", 403)
    if code == "guest_invalid":
        return ApiError(code, "The guest request is invalid.", 422)
    if code in {"guest_expired", "guest_revoked"}:
        return ApiError(code, "Guest access is no longer active.", 410)
    return ApiError("guest_unavailable", "Guest access is unavailable.", 404)


__all__ = ("GUEST_HEADER", "NO_STORE_HEADERS", "create_guest_room_router")
