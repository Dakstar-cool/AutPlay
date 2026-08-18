"""Real PostgreSQL owner-boundary evidence for the P07 repository."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from autplay.adapters.postgresql.library_runtime import LibraryRepository
from autplay.adapters.postgresql.models import (
    DeviceRow,
    ListeningEventRow,
    RecommendationRequestRow,
    UserAccountRow,
)
from autplay.domain.auth import AccountRole, OwnedObjectNotFoundError, Principal
from autplay.domain.library import (
    AppendListeningEvent,
    CreateUnresolvedTrack,
    RecommendationAttribution,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


def _principal(session: Session, name: str) -> Principal:
    user_id, device_id, session_id = uuid4(), uuid4(), uuid4()
    session.add(UserAccountRow(user_id=user_id, display_name=name, role="USER", status="ACTIVE"))
    session.flush()
    session.add(
        DeviceRow(
            device_id=device_id,
            user_id=user_id,
            device_name=f"{name}-device",
            platform="ANDROID",
            app_version="p07",
        )
    )
    session.flush()
    return Principal(user_id, device_id, session_id, AccountRole.USER)


def test_library_commands_and_queries_mask_cross_user_rows(database_url: str) -> None:
    engine = create_engine(database_url)
    now = datetime.now(UTC)
    try:
        with Session(engine) as session:
            owner, other = _principal(session, "p07-owner"), _principal(session, "p07-other")
            repository = LibraryRepository(session)
            ref_id = repository.create_unresolved(
                owner, CreateUnresolvedTrack(uuid4(), "100%_literal", "artist"), now=now
            )
            entry_id = repository.add_library_entry(
                owner,
                library_entry_id=uuid4(),
                user_track_ref_id=ref_id,
                source="LOCAL",
                availability_status="LOCAL",
                now=now,
            )
            second_ref_id = repository.create_unresolved(
                owner, CreateUnresolvedTrack(uuid4(), "second", "artist"), now=now
            )
            repository.add_library_entry(
                owner,
                library_entry_id=uuid4(),
                user_track_ref_id=second_ref_id,
                source="LOCAL",
                availability_status="LOCAL",
                now=now,
            )
            first_page = repository.library_page(owner, limit=1, before=None)
            second_page = repository.library_page(
                owner,
                limit=1,
                before=first_page[0].added_at,
                before_id=first_page[0].library_entry_id,
            )
            assert len(second_page) == 1
            assert second_page[0].library_entry_id != first_page[0].library_entry_id
            matches = repository.search_library(owner, query="100%_", limit=10)
            assert matches[0].library_entry_id == entry_id
            with pytest.raises(OwnedObjectNotFoundError):
                repository.add_library_entry(
                    other,
                    library_entry_id=uuid4(),
                    user_track_ref_id=ref_id,
                    source="LOCAL",
                    availability_status="LOCAL",
                    now=now,
                )
            assert repository.library_page(other, limit=10, before=None) == []
            repository.remove_library_entry(owner, entry_id, base_version=1, now=now)
            repository.restore_library_entry(owner, entry_id, base_version=2, now=now)
            playlist_id = repository.create_playlist(
                owner, playlist_id=uuid4(), name="P07", description=None, now=now
            )
            repository.create_playlist(
                owner, playlist_id=uuid4(), name="P07 second", description=None, now=now
            )
            playlist_page = repository.playlists_page(owner, limit=1)
            next_playlist_page = repository.playlists_page(
                owner,
                limit=1,
                before=playlist_page[0].updated_at,
                before_id=playlist_page[0].playlist_id,
            )
            assert len(next_playlist_page) == 1
            assert next_playlist_page[0].playlist_id != playlist_page[0].playlist_id
            first = repository.add_playlist_entry(
                owner,
                playlist_entry_id=uuid4(),
                playlist_id=playlist_id,
                user_track_ref_id=ref_id,
                position_key="a",
                now=now,
            )
            second = repository.add_playlist_entry(
                owner,
                playlist_entry_id=uuid4(),
                playlist_id=playlist_id,
                user_track_ref_id=ref_id,
                position_key="b",
                now=now,
            )
            repository.move_playlist_entry(owner, second, position_key="c", base_version=1, now=now)
            repository.delete_playlist(owner, playlist_id, base_version=1, now=now)
            with pytest.raises(OwnedObjectNotFoundError):
                repository.remove_playlist_entry(other, first, base_version=1, now=now)
            cross_owner_request_id = uuid4()
            session.add(
                RecommendationRequestRow(
                    recommendation_request_id=cross_owner_request_id,
                    user_id=other.user_id,
                    model_bundle_version="p07",
                    candidate_policy_version="p07",
                    filter_policy_version="p07",
                    reranker_version="p07",
                    seed=7,
                )
            )
            session.flush()
            with pytest.raises(OwnedObjectNotFoundError):
                repository.append_listening(
                    owner,
                    AppendListeningEvent(
                        uuid4(),
                        ref_id,
                        now,
                        1_000,
                        event_origin="RECOMMENDED",
                        attribution=RecommendationAttribution(
                            cross_owner_request_id, uuid4(), 1, "p07", "library"
                        ),
                    ),
                    now=now,
                )
            recommendation_request_id = uuid4()
            session.add(
                RecommendationRequestRow(
                    recommendation_request_id=recommendation_request_id,
                    user_id=owner.user_id,
                    model_bundle_version="p07",
                    candidate_policy_version="p07",
                    filter_policy_version="p07",
                    reranker_version="p07",
                    seed=7,
                )
            )
            session.flush()
            attribution = RecommendationAttribution(
                recommendation_request_id, uuid4(), 1, "p07", "library"
            )
            listening_id = repository.append_listening(
                owner,
                AppendListeningEvent(
                    uuid4(),
                    ref_id,
                    now,
                    1_000,
                    event_origin="RECOMMENDED",
                    attribution=attribution,
                ),
                now=now,
            )
            listening = session.get(ListeningEventRow, listening_id)
            assert listening is not None
            assert listening.recommendation_request_id == attribution.recommendation_request_id
            session.commit()
    finally:
        engine.dispose()
