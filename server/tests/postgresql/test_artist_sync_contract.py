"""Real PostgreSQL evidence for the stable Artist identity sync prerequisite."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.models.sync import SyncEventRow
from autplay.application.catalog_artist_sync import CatalogArtistMutationService
from autplay.application.sync import (
    CATALOG_ARTIST_ID_V1,
    CatalogArtistSyncPublisher,
    SyncError,
    SyncService,
)
from autplay.domain.auth import AccountRole, Principal
from psycopg import Connection
from psycopg.errors import CheckViolation
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from .identity_factory import (
    QueryRef,
    append_evaluation,
    append_policy_event,
    insert_recording,
    insert_release_set,
    insert_world,
    make_candidate,
)


class _FailingPublisher(CatalogArtistSyncPublisher):
    def publish(self, session: object, owner_user_id: UUID) -> int:
        del session, owner_user_id
        raise RuntimeError("injected publisher failure")


def _bind(service: SyncService, principal: Principal) -> dict[str, object]:
    body: dict[str, object] = {
        "protocol_version": 1,
        "user_id": str(principal.user_id),
        "device_id": str(principal.device_id),
        "server_profile_id": str(uuid4()),
        "journal_epoch": str(uuid4()),
        "device_name": "artist-contract",
        "platform": "ANDROID",
        "app_version": "artist-id-v1",
    }
    service.bind(principal, body)
    return body


def _required_value(cursor: Any) -> Any:
    row = cursor.fetchone()
    if row is None:
        raise AssertionError("expected PostgreSQL statement to return one row")
    return row[0]


def _catalog_world(
    connection: Connection[Any],
) -> tuple[Principal, UUID, UUID, UUID, UUID, UUID]:
    world = insert_world(connection)
    releases = insert_release_set(connection)
    append_policy_event(connection, releases, world.admin_user_id)
    candidate = make_candidate(
        world.seed_recording_id,
        1,
        releases,
        raw_score=Decimal("0.950000"),
        confidence=Decimal("0.950000"),
    )
    decoy = make_candidate(
        insert_recording(connection, "artist-contract-decoy"),
        2,
        releases,
        raw_score=Decimal("0.700000"),
        confidence=Decimal("0.700000"),
    )
    append_evaluation(
        connection,
        world.query("USER_TRACK_REF"),
        releases,
        [candidate, decoy],
        state="AUTO_MATCH",
        execution_mode="APPLIED",
        project=True,
    )
    credit_id = _required_value(
        connection.execute(
            "SELECT artist_credit_id FROM catalog.recording WHERE recording_id = %s",
            (world.seed_recording_id,),
        )
    )
    first_artist_id = _required_value(
        connection.execute(
            "INSERT INTO catalog.artist (name, sort_name, normalized_name) "
            "VALUES ('Same Name', 'Same Name', 'same name') RETURNING artist_id"
        )
    )
    second_artist_id = _required_value(
        connection.execute(
            "INSERT INTO catalog.artist (name, sort_name, normalized_name) "
            "VALUES ('Same Name', 'Same Name', 'same name') RETURNING artist_id"
        )
    )
    connection.execute(
        "INSERT INTO catalog.artist_credit_name "
        "(artist_credit_id, position, artist_id, credited_name, join_phrase, role) "
        "VALUES (%s, 0, %s, 'Lead', ' feat. ', 'PRIMARY'), "
        "(%s, 1, %s, 'Guest', '', 'FUTURE_ROLE')",
        (credit_id, first_artist_id, credit_id, second_artist_id),
    )
    second_recording_id = _required_value(
        connection.execute(
            "INSERT INTO catalog.recording "
            "(artist_credit_id, title, normalized_title, recording_kind, identity_status) "
            "VALUES (%s, 'Second recording', 'second recording', 'STUDIO', 'ACTIVE') "
            "RETURNING recording_id",
            (credit_id,),
        )
    )
    second_ref_id = _required_value(
        connection.execute(
            "INSERT INTO library.user_track_ref (user_id, raw_title, raw_artist) "
            "VALUES (%s, 'Second recording', 'Same Name') RETURNING user_track_ref_id",
            (world.owner_user_id,),
        )
    )
    append_evaluation(
        connection,
        QueryRef(
            "USER_TRACK_REF",
            "user_track_ref_id",
            second_ref_id,
            world.owner_user_id,
        ),
        releases,
        [
            make_candidate(
                second_recording_id,
                1,
                releases,
                raw_score=Decimal("0.950000"),
                confidence=Decimal("0.950000"),
            ),
            make_candidate(
                world.seed_recording_id,
                2,
                releases,
                raw_score=Decimal("0.700000"),
                confidence=Decimal("0.700000"),
            ),
        ],
        state="AUTO_MATCH",
        execution_mode="APPLIED",
        project=True,
    )
    release_group_id = _required_value(
        connection.execute(
            "INSERT INTO catalog.release_group "
            "(artist_credit_id, title, normalized_title, primary_type) "
            "VALUES (%s, 'Shared release', 'shared release', 'ALBUM') RETURNING release_group_id",
            (credit_id,),
        )
    )
    release_id = _required_value(
        connection.execute(
            "INSERT INTO catalog.release (release_group_id, artist_credit_id, title) "
            "VALUES (%s, %s, 'Shared release') RETURNING release_id",
            (release_group_id, credit_id),
        )
    )
    medium_id = _required_value(
        connection.execute(
            "INSERT INTO catalog.medium (release_id, position) VALUES (%s, 1) RETURNING medium_id",
            (release_id,),
        )
    )
    connection.execute(
        "INSERT INTO catalog.release_track "
        "(medium_id, recording_id, artist_credit_id, sequence_no, title) "
        "VALUES (%s, %s, %s, 1, 'Identity seed')",
        (medium_id, world.seed_recording_id, credit_id),
    )
    second_group_id = _required_value(
        connection.execute(
            "INSERT INTO catalog.release_group "
            "(artist_credit_id, title, normalized_title, primary_type) "
            "VALUES (%s, 'Second shared release', 'second shared release', 'SINGLE') "
            "RETURNING release_group_id",
            (credit_id,),
        )
    )
    second_release_id = _required_value(
        connection.execute(
            "INSERT INTO catalog.release (release_group_id, artist_credit_id, title) "
            "VALUES (%s, %s, 'Second shared release') RETURNING release_id",
            (second_group_id, credit_id),
        )
    )
    second_medium_id = _required_value(
        connection.execute(
            "INSERT INTO catalog.medium (release_id, position) VALUES (%s, 1) RETURNING medium_id",
            (second_release_id,),
        )
    )
    connection.execute(
        "INSERT INTO catalog.release_track "
        "(medium_id, recording_id, artist_credit_id, sequence_no, title) "
        "VALUES (%s, %s, %s, 1, 'Identity seed')",
        (second_medium_id, second_recording_id, credit_id),
    )
    empty_credit_id = _required_value(
        connection.execute(
            "INSERT INTO catalog.artist_credit (display_name, normalized_name) "
            "VALUES ('Unresolved legacy credit', 'unresolved legacy credit') "
            "RETURNING artist_credit_id"
        )
    )
    unresolved_group_id = _required_value(
        connection.execute(
            "INSERT INTO catalog.release_group "
            "(artist_credit_id, title, normalized_title, primary_type) "
            "VALUES (%s, 'Unresolved release', 'unresolved release', 'OTHER') "
            "RETURNING release_group_id",
            (empty_credit_id,),
        )
    )
    unresolved_release_id = _required_value(
        connection.execute(
            "INSERT INTO catalog.release (release_group_id, artist_credit_id, title) "
            "VALUES (%s, %s, 'Unresolved release') RETURNING release_id",
            (unresolved_group_id, empty_credit_id),
        )
    )
    unresolved_medium_id = _required_value(
        connection.execute(
            "INSERT INTO catalog.medium (release_id, position) VALUES (%s, 1) RETURNING medium_id",
            (unresolved_release_id,),
        )
    )
    connection.execute(
        "INSERT INTO catalog.release_track "
        "(medium_id, recording_id, artist_credit_id, sequence_no, title) "
        "VALUES (%s, %s, %s, 1, 'Identity seed')",
        (unresolved_medium_id, world.seed_recording_id, credit_id),
    )
    connection.commit()
    return (
        Principal(world.owner_user_id, world.device_id, uuid4(), AccountRole.OWNER),
        credit_id,
        first_artist_id,
        second_artist_id,
        release_id,
        empty_credit_id,
    )


def test_artist_bootstrap_incremental_owner_scope_and_atomicity(
    database_url: str, database_connection: Connection[Any]
) -> None:
    principal, credit_id, first_artist_id, second_artist_id, release_id, empty_credit_id = (
        _catalog_world(database_connection)
    )
    engine = create_engine(database_url)
    try:
        sync = SyncService(engine, cursor_secret=b"artist-contract-cursor-secret-32bytes")
        body = _bind(sync, principal)
        capable = {
            **body,
            "reason": "FIRST_SYNC",
            "snapshot_id": None,
            "page_token": None,
            "pending_local_event_count": 0,
            "catalog_projection_version": 1,
            "capabilities": [CATALOG_ARTIST_ID_V1],
        }
        snapshot = sync.bootstrap(principal, capable)
        catalog = {
            (row["aggregate_type"], row["aggregate_server_id"]): row
            for row in snapshot["aggregates"]
            if row["aggregate_type"].startswith("ARTIST")
            or row["aggregate_type"].endswith("ARTIST_CREDIT")
        }
        assert ("ARTIST", str(first_artist_id)) in catalog
        assert ("ARTIST", str(second_artist_id)) in catalog
        credit = catalog[("ARTIST_CREDIT", str(credit_id))]["payload"]
        assert [(row["position"], row["join_phrase"], row["role"]) for row in credit["names"]] == [
            (0, " feat. ", "PRIMARY"),
            (1, "", "FUTURE_ROLE"),
        ]
        assert catalog[("ARTIST_CREDIT", str(empty_credit_id))]["payload"]["names"] == []
        links = {
            (row["aggregate_type"], row["aggregate_server_id"]): row["payload"]
            for row in snapshot["aggregates"]
            if row["aggregate_type"] in {"RECORDING_ARTIST_CREDIT", "RELEASE_ARTIST_CREDIT"}
        }
        assert any(value["artist_credit_id"] == str(credit_id) for value in links.values())
        assert links[("RELEASE_ARTIST_CREDIT", str(release_id))]["artist_credit_id"] == str(
            credit_id
        )
        assert links[("RELEASE_ARTIST_CREDIT", str(release_id))]["owner_recording_ids"]
        assert links[("RELEASE_ARTIST_CREDIT", str(release_id))]["owner_recording_page"] == 0
        assert links[("RELEASE_ARTIST_CREDIT", str(release_id))]["owner_recording_page_count"] == 1
        assert len(links[("RELEASE_ARTIST_CREDIT", str(release_id))]["owner_scope_id"]) == 64
        assert (
            sum(
                payload["artist_credit_id"] == str(credit_id)
                for (aggregate_type, _), payload in links.items()
                if aggregate_type == "RELEASE_ARTIST_CREDIT"
            )
            >= 2
        )
        assert (
            sum(
                payload["artist_credit_id"] == str(credit_id)
                for (aggregate_type, _), payload in links.items()
                if aggregate_type == "RECORDING_ARTIST_CREDIT"
            )
            >= 2
        )

        mutation = CatalogArtistMutationService(engine)
        mutation.rename_artist(
            first_artist_id,
            name="Renamed Artist",
            sort_name="Renamed Artist",
            normalized_name="renamed artist",
        )
        old_page = sync.pull(
            principal,
            {**body, "cursor": snapshot["snapshot_cursor"], "limit": 100},
        )
        assert all(
            event["aggregate_type"]
            not in {
                "ARTIST",
                "ARTIST_CREDIT",
                "RECORDING_ARTIST_CREDIT",
                "RELEASE_ARTIST_CREDIT",
            }
            for event in old_page["events"]
        )
        assert any(event["aggregate_type"] == "USER_TRACK_REF" for event in old_page["events"])
        new_page = sync.pull(
            principal,
            {
                **body,
                "cursor": snapshot["snapshot_cursor"],
                "limit": 100,
                "catalog_projection_version": 1,
                "capabilities": [CATALOG_ARTIST_ID_V1],
            },
        )
        renamed = next(
            event
            for event in new_page["events"]
            if event["event_type"] == "CATALOG_ARTIST_UPSERTED"
            and event["aggregate_server_id"] == str(first_artist_id)
        )
        assert renamed["payload"]["name"] == "Renamed Artist"
        assert renamed["payload"]["artist_id"] == str(first_artist_id)
        assert all(
            event["payload"]["recording_id"]
            for event in new_page["events"]
            if event["aggregate_type"] == "USER_TRACK_REF"
        )

        with engine.connect() as connection:
            before = connection.execute(
                text("SELECT count(*) FROM sync.sync_event WHERE user_id = :owner"),
                {"owner": principal.user_id},
            ).scalar_one()
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        with sessions.begin() as session:
            CatalogArtistSyncPublisher().publish(session, principal.user_id)
            after_first_publish = session.scalar(
                select(func.count())
                .select_from(SyncEventRow)
                .where(SyncEventRow.user_id == principal.user_id)
            )
            CatalogArtistSyncPublisher().publish(session, principal.user_id)
            after_replay = session.scalar(
                select(func.count())
                .select_from(SyncEventRow)
                .where(SyncEventRow.user_id == principal.user_id)
            )
        with engine.connect() as connection:
            after = connection.execute(
                text("SELECT count(*) FROM sync.sync_event WHERE user_id = :owner"),
                {"owner": principal.user_id},
            ).scalar_one()
        assert after_first_publish is not None
        assert after_first_publish >= before
        assert after_replay == after_first_publish
        assert after == after_replay

        failing = CatalogArtistMutationService(engine, publisher=_FailingPublisher())
        with pytest.raises(RuntimeError, match="injected publisher failure"):
            failing.rename_artist(
                first_artist_id,
                name="Must Roll Back",
                sort_name="Must Roll Back",
                normalized_name="must roll back",
            )
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text("SELECT name FROM catalog.artist WHERE artist_id = :artist"),
                    {"artist": first_artist_id},
                ).scalar_one()
                == "Renamed Artist"
            )
    finally:
        engine.dispose()


def test_catalog_publisher_batches_event_dedupe_without_n_plus_one(
    database_url: str, database_connection: Connection[Any]
) -> None:
    principal, credit_id, *_ = _catalog_world(database_connection)
    recording_ids = [
        row[0]
        for row in database_connection.execute(
            "INSERT INTO catalog.recording "
            "(artist_credit_id, title, normalized_title, recording_kind, identity_status) "
            "SELECT %s, 'Bulk ' || value, 'bulk ' || value, 'STUDIO', 'ACTIVE' "
            "FROM generate_series(1, 40) AS value RETURNING recording_id",
            (credit_id,),
        ).fetchall()
    ]
    releases = insert_release_set(database_connection)
    activation_row = database_connection.execute(
        "SELECT activation_id, sequence_no FROM identity.match_policy_activation "
        "WHERE evidence_mode = %s AND evidence_tier = %s "
        "ORDER BY sequence_no DESC LIMIT 1",
        (releases.evidence_mode, releases.evidence_tier),
    ).fetchone()
    if activation_row is None:
        raise AssertionError("expected an active Artist fixture policy")
    append_policy_event(
        database_connection,
        releases,
        principal.user_id,
        sequence_no=int(activation_row[1]) + 1,
        supersedes_activation_id=activation_row[0],
    )
    decoy_recording_id = _required_value(
        database_connection.execute(
            "SELECT recording_id FROM library.user_track_ref "
            "WHERE user_id = %s AND recording_id IS NOT NULL "
            "ORDER BY user_track_ref_id LIMIT 1",
            (principal.user_id,),
        )
    )
    for recording_id in recording_ids:
        user_track_ref_id = _required_value(
            database_connection.execute(
                "INSERT INTO library.user_track_ref (user_id, raw_title, raw_artist) "
                "VALUES (%s, 'Bulk', 'Same Name') RETURNING user_track_ref_id",
                (principal.user_id,),
            )
        )
        append_evaluation(
            database_connection,
            QueryRef(
                "USER_TRACK_REF",
                "user_track_ref_id",
                user_track_ref_id,
                principal.user_id,
            ),
            releases,
            [
                make_candidate(
                    recording_id,
                    1,
                    releases,
                    raw_score=Decimal("0.950000"),
                    confidence=Decimal("0.950000"),
                ),
                make_candidate(
                    decoy_recording_id,
                    2,
                    releases,
                    raw_score=Decimal("0.700000"),
                    confidence=Decimal("0.700000"),
                ),
            ],
            state="AUTO_MATCH",
            execution_mode="APPLIED",
            project=True,
        )
    database_connection.commit()

    engine = create_engine(database_url)
    statements = 0

    def count_statement(*_args: object) -> None:
        nonlocal statements
        statements += 1

    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        with Session(engine) as session:
            candidate_count = CatalogArtistSyncPublisher().publish(session, principal.user_id)
            assert candidate_count > 80
            assert statements <= 12
            inserted_count = session.scalar(
                select(func.count())
                .select_from(SyncEventRow)
                .where(SyncEventRow.user_id == principal.user_id)
            )
            assert inserted_count == candidate_count

            statements = 0
            replay_count = CatalogArtistSyncPublisher().publish(session, principal.user_id)
            assert replay_count == candidate_count
            assert statements <= 12
            replayed_count = session.scalar(
                select(func.count())
                .select_from(SyncEventRow)
                .where(SyncEventRow.user_id == principal.user_id)
            )
            assert replayed_count == inserted_count
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)
        engine.dispose()


def test_catalog_publisher_concurrent_replay_is_atomic(
    database_url: str, database_connection: Connection[Any]
) -> None:
    principal, *_ = _catalog_world(database_connection)
    engine = create_engine(database_url)
    start = Barrier(2)

    def publish_and_commit() -> int:
        with Session(engine) as session:
            start.wait(timeout=10)
            candidate_count = CatalogArtistSyncPublisher().publish(session, principal.user_id)
            session.commit()
            return candidate_count

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            counts = list(executor.map(lambda _: publish_and_commit(), range(2)))
        assert counts[0] == counts[1]
        with Session(engine) as session:
            stored_count = session.scalar(
                select(func.count())
                .select_from(SyncEventRow)
                .where(SyncEventRow.user_id == principal.user_id)
            )
            distinct_count = session.scalar(
                select(func.count(func.distinct(SyncEventRow.event_id))).where(
                    SyncEventRow.user_id == principal.user_id
                )
            )
        assert stored_count == counts[0]
        assert distinct_count == stored_count
    finally:
        engine.dispose()


def test_bootstrap_continuation_uses_frozen_capability(
    database_url: str,
    database_connection: Connection[Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    principal, _, first_artist_id, _, _, _ = _catalog_world(database_connection)
    import autplay.application.sync as sync_module

    monkeypatch.setattr(sync_module, "_MAX_PULL", 2)
    engine = create_engine(database_url)
    try:
        service = SyncService(engine, cursor_secret=b"artist-continuation-secret-32bytes")
        body = _bind(service, principal)
        page = service.bootstrap(
            principal,
            {
                **body,
                "reason": "FIRST_SYNC",
                "snapshot_id": None,
                "page_token": None,
                "pending_local_event_count": 0,
                "capabilities": [CATALOG_ARTIST_ID_V1],
            },
        )
        seen: list[dict[str, object]] = list(page["aggregates"])
        while page["has_more"]:
            page = service.bootstrap(
                principal,
                {
                    **body,
                    "reason": "FIRST_SYNC",
                    "snapshot_id": page["snapshot_id"],
                    "page_token": page["next_page_token"],
                    "pending_local_event_count": 0,
                },
            )
            seen.extend(page["aggregates"])
        assert any(
            row["aggregate_type"] == "ARTIST" and row["aggregate_server_id"] == str(first_artist_id)
            for row in seen
        )
    finally:
        engine.dispose()


def test_artist_credit_member_limit_is_authoritative(
    database_url: str,
    database_connection: Connection[Any],
) -> None:
    principal, credit_id, _, _, _, _ = _catalog_world(database_connection)
    artist_id = _required_value(
        database_connection.execute(
            "SELECT artist_id FROM catalog.artist_credit_name "
            "WHERE artist_credit_id = %s ORDER BY position LIMIT 1",
            (credit_id,),
        )
    )
    database_connection.execute(
        "INSERT INTO catalog.artist_credit_name "
        "(artist_credit_id, position, artist_id, credited_name, join_phrase, role) "
        "SELECT %s, value, %s, repeat('M', 1000), repeat(' ', 1000), 'OTHER' "
        "FROM generate_series(2, 999) value",
        (credit_id, artist_id),
    )
    database_connection.execute(
        "UPDATE catalog.artist_credit_name SET credited_name = repeat('M', 1000), "
        "join_phrase = repeat(' ', 1000) WHERE artist_credit_id = %s",
        (credit_id,),
    )
    database_connection.commit()
    with pytest.raises(CheckViolation, match="artist credit member limit exceeded"):
        database_connection.execute(
            "INSERT INTO catalog.artist_credit_name "
            "(artist_credit_id, position, artist_id, credited_name, join_phrase, role) "
            "VALUES (%s, 1000, %s, 'Overflow', '', 'OTHER')",
            (credit_id, artist_id),
        )
    database_connection.rollback()
    assert (
        _required_value(
            database_connection.execute(
                "SELECT count(*) FROM catalog.artist_credit_name WHERE artist_credit_id = %s",
                (credit_id,),
            )
        )
        == 1000
    )
    engine = create_engine(database_url)
    try:
        with Session(engine) as session, pytest.raises(SyncError, match="PAYLOAD_TOO_LARGE"):
            CatalogArtistSyncPublisher().publish(session, principal.user_id)
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(SyncEventRow)
                    .where(
                        SyncEventRow.aggregate_type.in_(
                            (
                                "ARTIST",
                                "ARTIST_CREDIT",
                                "RECORDING_ARTIST_CREDIT",
                                "RELEASE_ARTIST_CREDIT",
                            )
                        )
                    )
                )
                == 0
            )
    finally:
        engine.dispose()


def test_artist_credit_raw_role_and_join_phrase_are_bounded(
    database_connection: Connection[Any],
) -> None:
    _, credit_id, artist_id, *_ = _catalog_world(database_connection)
    assert (
        _required_value(
            database_connection.execute(
                "SELECT role FROM catalog.artist_credit_name "
                "WHERE artist_credit_id = %s AND position = 1",
                (credit_id,),
            )
        )
        == "FUTURE_ROLE"
    )

    with pytest.raises(CheckViolation):
        database_connection.execute(
            "INSERT INTO catalog.artist_credit_name "
            "(artist_credit_id, position, artist_id, credited_name, join_phrase, role) "
            "VALUES (%s, 2, %s, 'Invalid role', '', %s)",
            (credit_id, artist_id, "R" * 101),
        )
    database_connection.rollback()

    with pytest.raises(CheckViolation):
        database_connection.execute(
            "INSERT INTO catalog.artist_credit_name "
            "(artist_credit_id, position, artist_id, credited_name, join_phrase, role) "
            "VALUES (%s, 2, %s, 'Invalid join', %s, 'OTHER')",
            (credit_id, artist_id, " " * 1001),
        )
    database_connection.rollback()
