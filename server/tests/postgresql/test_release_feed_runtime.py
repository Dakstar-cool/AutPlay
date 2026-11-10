"""Real PostgreSQL evidence for private release-feed ranking and rights gates."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.discovery_runtime import JAMENDO_PROVIDER_ID
from autplay.adapters.postgresql.models import (
    AcquisitionRecordRow,
    ArtistCreditNameRow,
    ArtistCreditRow,
    ArtistPolicyRevisionRow,
    ArtistPolicyRow,
    ArtistRow,
    AudioVariantRow,
    DiscoveryCandidateRow,
    DiscoveryRunCandidateRow,
    DiscoveryRunPageRow,
    DiscoveryRunRow,
    ExternalReferenceRow,
    LibraryEntryRow,
    RecordingRow,
    SourceAuthorizationRow,
    UserAccountRow,
    UserTrackRefRow,
    VaultObjectRow,
    VaultReplicaRow,
)
from autplay.adapters.postgresql.release_feed import ReleaseFeedRepository
from autplay.adapters.postgresql.release_watch import ReleaseWatchRepository
from autplay.application.release_feed import InvalidReleaseFeedCursor, ReleaseFeedService
from autplay.domain.auth import AccountRole, Principal

from .test_discovery_runtime import _seed_owned_artist


def _owner(session: Session, name: str) -> UUID:
    owner = UserAccountRow(display_name=name, role="OWNER", status="ACTIVE")
    session.add(owner)
    session.flush()
    return owner.user_id


def _artist(session: Session, name: str) -> tuple[UUID, UUID]:
    artist = ArtistRow(
        name=name, sort_name=name, normalized_name=name.casefold(), identity_status="ACTIVE"
    )
    credit = ArtistCreditRow(display_name=name, normalized_name=name.casefold())
    session.add_all((artist, credit))
    session.flush()
    session.add(
        ArtistCreditNameRow(
            artist_credit_id=credit.artist_credit_id,
            position=0,
            artist_id=artist.artist_id,
            credited_name=name,
            role="PRIMARY",
        )
    )
    recording = RecordingRow(
        artist_credit_id=credit.artist_credit_id,
        title=f"{name} recording",
        normalized_title=f"{name.casefold()} recording",
        identity_status="ACTIVE",
    )
    session.add(recording)
    session.flush()
    return artist.artist_id, recording.recording_id


def _run(session: Session, owner: UUID, artist: UUID, now: datetime) -> UUID:
    policy = ArtistPolicyRow(
        user_id=owner,
        canonical_artist_id=artist,
        provider_id=JAMENDO_PROVIDER_ID,
        provider_artist_id="20",
        discovery_mode="MANUAL_ONLY",
        import_mode="REVIEW_REQUIRED",
        automation_enabled=False,
        current_revision=1,
    )
    session.add(policy)
    session.flush()
    session.add(
        ArtistPolicyRevisionRow(
            policy_id=policy.policy_id,
            revision=1,
            owner_user_id=owner,
            discovery_mode="MANUAL_ONLY",
            import_mode="REVIEW_REQUIRED",
            automation_enabled=False,
            change_kind="SAFE_DEFAULT",
        )
    )
    session.flush()
    run = DiscoveryRunRow(
        user_id=owner,
        policy_id=policy.policy_id,
        policy_revision=1,
        provider_id=JAMENDO_PROVIDER_ID,
        provider_artist_id="20",
        adapter_id="autplay.jamendo.manual",
        adapter_version="1.0.0",
        canonical_query_sha256=b"q" * 32,
        due_slot_at=now - timedelta(hours=1),
        state="COMPLETED",
    )
    session.add(run)
    session.flush()
    session.add(
        DiscoveryRunPageRow(
            run_id=run.run_id,
            ordinal=0,
            page_offset=0,
            response_sha256=b"p" * 32,
            observed_count=1,
        )
    )
    session.flush()
    return run.run_id


def _candidate(
    session: Session,
    owner: UUID,
    artist: UUID,
    run: UUID,
    track_id: str,
    released_at: datetime,
    *,
    recording: UUID | None = None,
    state: str = "NOT_REQUESTED",
) -> DiscoveryCandidateRow:
    candidate = DiscoveryCandidateRow(
        user_id=owner,
        provider_id=JAMENDO_PROVIDER_ID,
        canonical_artist_id=artist,
        market_scope="GLOBAL",
        provider_track_id=track_id,
        provider_artist_id="20",
        title=f"Release {track_id}",
        artist="Artist",
        album="Album",
        duration_seconds=120,
        license_url="https://example.invalid/license",
        share_url="https://example.invalid/share",
        disposition="SELECTABLE",
        acquisition_state=state,
        source_authorization_revision=1,
        released_at=released_at,
        recording_id=recording,
    )
    session.add(candidate)
    session.flush()
    session.add(DiscoveryRunCandidateRow(run_id=run, candidate_id=candidate.candidate_id))
    session.flush()
    return candidate


def test_two_owners_exclusion_dedup_and_stable_pages(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            now = datetime.now(UTC)
            first = _owner(session, "feed owner one")
            second = _owner(session, "feed owner two")
            artist_a, recording_a = _artist(session, "Feed A")
            artist_b, _ = _artist(session, "Feed B")
            artist_c, _ = _artist(session, "Feed C")
            watch = ReleaseWatchRepository(session)
            for owner, artist in ((first, artist_a), (first, artist_b), (second, artist_c)):
                watch.set_manual_follow(owner, artist, follow=True, now=now)
            run_a = _run(session, first, artist_a, now)
            run_b = _run(session, first, artist_b, now)
            run_c = _run(session, second, artist_c, now)
            first_duplicate = _candidate(
                session,
                first,
                artist_a,
                run_a,
                "101",
                now - timedelta(days=1),
                recording=recording_a,
            )
            second_duplicate = _candidate(
                session,
                first,
                artist_a,
                run_a,
                "102",
                now - timedelta(days=1),
                recording=recording_a,
            )
            for candidate in (first_duplicate, second_duplicate):
                ext = ExternalReferenceRow(
                    provider_id=JAMENDO_PROVIDER_ID,
                    external_entity_type="TRACK",
                    external_id=candidate.provider_track_id,
                    market_scope="GLOBAL",
                    recording_id=recording_a,
                )
                session.add(ext)
                session.flush()
                candidate.external_reference_id = ext.external_reference_id
            session.flush()
            _candidate(session, first, artist_a, run_a, "103", now - timedelta(days=2))
            _candidate(session, first, artist_b, run_b, "104", now - timedelta(days=3))
            _candidate(session, second, artist_c, run_c, "101", now - timedelta(days=1))
            orphan = _candidate(session, first, artist_a, run_a, "999", now - timedelta(days=1))
            session.execute(
                text("DELETE FROM discovery.run_candidate WHERE candidate_id=:candidate"),
                {"candidate": orphan.candidate_id},
            )
            as_of = datetime.now(UTC) + timedelta(seconds=1)
            feed = ReleaseFeedRepository(session)
            first_page = feed.list_page(first, as_of=as_of, limit=2)
            assert len(first_page) == 2
            assert all(
                item.state == "DISCOVERED" and item.user_track_ref_id is None for item in first_page
            )
            assert all(item.canonical_artist_id in {artist_a, artist_b} for item in first_page)
            from autplay.adapters.postgresql.release_feed import FeedPosition

            last = first_page[-1]
            after = FeedPosition(
                last.diversity_round,
                last.released_at,
                last.affinity_rank,
                last.availability_rank,
                last.score_milli,
                last.candidate_id,
            )
            second_page = feed.list_page(first, as_of=as_of, limit=2, after=after)
            all_items = first_page + second_page
            assert len(all_items) == 3
            assert len({item.candidate_id for item in all_items}) == 3
            assert len({item.recording_id for item in all_items if item.recording_id}) == 0
            assert len(feed.list_page(second, as_of=as_of, limit=10)) == 1
            session.commit()
            service = ReleaseFeedService(sessionmaker(engine), lambda: datetime.now(UTC))
            actor = Principal(first, uuid4(), uuid4(), AccountRole.OWNER)
            other_actor = Principal(second, uuid4(), uuid4(), AccountRole.OWNER)
            page = service.page(actor, limit=2)
            assert len(page.items) == 2 and page.next_cursor is not None
            assert len(service.page(actor, limit=2, cursor=page.next_cursor).items) == 1
            with pytest.raises(InvalidReleaseFeedCursor):
                service.page(other_actor, cursor=page.next_cursor)
            with pytest.raises(InvalidReleaseFeedCursor):
                service.page(actor, cursor="%%%%")
            payload = json.loads(base64.urlsafe_b64decode(page.next_cursor + "=="))
            payload[3] = 2**64
            forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
            with pytest.raises(InvalidReleaseFeedCursor):
                service.page(actor, cursor=forged)
            watch.set_manual_follow(first, artist_a, follow=False, now=now)
            assert {
                item.canonical_artist_id for item in feed.list_page(first, as_of=as_of, limit=10)
            } == {artist_b}
            assert (
                session.scalar(
                    text("SELECT count(*) FROM jobs.job WHERE user_id=:owner"), {"owner": first}
                )
                == 0
            )
            session.rollback()
    finally:
        engine.dispose()


def test_ready_requires_current_rights_and_owner_materialization(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with Session(engine) as session:
            now = datetime.now(UTC)
            owner = _owner(session, "feed rights owner")
            other = _owner(session, "feed rights other")
            artist = _seed_owned_artist(session, owner, "Rights Artist")
            ReleaseWatchRepository(session).set_manual_follow(owner, artist, follow=True, now=now)
            run = _run(session, owner, artist, now)
            ref = session.scalar(
                select(UserTrackRefRow).where(
                    UserTrackRefRow.user_id == owner,
                    UserTrackRefRow.resolution_status == "RESOLVED",
                )
            )
            assert ref is not None and ref.recording_id is not None
            entry = session.scalar(
                select(LibraryEntryRow).where(
                    LibraryEntryRow.user_id == owner,
                    LibraryEntryRow.user_track_ref_id == ref.user_track_ref_id,
                )
            )
            assert entry is not None
            candidate = _candidate(
                session,
                owner,
                artist,
                run,
                "205",
                now - timedelta(days=1),
                recording=ref.recording_id,
                state="READY",
            )
            as_of = datetime.now(UTC) + timedelta(seconds=1)
            feed = ReleaseFeedRepository(session)
            assert feed.list_page(owner, as_of=as_of, limit=10)[0].state == "REVIEW_REQUIRED"
            ext = ExternalReferenceRow(
                provider_id=JAMENDO_PROVIDER_ID,
                external_entity_type="TRACK",
                external_id="205",
                market_scope="GLOBAL",
                recording_id=ref.recording_id,
            )
            session.add(ext)
            session.flush()
            authorization = SourceAuthorizationRow(
                user_id=owner,
                provider_id=JAMENDO_PROVIDER_ID,
                canonical_artist_id=artist,
                adapter_id="autplay.jamendo.manual",
                adapter_version="1.0.0",
                market_scope="GLOBAL",
                rights_capability="AUTHORIZED_DOWNLOAD",
                revision=1,
                policy_reference="ADR-034:a1b-manual:1",
                purpose="MANUAL",
                granted_at=now - timedelta(hours=1),
                expires_at=now + timedelta(hours=1),
            )
            session.add(authorization)
            session.flush()
            vault_object = VaultObjectRow(
                sha256=b"v" * 32,
                byte_size=1000,
                detected_mime_type="audio/mpeg",
                commit_status="COMMITTED",
                committed_at=now,
            )
            session.add(vault_object)
            session.flush()
            session.add(
                VaultReplicaRow(
                    vault_object_id=vault_object.vault_object_id,
                    storage_backend="LOCAL",
                    storage_key="feed-fixture-205",
                    replica_status="AVAILABLE",
                    verified_at=now,
                )
            )
            session.flush()
            variant = AudioVariantRow(
                recording_id=ref.recording_id,
                vault_object_id=vault_object.vault_object_id,
                codec="MP3",
                container="MP3",
                sample_rate_hz=44100,
                channels=2,
                duration_ms=120000,
                validation_status="VALID",
            )
            session.add(variant)
            session.flush()
            session.add(
                AcquisitionRecordRow(
                    audio_variant_id=variant.audio_variant_id,
                    provider_id=JAMENDO_PROVIDER_ID,
                    external_reference_id=ext.external_reference_id,
                    authorized_by_user_id=owner,
                    rights_capability="AUTHORIZED_DOWNLOAD",
                )
            )
            entry.availability_status = "VAULT"
            candidate.disposition = "SELECTED"
            candidate.external_reference_id = ext.external_reference_id
            candidate.source_authorization_id = authorization.authorization_id
            candidate.audio_variant_id = variant.audio_variant_id
            candidate.user_track_ref_id = ref.user_track_ref_id
            candidate.library_entry_id = entry.library_entry_id
            session.flush()
            ready = feed.list_page(owner, as_of=as_of, limit=10)[0]
            assert ready.state == "READY"
            assert ready.user_track_ref_id == ref.user_track_ref_id
            assert feed.list_page(other, as_of=as_of, limit=10) == []
            candidate.acquisition_state = "ACQUIRING"
            session.flush()
            assert feed.list_page(owner, as_of=as_of, limit=10)[0].state == "ACQUIRING"
            candidate.acquisition_state = "FAILED_TERMINAL"
            session.flush()
            assert feed.list_page(owner, as_of=as_of, limit=10)[0].state == "FAILED"
            candidate.acquisition_state = "READY"
            candidate.disposition = "UNAVAILABLE"
            session.flush()
            assert feed.list_page(owner, as_of=as_of, limit=10)[0].state == "UNAVAILABLE"
            candidate.disposition = "SELECTED"
            authorization.revoked_at = now
            session.flush()
            revoked = feed.list_page(owner, as_of=as_of, limit=10)[0]
            assert revoked.state == "REVIEW_REQUIRED"
            assert revoked.user_track_ref_id is None and revoked.recording_id is None
            session.rollback()
    finally:
        engine.dispose()
