"""One-time PostgreSQL binding for owner-scoped Sona shadow evidence."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from autplay.application.sona_shadow import (
    sona_shadow_evidence_document,
    sona_shadow_evidence_from_document,
)
from autplay.domain.recommendations import RecommendationNotFound
from autplay.domain.sona import SonaShadowEvidence

from .models import (
    RecommendationPipelineVersionRow,
    RecommendationRequestRow,
    RecommendationTemporalSnapshotRow,
)


class SqlAlchemySonaShadowEvidenceRepository:
    """Append exactly one immutable model result to an existing served P11 request."""

    def __init__(
        self,
        sessions: Callable[[], Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions = sessions
        self._clock = clock or (lambda: datetime.now(UTC))

    def save(self, evidence: SonaShadowEvidence) -> None:
        document = sona_shadow_evidence_document(evidence)
        with self._sessions() as session, session.begin():
            row = session.scalar(
                select(RecommendationRequestRow)
                .where(
                    RecommendationRequestRow.user_id == evidence.owner_user_id,
                    RecommendationRequestRow.recommendation_request_id
                    == evidence.recommendation_request_id,
                )
                .with_for_update()
            )
            if row is None:
                raise RecommendationNotFound
            if row.sona_shadow_status is not None:
                stored = _evidence_from_row(row)
                if stored != evidence:
                    raise ValueError("Sona shadow evidence identity conflict")
                return
            _validate_p11_binding(row, evidence)
            temporal = session.scalar(
                select(RecommendationTemporalSnapshotRow).where(
                    RecommendationTemporalSnapshotRow.user_id == evidence.owner_user_id,
                    RecommendationTemporalSnapshotRow.recommendation_temporal_snapshot_id
                    == evidence.temporal_snapshot_id,
                    RecommendationTemporalSnapshotRow.retained_until > self._clock(),
                )
            )
            if temporal is None or (
                temporal.recommendation_input_snapshot_id != evidence.baseline_snapshot_id
                or temporal.baseline_input_snapshot_sha256.hex()
                != evidence.baseline_input_snapshot_sha256
                or temporal.snapshot_sha256.hex() != evidence.temporal_snapshot_sha256
                or temporal.feature_policy_sha256.hex() != evidence.feature_policy_sha256
            ):
                raise ValueError("Sona temporal snapshot binding mismatch")
            pipeline = session.get(
                RecommendationPipelineVersionRow,
                (evidence.shadow_pipeline_key, evidence.shadow_pipeline_version),
            )
            if pipeline is None or (
                pipeline.manifest_sha256.hex() != evidence.shadow_pipeline_manifest_sha256
                or pipeline.lifecycle_status != "SHADOW"
            ):
                raise ValueError("Sona shadow pipeline identity is unavailable")
            row.recommendation_temporal_snapshot_id = evidence.temporal_snapshot_id
            row.temporal_snapshot_sha256 = bytes.fromhex(evidence.temporal_snapshot_sha256)
            row.adaptive_feature_policy_sha256 = bytes.fromhex(evidence.feature_policy_sha256)
            row.sona_shadow_pipeline_key = evidence.shadow_pipeline_key
            row.sona_shadow_pipeline_version = evidence.shadow_pipeline_version
            row.sona_shadow_pipeline_manifest_sha256 = bytes.fromhex(
                evidence.shadow_pipeline_manifest_sha256
            )
            row.sona_tokenizer_sha256 = bytes.fromhex(evidence.tokenizer_sha256)
            row.sona_model_manifest_sha256 = bytes.fromhex(evidence.model_manifest_sha256)
            row.sona_request_sha256 = _optional_digest(evidence.sona_request_sha256)
            row.sona_output_sha256 = _optional_digest(evidence.sona_output_sha256)
            row.sona_shadow_evidence_sha256 = bytes.fromhex(evidence.evidence_sha256)
            row.sona_shadow_status = evidence.status.value
            row.sona_shadow_reason = evidence.reason
            row.sona_shadow_document = document
            session.flush()

    def load(self, user_id: UUID, recommendation_request_id: UUID) -> SonaShadowEvidence | None:
        with self._sessions() as session:
            row = session.scalar(
                select(RecommendationRequestRow).where(
                    RecommendationRequestRow.user_id == user_id,
                    RecommendationRequestRow.recommendation_request_id == recommendation_request_id,
                )
            )
            if row is None or row.sona_shadow_status is None:
                return None
            return _evidence_from_row(row)


def _validate_p11_binding(row: RecommendationRequestRow, evidence: SonaShadowEvidence) -> None:
    if row.shadow or (
        row.request_sha256 is None
        or row.request_sha256.hex() != evidence.p11_request_sha256
        or row.pipeline_manifest_sha256 is None
        or row.pipeline_manifest_sha256.hex() != evidence.p11_pipeline_manifest_sha256
        or row.recommendation_input_snapshot_id != evidence.baseline_snapshot_id
        or row.input_snapshot_sha256 is None
        or row.input_snapshot_sha256.hex() != evidence.baseline_input_snapshot_sha256
    ):
        raise ValueError("Sona evidence is not bound to the persisted P11 request")


def _evidence_from_row(row: RecommendationRequestRow) -> SonaShadowEvidence:
    raw = row.sona_shadow_document
    if not isinstance(raw, dict):
        raise ValueError("persisted Sona shadow document is invalid")
    evidence = sona_shadow_evidence_from_document(cast(dict[str, object], raw))
    if (
        evidence.owner_user_id != row.user_id
        or evidence.recommendation_request_id != row.recommendation_request_id
        or evidence.p11_request_sha256 != _required_hex(row.request_sha256)
        or evidence.p11_pipeline_manifest_sha256 != _required_hex(row.pipeline_manifest_sha256)
        or (
            row.recommendation_input_snapshot_id is not None
            and evidence.baseline_snapshot_id != row.recommendation_input_snapshot_id
        )
        or evidence.baseline_input_snapshot_sha256 != _required_hex(row.input_snapshot_sha256)
        or (
            row.recommendation_temporal_snapshot_id is not None
            and evidence.temporal_snapshot_id != row.recommendation_temporal_snapshot_id
        )
        or evidence.temporal_snapshot_sha256 != _required_hex(row.temporal_snapshot_sha256)
        or evidence.feature_policy_sha256 != _required_hex(row.adaptive_feature_policy_sha256)
        or evidence.shadow_pipeline_key != row.sona_shadow_pipeline_key
        or evidence.shadow_pipeline_version != row.sona_shadow_pipeline_version
        or evidence.shadow_pipeline_manifest_sha256
        != _required_hex(row.sona_shadow_pipeline_manifest_sha256)
        or evidence.tokenizer_sha256 != _required_hex(row.sona_tokenizer_sha256)
        or evidence.model_manifest_sha256 != _required_hex(row.sona_model_manifest_sha256)
        or evidence.sona_request_sha256 != _optional_hex(row.sona_request_sha256)
        or evidence.sona_output_sha256 != _optional_hex(row.sona_output_sha256)
        or evidence.evidence_sha256 != _required_hex(row.sona_shadow_evidence_sha256)
        or evidence.status.value != row.sona_shadow_status
        or evidence.reason != row.sona_shadow_reason
    ):
        raise ValueError("persisted Sona shadow columns do not match the canonical document")
    return evidence


def _optional_digest(value: str | None) -> bytes | None:
    return bytes.fromhex(value) if value is not None else None


def _required_hex(value: bytes | None) -> str:
    if value is None:
        raise ValueError("persisted Sona shadow hash is missing")
    return value.hex()


def _optional_hex(value: bytes | None) -> str | None:
    return value.hex() if value is not None else None


__all__ = ("SqlAlchemySonaShadowEvidenceRepository",)
