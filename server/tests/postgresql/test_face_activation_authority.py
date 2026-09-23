"""Dormant Face authority starts disabled and fences lifecycle writes."""

from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

from .conftest import DatabaseHarness


def test_face_qualification_state_and_default_activation(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        assert connection.execute(
            "SELECT activation_epoch,face_timeline_activation_id "
            "FROM ml.face_activation_current WHERE singleton=1"
        ).fetchone() == (0, None)
        with (
            pytest.raises(psycopg.Error, match="face_activation_evidence_immutable"),
            connection.transaction(),
        ):
            connection.execute("DELETE FROM ml.face_activation_current WHERE singleton=1")
        policy_count = connection.execute(
            "SELECT count(*) FROM ml.face_analysis_policy_current"
        ).fetchone()
        assert policy_count == (0,)

        values = (
            b"m" * 32,
            b"f" * 32,
            b"r" * 32,
        )
        with (
            pytest.raises(psycopg.Error, match="face_qualification_initial_state_invalid"),
            connection.transaction(),
        ):
            connection.execute(
                "INSERT INTO ml.face_qualification_set "
                "(manifest_sha256,collection_kind,fixture_authority_sha256,"
                "fixture_authority_generation,rater_authority_sha256,"
                "rater_authority_generation,source_count,segment_count,rater_count,"
                "retain_until,sealed_at,state,invalidated_at) "
                "VALUES(%s,'FINAL',%s,1,%s,1,15,180,3,"
                "now()+interval '90 days',now(),'DELETED',now())",
                values,
            )
        row = connection.execute(
            "INSERT INTO ml.face_qualification_set "
            "(manifest_sha256,collection_kind,fixture_authority_sha256,"
            "fixture_authority_generation,rater_authority_sha256,"
            "rater_authority_generation,source_count,segment_count,rater_count,"
            "retain_until,sealed_at) "
            "VALUES(%s,'FINAL',%s,1,%s,1,15,180,3,"
            "now()+interval '90 days',now()) RETURNING face_qualification_set_id",
            values,
        ).fetchone()
        assert row is not None
        qualification_id = row[0]
        with (
            pytest.raises(psycopg.Error, match="face_qualification_state_conflict"),
            connection.transaction(),
        ):
            connection.execute(
                "UPDATE ml.face_qualification_set SET state='DELETED',"
                "invalidated_at=now() WHERE face_qualification_set_id=%s",
                (qualification_id,),
            )
        connection.execute(
            "UPDATE ml.face_qualification_set SET state='INVALIDATED',"
            "state_generation=2,invalidated_at=now() WHERE face_qualification_set_id=%s",
            (qualification_id,),
        )
        connection.execute(
            "UPDATE ml.face_qualification_set SET state='DELETED',"
            "state_generation=3,invalidated_at=now() WHERE face_qualification_set_id=%s",
            (qualification_id,),
        )
        assert connection.execute(
            "SELECT state,state_generation FROM ml.face_qualification_set "
            "WHERE face_qualification_set_id=%s",
            (qualification_id,),
        ).fetchone() == ("DELETED", 3)
        connection.commit()


def test_face_policy_projection_and_owner_deletion(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        owner = connection.execute(
            "INSERT INTO account.user_account(display_name,role) "
            "VALUES('face-policy-owner','USER') RETURNING user_id"
        ).fetchone()
        assert owner is not None
        owner_id = owner[0]
        event = connection.execute(
            "INSERT INTO ml.face_analysis_policy_history "
            "(owner_user_id,policy_generation,desired_enabled,admin_inhibited,"
            "activation_epoch,actor_user_id,actor_role,scope,reason,operation_id,"
            "request_sha256) "
            "VALUES(%s,1,false,false,0,%s,'USER','SELF','initial-off',%s,%s) "
            "RETURNING face_analysis_policy_event_id",
            (owner_id, owner_id, uuid4(), b"p" * 32),
        ).fetchone()
        assert event is not None
        connection.execute(
            "INSERT INTO ml.face_analysis_policy_current "
            "(owner_user_id,policy_generation,desired_enabled,admin_inhibited,"
            "activation_epoch,face_analysis_policy_event_id) "
            "VALUES(%s,1,false,false,0,%s)",
            (owner_id, event[0]),
        )
        with (
            pytest.raises(psycopg.Error, match="face_policy_projection_mismatch"),
            connection.transaction(),
        ):
            connection.execute(
                "UPDATE ml.face_analysis_policy_current SET desired_enabled=true,"
                "enable_watermark=now() WHERE owner_user_id=%s",
                (owner_id,),
            )
        connection.execute("DELETE FROM account.user_account WHERE user_id=%s", (owner_id,))
        assert connection.execute(
            "SELECT count(*) FROM ml.face_analysis_policy_history WHERE owner_user_id=%s",
            (owner_id,),
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM ml.face_analysis_policy_current WHERE owner_user_id=%s",
            (owner_id,),
        ).fetchone() == (0,)
        connection.commit()


def test_face_policy_sql_rejects_role_spoof_and_initial_safety_grant(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        participant = connection.execute(
            "INSERT INTO account.user_account(display_name,role) "
            "VALUES('face-policy-participant','USER') RETURNING user_id"
        ).fetchone()
        admin = connection.execute(
            "INSERT INTO account.user_account(display_name,role) "
            "VALUES('face-policy-admin','ADMIN') RETURNING user_id"
        ).fetchone()
        assert participant is not None and admin is not None
        owner_id = participant[0]
        admin_id = admin[0]
        connection.commit()

        with (
            pytest.raises(psycopg.Error, match="face_policy_actor_forbidden"),
            connection.transaction(),
        ):
            connection.execute(
                "INSERT INTO ml.face_analysis_policy_history "
                "(owner_user_id,policy_generation,desired_enabled,admin_inhibited,"
                "activation_epoch,actor_user_id,actor_role,scope,reason,operation_id,"
                "request_sha256) "
                "VALUES(%s,1,false,false,0,%s,'ADMIN','SAFETY','spoofed',%s,%s)",
                (owner_id, owner_id, uuid4(), b"h" * 32),
            )

        with (
            pytest.raises(psycopg.Error, match="face_policy_initial_generation"),
            connection.transaction(),
        ):
            event = connection.execute(
                "INSERT INTO ml.face_analysis_policy_history "
                "(owner_user_id,policy_generation,desired_enabled,admin_inhibited,"
                "enable_watermark,activation_epoch,actor_user_id,actor_role,scope,reason,operation_id,"
                "request_sha256) "
                "VALUES(%s,1,true,true,now(),0,%s,'ADMIN','SAFETY','grant',%s,%s) "
                "RETURNING face_analysis_policy_event_id",
                (owner_id, admin_id, uuid4(), b"g" * 32),
            ).fetchone()
            assert event is not None
            connection.execute(
                "INSERT INTO ml.face_analysis_policy_current "
                "(owner_user_id,policy_generation,desired_enabled,admin_inhibited,"
                "enable_watermark,activation_epoch,face_analysis_policy_event_id) "
                "VALUES(%s,1,true,true,now(),0,%s)",
                (owner_id, event[0]),
            )
        assert connection.execute(
            "SELECT count(*) FROM ml.face_analysis_policy_history WHERE owner_user_id=%s",
            (owner_id,),
        ).fetchone() == (0,)
