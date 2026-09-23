"""A reviewed artifact successor is atomic and generation fenced."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.artifact_license import (
    SqlAlchemyArtifactLicenseAuthority,
)
from autplay.application.face_artifact_policy import (
    FaceArtifactPolicyError,
    RequiredArtifact,
)
from autplay.domain.artifact_license_review import (
    ArtifactLicenseReview,
    ArtifactLicenseReviewError,
    LicenseDecisionState,
)

from .conftest import DatabaseHarness
from .test_ml_artifact_authority import _legacy_model

ARTIFACT = b"a" * 32


def _seed(database_harness: DatabaseHarness, name: str) -> tuple[UUID, UUID]:
    with database_harness.connect(name) as connection:
        reviewer = connection.execute(
            "INSERT INTO account.user_account(display_name,role) "
            "VALUES('reviewer','OWNER') RETURNING user_id"
        ).fetchone()
        ordinary = connection.execute(
            "INSERT INTO account.user_account(display_name,role) "
            "VALUES('ordinary','USER') RETURNING user_id"
        ).fetchone()
        assert reviewer is not None and ordinary is not None
        connection.execute(
            """
            INSERT INTO ml.artifact(artifact_sha256,artifact_byte_size,artifact_format,
              source,source_revision,manifest_sha256,artifact_manifest)
            VALUES(%s,100,'ONNX','fixture://license','r1',%s,'{}'::jsonb)
            """,
            (ARTIFACT, b"m" * 32),
        )
        connection.execute(
            """
            INSERT INTO ml.artifact_license_decision(
              artifact_sha256,decision_sequence,effective_generation,state)
            VALUES(%s,1,1,'LEGACY_UNREVIEWED')
            """,
            (ARTIFACT,),
        )
        connection.commit()
        return reviewer[0], ordinary[0]


def _review(state: LicenseDecisionState, *, lag_ms: int) -> ArtifactLicenseReview:
    return ArtifactLicenseReview(
        state=state,
        license_identifier="LicenseRef-1",
        license_text_sha256=b"l" * 32,
        use_restrictions={"non_commercial": True},
        redistribution_decision="SEPARATE_INSTALL_ONLY",
        modification_decision="DENIED",
        attribution_payload={"notice": "Operator provided"},
        review_reference="review-record-1",
        max_offline_revocation_lag_ms=lag_ms,
        derived_output_disposition="DELETE_AFTER_LEASE",
    )


def test_review_successor_and_revocation_are_current_only_after_commit(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    reviewer, ordinary = _seed(database_harness, empty_database_name)
    engine = create_engine(database_harness.database_url(empty_database_name))
    sessions = sessionmaker(engine, class_=Session)
    authority = SqlAlchemyArtifactLicenseAuthority()
    try:
        with sessions.begin() as session:
            with pytest.raises(ArtifactLicenseReviewError, match="reviewer_forbidden"):
                authority.append_review(
                    session,
                    artifact_sha256=ARTIFACT,
                    reviewer_user_id=ordinary,
                    expected_generation=1,
                    review=_review(LicenseDecisionState.APPROVED, lag_ms=86_400_000),
                )
            assert session.scalar(text("SELECT count(*) FROM ml.artifact_license_decision")) == 1

        with sessions.begin() as session:
            approved = authority.append_review(
                session,
                artifact_sha256=ARTIFACT,
                reviewer_user_id=reviewer,
                expected_generation=1,
                review=_review(LicenseDecisionState.APPROVED, lag_ms=86_400_000),
            )
            assert (approved.decision_sequence, approved.effective_generation) == (2, 2)
            assert session.execute(
                text("""
                    SELECT state,effective_generation FROM ml.artifact_license_current
                    WHERE artifact_sha256=:artifact
                """),
                {"artifact": ARTIFACT},
            ).one() == ("APPROVED", 2)

        with sessions.begin() as session:
            with pytest.raises(ArtifactLicenseReviewError, match="generation_conflict"):
                authority.append_review(
                    session,
                    artifact_sha256=ARTIFACT,
                    reviewer_user_id=reviewer,
                    expected_generation=1,
                    review=_review(LicenseDecisionState.REVOKED, lag_ms=0),
                )
            revoked = authority.append_review(
                session,
                artifact_sha256=ARTIFACT,
                reviewer_user_id=reviewer,
                expected_generation=2,
                review=_review(LicenseDecisionState.REVOKED, lag_ms=0),
            )
            assert revoked.decision_sequence == revoked.effective_generation == 3

        with sessions() as session:
            assert session.execute(
                text("""
                    SELECT state,effective_generation FROM ml.artifact_license_current
                    WHERE artifact_sha256=:artifact
                """),
                {"artifact": ARTIFACT},
            ).one() == ("REVOKED", 3)
            assert session.scalar(text("SELECT count(*) FROM ml.artifact_license_decision")) == 3
    finally:
        engine.dispose()


def test_review_rollback_leaves_current_decision_untouched(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    reviewer, _ = _seed(database_harness, empty_database_name)
    engine = create_engine(database_harness.database_url(empty_database_name))
    sessions = sessionmaker(engine, class_=Session)
    try:
        with (
            pytest.raises(RuntimeError, match="injected_failure"),
            sessions.begin() as session,
        ):
            SqlAlchemyArtifactLicenseAuthority().append_review(
                session,
                artifact_sha256=ARTIFACT,
                reviewer_user_id=reviewer,
                expected_generation=1,
                review=_review(LicenseDecisionState.APPROVED, lag_ms=86_400_000),
            )
            raise RuntimeError("injected_failure")
        with sessions() as session:
            assert session.execute(
                text("""
                    SELECT state,effective_generation FROM ml.artifact_license_current
                    WHERE artifact_sha256=:artifact
                """),
                {"artifact": ARTIFACT},
            ).one() == ("LEGACY_UNREVIEWED", 1)
            assert session.scalar(text("SELECT count(*) FROM ml.artifact_license_decision")) == 1
    finally:
        engine.dispose()


def test_approval_requires_positive_reviewed_offline_lag() -> None:
    with pytest.raises(ArtifactLicenseReviewError, match="review_invalid"):
        _review(LicenseDecisionState.APPROVED, lag_ms=0)
    with pytest.raises(ArtifactLicenseReviewError, match="review_invalid"):
        _review(LicenseDecisionState.DENIED, lag_ms=1)
    with pytest.raises(ArtifactLicenseReviewError, match="review_invalid"):
        replace(
            _review(LicenseDecisionState.APPROVED, lag_ms=1),
            max_offline_revocation_lag_ms=True,
        )
    with pytest.raises(ArtifactLicenseReviewError, match="review_invalid"):
        replace(
            _review(LicenseDecisionState.APPROVED, lag_ms=1),
            use_restrictions={"invalid": float("nan")},
        )


def test_face_tuple_freeze_holds_current_decisions_against_revocation(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    reviewer, _ = _seed(database_harness, empty_database_name)
    second = b"b" * 32
    with database_harness.connect(empty_database_name) as connection:
        connection.execute(
            """
            INSERT INTO ml.artifact(artifact_sha256,artifact_byte_size,artifact_format,
              source,source_revision,manifest_sha256,artifact_manifest)
            VALUES(%s,200,'ONNX','fixture://interpreter','r1',%s,'{}'::jsonb)
            """,
            (second, b"n" * 32),
        )
        connection.execute(
            """
            INSERT INTO ml.artifact_license_decision(
              artifact_sha256,decision_sequence,effective_generation,state)
            VALUES(%s,1,1,'LEGACY_UNREVIEWED')
            """,
            (second,),
        )
        connection.commit()
    engine = create_engine(database_harness.database_url(empty_database_name))
    sessions = sessionmaker(engine, class_=Session)
    authority = SqlAlchemyArtifactLicenseAuthority()
    required = (
        RequiredArtifact("ENCODER_WEIGHTS", ARTIFACT),
        RequiredArtifact("INTERPRETER_EXPORT", second),
    )
    cardinalities = {
        "ENCODER_WEIGHTS": 1,
        "INTERPRETER_EXPORT": 1,
        "CALIBRATION": 0,
        "PREPROCESSING_EXECUTABLE": 0,
        "DECODER_PROBE": 0,
        "TIMELINE_CODEC": 0,
    }
    try:
        with sessions.begin() as session:
            authority.append_review(
                session,
                artifact_sha256=ARTIFACT,
                reviewer_user_id=reviewer,
                expected_generation=1,
                review=replace(
                    _review(LicenseDecisionState.APPROVED, lag_ms=172_800_000),
                    derived_output_disposition="RETAIN_NON_DISTRIBUTABLE",
                ),
            )
            authority.append_review(
                session,
                artifact_sha256=second,
                reviewer_user_id=reviewer,
                expected_generation=1,
                review=_review(LicenseDecisionState.APPROVED, lag_ms=86_400_000),
            )
        with sessions.begin() as holder:
            frozen = authority.freeze_current_face_policy(
                holder, required=required, cardinalities=cardinalities
            )
            assert frozen.offline_lease_ms == 86_400_000
            assert frozen.derived_output_disposition == "DELETE_AFTER_LEASE"
            assert len(frozen.required_set.sha256) == len(frozen.policy_list.sha256) == 32
            with database_harness.connect(empty_database_name) as contender:
                contender.execute("SET lock_timeout = '200ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    contender.execute(
                        """
                        INSERT INTO ml.artifact_license_decision(
                          artifact_sha256,decision_sequence,effective_generation,
                          superseded_sequence,state,license_identifier,
                          license_text_sha256,redistribution_decision,
                          modification_decision,reviewer_user_id,reviewed_at,
                          review_reference,max_offline_revocation_lag_ms)
                        VALUES(%s,3,3,2,'REVOKED','LicenseRef-1',%s,
                          'SEPARATE_INSTALL_ONLY','DENIED',%s,now(),'revoked',0)
                        """,
                        (second, b"l" * 32, reviewer),
                    )
                contender.rollback()
        with sessions.begin() as session:
            authority.append_review(
                session,
                artifact_sha256=second,
                reviewer_user_id=reviewer,
                expected_generation=2,
                review=_review(LicenseDecisionState.REVOKED, lag_ms=0),
            )
        with (
            sessions.begin() as session,
            pytest.raises(FaceArtifactPolicyError, match="artifact_not_approved"),
        ):
            authority.freeze_current_face_policy(
                session, required=required, cardinalities=cardinalities
            )
    finally:
        engine.dispose()


def test_face_tuple_freeze_blocks_new_migration_issue_fk(
    database_harness: DatabaseHarness, empty_database_name: str
) -> None:
    database_harness.upgrade(empty_database_name)
    reviewer, _ = _seed(database_harness, empty_database_name)
    with database_harness.connect(empty_database_name) as connection:
        _legacy_model(connection, key="issue-contender")
        model = connection.execute(
            "SELECT embedding_model_id FROM ml.embedding_model WHERE model_key='issue-contender'"
        ).fetchone()
        assert model is not None
        model_id = model[0]
        connection.commit()
    engine = create_engine(database_harness.database_url(empty_database_name))
    sessions = sessionmaker(engine, class_=Session)
    authority = SqlAlchemyArtifactLicenseAuthority()
    required = (
        RequiredArtifact("ENCODER_WEIGHTS", ARTIFACT),
        RequiredArtifact("INTERPRETER_EXPORT", ARTIFACT),
    )
    cardinalities = {
        "ENCODER_WEIGHTS": 1,
        "INTERPRETER_EXPORT": 1,
        "CALIBRATION": 0,
        "PREPROCESSING_EXECUTABLE": 0,
        "DECODER_PROBE": 0,
        "TIMELINE_CODEC": 0,
    }
    issue_sql = """
        INSERT INTO ml.artifact_migration_issue(
          embedding_model_id,artifact_sha256,reason)
        VALUES(%s,%s,'LICENSE_CLAIM_CONFLICT')
    """
    try:
        with sessions.begin() as session:
            authority.append_review(
                session,
                artifact_sha256=ARTIFACT,
                reviewer_user_id=reviewer,
                expected_generation=1,
                review=_review(LicenseDecisionState.APPROVED, lag_ms=86_400_000),
            )
        with sessions.begin() as holder:
            authority.freeze_current_face_policy(
                holder, required=required, cardinalities=cardinalities
            )
            with database_harness.connect(empty_database_name) as contender:
                contender.execute("SET lock_timeout = '200ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    contender.execute(issue_sql, (model_id, ARTIFACT))
                contender.rollback()
        with database_harness.connect(empty_database_name) as contender:
            contender.execute(issue_sql, (model_id, ARTIFACT))
            contender.commit()
        with (
            sessions.begin() as session,
            pytest.raises(ArtifactLicenseReviewError, match="artifact_unselectable"),
        ):
            authority.freeze_current_face_policy(
                session, required=required, cardinalities=cardinalities
            )
    finally:
        engine.dispose()
