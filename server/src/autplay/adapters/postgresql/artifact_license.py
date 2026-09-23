"""Internal, transaction-bound artifact license decision writer.

This is not an admin entrypoint. A caller must first verify the operation-bound
WebAuthn proof and consume its challenge in the same transaction. No runtime
composition exposes this writer until that control-plane authority exists.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

import rfc8785
from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.application.face_artifact_policy import (
    ArtifactPolicyEntry,
    FrozenFaceArtifactPolicy,
    RequiredArtifact,
    freeze_face_artifact_policy,
    required_artifact_set,
)
from autplay.domain.artifact_license_review import (
    ArtifactLicenseDecision,
    ArtifactLicenseReview,
    ArtifactLicenseReviewError,
)


class SqlAlchemyArtifactLicenseAuthority:
    """Append a reviewed successor inside the caller's authorization transaction."""

    def append_review(
        self,
        session: Session,
        *,
        artifact_sha256: bytes,
        reviewer_user_id: UUID,
        expected_generation: int,
        review: ArtifactLicenseReview,
    ) -> ArtifactLicenseDecision:
        if not session.in_transaction():
            raise ArtifactLicenseReviewError("artifact_license_transaction_required")
        if (
            type(artifact_sha256) is not bytes
            or len(artifact_sha256) != 32
            or type(expected_generation) is not int
            or not 1 <= expected_generation < 9_007_199_254_740_991
            or type(review) is not ArtifactLicenseReview
        ):
            raise ArtifactLicenseReviewError("artifact_license_review_invalid")
        actor = (
            session.execute(
                text("""
                SELECT role,status,deleted_at FROM account.user_account
                WHERE user_id=:actor FOR UPDATE
            """),
                {"actor": reviewer_user_id},
            )
            .mappings()
            .first()
        )
        if (
            actor is None
            or actor["role"] not in {"OWNER", "ADMIN"}
            or actor["status"] != "ACTIVE"
            or actor["deleted_at"] is not None
        ):
            raise ArtifactLicenseReviewError("artifact_license_reviewer_forbidden")
        _lock_artifact(session, artifact_sha256)
        current = (
            session.execute(
                text("""
                SELECT decision_sequence,effective_generation,state
                FROM ml.artifact_license_current
                WHERE artifact_sha256=:artifact_sha256 FOR UPDATE
            """),
                {"artifact_sha256": artifact_sha256},
            )
            .mappings()
            .first()
        )
        if current is None:
            raise ArtifactLicenseReviewError("artifact_license_artifact_unknown")
        if current["effective_generation"] != expected_generation:
            raise ArtifactLicenseReviewError("artifact_license_generation_conflict")
        unresolved = session.scalar(
            text("""
                SELECT EXISTS(
                  SELECT 1 FROM ml.artifact_migration_issue
                  WHERE artifact_sha256=:artifact_sha256
                )
            """),
            {"artifact_sha256": artifact_sha256},
        )
        if unresolved:
            raise ArtifactLicenseReviewError("artifact_license_migration_issue")
        sequence = current["decision_sequence"] + 1
        generation = current["effective_generation"] + 1
        session.execute(
            text("""
                INSERT INTO ml.artifact_license_decision(
                  artifact_sha256,decision_sequence,effective_generation,
                  superseded_sequence,state,license_identifier,license_text_sha256,
                  use_restrictions,redistribution_decision,modification_decision,
                  attribution_payload,reviewer_user_id,reviewed_at,review_reference,
                  max_offline_revocation_lag_ms,derived_output_disposition)
                VALUES(:artifact_sha256,:sequence,:generation,:previous_sequence,
                  :state,:license_identifier,:license_text_sha256,
                  CAST(:use_restrictions AS jsonb),:redistribution_decision,
                  :modification_decision,CAST(:attribution_payload AS jsonb),
                  :reviewer_user_id,clock_timestamp(),:review_reference,
                  :lag_ms,:disposition)
            """),
            {
                "artifact_sha256": artifact_sha256,
                "sequence": sequence,
                "generation": generation,
                "previous_sequence": current["decision_sequence"],
                "state": review.state.value,
                "license_identifier": review.license_identifier,
                "license_text_sha256": review.license_text_sha256,
                "use_restrictions": rfc8785.dumps(review.use_restrictions).decode("utf-8"),
                "redistribution_decision": review.redistribution_decision,
                "modification_decision": review.modification_decision,
                "attribution_payload": rfc8785.dumps(review.attribution_payload).decode("utf-8"),
                "reviewer_user_id": reviewer_user_id,
                "review_reference": review.review_reference,
                "lag_ms": review.max_offline_revocation_lag_ms,
                "disposition": review.derived_output_disposition,
            },
        )
        return ArtifactLicenseDecision(artifact_sha256, sequence, generation, review.state)

    def freeze_current_face_policy(
        self,
        session: Session,
        *,
        required: Sequence[RequiredArtifact],
        cardinalities: Mapping[str, int],
    ) -> FrozenFaceArtifactPolicy:
        """Lock every current license through the caller's activation transaction."""

        if not session.in_transaction():
            raise ArtifactLicenseReviewError("artifact_license_transaction_required")
        required_artifact_set(required)
        decisions_by_hash: dict[bytes, tuple[int, int, str, int, str]] = {}
        for artifact_sha256 in sorted({entry.artifact_sha256 for entry in required}):
            _lock_artifact(session, artifact_sha256)
            row = session.execute(
                text("""
                    SELECT current.decision_sequence,current.effective_generation,
                      current.state,decision.max_offline_revocation_lag_ms,
                      decision.derived_output_disposition
                    FROM ml.artifact_license_current current
                    JOIN ml.artifact_license_decision decision
                      ON decision.artifact_sha256=current.artifact_sha256
                     AND decision.decision_sequence=current.decision_sequence
                    WHERE current.artifact_sha256=:artifact_sha256
                      AND NOT EXISTS(
                        SELECT 1 FROM ml.artifact_migration_issue issue
                        WHERE issue.artifact_sha256=current.artifact_sha256
                      )
                    FOR SHARE OF current,decision
                """),
                {"artifact_sha256": artifact_sha256},
            ).one_or_none()
            if row is None:
                raise ArtifactLicenseReviewError("artifact_license_artifact_unselectable")
            decisions_by_hash[artifact_sha256] = (
                row.decision_sequence,
                row.effective_generation,
                row.state,
                row.max_offline_revocation_lag_ms,
                row.derived_output_disposition,
            )
        decisions = tuple(
            ArtifactPolicyEntry(
                role=entry.role,
                artifact_sha256=entry.artifact_sha256,
                decision_sequence=decisions_by_hash[entry.artifact_sha256][0],
                decision_generation=decisions_by_hash[entry.artifact_sha256][1],
                state=decisions_by_hash[entry.artifact_sha256][2],
                max_offline_revocation_lag_ms=decisions_by_hash[entry.artifact_sha256][3],
                disposition=decisions_by_hash[entry.artifact_sha256][4],
            )
            for entry in required
        )
        return freeze_face_artifact_policy(required, decisions, cardinalities=cardinalities)


def _lock_artifact(session: Session, artifact_sha256: bytes) -> None:
    """Serialize issue-insert trigger locks with review and tuple selection."""

    found = session.scalar(
        text("""
            SELECT artifact_sha256 FROM ml.artifact
            WHERE artifact_sha256=:artifact_sha256 FOR UPDATE
        """),
        {"artifact_sha256": artifact_sha256},
    )
    if found is None:
        raise ArtifactLicenseReviewError("artifact_license_artifact_unknown")
