"""Authenticated, bounded Wave REST and ephemeral WebSocket endpoints."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.exc import NoResultFound

from autplay.domain.auth import InvalidAccessTokenError, Principal
from autplay.domain.vault import VaultError
from autplay.domain.wave import Availability, WaveConflict, WaveForbidden, WaveRoom
from autplay.runtime.http import ApiError
from autplay.runtime.metrics import RuntimeMetrics


class CreateBody(BaseModel):
    allow_user_ids: list[UUID] = Field(default_factory=list, max_length=7)


class JoinBody(BaseModel):
    room_code: str = Field(min_length=10, max_length=10)


class CommandBody(BaseModel):
    kind: str = Field(max_length=20)
    idempotency_key: str = Field(min_length=1, max_length=128)
    base_version: int = Field(ge=1)
    expected_sequence: int = Field(default=0, ge=0)
    recording_id: UUID | None = None


class AvailabilityBody(BaseModel):
    queue_entry_id: UUID
    recording_id: UUID
    queue_version: int = Field(ge=1)
    availability: Availability
    final_ready: bool = False


class ScheduleBody(BaseModel):
    recording_id: UUID
    accepted_rtts_ms: list[int] = Field(default_factory=list, min_length=1, max_length=7)
    uncertainty_ms: int = Field(default=0, ge=0, le=100)


class SourceBody(BaseModel):
    audio_variant_id: UUID


class TransferBody(BaseModel):
    target_device_id: UUID


class StartBody(BaseModel):
    queue_entry_id: UUID
    recording_id: UUID
    queue_version: int = Field(ge=1)
    expected_sequence: int = Field(ge=0)


class TimingBody(BaseModel):
    command_sequence: int = Field(ge=0)
    rtt_ms: int = Field(ge=0, le=1_000)
    offset_ms: int = Field(ge=-86_400_000, le=86_400_000)
    uncertainty_ms: int = Field(ge=0, le=100)
    command_lag_ms: int | None = Field(default=None, ge=0, le=60_000)
    start_skew_ms: int | None = Field(default=None, ge=-60_000, le=60_000)
    drift_ms: int | None = Field(default=None, ge=-60_000, le=60_000)


class AccessAuthenticator(Protocol):
    def authenticate_access(self, token: str) -> Principal: ...


class WaveBroadcaster:
    """Bounded in-process invalidation fanout; never a durable event log."""

    def __init__(self) -> None:
        self._subscribers: dict[UUID, set[asyncio.Queue[dict[str, object]]]] = defaultdict(set)

    def subscribe(self, room_id: UUID) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=8)
        self._subscribers[room_id].add(queue)
        return queue

    def unsubscribe(self, room_id: UUID, queue: asyncio.Queue[dict[str, object]]) -> None:
        self._subscribers[room_id].discard(queue)

    def publish(self, room_id: UUID, event: dict[str, object]) -> None:
        event = {"protocol_version": 1, **event}
        for queue in tuple(self._subscribers[room_id]):
            if queue.full():
                queue.get_nowait()
                queue.put_nowait({"type": "snapshot_required"})
                continue
            queue.put_nowait(event)


def create_wave_router(
    service: Any,
    *,
    authenticated: Callable[[Request], None],
    auth_service: AccessAuthenticator,
    broadcaster: WaveBroadcaster | None = None,
    source_lookup: Any | None = None,
    metrics: RuntimeMetrics | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/wave", dependencies=[Depends(authenticated)])
    live = broadcaster or WaveBroadcaster()

    def principal(request: Request) -> Principal:
        value = request.state.principal
        if not isinstance(value, Principal):
            raise RuntimeError("principal missing")
        return value

    def now() -> datetime:
        return datetime.now(UTC)

    def call(fn: Callable[[], object]) -> object:
        try:
            expiry = getattr(service, "expire_due", None)
            if expiry is not None:
                expiry(now())
            return fn()
        except KeyError as error:
            raise ApiError("wave_not_found", "Wave room was not found.", 404) from error
        except NoResultFound as error:
            raise ApiError("wave_not_found", "Wave room was not found.", 404) from error
        except WaveForbidden as error:
            raise ApiError("wave_forbidden", "Wave operation is not permitted.", 403) from error
        except WaveConflict as error:
            raise ApiError(
                "wave_conflict", "Wave state conflicts with this request.", 409
            ) from error
        except ValueError as error:
            raise ApiError("wave_invalid", "Wave request is invalid.", 422) from error

    def view(value: WaveRoom) -> dict[str, object]:
        result: dict[str, object] = {
            "protocol_version": 1,
            "room_id": str(value.room_id),
            "host_user_id": str(value.host_user_id),
            "room_epoch": str(value.room_epoch),
            "state": value.state,
            "playback_state": value.playback_state,
            "role": value.self_role,
            "queue_version": value.version,
            "latest_sequence": value.command_sequence,
            "sequence": value.command_sequence,
            "expires_at": value.expires_at.isoformat(),
            "queue": [
                {
                    "queue_entry_id": str(x.queue_entry_id),
                    "recording_id": str(x.recording_id),
                    "position": x.position,
                }
                for x in value.queue
            ],
            "preflight": {
                str(queue_entry_id): availability.value
                for queue_entry_id, availability in value.self_preflight.items()
            },
        }
        if value.code:
            result["room_code"] = value.code
        return result

    @router.post("/rooms")
    def create(request: Request, body: CreateBody) -> dict[str, object]:
        return view(service.create(principal(request), now(), tuple(body.allow_user_ids)))

    @router.post("/rooms/join")
    def join(request: Request, body: JoinBody) -> dict[str, object]:
        return view(
            cast(
                WaveRoom,
                call(lambda: service.join(body.room_code, principal(request), now())),
            )
        )

    @router.get("/rooms/{room_id}")
    @router.get("/rooms/{room_id}/snapshot")
    def snapshot(request: Request, room_id: UUID) -> dict[str, object]:
        return view(
            cast(WaveRoom, call(lambda: service.snapshot(room_id, principal(request), now())))
        )

    @router.post("/rooms/{room_id}/commands")
    def command(request: Request, room_id: UUID, body: CommandBody) -> object:
        result = call(
            lambda: service.command(
                room_id,
                principal(request),
                body.kind,
                body.idempotency_key,
                __import__("hashlib").sha256(body.model_dump_json().encode()).digest(),
                body.base_version,
                body.expected_sequence,
                body.recording_id,
                now(),
            )
        )
        live.publish(room_id, {"type": "invalidate", "room_id": str(room_id)})
        return result

    @router.post("/rooms/{room_id}/availability", status_code=204)
    def availability(request: Request, room_id: UUID, body: AvailabilityBody) -> None:
        call(
            lambda: service.preflight(
                room_id,
                principal(request),
                body.queue_entry_id,
                body.recording_id,
                body.queue_version,
                body.availability,
                body.final_ready,
                now(),
            )
        )

    @router.post("/rooms/{room_id}/leave", status_code=204)
    def leave(request: Request, room_id: UUID) -> None:
        call(lambda: service.leave(room_id, principal(request), now()))
        live.publish(room_id, {"type": "invalidate", "room_id": str(room_id)})

    @router.post("/rooms/{room_id}/close", status_code=204)
    def close(request: Request, room_id: UUID) -> None:
        call(lambda: service.close(room_id, principal(request), now()))
        live.publish(room_id, {"type": "invalidate", "room_id": str(room_id)})

    @router.post("/rooms/{room_id}/host-transfer", status_code=204)
    def transfer(request: Request, room_id: UUID, body: TransferBody) -> None:
        call(
            lambda: service.transfer_host(room_id, principal(request), body.target_device_id, now())
        )
        live.publish(room_id, {"type": "invalidate", "room_id": str(room_id)})

    @router.post("/rooms/{room_id}/start")
    def start(request: Request, room_id: UUID, body: StartBody) -> object:
        result = call(
            lambda: service.start(
                room_id,
                principal(request),
                body.queue_entry_id,
                body.recording_id,
                body.queue_version,
                body.expected_sequence,
                now(),
            )
        )
        if metrics is not None and isinstance(result, dict) and not result.get("started", False):
            metrics.increment_wave_failure("buffer")
        live.publish(room_id, {"type": "invalidate", "room_id": str(room_id)})
        return result

    @router.post("/rooms/{room_id}/timing", status_code=204)
    def timing(request: Request, room_id: UUID, body: TimingBody) -> None:
        call(
            lambda: service.timing(
                room_id,
                principal(request),
                body.command_sequence,
                body.rtt_ms,
                body.offset_ms,
                body.uncertainty_ms,
                now(),
                start_skew_ms=body.start_skew_ms,
                drift_ms=body.drift_ms,
            )
        )
        if metrics is not None:
            if body.command_lag_ms is not None:
                metrics.observe_wave_timing("command_lag", body.command_lag_ms)
            if body.start_skew_ms is not None:
                metrics.observe_wave_timing("start_skew", abs(body.start_skew_ms))
            if body.drift_ms is not None:
                metrics.observe_wave_timing("drift", abs(body.drift_ms))

    @router.post("/rooms/{room_id}/source")
    def source(request: Request, room_id: UUID, body: SourceBody) -> dict[str, bool]:
        # P06 resolver applies its normal owner/device ACL.  Failure is deliberately
        # indistinguishable from absent media and never grants a stream URL.
        try:
            service.snapshot(room_id, principal(request), now())
            if source_lookup is None:
                return {"vault_streamable": False}
            source_lookup.resolve(principal(request), body.audio_variant_id)
            return {"vault_streamable": True}
        except KeyError, NoResultFound, ValueError, VaultError, WaveForbidden:
            if metrics is not None:
                metrics.increment_wave_failure("buffer")
            return {"vault_streamable": False}

    @router.post("/clock")
    def clock() -> dict[str, object]:
        received = now()
        sent = now()
        return {
            "server_receive_epoch_ms": int(received.timestamp() * 1_000),
            "server_send_epoch_ms": int(sent.timestamp() * 1_000),
        }

    @router.websocket("/ws/{room_id}")
    @router.websocket("/rooms/{room_id}/events")
    async def ws(websocket: WebSocket, room_id: UUID) -> None:
        # Authorization is header-only: query credentials are deliberately ignored.
        raw = websocket.headers.get("authorization", "")
        scheme, _, token = raw.partition(" ")
        try:
            if scheme.lower() != "bearer" or not token:
                raise InvalidAccessTokenError()
            user = auth_service.authenticate_access(token)
            expiry = getattr(service, "expire_due", None)
            if expiry is not None:
                expiry(now())
            room = service.snapshot(room_id, user, now())
        except InvalidAccessTokenError, KeyError, NoResultFound, WaveForbidden:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        subscription = live.subscribe(room_id)
        try:
            # A hello cursor is advisory only.  Missing history means REST snapshot recovery.
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
            events = service.catch_up(room_id, user, after, now())
            if len(events) >= 100:
                await websocket.send_json({"protocol_version": 1, "type": "snapshot_required"})
            else:
                for event in events:
                    await websocket.send_json(
                        {
                            "protocol_version": 1,
                            "type": "event",
                            "epoch": str(room.room_epoch),
                            **event,
                        }
                    )
                await websocket.send_json(
                    {
                        "type": "hello",
                        "protocol_version": 1,
                        "room_id": str(room_id),
                        "room_epoch": str(room.room_epoch),
                        "latest_sequence": room.command_sequence,
                    }
                )
            while True:
                receive_task = asyncio.create_task(websocket.receive_json())
                event_task = asyncio.create_task(subscription.get())
                done, pending = await asyncio.wait(
                    {receive_task, event_task}, timeout=10, return_when=asyncio.FIRST_COMPLETED
                )
                for task in pending:
                    task.cancel()
                results: list[object | BaseException] = await asyncio.gather(
                    receive_task,
                    event_task,
                    return_exceptions=True,
                )
                receive_result, event_result = results
                if not done:
                    user = auth_service.authenticate_access(token)
                    service.snapshot(room_id, user, now())
                    await websocket.send_json(
                        {
                            "protocol_version": 1,
                            "type": "ping",
                            "server_time_ms": int(now().timestamp() * 1_000),
                        }
                    )
                    continue
                if event_task in done:
                    if isinstance(event_result, BaseException):
                        if isinstance(event_result, asyncio.CancelledError):
                            continue
                        raise event_result
                    await websocket.send_json(event_result)
                    continue
                if isinstance(receive_result, WebSocketDisconnect):
                    return
                if isinstance(receive_result, BaseException):
                    if isinstance(receive_result, asyncio.CancelledError):
                        continue
                    raise receive_result
                message = receive_result
                if not isinstance(message, dict) or message.get("type") != "ping":
                    await websocket.close(code=4400)
                    return
                user = auth_service.authenticate_access(token)
                service.snapshot(room_id, user, now())
                await websocket.send_json(
                    {
                        "protocol_version": 1,
                        "type": "pong",
                        "server_time": now().isoformat(),
                    }
                )
        except InvalidAccessTokenError, WaveForbidden, KeyError, NoResultFound:
            if metrics is not None:
                metrics.increment_wave_failure("rejoin")
            await websocket.close(code=4401)
        except WebSocketDisconnect:
            return
        except asyncio.CancelledError:
            return
        finally:
            live.unsubscribe(room_id, subscription)

    return router


__all__ = ("create_wave_router",)
