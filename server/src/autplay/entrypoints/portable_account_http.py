"""Bounded, uncached portable-account challenges and proofs."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from autplay.application.portable_account import PortableAccountService
from autplay.application.portable_account_data import PortableAccountDataService
from autplay.domain.auth import Principal
from autplay.domain.profile_pairing import ProfilePairingError
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.runtime.http import ApiError

_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}
_ZERO_UUID = UUID(int=0)


def _pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in values:
        if key in result:
            raise ValueError("duplicate member")
        result[key] = value
    return result


def _error(code: str, status: int = 403) -> ApiError:
    if code in {
        "operation_conflict",
        "public_id_unavailable",
        "account_identity_conflict",
        "account_device_limit_reached",
        "account_limit_reached",
    }:
        status = 409
    if code == "rate_limited":
        status = 429
    return ApiError(
        code=code,
        message="The account operation could not be completed.",
        status_code=status,
        headers=dict(_HEADERS),
    )


def create_portable_account_router(
    service: PortableAccountService | None,
    *,
    authenticated: Callable[[Request], None],
    canonical_source: Callable[[Request], str | None] | None = None,
) -> APIRouter:
    router = APIRouter()

    def actor(request: Request) -> Principal:
        authenticated(request)
        value = getattr(request.state, "principal", None)
        if not isinstance(value, Principal):
            raise _error("account_authentication_failed")
        return value

    @router.get("/portable-accounts/current")
    def current(request: Request) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            principal = actor(request)
            return JSONResponse(
                {"account": service.document(principal), "public_id": service.public_id(principal)},
                headers=_HEADERS,
            )
        except ProfilePairingError as error:
            raise _error(error.code) from None

    @router.get("/portable-accounts/records")
    def records(
        request: Request,
        source: str = "records",
        after_time: str = "1970-01-01T00:00:00Z",
        after_id: UUID = _ZERO_UUID,
    ) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            if source not in {
                "records",
                "history",
                "preferences",
                "playlists",
                "playlist_entries",
                "tastes",
            }:
                raise ValueError("source")
            if len(after_time) > 40:
                raise ValueError("cursor")
            timestamp = datetime.fromisoformat(after_time)
            if timestamp.tzinfo is None or timestamp < datetime(1970, 1, 1, tzinfo=UTC):
                raise ValueError("cursor")
            result = PortableAccountDataService(service.sessions).get(
                actor(request), source, timestamp, after_id
            )
            return JSONResponse(result, headers=_HEADERS)
        except ProfilePairingError as error:
            raise _error(error.code) from None
        except ValueError:
            raise _error("account_request_invalid", 400) from None

    def process(kind: str, request: Request, body: dict[str, Any]) -> JSONResponse:
        assert service is not None
        source = (canonical_source(request) if canonical_source else None) or (
            request.client.host if request.client else "unknown"
        )
        try:
            if kind == "records":
                result = PortableAccountDataService(service.sessions).put(actor(request), body)
            elif kind == "login-challenge":
                login = body.get("public_id")
                if (
                    set(body) != {"public_id"}
                    or not isinstance(login, str)
                    or re.fullmatch(r"[a-z0-9_]{3,24}", login) is None
                ):
                    raise ValueError("login")
                result = service.challenge("LOGIN", source, login=login)
            elif kind in {"register-challenge", "claim-challenge", "rotation-challenge"}:
                if set(body) != {"account"} or not isinstance(body["account"], dict):
                    raise ValueError("account")
                result = service.challenge(
                    {
                        "register-challenge": "REGISTER",
                        "claim-challenge": "CLAIM",
                        "rotation-challenge": "ROTATE",
                    }[kind],
                    source,
                    document=body["account"],
                    actor=actor(request) if kind == "claim-challenge" else None,
                )
            elif kind == "reauth-challenge":
                if set(body) != {"action", "operation_id", "target_device_id"}:
                    raise ValueError("reauth")
                result = service.challenge(
                    "REAUTH",
                    source,
                    actor=actor(request),
                    action=str(body["action"]),
                    operation_id=UUID(str(body["operation_id"])),
                    target_device_id=UUID(str(body["target_device_id"]))
                    if body["target_device_id"]
                    else None,
                )
            elif kind == "complete":
                if (
                    set(body)
                    not in ({"request", "proof"}, {"request", "proof", "replacement_proof"})
                    or not isinstance(body["request"], dict)
                    or (not isinstance(body["proof"], str) or len(body["proof"]) > 96)
                ):
                    raise ValueError("proof")
                command = body["request"]
                if not isinstance(command.get("challenge"), dict):
                    raise ValueError("challenge")
                purpose = command["challenge"].get("purpose")
                expected = {"challenge", "account"}
                if purpose == "ROTATE":
                    expected.add("replacement_account")
                    if (
                        not isinstance(body.get("replacement_proof"), str)
                        or len(body["replacement_proof"]) > 96
                    ):
                        raise ValueError("replacement proof")
                elif "replacement_proof" in body:
                    raise ValueError("unexpected replacement proof")
                elif purpose not in {"REGISTER", "LOGIN", "CLAIM"}:
                    raise ValueError("purpose")
                if purpose in {"REGISTER", "LOGIN"}:
                    expected |= {
                        "device_public_key_spki_b64",
                        "device_name",
                        "app_version",
                        "next_refresh_token_sha256",
                        "binding_commit_id",
                    }
                    for key, maximum in (("device_name", 120), ("app_version", 32)):
                        if (
                            not isinstance(command.get(key), str)
                            or not 1 <= len(command[key]) <= maximum
                        ):
                            raise ValueError("device")
                    if (
                        not isinstance(command.get("next_refresh_token_sha256"), str)
                        or re.fullmatch(r"[0-9a-f]{64}", command["next_refresh_token_sha256"])
                        is None
                    ):
                        raise ValueError("refresh hash")
                    UUID(command["binding_commit_id"])
                if set(command) != expected:
                    raise ValueError("request members")
                result = service.complete(
                    command,
                    body["proof"],
                    actor=actor(request) if purpose == "CLAIM" else None,
                    replacement_proof=body.get("replacement_proof"),
                    source=source,
                )
            else:
                raise _error("not_found", 404)
            return JSONResponse(result, headers=_HEADERS)
        except ProfilePairingError as error:
            raise _error(error.code) from None
        except ResourceAdmissionError as error:
            raise _error(error.code) from None
        except ValueError, TypeError, KeyError, OverflowError:
            raise _error("account_request_invalid", 400) from None

    @router.post("/portable-accounts/{kind}")
    async def dispatch(kind: str, request: Request) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            if (
                request.headers.get("content-type", "").split(";", 1)[0].lower()
                != "application/json"
            ):
                raise ValueError("content type")
            payload = bytearray()
            async for chunk in request.stream():
                if len(payload) + len(chunk) > (524288 if kind == "records" else 16384):
                    raise ValueError("body bound")
                payload.extend(chunk)
            body = json.loads(bytes(payload).decode("utf-8"), object_pairs_hook=_pairs)
            if not isinstance(body, dict):
                raise ValueError("object required")
        except ValueError, UnicodeError, RecursionError:
            raise _error("account_request_invalid", 400) from None
        return await run_in_threadpool(process, kind, request, body)

    return router
