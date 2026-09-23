"""Dormant exact canonical-source snapshot for Face v2 work.

This reader locks metadata rows in the caller's transaction. It does not
authorize a sponsor, prove on-disk Vault bytes, or grant publication by itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class FaceSourceError(ValueError):
    """The requested recording/variant is not the exact valid canonical source."""


@dataclass(frozen=True, slots=True)
class CanonicalFaceSource:
    recording_id: UUID
    audio_variant_id: UUID
    vault_object_id: UUID
    source_sha256: bytes
    source_byte_size: int
    variant_row_version: int
    object_row_version: int
    canonical_policy_version: str


class SqlAlchemyFaceSourceReader:
    """Read one current, committed, valid source while holding row locks."""

    def lock_current(
        self,
        session: Session,
        *,
        recording_id: UUID,
        audio_variant_id: UUID,
        expected_source_sha256: bytes,
    ) -> CanonicalFaceSource:
        if not session.in_transaction():
            raise FaceSourceError("face_source_transaction_required")
        if (
            type(recording_id) is not UUID
            or type(audio_variant_id) is not UUID
            or type(expected_source_sha256) is not bytes
            or len(expected_source_sha256) != 32
        ):
            raise FaceSourceError("face_source_identity_invalid")
        row = (
            session.execute(
                text("""
                    SELECT c.recording_id,c.audio_variant_id,c.policy_version,
                      v.vault_object_id,v.row_version AS variant_row_version,
                      o.sha256 AS source_sha256,o.byte_size AS source_byte_size,
                      o.row_version AS object_row_version
                    FROM vault.recording_canonical_variant AS c
                    JOIN catalog.recording AS r
                      ON r.recording_id=c.recording_id
                    JOIN vault.audio_variant AS v
                      ON v.audio_variant_id=c.audio_variant_id
                     AND v.recording_id=c.recording_id
                    JOIN vault.vault_object AS o
                      ON o.vault_object_id=v.vault_object_id
                    WHERE c.recording_id=:recording
                      AND c.audio_variant_id=:variant
                      AND v.validation_status='VALID'
                      AND v.deleted_at IS NULL
                      AND r.deleted_at IS NULL
                      AND o.commit_status='COMMITTED'
                      AND o.committed_at IS NOT NULL
                      AND o.sha256=:expected_sha256
                      AND EXISTS(
                        SELECT 1 FROM vault.vault_replica AS replica
                        WHERE replica.vault_object_id=o.vault_object_id
                          AND replica.storage_backend='LOCAL_FILESYSTEM'
                          AND replica.replica_status='AVAILABLE'
                          AND replica.verified_at IS NOT NULL
                      )
                      AND NOT EXISTS(
                        SELECT 1 FROM identity.recording_redirect AS redirect
                        WHERE redirect.source_recording_id=c.recording_id
                      )
                    FOR SHARE OF c,r,v,o
                """),
                {
                    "recording": recording_id,
                    "variant": audio_variant_id,
                    "expected_sha256": expected_source_sha256,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise FaceSourceError("face_source_not_current")
        return CanonicalFaceSource(
            recording_id=row["recording_id"],
            audio_variant_id=row["audio_variant_id"],
            vault_object_id=row["vault_object_id"],
            source_sha256=row["source_sha256"],
            source_byte_size=row["source_byte_size"],
            variant_row_version=row["variant_row_version"],
            object_row_version=row["object_row_version"],
            canonical_policy_version=row["policy_version"],
        )
