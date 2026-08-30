"""Bearer-authenticated Android control surface for A1C discovery automation."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from autplay.application.discovery_automation import (
    DISCOVERY_CONTRACT_VERSION,
    CandidateActionResult,
    DiscoveryAutomationError,
    DiscoveryAutomationService,
    DiscoveryRunView,
    PolicyMutation,
    PolicyMutationResult,
    PolicyView,
    ReleaseCandidateView,
)
from autplay.domain.auth import Principal
from autplay.runtime.http import ApiError

_PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store, max-age=0",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "Vary": "Authorization",
}


class _PrivateAutomationRoute(APIRoute):
    """Apply private response headers to success, auth and validation outcomes."""

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
                    headers=dict(_PRIVATE_HEADERS),
                ) from error
            except ApiError as error:
                headers = dict(error.headers or {})
                headers.update(_PRIVATE_HEADERS)
                raise ApiError(
                    error.code,
                    error.message,
                    error.status_code,
                    retryable=error.retryable,
                    headers=headers,
                    details=error.details,
                ) from error
            response.headers.update(_PRIVATE_HEADERS)
            return response

        return private_route_handler


class _StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _StartDiscovery(_StrictBody):
    contract_version: Literal["release-discovery-v1"]
    schema_version: Literal[1]
    operation_id: UUID
    action: Literal["START_DISCOVERY"]
    policy_id: UUID


class _CandidateCommand(_StrictBody):
    contract_version: Literal["release-discovery-v1"]
    schema_version: Literal[1]
    operation_id: UUID
    action: Literal["SELECT_CANDIDATE", "RETRY_CANDIDATE", "IGNORE_CANDIDATE"]
    candidate_id: UUID


class _SetArtistPolicy(_StrictBody):
    contract_version: Literal["release-discovery-v1"]
    schema_version: Literal[1]
    operation_id: UUID
    action: Literal["SET_ARTIST_POLICY"]
    canonical_artist_id: UUID
    provider_artist_id: str = Field(pattern="^[0-9]{1,20}$")
    discovery_mode: Literal["DISABLED", "MANUAL_ONLY", "SCHEDULED"]
    import_mode: Literal["REVIEW_REQUIRED", "AUTO_IMPORT"]
    automation_enabled: StrictBool
    expected_policy_revision: Annotated[StrictInt, Field(ge=1)] | None
    consequence_confirmation: str | None = Field(max_length=80)


AutomationCommand = Annotated[
    _StartDiscovery | _CandidateCommand | _SetArtistPolicy,
    Field(discriminator="action"),
]


def create_discovery_automation_api_router(
    service: DiscoveryAutomationService,
    *,
    authenticated: Callable[[Request], None],
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> APIRouter:
    """Expose bounded owner projections and the frozen A1C command vocabulary."""

    router = APIRouter(
        prefix="/discovery/automation",
        dependencies=[Depends(authenticated)],
        route_class=_PrivateAutomationRoute,
    )

    def principal(request: Request) -> Principal:
        value = request.state.principal
        if not isinstance(value, Principal):
            raise RuntimeError("authenticated request is missing its principal")
        return value

    def private(response: Response) -> None:
        response.headers.update(_PRIVATE_HEADERS)

    def call(operation: Callable[[], object]) -> object:
        try:
            return operation()
        except DiscoveryAutomationError as error:
            raise _api_error(error) from error
        except ValueError as error:
            raise ApiError(
                "discovery_automation_invalid",
                "The discovery automation request is invalid.",
                422,
                headers=dict(_PRIVATE_HEADERS),
            ) from error

    @router.get("/snapshot")
    def snapshot(request: Request, response: Response) -> dict[str, object]:
        private(response)
        actor = principal(request)
        return {
            "contract_version": DISCOVERY_CONTRACT_VERSION,
            "schema_version": 1,
            "policies": [_policy_view(value) for value in service.policies(actor, limit=100)],
            "runs": [_run_view(value) for value in service.runs(actor, limit=50)],
        }

    @router.get("/runs/{run_id}/candidates")
    def candidates(
        request: Request,
        run_id: UUID,
        response: Response,
    ) -> dict[str, object]:
        private(response)
        values = call(lambda: service.candidates(principal(request), run_id, limit=50))
        assert isinstance(values, tuple)
        return {
            "contract_version": DISCOVERY_CONTRACT_VERSION,
            "schema_version": 1,
            "run_id": str(run_id),
            "candidates": [_candidate_view(value) for value in values],
        }

    @router.post("/commands")
    def command(
        request: Request,
        body: AutomationCommand,
        response: Response,
    ) -> dict[str, object]:
        private(response)
        actor = principal(request)
        if isinstance(body, _SetArtistPolicy):
            result = call(
                lambda: service.set_policy(
                    actor,
                    PolicyMutation(
                        canonical_artist_id=body.canonical_artist_id,
                        provider_artist_id=body.provider_artist_id,
                        discovery_mode=body.discovery_mode,
                        import_mode=body.import_mode,
                        automation_enabled=body.automation_enabled,
                        expected_revision=body.expected_policy_revision,
                        operation_id=body.operation_id,
                        confirmation_code=body.consequence_confirmation,
                    ),
                    now=now(),
                )
            )
            assert isinstance(result, PolicyMutationResult)
            return _receipt(body.action, result.replayed, policy=_policy_view(result.policy))
        if isinstance(body, _StartDiscovery):
            result = call(
                lambda: service.run_now(actor, body.policy_id, body.operation_id, now=now())
            )
            assert isinstance(result, DiscoveryRunView)
            return _receipt(body.action, None, run=_run_view(result))
        action = {
            "SELECT_CANDIDATE": service.select_candidate,
            "RETRY_CANDIDATE": service.retry_candidate,
            "IGNORE_CANDIDATE": service.ignore_candidate,
        }[body.action]
        result = call(lambda: action(actor, body.candidate_id, body.operation_id, now=now()))
        assert isinstance(result, CandidateActionResult)
        return _receipt(body.action, result.replayed, candidate=_candidate_receipt(result))

    return router


def _receipt(action: str, replayed: bool | None, **projection: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "contract_version": DISCOVERY_CONTRACT_VERSION,
        "schema_version": 1,
        "action": action,
        **projection,
    }
    if replayed is not None:
        receipt["replayed"] = replayed
    return receipt


def _policy_view(value: PolicyView) -> dict[str, object]:
    return {
        "policy_id": str(value.policy_id),
        "canonical_artist_id": str(value.canonical_artist_id),
        "provider_artist_id": value.provider_artist_id,
        "discovery_mode": value.discovery_mode,
        "import_mode": value.import_mode,
        "automation_enabled": value.automation_enabled,
        "revision": value.revision,
        "last_checked_at": _timestamp(value.last_checked_at),
        "next_eligible_at": _timestamp(value.next_eligible_at),
    }


def _run_view(value: DiscoveryRunView) -> dict[str, object]:
    return {
        "run_id": str(value.run_id),
        "policy_id": str(value.policy_id),
        "policy_revision": value.policy_revision,
        "state": value.state,
        "observed_count": value.observed_count,
        "selected_count": value.selected_count,
        "page_count": value.page_count,
        "created_at": _timestamp(value.created_at),
        "completed_at": _timestamp(value.completed_at),
        "error_code": value.error_code,
    }


def _candidate_view(value: ReleaseCandidateView) -> dict[str, object]:
    return {
        "candidate_id": str(value.candidate_id),
        "run_id": str(value.run_id),
        "title": value.title,
        "artist": value.artist,
        "album": value.album,
        "released_at": _timestamp(value.released_at),
        "disposition": value.disposition,
        "acquisition_state": value.acquisition_state,
        "selected_automatically": value.selected_automatically,
    }


def _candidate_receipt(value: CandidateActionResult) -> dict[str, object]:
    return {
        "candidate_id": str(value.candidate_id),
        "disposition": value.disposition,
        "acquisition_state": value.acquisition_state,
    }


def _timestamp(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _api_error(error: DiscoveryAutomationError) -> ApiError:
    code = error.code
    if code in {
        "discovery_target_not_found",
        "discovery_policy_not_found",
        "discovery_run_not_found",
    }:
        return ApiError(
            code,
            "The discovery automation target was not found.",
            404,
            headers=dict(_PRIVATE_HEADERS),
        )
    if code in {"discovery_adapter_unavailable", "source_authorization_unavailable"}:
        return ApiError(
            code,
            "Discovery automation is unavailable.",
            503,
            retryable=True,
            headers=dict(_PRIVATE_HEADERS),
        )
    return ApiError(
        code,
        "The discovery automation command could not be applied.",
        409,
        headers=dict(_PRIVATE_HEADERS),
    )


__all__ = ("AutomationCommand", "create_discovery_automation_api_router")
