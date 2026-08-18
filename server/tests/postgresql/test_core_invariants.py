"""Core P02 relational invariants against real PostgreSQL."""

from __future__ import annotations

import hashlib
import struct
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg import Connection, Cursor


def _id() -> uuid.UUID:
    return uuid.uuid4()


def _returned_uuid(cursor: Cursor[Any]) -> uuid.UUID:
    row = cursor.fetchone()
    if row is None or not isinstance(row[0], uuid.UUID):
        raise AssertionError("INSERT ... RETURNING did not return a UUID")
    return row[0]


def _insert_user(connection: Connection[Any], *, role: str = "USER") -> uuid.UUID:
    return _returned_uuid(
        connection.execute(
            """
            INSERT INTO account.user_account (display_name, role)
            VALUES (%s, %s) RETURNING user_id
            """,
            (f"user-{_id().hex[:8]}", role),
        )
    )


def _insert_device(connection: Connection[Any], user_id: uuid.UUID) -> uuid.UUID:
    return _returned_uuid(
        connection.execute(
            """
        INSERT INTO account.device (user_id, device_name, platform, app_version)
        VALUES (%s, %s, 'ANDROID', 'p02') RETURNING device_id
        """,
            (user_id, f"device-{_id().hex[:8]}"),
        )
    )


def _insert_recording(connection: Connection[Any], title: str) -> uuid.UUID:
    artist_credit_id = _returned_uuid(
        connection.execute(
            """
        INSERT INTO catalog.artist_credit (display_name, normalized_name)
        VALUES (%s, %s) RETURNING artist_credit_id
        """,
            (f"artist-{title}", f"artist-{title}"),
        )
    )
    return _returned_uuid(
        connection.execute(
            """
        INSERT INTO catalog.recording (artist_credit_id, title, normalized_title)
        VALUES (%s, %s, %s) RETURNING recording_id
        """,
            (artist_credit_id, title, title.lower()),
        )
    )


def _insert_user_track_ref(connection: Connection[Any], user_id: uuid.UUID) -> uuid.UUID:
    return _returned_uuid(
        connection.execute(
            """
            INSERT INTO library.user_track_ref (user_id, raw_title)
            VALUES (%s, 'P02 fixture') RETURNING user_track_ref_id
            """,
            (user_id,),
        )
    )


def _prepare_active_policy(
    connection: Connection[Any], actor_user_id: uuid.UUID
) -> tuple[str, str, str]:
    suffix = _id().hex
    matcher = f"matcher-{suffix}"
    calibrator = f"calibrator-{suffix}"
    threshold = f"threshold-{suffix}"
    connection.execute(
        """
        INSERT INTO identity.matcher_release (
            matcher_version, candidate_generation_version, normalization_version,
            feature_extractor_versions, feature_schema_version, manifest_sha256
        ) VALUES (%s, %s, %s, '{}'::jsonb, '1', %s)
        """,
        (
            matcher,
            f"generator-{suffix}",
            f"normalizer-{suffix}",
            hashlib.sha256(suffix.encode()).digest(),
        ),
    )
    connection.execute(
        """
        INSERT INTO identity.calibrator_release (
            calibrator_version, matcher_version, evidence_mode,
            artifact_sha256, input_schema_version
        ) VALUES (%s, %s, 'METADATA_ONLY', %s, '1')
        """,
        (calibrator, matcher, hashlib.sha256(f"cal-{suffix}".encode()).digest()),
    )
    connection.execute(
        """
        INSERT INTO identity.threshold_set (
            threshold_set_version, matcher_version, calibrator_version,
            evidence_mode, minimum_evidence_tier, auto_threshold,
            review_threshold, margin_threshold, benchmark_report_sha256,
            gate_metadata, gate_metadata_schema_version
        ) VALUES (%s, %s, %s, 'METADATA_ONLY', 'T0', 0, 0, 0, %s, '{}'::jsonb, '1')
        """,
        (threshold, matcher, calibrator, hashlib.sha256(f"benchmark-{suffix}".encode()).digest()),
    )
    connection.execute(
        """
        INSERT INTO identity.match_policy_activation (
            evidence_mode, evidence_tier, sequence_no, action,
            threshold_set_version, actor_user_id, reason
        ) VALUES ('METADATA_ONLY', 'T0', 1, 'ACTIVATE', %s, %s, 'P02 invariant fixture')
        """,
        (threshold, actor_user_id),
    )
    return matcher, calibrator, threshold


def _append_applied_auto_match(
    connection: Connection[Any],
    *,
    user_id: uuid.UUID,
    user_track_ref_id: uuid.UUID,
    selected_recording_id: uuid.UUID,
    second_recording_id: uuid.UUID,
    matcher: str,
    calibrator: str,
    threshold: str,
) -> uuid.UUID:
    evidence_hashes = (
        hashlib.sha256(f"candidate-1-{_id()}".encode()).digest(),
        hashlib.sha256(f"candidate-2-{_id()}".encode()).digest(),
    )
    aggregate_hash = hashlib.sha256(
        b"".join(
            struct.pack("!i", rank) + evidence_hash
            for rank, evidence_hash in enumerate(evidence_hashes, start=1)
        )
    ).digest()
    suffix = _id().hex
    decision_id = _returned_uuid(
        connection.execute(
            """
            INSERT INTO identity.match_decision (
                query_type, owner_user_id, user_track_ref_id, query_snapshot,
                query_snapshot_schema_version, snapshot_canonicalization_version,
                query_snapshot_sha256, decision_kind, execution_mode,
                candidate_recording_id, decision_state, candidate_count,
                candidate_evidence_sha256, candidate_evidence_size_bytes,
                evidence_mode, candidate_generation_version, normalization_version,
                feature_extractor_versions, matcher_version, calibrator_version,
                threshold_set_version, raw_score, confidence, top2_confidence,
                margin, evidence_tier, feature_scores, hard_conflicts,
                candidate_origins, explanation_schema_version, actor_type,
                idempotency_scope, idempotency_key, request_sha256
            ) VALUES (
                'USER_TRACK_REF', %s, %s, '{}'::jsonb, '1', 'RFC8785', %s,
                'EVALUATION', 'APPLIED', %s, 'AUTO_MATCH', 2, %s, 4,
                'METADATA_ONLY', %s, %s, '{}'::jsonb, %s, %s, %s,
                1, 1, 0, 1, 'T0', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                '1', 'SYSTEM', 'p02-core', %s, %s
            ) RETURNING decision_id
            """,
            (
                user_id,
                user_track_ref_id,
                hashlib.sha256(b"{}").digest(),
                selected_recording_id,
                aggregate_hash,
                f"generator-{matcher.removeprefix('matcher-')}",
                f"normalizer-{matcher.removeprefix('matcher-')}",
                matcher,
                calibrator,
                threshold,
                suffix,
                hashlib.sha256(f"request-{suffix}".encode()).digest(),
            ),
        )
    )
    connection.execute(
        """
        INSERT INTO identity.match_candidate_evidence (
            decision_id, recording_id, rank, raw_score, confidence,
            evidence_tier, feature_scores, hard_conflicts, candidate_origins,
            extractor_versions, evidence_schema_version, evidence_sha256,
            evidence_document_size_bytes
        ) VALUES
            (%s, %s, 1, 1, 1, 'T0', '[]', '[]', '[]', '{}', '1', %s, 2),
            (%s, %s, 2, 0, 0, 'T0', '[]', '[]', '[]', '{}', '1', %s, 2)
        """,
        (
            decision_id,
            selected_recording_id,
            evidence_hashes[0],
            decision_id,
            second_recording_id,
            evidence_hashes[1],
        ),
    )
    connection.execute(
        """
        UPDATE library.user_track_ref
        SET recording_id = %s, resolution_status = 'RESOLVED', resolved_at = now(),
            resolution_confidence = 1, current_match_decision_id = %s
        WHERE user_track_ref_id = %s
        """,
        (selected_recording_id, decision_id, user_track_ref_id),
    )
    return decision_id


def _insert_vault_object(connection: Connection[Any], marker: int) -> uuid.UUID:
    return _returned_uuid(
        connection.execute(
            """
        INSERT INTO vault.vault_object (sha256, byte_size, detected_mime_type)
        VALUES (%s, 1, 'audio/test') RETURNING vault_object_id
        """,
            (bytes([marker]) * 32,),
        )
    )


def _insert_variant(connection: Connection[Any], recording_id: uuid.UUID, marker: int) -> uuid.UUID:
    vault_object_id = _insert_vault_object(connection, marker)
    return _returned_uuid(
        connection.execute(
            """
        INSERT INTO vault.audio_variant (
            recording_id, vault_object_id, codec, container,
            sample_rate_hz, channels, duration_ms
        ) VALUES (%s, %s, 'pcm', 'wav', 48000, 2, 1000)
        RETURNING audio_variant_id
        """,
            (recording_id, vault_object_id),
        )
    )


def _assert_constraint(exc_info: pytest.ExceptionInfo[psycopg.IntegrityError], name: str) -> None:
    assert exc_info.value.diag.constraint_name == name


@pytest.mark.parametrize("length", [31, 33])
def test_vault_rejects_invalid_sha256_lengths(
    database_connection: Connection[Any], length: int
) -> None:
    with pytest.raises(psycopg.errors.CheckViolation) as exc_info:
        database_connection.execute(
            """
            INSERT INTO vault.vault_object (sha256, byte_size, detected_mime_type)
            VALUES (%s, 1, 'audio/test')
            """,
            (b"x" * length,),
        )
    _assert_constraint(exc_info, "ck_vault_object_sha256_len")


def test_vault_accepts_exact_sha_and_rejects_duplicate(
    database_connection: Connection[Any],
) -> None:
    sha256 = b"v" * 32
    database_connection.execute(
        """
        INSERT INTO vault.vault_object (sha256, byte_size, detected_mime_type)
        VALUES (%s, 1, 'audio/test')
        """,
        (sha256,),
    )
    with pytest.raises(psycopg.errors.UniqueViolation) as exc_info:
        database_connection.execute(
            """
            INSERT INTO vault.vault_object (sha256, byte_size, detected_mime_type)
            VALUES (%s, 2, 'audio/test')
            """,
            (sha256,),
        )
    _assert_constraint(exc_info, "vault_object_sha256_key")


def test_active_user_track_and_library_uniqueness_respects_tombstones(
    database_connection: Connection[Any],
) -> None:
    user_id = _insert_user(database_connection, role="OWNER")
    recording_id = _insert_recording(database_connection, "active-unique")
    second_recording_id = _insert_recording(database_connection, "active-unique-second")
    matcher, calibrator, threshold = _prepare_active_policy(database_connection, user_id)
    first_ref = _insert_user_track_ref(database_connection, user_id)
    _append_applied_auto_match(
        database_connection,
        user_id=user_id,
        user_track_ref_id=first_ref,
        selected_recording_id=recording_id,
        second_recording_id=second_recording_id,
        matcher=matcher,
        calibrator=calibrator,
        threshold=threshold,
    )
    database_connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
    database_connection.execute("SET CONSTRAINTS ALL DEFERRED")
    first_entry = _returned_uuid(
        database_connection.execute(
            """
        INSERT INTO library.library_entry (user_id, user_track_ref_id, source)
        VALUES (%s, %s, 'LOCAL') RETURNING library_entry_id
        """,
            (user_id, first_ref),
        )
    )

    duplicate_ref = _insert_user_track_ref(database_connection, user_id)
    with (
        pytest.raises(psycopg.errors.UniqueViolation) as ref_exc,
        database_connection.transaction(),
    ):
        _append_applied_auto_match(
            database_connection,
            user_id=user_id,
            user_track_ref_id=duplicate_ref,
            selected_recording_id=recording_id,
            second_recording_id=second_recording_id,
            matcher=matcher,
            calibrator=calibrator,
            threshold=threshold,
        )
    assert ref_exc.value.diag.constraint_name == "uq_user_track_ref_active_recording"

    with (
        pytest.raises(psycopg.errors.UniqueViolation) as entry_exc,
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            INSERT INTO library.library_entry (user_id, user_track_ref_id, source)
            VALUES (%s, %s, 'RESTORE')
            """,
            (user_id, first_ref),
        )
    assert entry_exc.value.diag.constraint_name == "uq_library_entry_active"

    now = datetime.now(UTC)
    database_connection.execute(
        "UPDATE library.library_entry SET removed_at = %s WHERE library_entry_id = %s",
        (now, first_entry),
    )
    database_connection.execute(
        "UPDATE library.user_track_ref SET deleted_at = %s WHERE user_track_ref_id = %s",
        (now, first_ref),
    )
    replacement_ref = _insert_user_track_ref(database_connection, user_id)
    _append_applied_auto_match(
        database_connection,
        user_id=user_id,
        user_track_ref_id=replacement_ref,
        selected_recording_id=recording_id,
        second_recording_id=second_recording_id,
        matcher=matcher,
        calibrator=calibrator,
        threshold=threshold,
    )
    database_connection.execute(
        """
        INSERT INTO library.library_entry (user_id, user_track_ref_id, source)
        VALUES (%s, %s, 'RESTORE')
        """,
        (user_id, replacement_ref),
    )
    database_connection.execute("SET CONSTRAINTS ALL IMMEDIATE")


def test_playlist_allows_duplicate_track_but_rejects_active_position(
    database_connection: Connection[Any],
) -> None:
    user_id = _insert_user(database_connection)
    ref_id = _insert_user_track_ref(database_connection, user_id)
    playlist_id = _returned_uuid(
        database_connection.execute(
            """
        INSERT INTO playlist.playlist (owner_user_id, name)
        VALUES (%s, 'p') RETURNING playlist_id
        """,
            (user_id,),
        )
    )
    database_connection.execute(
        """
        INSERT INTO playlist.playlist_entry (
            playlist_id, user_track_ref_id, position_key, added_by_user_id
        ) VALUES (%s, %s, 'a', %s), (%s, %s, 'b', %s)
        """,
        (playlist_id, ref_id, user_id, playlist_id, ref_id, user_id),
    )
    with pytest.raises(psycopg.errors.UniqueViolation) as exc_info:
        database_connection.execute(
            """
            INSERT INTO playlist.playlist_entry (
                playlist_id, user_track_ref_id, position_key, added_by_user_id
            ) VALUES (%s, %s, 'a', %s)
            """,
            (playlist_id, ref_id, user_id),
        )
    _assert_constraint(exc_info, "uq_playlist_entry_active_position")


def test_cross_user_device_ownership_is_rejected(
    database_connection: Connection[Any],
) -> None:
    owner = _insert_user(database_connection)
    other = _insert_user(database_connection)
    device = _insert_device(database_connection, owner)
    with pytest.raises(psycopg.errors.ForeignKeyViolation) as exc_info:
        database_connection.execute(
            """
            INSERT INTO account.user_session (
                user_id, device_id, refresh_token_hash, expires_at
            ) VALUES (%s, %s, %s, %s)
            """,
            (other, device, b"s" * 32, datetime.now(UTC) + timedelta(hours=1)),
        )
    _assert_constraint(exc_info, "fk_user_session_device_owner")


def test_event_sequence_and_idempotency_uniqueness(
    database_connection: Connection[Any],
) -> None:
    user_id = _insert_user(database_connection)
    device_id = _insert_device(database_connection, user_id)
    aggregate_id = _id()
    database_connection.execute(
        """
        INSERT INTO sync.device_event_inbox (
            event_id, device_id, user_id, device_sequence, event_type,
            schema_version, aggregate_type, aggregate_id, occurred_at, request_hash
        ) VALUES (%s, %s, %s, 1, 'TEST', 1, 'TEST', %s, now(), %s)
        """,
        (_id(), device_id, user_id, aggregate_id, b"e" * 32),
    )
    with (
        pytest.raises(psycopg.errors.UniqueViolation) as sequence_exc,
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
            INSERT INTO sync.device_event_inbox (
                event_id, device_id, user_id, device_sequence, event_type,
                schema_version, aggregate_type, aggregate_id, occurred_at, request_hash
            ) VALUES (%s, %s, %s, 1, 'TEST', 1, 'TEST', %s, now(), %s)
            """,
            (_id(), device_id, user_id, aggregate_id, b"f" * 32),
        )
    assert sequence_exc.value.diag.constraint_name == "uq_device_event_sequence"

    database_connection.execute(
        """
        INSERT INTO sync.idempotency_record (
            scope, idempotency_key, request_hash, expires_at
        ) VALUES ('scope', 'key', %s, now() + interval '1 hour')
        """,
        (b"i" * 32,),
    )
    with pytest.raises(psycopg.errors.UniqueViolation) as idempotency_exc:
        database_connection.execute(
            """
            INSERT INTO sync.idempotency_record (
                scope, idempotency_key, request_hash, expires_at
            ) VALUES ('scope', 'key', %s, now() + interval '1 hour')
            """,
            (b"j" * 32,),
        )
    assert idempotency_exc.value.diag.constraint_name == "idempotency_record_pkey"


def test_canonical_variant_must_belong_to_same_recording(
    database_connection: Connection[Any],
) -> None:
    recording_a = _insert_recording(database_connection, "canonical-a")
    recording_b = _insert_recording(database_connection, "canonical-b")
    variant_a = _insert_variant(database_connection, recording_a, 1)
    with pytest.raises(psycopg.errors.RaiseException, match="another recording"):
        database_connection.execute(
            """
            INSERT INTO vault.recording_canonical_variant (
                recording_id, audio_variant_id, policy_version
            ) VALUES (%s, %s, 'p02')
            """,
            (recording_b, variant_a),
        )


def test_recording_redirect_cycle_is_rejected(database_connection: Connection[Any]) -> None:
    recordings = [_insert_recording(database_connection, f"redirect-{index}") for index in range(3)]
    change_set = _returned_uuid(
        database_connection.execute(
            """
        INSERT INTO audit.catalog_change_set (
            operation_type, actor_type, reason
        ) VALUES ('MERGE', 'SYSTEM', 'cycle test') RETURNING change_set_id
        """
        )
    )
    database_connection.execute(
        """
        INSERT INTO identity.recording_redirect (
            source_recording_id, target_recording_id, change_set_id, reason
        ) VALUES (%s, %s, %s, 'a-b'), (%s, %s, %s, 'b-c')
        """,
        (recordings[0], recordings[1], change_set, recordings[1], recordings[2], change_set),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="cycle"):
        database_connection.execute(
            """
            INSERT INTO identity.recording_redirect (
                source_recording_id, target_recording_id, change_set_id, reason
            ) VALUES (%s, %s, %s, 'c-a')
            """,
            (recordings[2], recordings[0], change_set),
        )


def test_job_dependency_cycle_is_rejected(database_connection: Connection[Any]) -> None:
    jobs = [
        _returned_uuid(
            database_connection.execute(
                """
                INSERT INTO jobs.job (job_type, schema_version)
                VALUES ('TEST', 1) RETURNING job_id
                """
            )
        )
        for _ in range(3)
    ]
    database_connection.execute(
        """
        INSERT INTO jobs.job_dependency (job_id, depends_on_job_id)
        VALUES (%s, %s), (%s, %s)
        """,
        (jobs[0], jobs[1], jobs[1], jobs[2]),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="cycle"):
        database_connection.execute(
            "INSERT INTO jobs.job_dependency (job_id, depends_on_job_id) VALUES (%s, %s)",
            (jobs[2], jobs[0]),
        )


def test_embedding_dimension_and_recording_integrity(
    database_connection: Connection[Any],
) -> None:
    recording_a = _insert_recording(database_connection, "embedding-a")
    recording_b = _insert_recording(database_connection, "embedding-b")
    variant_a = _insert_variant(database_connection, recording_a, 2)
    model_id = _returned_uuid(
        database_connection.execute(
            """
        INSERT INTO ml.embedding_model (
            model_key, version, task, source, source_revision, artifact_filename,
            artifact_format, artifact_byte_size, artifact_manifest, manifest_sha256,
            weights_sha256, license_id, runtime, runtime_revision,
            inference_precision, input_sample_rate_hz, segment_duration_ms,
            preprocessing_version, preprocessing_manifest, preprocessing_sha256,
            pooling_strategy, dimension, license_review_reference
        ) VALUES (
            'test', '1', 'EMBEDDING', 'fixture://model', 'fixture-revision',
            'model.bin', 'fixture', 1, '{}'::jsonb, %s, %s, 'test', 'cpu', 'fixture-runtime',
            'fp32', 48000, 1000, '1', '{}'::jsonb, %s, 'mean', 3, 'fixture-review'
        ) RETURNING embedding_model_id
        """,
            (b"a" * 32, b"m" * 32, b"p" * 32),
        )
    )

    with (
        pytest.raises(psycopg.errors.RaiseException, match="dimension"),
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
                INSERT INTO ml.recording_embedding (
                    recording_id, embedding_model_id, audio_variant_id, embedding
                ) VALUES (%s, %s, %s, '[1,2]')
                """,
            (recording_a, model_id, variant_a),
        )

    with (
        pytest.raises(psycopg.errors.RaiseException, match="another recording"),
        database_connection.transaction(),
    ):
        database_connection.execute(
            """
                INSERT INTO ml.recording_embedding (
                    recording_id, embedding_model_id, audio_variant_id, embedding
                ) VALUES (%s, %s, %s, '[1,2,3]')
                """,
            (recording_b, model_id, variant_a),
        )

    embedding_id = _returned_uuid(
        database_connection.execute(
            """
        INSERT INTO ml.recording_embedding (
            recording_id, embedding_model_id, audio_variant_id, embedding
        ) VALUES (%s, %s, %s, '[1,2,3]') RETURNING recording_embedding_id
        """,
            (recording_a, model_id, variant_a),
        )
    )
    assert embedding_id is not None
