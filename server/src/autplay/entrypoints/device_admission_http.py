"""Android-only S1B admission transport; bearer material is body/header-only."""

from __future__ import annotations

from datetime import datetime
from typing import Final
from uuid import UUID

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from autplay.application.profile_pairing import ProfilePairingService
from autplay.domain.profile_pairing import ProfilePairingError
from autplay.runtime.http import ApiError

_NO_STORE: Final = {"Cache-Control": "no-store", "Pragma": "no-cache"}


class AdmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    expected_server_instance_id: UUID
    expected_identity_epoch: int = Field(ge=1)
    expected_identity_thumbprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    client_nonce_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    api_major: int = Field(ge=1, le=1)
    device_key_thumbprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    device_public_key_jwk: dict[str, str]
    proof_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")
    nickname: str = Field(min_length=1, max_length=120)
    device_model_hint: str | None = Field(default=None, max_length=96)
    platform: str = Field(pattern="^ANDROID$")
    app_version: str = Field(min_length=1, max_length=32)
    requested_at: datetime


class AdmissionPoll(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    device_key_thumbprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    client_nonce_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    proof_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class AdmissionRecovery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    device_key_thumbprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_server_instance_id: UUID
    expected_identity_epoch: int = Field(ge=1)
    expected_identity_thumbprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    recovery_nonce_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    proof_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class AdmissionExchange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    request_id: UUID
    exchange_id: UUID
    binding_commit_id: UUID
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    poll_bearer_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_server_instance_id: UUID
    expected_identity_epoch: int = Field(ge=1)
    expected_identity_thumbprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_api_origin: str = Field(min_length=1, max_length=2048)
    expected_stream_origin: str = Field(min_length=1, max_length=2048)
    approved_account_id: UUID
    device_key_thumbprint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    device_public_key_jwk: dict[str, str]
    client_nonce_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    next_refresh_token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proof_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class TrustedChallengeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    challenge_request_id: UUID
    account_id: UUID
    expected_server_instance_id: UUID
    expected_identity_epoch: int = Field(ge=1)
    expected_identity_thumbprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    device_key_thumbprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    device_public_key_jwk: dict[str, str]
    client_nonce_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    proof_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class TrustedReenrollment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    challenge_id: UUID
    challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{22}$")
    exchange_id: UUID
    binding_commit_id: UUID
    expected_server_instance_id: UUID
    expected_identity_epoch: int = Field(ge=1)
    expected_identity_thumbprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_api_origin: str = Field(min_length=1, max_length=2048)
    expected_stream_origin: str = Field(min_length=1, max_length=2048)
    account_id: UUID
    device_key_thumbprint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    device_public_key_jwk: dict[str, str]
    next_refresh_token_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    client_nonce_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    proof_b64url: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


def create_device_admission_router(service: ProfilePairingService | None) -> APIRouter:
    router = APIRouter()

    @router.post("/social/admission-requests", status_code=202)
    def submit(body: AdmissionRequest, request: Request) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            source = None if request.client is None else request.client.host
            result, replayed = service.submit_device_admission(
                body.model_dump(mode="json", exclude_none=True), source=source
            )
        except ProfilePairingError as error:
            raise _profile_error(error) from error
        return JSONResponse(result, status_code=200 if replayed else 202, headers=_NO_STORE)

    @router.post("/social/admission-requests/{request_id}/poll")
    def poll(
        request_id: UUID,
        body: AdmissionPoll,
        x_autplay_admission_poll: str = Header(min_length=22, max_length=22),
    ) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            if request_id != body.request_id:
                raise _error("admission_request_unavailable", 400)
            result = service.poll_device_admission(
                body.model_dump(mode="json"), x_autplay_admission_poll
            )
        except ProfilePairingError as error:
            raise _profile_error(error) from error
        return JSONResponse(result, headers=_NO_STORE)

    @router.post("/social/admission-requests/{request_id}/recover")
    def recover(request_id: UUID, body: AdmissionRecovery) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            if request_id != body.request_id:
                raise _error("admission_request_unavailable", 400)
            result = service.recover_device_admission(body.model_dump(mode="json"))
        except ProfilePairingError as error:
            raise _profile_error(error) from error
        return JSONResponse(result, headers=_NO_STORE)

    @router.post("/social/admission-requests/{request_id}/exchange")
    def exchange(
        request_id: UUID,
        body: AdmissionExchange,
        x_autplay_admission_poll: str = Header(min_length=22, max_length=22),
    ) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            if request_id != body.request_id:
                raise _error("admission_request_unavailable", 400)
            result, replayed = service.exchange_device_admission(
                body.model_dump(mode="json"), x_autplay_admission_poll
            )
        except ProfilePairingError as error:
            raise _profile_error(error) from error
        return JSONResponse(result, status_code=200 if replayed else 201, headers=_NO_STORE)

    @router.post("/social/trusted-keys/re-enrollment/challenge", status_code=201)
    def trusted_challenge(body: TrustedChallengeRequest) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            result = service.request_trusted_reenrollment_challenge(body.model_dump(mode="json"))
        except ProfilePairingError as error:
            raise _profile_error(error) from error
        return JSONResponse(result, headers=_NO_STORE)

    @router.post("/social/trusted-keys/re-enrollment/exchange")
    def trusted_exchange(body: TrustedReenrollment) -> JSONResponse:
        if service is None:
            raise _error("capability_missing", 503)
        try:
            result, replayed = service.complete_trusted_reenrollment(body.model_dump(mode="json"))
        except ProfilePairingError as error:
            raise _profile_error(error) from error
        return JSONResponse(result, status_code=200 if replayed else 201, headers=_NO_STORE)

    return router


def _error(code: str, status: int) -> ApiError:
    return ApiError(
        code=code,
        message="The requested admission operation is unavailable.",
        status_code=status,
        headers=dict(_NO_STORE),
    )


def _profile_error(error: ProfilePairingError) -> ApiError:
    return _error(
        error.code,
        429
        if error.code.endswith("rate_limited")
        else 403
        if error.code == "unauthorized"
        else 400,
    )


__all__ = ("create_device_admission_router",)
