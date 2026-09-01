"""P11 recommendation, home, replay and offline-pack HTTP contract tests."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import cast
from uuid import UUID, uuid4

from autplay.application.recommendations import baseline_pipeline_definition
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.recommendations import (
    CandidateContribution,
    HomeFeed,
    HomeSection,
    OfflinePack,
    RankedRecommendation,
    RecommendationNotFound,
    RecommendationQuery,
    RecommendationRequestTrace,
    RecommendationResponse,
    RecommendationSnapshotRef,
    RecommendationSurface,
    ReplayInputUnavailable,
)
from autplay.entrypoints.api import create_app
from autplay.runtime.settings import ApiSettings
from pydantic import SecretStr
from starlette.testclient import TestClient

SETTINGS = ApiSettings(
    database_url=SecretStr("postgresql+psycopg://runtime:runtime@127.0.0.1:1/autplay"),
    auth_signing_secret=SecretStr("runtime-test-signing-secret-at-least-32-bytes"),
    public_access_source_hmac_secret=SecretStr(
        "public-access-source-hmac-secret-at-least-32-bytes"
    ),
)
OWNER = Principal(uuid4(), uuid4(), uuid4(), AccountRole.OWNER)
NOW = datetime(2026, 8, 17, 12, tzinfo=UTC)


class Auth:
    def authenticate_access(self, token: str) -> Principal:
        if token != "good":
            from autplay.domain.auth import InvalidAccessTokenError

            raise InvalidAccessTokenError()
        return OWNER


class FakeRecommendations:
    def __init__(self, source_key: str = "preferences") -> None:
        self.source_key = source_key
        self.request_id = uuid4()
        self.replay_unavailable = False

    def recommend(
        self,
        query: RecommendationQuery,
        *,
        pipeline_key: str,
        pipeline_version: str | None,
    ) -> RecommendationResponse:
        if pipeline_key != "cpu-baseline" or pipeline_version is not None:
            raise ReplayInputUnavailable
        return self._response(query)

    def exact_replay(self, user_id: UUID, request_id: UUID) -> RecommendationResponse:
        if user_id != OWNER.user_id or request_id != self.request_id:
            raise RecommendationNotFound
        return self._response(
            RecommendationQuery(OWNER.user_id, RecommendationSurface.RECOMMENDATIONS)
        )

    def algorithmic_replay(self, user_id: UUID, request_id: UUID) -> RecommendationResponse:
        if self.replay_unavailable:
            raise ReplayInputUnavailable
        return self.exact_replay(user_id, request_id)

    def home(
        self,
        query: RecommendationQuery,
        *,
        pipeline_key: str,
        pipeline_version: str | None,
    ) -> HomeFeed:
        assert pipeline_key == "cpu-baseline" and pipeline_version is None
        response = self._response(query)
        return HomeFeed(
            self.request_id,
            (HomeSection("recommendations", "Recommendations", response.items),),
        )

    def offline_pack(
        self,
        query: RecommendationQuery,
        *,
        device_id: UUID,
        ttl: timedelta,
        pipeline_key: str,
        pipeline_version: str | None,
    ) -> OfflinePack:
        assert query.surface is RecommendationSurface.OFFLINE_PACK
        assert device_id == OWNER.device_id and ttl == timedelta(days=7)
        assert pipeline_key == "cpu-baseline" and pipeline_version is None
        payload = b'{"payload_version":1,"surface":"offline_pack"}'
        return OfflinePack(
            uuid4(),
            self.request_id,
            1,
            "RAW_JSON",
            payload,
            sha256(payload).hexdigest(),
            NOW,
            NOW + ttl,
        )

    def _response(self, query: RecommendationQuery) -> RecommendationResponse:
        pipeline = baseline_pipeline_definition()
        trace = RecommendationRequestTrace(
            self.request_id,
            query,
            pipeline,
            RecommendationSnapshotRef(uuid4(), "a" * 64, 0, 0, "b" * 64, "c" * 64),
            "d" * 64,
            {},
            NOW,
        )
        contribution = CandidateContribution(self.source_key, "1", 1, 0.75, {})
        item = RankedRecommendation(
            uuid4(), 1, 0.75, "LIKED_TRACK", ("LIKED_TRACK",), (contribution,), "artist", None
        )
        return RecommendationResponse(trace, (item,))


def _client(service: FakeRecommendations) -> TestClient:
    return TestClient(
        create_app(
            SETTINGS,
            auth_service=Auth(),  # type: ignore[arg-type]
            recommendation_service=service,
        )
    )


def test_recommendation_and_home_dtos_are_model_independent_and_lowercase_surface() -> None:
    service = FakeRecommendations()
    with _client(service) as client:
        recommendation = client.post(
            "/api/v1/recommendations",
            headers={"Authorization": "Bearer good"},
            json={"seed": 9, "limit": 10},
        )
        home = client.post(
            "/api/v1/home",
            headers={"Authorization": "Bearer good"},
            json={"seed": 9, "limit": 10},
        )

    body = recommendation.json()
    assert recommendation.status_code == 200
    assert recommendation.headers["cache-control"] == "no-store"
    assert body["surface"] == "recommendations"
    assert body["items"][0]["score_kind"] == "heuristic"
    assert not ({"tensor", "cuda", "semantic_id", "probability"} & set(body["items"][0]))
    assert home.status_code == 200
    assert home.json()["sections"][0]["items"][0]["source_rank"] == 1


def test_swapping_candidate_generator_preserves_public_dto_shape() -> None:
    bodies: list[dict[str, object]] = []
    for source in ("preferences", "exploration"):
        with _client(FakeRecommendations(source)) as client:
            response = client.post(
                "/api/v1/recommendations",
                headers={"Authorization": "Bearer good"},
                json={},
            )
        bodies.append(response.json())

    assert set(bodies[0]) == set(bodies[1])
    first_item = cast(list[dict[str, object]], bodies[0]["items"])
    second_item = cast(list[dict[str, object]], bodies[1]["items"])
    assert set(first_item[0]) == set(second_item[0])


def test_exact_algorithmic_replay_errors_and_offline_pack_integrity_contract() -> None:
    service = FakeRecommendations()
    with _client(service) as client:
        exact = client.get(
            f"/api/v1/recommendations/{service.request_id}",
            headers={"Authorization": "Bearer good"},
        )
        service.replay_unavailable = True
        replay = client.post(
            f"/api/v1/recommendations/{service.request_id}/replay",
            headers={"Authorization": "Bearer good"},
        )
        pack = client.post(
            "/api/v1/recommendation-packs",
            headers={"Authorization": "Bearer good"},
            json={},
        )
        unauthorized = client.post("/api/v1/recommendations", json={})

    assert exact.status_code == 200 and exact.json()["replay"] == "exact"
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "REPLAY_INPUT_UNAVAILABLE"
    pack_body = pack.json()
    payload = base64.b64decode(pack_body["payload_base64"])
    assert pack_body["payload_encoding"] == "RAW_JSON"
    assert sha256(payload).hexdigest() == pack_body["payload_sha256"]
    assert b'"surface":"offline_pack"' in payload
    assert unauthorized.status_code == 401


def test_unknown_pipeline_is_a_stable_client_error() -> None:
    with _client(FakeRecommendations()) as client:
        response = client.post(
            "/api/v1/recommendations",
            headers={"Authorization": "Bearer good"},
            json={"pipeline_key": "unknown"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "recommendation_pipeline_unavailable"
