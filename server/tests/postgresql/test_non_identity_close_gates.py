"""P02 cross-owner and serving invariants outside identity matching."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from psycopg import Connection, Cursor


def _returned_uuid(cursor: Cursor[Any]) -> uuid.UUID:
    row = cursor.fetchone()
    if row is None or not isinstance(row[0], uuid.UUID):
        raise AssertionError("INSERT ... RETURNING did not return a UUID")
    return row[0]


def _insert_user(connection: Connection[Any], label: str) -> uuid.UUID:
    return _returned_uuid(
        connection.execute(
            """
            INSERT INTO account.user_account (display_name)
            VALUES (%s) RETURNING user_id
            """,
            (f"p02-{label}-{uuid.uuid4().hex[:8]}",),
        )
    )


def _insert_device(connection: Connection[Any], user_id: uuid.UUID) -> uuid.UUID:
    return _returned_uuid(
        connection.execute(
            """
            INSERT INTO account.device (user_id, device_name, platform, app_version)
            VALUES (%s, %s, 'ANDROID', 'p02') RETURNING device_id
            """,
            (user_id, f"p02-device-{uuid.uuid4().hex[:8]}"),
        )
    )


def _insert_user_track_ref(connection: Connection[Any], user_id: uuid.UUID) -> uuid.UUID:
    return _returned_uuid(
        connection.execute(
            """
            INSERT INTO library.user_track_ref (user_id, raw_title)
            VALUES (%s, 'P02 close-gate fixture') RETURNING user_track_ref_id
            """,
            (user_id,),
        )
    )


def _insert_recording(connection: Connection[Any], label: str) -> uuid.UUID:
    artist_credit_id = _returned_uuid(
        connection.execute(
            """
            INSERT INTO catalog.artist_credit (display_name, normalized_name)
            VALUES (%s, %s) RETURNING artist_credit_id
            """,
            (label, label.lower()),
        )
    )
    return _returned_uuid(
        connection.execute(
            """
            INSERT INTO catalog.recording (artist_credit_id, title, normalized_title)
            VALUES (%s, %s, %s) RETURNING recording_id
            """,
            (artist_credit_id, label, label.lower()),
        )
    )


def test_external_reference_rejects_more_than_one_internal_target(
    database_connection: Connection[Any],
) -> None:
    provider_id = _returned_uuid(
        database_connection.execute(
            """
            INSERT INTO identity.source_provider (
                provider_key, display_name, adapter_id, adapter_version
            ) VALUES ('p02-close', 'P02 close provider', 'p02', '1')
            RETURNING provider_id
            """
        )
    )
    artist_id = _returned_uuid(
        database_connection.execute(
            """
            INSERT INTO catalog.artist (name, sort_name, normalized_name)
            VALUES ('P02 artist', 'P02 artist', 'p02 artist') RETURNING artist_id
            """
        )
    )
    recording_id = _insert_recording(database_connection, "P02 external target")

    with (
        pytest.raises(psycopg.errors.CheckViolation) as exc_info,
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            INSERT INTO identity.external_reference (
                provider_id, external_entity_type, external_id, artist_id, recording_id
            ) VALUES (%s, 'recording', 'two-targets', %s, %s)
            """,
            (provider_id, artist_id, recording_id),
        )
    assert exc_info.value.diag.constraint_name == "ck_external_reference_single_target"


def test_playlist_entry_rejects_cross_user_track_reference(
    database_connection: Connection[Any],
) -> None:
    owner_id = _insert_user(database_connection, "playlist-owner")
    other_id = _insert_user(database_connection, "playlist-other")
    other_ref_id = _insert_user_track_ref(database_connection, other_id)
    playlist_id = _returned_uuid(
        database_connection.execute(
            """
            INSERT INTO playlist.playlist (owner_user_id, name)
            VALUES (%s, 'P02 ownership') RETURNING playlist_id
            """,
            (owner_id,),
        )
    )

    with (
        pytest.raises(
            psycopg.errors.RaiseException,
            match="playlist entry crosses the v1 owner boundary",
        ),
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            INSERT INTO playlist.playlist_entry (
                playlist_id, user_track_ref_id, position_key, added_by_user_id
            ) VALUES (%s, %s, 'a', %s)
            """,
            (playlist_id, other_ref_id, owner_id),
        )


def test_tombstone_rejects_deletion_event_owned_by_another_user(
    database_connection: Connection[Any],
) -> None:
    event_owner_id = _insert_user(database_connection, "event-owner")
    tombstone_owner_id = _insert_user(database_connection, "tombstone-owner")
    event_id = _returned_uuid(
        database_connection.execute(
            """
            INSERT INTO sync.sync_event (
                user_id, event_type, schema_version, aggregate_type, aggregate_id
            ) VALUES (%s, 'DELETE', 1, 'TRACK', %s) RETURNING event_id
            """,
            (event_owner_id, uuid.uuid4()),
        )
    )

    with (
        pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_info,
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            INSERT INTO sync.tombstone (
                user_id, aggregate_type, aggregate_id, deleted_by_event_id,
                deleted_at, retain_until
            ) VALUES (%s, 'TRACK', %s, %s, now(), now() + interval '30 days')
            """,
            (tombstone_owner_id, uuid.uuid4(), event_id),
        )
    assert exc_info.value.diag.constraint_name == "fk_tombstone_event_owner"


def test_taste_cluster_rejects_member_owned_by_another_user(
    database_connection: Connection[Any],
) -> None:
    cluster_owner_id = _insert_user(database_connection, "cluster-owner")
    other_id = _insert_user(database_connection, "cluster-other")
    other_ref_id = _insert_user_track_ref(database_connection, other_id)
    cluster_id = _returned_uuid(
        database_connection.execute(
            """
            INSERT INTO ml.taste_cluster (
                user_id, model_bundle_version, centroid, weight
            ) VALUES (%s, 'p02', '[1,2,3]', 1) RETURNING taste_cluster_id
            """,
            (cluster_owner_id,),
        )
    )

    with (
        pytest.raises(
            psycopg.errors.RaiseException,
            match="taste cluster member crosses user boundary",
        ),
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            INSERT INTO ml.taste_cluster_member (
                taste_cluster_id, user_track_ref_id, membership_score
            ) VALUES (%s, %s, 1)
            """,
            (cluster_id, other_ref_id),
        )


def test_listening_event_rejects_track_reference_owned_by_another_user(
    database_connection: Connection[Any],
) -> None:
    listener_id = _insert_user(database_connection, "listener")
    other_id = _insert_user(database_connection, "track-owner")
    device_id = _insert_device(database_connection, listener_id)
    other_ref_id = _insert_user_track_ref(database_connection, other_id)

    with (
        pytest.raises(
            psycopg.errors.RaiseException,
            match="listening event and user track reference owners differ",
        ),
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            INSERT INTO library.listening_event (
                user_id, device_id, user_track_ref_id, started_at
            ) VALUES (%s, %s, %s, now())
            """,
            (listener_id, device_id, other_ref_id),
        )


def test_listening_event_rejects_recommendation_owned_by_another_user(
    database_connection: Connection[Any],
) -> None:
    listener_id = _insert_user(database_connection, "listener")
    other_id = _insert_user(database_connection, "recommendation-owner")
    device_id = _insert_device(database_connection, listener_id)
    own_ref_id = _insert_user_track_ref(database_connection, listener_id)
    recommendation_id = _returned_uuid(
        database_connection.execute(
            """
            INSERT INTO ml.recommendation_request (
                user_id, model_bundle_version, candidate_policy_version,
                filter_policy_version, reranker_version, seed
            ) VALUES (%s, 'p02', 'p02', 'p02', 'p02', 1)
            RETURNING recommendation_request_id
            """,
            (other_id,),
        )
    )

    with (
        pytest.raises(
            psycopg.errors.RaiseException,
            match="listening event references another user recommendation",
        ),
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            INSERT INTO library.listening_event (
                user_id, device_id, user_track_ref_id, started_at,
                event_origin, recommendation_request_id
            ) VALUES (%s, %s, %s, now(), 'RECOMMENDED', %s)
            """,
            (listener_id, device_id, own_ref_id, recommendation_id),
        )


@pytest.mark.parametrize(
    (
        "commit_status",
        "validation_status",
        "variant_is_deleted",
        "expected_servable",
    ),
    (
        ("COMMITTED", "VALID", False, True),
        ("COMMITTED", "INVALID", False, False),
        ("QUARANTINED", "VALID", False, False),
        ("DELETED", "VALID", False, False),
        ("COMMITTED", "QUARANTINED", False, False),
        ("COMMITTED", "VALID", True, False),
    ),
)
def test_audio_variant_serving_requires_committed_valid_live_state(
    database_connection: Connection[Any],
    commit_status: str,
    validation_status: str,
    variant_is_deleted: bool,
    expected_servable: bool,
) -> None:
    recording_id = _insert_recording(
        database_connection,
        f"P02 serving {commit_status} {validation_status} {variant_is_deleted}",
    )
    marker = uuid.uuid4().bytes
    committed_at = datetime.now(UTC) if commit_status == "COMMITTED" else None
    vault_object_id = _returned_uuid(
        database_connection.execute(
            """
            INSERT INTO vault.vault_object (
                sha256, byte_size, detected_mime_type, commit_status, committed_at
            ) VALUES (%s, 1, 'audio/test', %s, %s) RETURNING vault_object_id
            """,
            (marker + marker, commit_status, committed_at),
        )
    )
    deleted_at = datetime.now(UTC) if variant_is_deleted else None
    audio_variant_id = _returned_uuid(
        database_connection.execute(
            """
            INSERT INTO vault.audio_variant (
                recording_id, vault_object_id, codec, container, sample_rate_hz,
                channels, duration_ms, validation_status, deleted_at
            ) VALUES (%s, %s, 'pcm', 'wav', 48000, 2, 1000, %s, %s)
            RETURNING audio_variant_id
            """,
            (recording_id, vault_object_id, validation_status, deleted_at),
        )
    )

    row = database_connection.execute(
        "SELECT app_private.audio_variant_is_servable(%s)",
        (audio_variant_id,),
    ).fetchone()
    assert row == (expected_servable,)
