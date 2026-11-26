"""Capture and replay likes using the catalog's release and recording credits."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from autplay.adapters.postgresql.models import (
    ArtistCreditNameRow,
    ArtistCreditRow,
    ArtistRow,
    LibraryEntryRow,
    RecordingRow,
    ReleaseGroupRow,
    UserAccountRow,
    UserTrackPreferenceRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.recommendation_credits import credit_acts
from autplay.adapters.postgresql.recommendations import SqlAlchemyRecommendationRuntime
from autplay.application.music_library import MusicLibraryService
from autplay.domain.recommendations import RecommendationQuery, RecommendationSurface
from autplay.entrypoints.composition import build_recommendation_service
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def _credit(session: Session, *members: tuple[UUID, str]) -> UUID:
    credit = ArtistCreditRow(display_name="Shared label", normalized_name="shared label")
    session.add(credit)
    session.flush()
    for index, (artist, role) in enumerate(members):
        session.add(
            ArtistCreditNameRow(
                artist_credit_id=credit.artist_credit_id,
                position=index,
                artist_id=artist,
                credited_name="Shared label",
                role=role,
            )
        )
    return credit.artist_credit_id


def _recording(session: Session, credit: UUID) -> UUID:
    row = RecordingRow(
        artist_credit_id=credit, title="Song", normalized_title="song", identity_status="ACTIVE"
    )
    session.add(row)
    session.flush()
    return row.recording_id


def test_catalog_group_guest_billing_and_unresolved_names(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            group, guest, uncertain = (uuid4() for _ in range(3))
            for artist, label, status in (
                (group, "A & B", "ACTIVE"),
                (guest, "C", "ACTIVE"),
                (uncertain, "C", "PROVISIONAL"),
            ):
                session.add(
                    ArtistRow(
                        artist_id=artist,
                        name=label,
                        sort_name=label,
                        normalized_name=label.lower(),
                        identity_status=status,
                        artist_type="GROUP" if artist == group else "PERSON",
                    )
                )
            session.flush()
            fit = _credit(session, (group, "PRIMARY"), (guest, "FEATURED"))
            partial = _credit(session, (group, "PRIMARY"), (uncertain, "FEATURED"))
            unlisted = _credit(session)
            for title in ("First", "Second"):
                session.add(
                    ReleaseGroupRow(
                        artist_credit_id=fit,
                        title=title,
                        normalized_title=title.lower(),
                        primary_type="SINGLE",
                    )
                )
            session.flush()
            assert credit_acts(session, [str(fit), str(partial), str(unlisted)]) == {
                str(fit): (str(group), str(guest))
            }
    finally:
        engine.dispose()


def test_duo_evidence_across_catalog_and_frozen_like_replay(database_url: str) -> None:
    engine = create_engine(database_url)
    sessions = sessionmaker(engine)
    owner = uuid4()
    a, b, guest = (uuid4() for _ in range(3))
    try:
        with sessions.begin() as session:
            session.add(UserAccountRow(user_id=owner, display_name="Listener", role="USER"))
            for artist in (a, b, guest):
                session.add(
                    ArtistRow(
                        artist_id=artist, name="Same", normalized_name="same", sort_name="same"
                    )
                )
            session.flush()
            duo = _credit(session, (a, "PRIMARY"), (b, "PRIMARY"))
            reversed_duo = _credit(session, (b, "PRIMARY"), (a, "PRIMARY"))
            fit = _credit(session, (a, "PRIMARY"), (b, "PRIMARY"), (guest, "FEATURED"))
            solo = _credit(session, (guest, "PRIMARY"), (guest, "PRIMARY"))
            group = ReleaseGroupRow(
                artist_credit_id=duo, title="One", normalized_title="one", primary_type="EP"
            )
            session.add(group)
            for credit in (fit, reversed_duo, solo):
                ref = UserTrackRefRow(user_id=owner, raw_title="Song", raw_artist="File artist")
                session.add(ref)
                session.flush()
                session.add(
                    LibraryEntryRow(
                        user_id=owner,
                        user_track_ref_id=ref.user_track_ref_id,
                        source="SEARCH",
                        availability_status="LOCAL",
                    )
                )
                session.flush()
                recording_id = MusicLibraryService.prepare_in_transaction(
                    session, owner, ref.user_track_ref_id
                )
                recording = session.get(RecordingRow, recording_id)
                assert recording is not None
                recording.artist_credit_id = credit
                recording.identity_status = "ACTIVE"
                if credit == fit:
                    session.add(
                        UserTrackPreferenceRow(
                            user_track_ref_id=ref.user_track_ref_id, preference="LIKED"
                        )
                    )

        runtime = SqlAlchemyRecommendationRuntime(sessions, snapshot_policy_version="3")

        def acts() -> dict[str, tuple[str, ...]]:
            snapshot = runtime.capture(owner, retained_until=datetime.now(UTC) + timedelta(days=1))
            return {t.artist_key: t.artist_acts for t in snapshot.tracks}

        assert acts()[str(fit)] == (str(a), str(b), str(guest))
        with sessions.begin() as session:
            session.add(
                ReleaseGroupRow(
                    artist_credit_id=reversed_duo,
                    title="Two",
                    normalized_title="two",
                    primary_type="ALBUM",
                )
            )
        current = acts()
        assert len(current[str(reversed_duo)]) == 1
        assert current[str(fit)] == (*current[str(reversed_duo)], str(guest))
        assert current[str(solo)] == (str(guest),)

        service = build_recommendation_service(engine)
        query = RecommendationQuery(owner, RecommendationSurface.RECOMMENDATIONS, exploration=0)
        response = service.recommend(query)
        assert response.request.pipeline.version == "3"
        assert [item.artist_key for item in response.items] == list(
            map(str, (fit, reversed_duo, solo))
        )
        weights = {
            item.artist_key: next(
                c.provenance["artist_like_weight"]
                for c in item.contributions
                if c.source_key == "artist_release_metadata"
            )
            for item in response.items
        }
        assert weights[str(reversed_duo)] == 0.65
        assert weights[str(solo)] == 0.35

        # Some sources leave PRIMARY on both sides of an explicit feature separator.
        with sessions.begin() as session:
            marked = _credit(session, (a, "PRIMARY"), (b, "PRIMARY"))
            counterexample = _recording(session, marked)
            lead = session.get(ArtistCreditNameRow, (marked, 0))
            assert lead is not None
            lead.join_phrase = " (feat. "
        assert acts()[str(fit)] == (str(a), str(b), str(guest))
        with sessions.begin() as session:
            withdrawn = session.get(RecordingRow, counterexample)
            assert withdrawn is not None
            withdrawn.deleted_at = datetime.now(UTC)
        assert acts() == current

        # One independent recording outside the owner's library overturns the inference.
        with sessions.begin() as session:
            _recording(session, _credit(session, (b, "PRIMARY")))
        assert acts()[str(fit)] == (str(a), str(b), str(guest))
        assert (
            service.algorithmic_replay(owner, response.request.recommendation_request_id).items
            == response.items
        )
    finally:
        engine.dispose()
