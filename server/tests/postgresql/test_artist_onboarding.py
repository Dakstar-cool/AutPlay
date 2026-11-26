from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4, uuid7

import pytest
from autplay.adapters.external_collections import CollectionReader
from autplay.adapters.postgresql.artist_onboarding import ArtistOnboarding
from autplay.adapters.postgresql.recommendations import SqlAlchemyRecommendationRuntime
from autplay.adapters.postgresql.shared_catalog import enable_bridge_catalog
from autplay.application.external_collections import ExternalCollections
from autplay.application.recommendations import (
    RecommendationService,
    StaticRecommendationVersionRegistry,
    baseline_pipeline_definition,
)
from autplay.domain.recommendations import RecommendationQuery, RecommendationSurface
from sqlalchemy import text

from .test_library_search import _track
from .test_music_library import owner
from .test_track_metadata import setup
from .test_track_metadata_acquisition import publish_audio


def test_first_wave_from_import_and_explicit_artist(database_url: str) -> None:
    engine, sessions, uploader, ref, _ = setup(database_url)
    listener = owner(sessions).user_id
    other = owner(sessions).user_id
    welcome = ArtistOnboarding(sessions)
    try:
        publish_audio(sessions, uploader, ref)
        with sessions.begin() as session:
            chosen_ref, _ = _track(session, uploader, title="New song", artist="Chosen")
        publish_audio(sessions, uploader, chosen_ref)
        assert welcome.pending(listener)
        assert welcome.browse(listener, None, "", []) == []
        with sessions.begin() as session:
            session.execute(text("UPDATE catalog.recording SET identity_status='ACTIVE'"))
            session.execute(
                text(
                    "UPDATE vault.recording_canonical_variant "
                    "SET policy_version='acquisition-verified-upload-v1'"
                )
            )
            assert enable_bridge_catalog(session) == 2

        def source(provider: str, path: str, token: str | None) -> dict[str, Any]:
            return {
                "result": {
                    "title": "Favorites",
                    "trackCount": 1,
                    "tracks": [
                        {
                            "track": {
                                "id": "512",
                                "title": "Familiar track",
                                "artists": [{"name": "Artist"}],
                                "durationMs": 180000,
                            }
                        }
                    ],
                }
            }

        importer = ExternalCollections(sessions, CollectionReader(source))
        op = uuid4()
        preview = importer.start(
            listener,
            op,
            "https://music.yandex.ru/users/test/playlists/8",
            favorites=True,
            new_copy=False,
        )
        importer.confirm(listener, op, preview["revision"])
        roots = welcome.browse(listener, None, "", [])
        assert roots[0]["track"] == "Familiar track"
        chosen = UUID(welcome.browse(listener, None, "Chosen", [])[0]["id"])
        assert welcome.browse(listener, chosen, "", [UUID(roots[0]["id"])]) == []
        with sessions.begin() as session:
            guest = session.scalar(
                text("""
                INSERT INTO catalog.artist(name,sort_name,normalized_name)
                VALUES ('Guest','Guest','guest') RETURNING artist_id
            """)
            )
            session.execute(
                text("""
                INSERT INTO catalog.artist_credit_name
                    (artist_credit_id,position,artist_id,credited_name,join_phrase,role)
                SELECT artist_credit_id,1,:guest,'Guest','','FEATURED'
                FROM catalog.artist_credit_name WHERE artist_id=:chosen
            """),
                {"guest": guest, "chosen": chosen},
            )
        assert [row["id"] for row in welcome.browse(listener, chosen, "", [])] == [str(guest)]
        assert welcome.browse(listener, chosen, "", [guest]) == []
        with pytest.raises(ValueError, match="artist_unavailable"):
            welcome.finish(listener, [uuid4()])
        welcome.finish(listener, [chosen])
        welcome.finish(listener, [])
        assert not welcome.pending(listener) and welcome.pending(other)
        runtime = SqlAlchemyRecommendationRuntime(sessions, snapshot_policy_version="2")
        service = RecommendationService(
            snapshots=runtime,
            traces=runtime,
            registry=StaticRecommendationVersionRegistry(baseline_pipeline_definition("2")),
            ids=uuid7,
            clock=lambda: datetime.now(UTC),
        )
        query = RecommendationQuery(
            listener, RecommendationSurface.RECOMMENDATIONS, limit=10, seed=123, exploration=0
        )
        result = service.recommend(query)
        weights = [
            next(
                c.provenance["starter_artist_weight"]
                for c in row.contributions
                if "starter_artist_weight" in c.provenance
            )
            for row in result.items
        ]
        assert weights == [5, 4]
        assert (
            service.algorithmic_replay(listener, result.request.recommendation_request_id).items
            == result.items
        )
        assert (
            service.recommend(
                RecommendationQuery(other, RecommendationSurface.RECOMMENDATIONS)
            ).items
            == ()
        )
        with sessions() as session:
            from autplay.application.sync import _bootstrap_projections

            handles = {
                state["recording_id"]
                for kind, _, _, state in _bootstrap_projections(
                    session, listener, include_editions=True
                )
                if kind == "USER_TRACK_REF" and state["resolution_status"] == "RESOLVED"
            }
            assert {str(item.recording_id) for item in result.items} <= handles
            assert (
                session.scalar(
                    text("SELECT count(*) FROM library.listening_event WHERE user_id=:owner"),
                    {"owner": listener},
                )
                == 0
            )
        welcome.finish(other, [])
        assert not welcome.pending(other)
        with sessions.begin() as session:
            session.execute(
                text(
                    "UPDATE importing.collection_import SET provider='spotify' "
                    "WHERE owner=:owner AND operation_id=:op"
                ),
                {"owner": listener, "op": op},
            )
        assert all(
            row["track"] != "Familiar track" for row in welcome.browse(listener, None, "", [])
        )
        assert len(service.recommend(query).items) == 1
    finally:
        engine.dispose()


def test_artist_welcome_http_uses_authenticated_account(database_url: str) -> None:
    from secrets import token_hex
    from unittest.mock import Mock

    from autplay.entrypoints.api import create_app
    from autplay.runtime.settings import ApiSettings
    from pydantic import SecretStr
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from starlette.testclient import TestClient

    engine = create_engine(database_url)
    auth = Mock(spec_set=["authenticate_access"])
    auth.authenticate_access.return_value = owner(sessionmaker(engine))
    engine.dispose()
    settings = ApiSettings(
        database_url=SecretStr(database_url),
        auth_signing_secret=SecretStr(token_hex(32)),
        public_access_source_hmac_secret=SecretStr(token_hex(32)),
    )
    app = create_app(settings, auth_service=auth)
    route = "/api/v1/artist-onboarding"
    with TestClient(app) as client:
        assert client.get(route).status_code == 401
        client.headers.update({"Authorization": "Bearer good"})
        assert client.get(route).json() == {"pending": True}
        assert client.post(route + "/browse", json={"query": ""}).json() == {"artists": []}
        assert (
            client.post(route + "/finish", json={"artists": [], "owner": str(uuid4())}).status_code
            == 422
        )
        assert client.post(route + "/finish", json={"artists": [str(uuid4())]}).status_code == 409
        assert client.post(route + "/finish", json={"artists": []}).json() == {"pending": False}
        assert client.get(route).json() == {"pending": False}
