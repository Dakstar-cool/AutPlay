"""Factories for the P02 identity-history integration contract."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from autplay.application.identity_evidence import (
    CanonicalDocument,
    JsonValue,
    candidate_aggregate_sha256,
    canonical_candidate_evidence,
    canonical_query_snapshot,
)
from psycopg import Connection, Cursor
from psycopg.types.json import Jsonb


def new_id() -> uuid.UUID:
    """Return a random fixture identifier."""

    return uuid.uuid4()


def returned_uuid(cursor: Cursor[Any]) -> uuid.UUID:
    """Read one UUID returned by an INSERT statement."""

    row = cursor.fetchone()
    if row is None or not isinstance(row[0], uuid.UUID):
        raise AssertionError("INSERT ... RETURNING did not return a UUID")
    return row[0]


def force_deferred_constraints(connection: Connection[Any]) -> None:
    """Evaluate all initially deferred identity constraints in the current transaction."""

    connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
    connection.execute("SET CONSTRAINTS ALL DEFERRED")


def insert_user(connection: Connection[Any], *, role: str = "USER") -> uuid.UUID:
    """Insert an active account used by identity fixtures."""

    suffix = new_id().hex[:12]
    return returned_uuid(
        connection.execute(
            """
            INSERT INTO account.user_account (display_name, role)
            VALUES (%s, %s) RETURNING user_id
            """,
            (f"identity-{suffix}", role),
        )
    )


def insert_recording(connection: Connection[Any], title: str | None = None) -> uuid.UUID:
    """Insert the minimum valid artist-credit and recording pair."""

    suffix = new_id().hex[:12]
    recording_title = title or f"recording-{suffix}"
    credit_id = returned_uuid(
        connection.execute(
            """
            INSERT INTO catalog.artist_credit (display_name, normalized_name)
            VALUES (%s, %s) RETURNING artist_credit_id
            """,
            (f"artist-{suffix}", f"artist-{suffix}"),
        )
    )
    return returned_uuid(
        connection.execute(
            """
            INSERT INTO catalog.recording (artist_credit_id, title, normalized_title)
            VALUES (%s, %s, %s) RETURNING recording_id
            """,
            (credit_id, recording_title, recording_title.lower()),
        )
    )


@dataclass(frozen=True, slots=True)
class QueryRef:
    """One typed identity query and its ownership scope."""

    query_type: str
    typed_column: str
    typed_id: uuid.UUID
    owner_user_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None

    def persistence_values(self) -> dict[str, object]:
        """Return all typed query columns, with exactly one populated."""

        values: dict[str, object] = {
            "query_type": self.query_type,
            "owner_user_id": self.owner_user_id,
            "device_id": self.device_id,
            "import_entry_id": None,
            "user_track_ref_id": None,
            "local_audio_id": None,
            "external_reference_id": None,
            "vault_object_id": None,
            "audio_variant_id": None,
        }
        values[self.typed_column] = self.typed_id
        return values


@dataclass(frozen=True, slots=True)
class IdentityWorld:
    """Relational prerequisites for all six v1 identity query types."""

    owner_user_id: uuid.UUID
    admin_user_id: uuid.UUID
    other_user_id: uuid.UUID
    device_id: uuid.UUID
    provider_id: uuid.UUID
    external_reference_id: uuid.UUID
    vault_object_id: uuid.UUID
    audio_variant_id: uuid.UUID
    import_entry_id: uuid.UUID
    user_track_ref_id: uuid.UUID
    local_audio_id: uuid.UUID
    seed_recording_id: uuid.UUID

    def query(self, query_type: str) -> QueryRef:
        """Return the valid typed key and ownership for one query type."""

        if query_type == "IMPORT_ENTRY":
            return QueryRef(query_type, "import_entry_id", self.import_entry_id, self.owner_user_id)
        if query_type == "USER_TRACK_REF":
            return QueryRef(
                query_type, "user_track_ref_id", self.user_track_ref_id, self.owner_user_id
            )
        if query_type == "LOCAL_AUDIO":
            return QueryRef(
                query_type,
                "local_audio_id",
                self.local_audio_id,
                self.owner_user_id,
                self.device_id,
            )
        if query_type == "EXTERNAL_REFERENCE":
            return QueryRef(query_type, "external_reference_id", self.external_reference_id)
        if query_type == "VAULT_OBJECT":
            return QueryRef(query_type, "vault_object_id", self.vault_object_id)
        if query_type == "AUDIO_VARIANT":
            return QueryRef(query_type, "audio_variant_id", self.audio_variant_id)
        raise ValueError(f"unknown query type: {query_type}")


def insert_world(connection: Connection[Any]) -> IdentityWorld:
    """Create relational prerequisites without creating identity history."""

    owner = insert_user(connection, role="OWNER")
    admin = insert_user(connection, role="ADMIN")
    other = insert_user(connection)
    device = returned_uuid(
        connection.execute(
            """
            INSERT INTO account.device (user_id, device_name, platform, app_version)
            VALUES (%s, %s, 'ANDROID', 'p02') RETURNING device_id
            """,
            (owner, f"identity-device-{new_id().hex[:8]}"),
        )
    )
    seed_recording = insert_recording(connection, "identity-seed")
    provider_suffix = new_id().hex[:12]
    provider = returned_uuid(
        connection.execute(
            """
            INSERT INTO identity.source_provider (
                provider_key, display_name, adapter_id, adapter_version, capabilities
            ) VALUES (%s, 'P02 provider', 'p02.adapter', '1', ARRAY['METADATA'])
            RETURNING provider_id
            """,
            (f"p02-{provider_suffix}",),
        )
    )
    external = returned_uuid(
        connection.execute(
            """
            INSERT INTO identity.external_reference (
                provider_id, external_entity_type, external_id, market_scope
            ) VALUES (%s, 'recording', %s, 'GLOBAL') RETURNING external_reference_id
            """,
            (provider, f"external-{new_id().hex}"),
        )
    )
    object_digest = hashlib.sha256(f"vault-{new_id()}".encode()).digest()
    vault_object = returned_uuid(
        connection.execute(
            """
            INSERT INTO vault.vault_object (
                sha256, byte_size, detected_mime_type, commit_status, committed_at
            ) VALUES (%s, 4096, 'audio/flac', 'COMMITTED', now())
            RETURNING vault_object_id
            """,
            (object_digest,),
        )
    )
    variant = returned_uuid(
        connection.execute(
            """
            INSERT INTO vault.audio_variant (
                recording_id, vault_object_id, codec, container,
                sample_rate_hz, channels, duration_ms
            ) VALUES (%s, %s, 'flac', 'flac', 48000, 2, 180000)
            RETURNING audio_variant_id
            """,
            (seed_recording, vault_object),
        )
    )
    job = returned_uuid(
        connection.execute(
            """
            INSERT INTO jobs.job (job_type, schema_version, user_id)
            VALUES ('IMPORT', 1, %s) RETURNING job_id
            """,
            (owner,),
        )
    )
    import_job = returned_uuid(
        connection.execute(
            """
            INSERT INTO importing.import_job (
                job_id, user_id, adapter_id, adapter_version, input_sha256, mode
            ) VALUES (%s, %s, 'p02.adapter', '1', %s, 'LIBRARY_ONLY')
            RETURNING import_job_id
            """,
            (job, owner, hashlib.sha256(b"p02-import").digest()),
        )
    )
    import_entry = returned_uuid(
        connection.execute(
            """
            INSERT INTO importing.import_entry (
                import_job_id, source_row_key, raw_title, raw_artist
            ) VALUES (%s, %s, 'P02 title', 'P02 artist') RETURNING import_entry_id
            """,
            (import_job, new_id().hex),
        )
    )
    user_track = returned_uuid(
        connection.execute(
            """
            INSERT INTO library.user_track_ref (user_id, raw_title, raw_artist)
            VALUES (%s, 'P02 title', 'P02 artist') RETURNING user_track_ref_id
            """,
            (owner,),
        )
    )
    return IdentityWorld(
        owner_user_id=owner,
        admin_user_id=admin,
        other_user_id=other,
        device_id=device,
        provider_id=provider,
        external_reference_id=external,
        vault_object_id=vault_object,
        audio_variant_id=variant,
        import_entry_id=import_entry,
        user_track_ref_id=user_track,
        local_audio_id=new_id(),
        seed_recording_id=seed_recording,
    )


@dataclass(frozen=True, slots=True)
class ReleaseSet:
    """Immutable matcher/calibrator/threshold release snapshot."""

    matcher_version: str
    candidate_generation_version: str
    normalization_version: str
    feature_extractor_versions: dict[str, JsonValue]
    calibrator_version: str | None
    threshold_set_version: str | None
    evidence_mode: str
    evidence_tier: str


def insert_release_set(
    connection: Connection[Any],
    *,
    evidence_mode: str = "METADATA_ONLY",
    evidence_tier: str = "T0",
    include_calibrator: bool = True,
    include_threshold: bool = True,
    benchmark: bool = True,
    auto_threshold: Decimal = Decimal("0.800000"),
    review_threshold: Decimal = Decimal("0.500000"),
    margin_threshold: Decimal = Decimal("0.100000"),
) -> ReleaseSet:
    """Insert one immutable release family, optionally pre-activation."""

    suffix = new_id().hex
    matcher = f"matcher-{suffix}"
    generator = f"generator-{suffix}"
    normalizer = f"normalizer-{suffix}"
    feature_versions: dict[str, JsonValue] = {"metadata": "1.0.0"}
    connection.execute(
        """
        INSERT INTO identity.matcher_release (
            matcher_version, candidate_generation_version, normalization_version,
            feature_extractor_versions, feature_schema_version, manifest_sha256
        ) VALUES (%s, %s, %s, %s, '1', %s)
        """,
        (
            matcher,
            generator,
            normalizer,
            Jsonb(feature_versions),
            hashlib.sha256(f"matcher:{suffix}".encode()).digest(),
        ),
    )
    calibrator: str | None = None
    if include_calibrator:
        calibrator = f"calibrator-{suffix}"
        connection.execute(
            """
            INSERT INTO identity.calibrator_release (
                calibrator_version, matcher_version, evidence_mode,
                artifact_sha256, input_schema_version
            ) VALUES (%s, %s, %s, %s, '1')
            """,
            (
                calibrator,
                matcher,
                evidence_mode,
                hashlib.sha256(f"calibrator:{suffix}".encode()).digest(),
            ),
        )
    threshold: str | None = None
    if include_threshold:
        threshold = f"threshold-{suffix}"
        benchmark_hash = (
            hashlib.sha256(f"benchmark:{suffix}".encode()).digest() if benchmark else None
        )
        connection.execute(
            """
            INSERT INTO identity.threshold_set (
                threshold_set_version, matcher_version, calibrator_version,
                evidence_mode, minimum_evidence_tier, auto_threshold,
                review_threshold, margin_threshold, benchmark_report_sha256,
                gate_metadata, gate_metadata_schema_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '1')
            """,
            (
                threshold,
                matcher,
                calibrator,
                evidence_mode,
                evidence_tier,
                auto_threshold,
                review_threshold,
                margin_threshold,
                benchmark_hash,
                Jsonb({"fixture": "p02"}),
            ),
        )
    return ReleaseSet(
        matcher_version=matcher,
        candidate_generation_version=generator,
        normalization_version=normalizer,
        feature_extractor_versions=feature_versions,
        calibrator_version=calibrator,
        threshold_set_version=threshold,
        evidence_mode=evidence_mode,
        evidence_tier=evidence_tier,
    )


def append_policy_event(
    connection: Connection[Any],
    releases: ReleaseSet,
    actor_user_id: uuid.UUID,
    *,
    sequence_no: int = 1,
    action: str = "ACTIVATE",
    supersedes_activation_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Append one policy lifecycle event."""

    if releases.threshold_set_version is None:
        raise ValueError("policy events require a threshold set")
    return returned_uuid(
        connection.execute(
            """
            INSERT INTO identity.match_policy_activation (
                evidence_mode, evidence_tier, sequence_no, action,
                threshold_set_version, supersedes_activation_id,
                actor_user_id, reason
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'P02 integration fixture')
            RETURNING activation_id
            """,
            (
                releases.evidence_mode,
                releases.evidence_tier,
                sequence_no,
                action,
                releases.threshold_set_version,
                supersedes_activation_id,
                actor_user_id,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class Candidate:
    """One ranked candidate and its canonical sealed evidence document."""

    recording_id: uuid.UUID
    rank: int
    raw_score: Decimal | None
    confidence: Decimal | None
    evidence_tier: str
    feature_scores: list[JsonValue]
    hard_conflicts: list[JsonValue]
    candidate_origins: list[JsonValue]
    extractor_versions: dict[str, JsonValue]
    document: CanonicalDocument


def make_candidate(
    recording_id: uuid.UUID,
    rank: int,
    releases: ReleaseSet,
    *,
    raw_score: Decimal | None = None,
    confidence: Decimal | None = None,
    feature_scores: list[JsonValue] | None = None,
    hard_conflicts: list[JsonValue] | None = None,
    candidate_origins: list[JsonValue] | None = None,
    scores_are_null: bool = False,
) -> Candidate:
    """Build one production-canonical candidate evidence record."""

    score = (
        None
        if scores_are_null
        else (raw_score if raw_score is not None else Decimal(max(0, 100 - rank)) / Decimal(100))
    )
    calibrated = None if scores_are_null else (confidence if confidence is not None else score)
    features: list[JsonValue] = (
        feature_scores
        if feature_scores is not None
        else [{"feature": "normalized_title", "score": str(score)}]
    )
    conflicts: list[JsonValue] = hard_conflicts if hard_conflicts is not None else []
    origins: list[JsonValue] = (
        candidate_origins if candidate_origins is not None else [{"kind": "fixture", "rank": rank}]
    )
    document = canonical_candidate_evidence(
        {
            "recording_id": str(recording_id),
            "raw_score": float(score) if score is not None else None,
            "confidence": float(calibrated) if calibrated is not None else None,
            "evidence_tier": releases.evidence_tier,
            "feature_scores": features,
            "hard_conflicts": conflicts,
            "candidate_origins": origins,
            "extractor_versions": releases.feature_extractor_versions,
        }
    )
    return Candidate(
        recording_id=recording_id,
        rank=rank,
        raw_score=score,
        confidence=calibrated,
        evidence_tier=releases.evidence_tier,
        feature_scores=features,
        hard_conflicts=conflicts,
        candidate_origins=origins,
        extractor_versions=releases.feature_extractor_versions,
        document=document,
    )


def make_candidates(
    connection: Connection[Any], releases: ReleaseSet, count: int
) -> list[Candidate]:
    """Create recordings and a contiguous ranked candidate set."""

    return [
        make_candidate(insert_recording(connection, f"candidate-{rank}"), rank, releases)
        for rank in range(1, count + 1)
    ]


def make_auto_candidates(connection: Connection[Any], releases: ReleaseSet) -> list[Candidate]:
    """Create a rank-one/rank-two pair that clears the default policy gate."""

    return [
        make_candidate(
            insert_recording(connection, "auto-candidate-1"),
            1,
            releases,
            raw_score=Decimal("0.950000"),
            confidence=Decimal("0.950000"),
        ),
        make_candidate(
            insert_recording(connection, "auto-candidate-2"),
            2,
            releases,
            raw_score=Decimal("0.700000"),
            confidence=Decimal("0.700000"),
        ),
    ]


@dataclass(frozen=True, slots=True)
class StoredDecision:
    """Identifiers and source values for a newly appended decision."""

    decision_id: uuid.UUID
    query: QueryRef
    releases: ReleaseSet
    candidates: tuple[Candidate, ...]
    state: str
    execution_mode: str
    decided_at: datetime


def _query_document(query: QueryRef) -> CanonicalDocument:
    return canonical_query_snapshot(
        {
            "normalized_title": "p02 title",
            "normalized_artists": ["p02 artist"],
            "duration_ms": 180000,
            "version_markers": ["normalization:1"],
            "market_scope": "GLOBAL",
            "evidence_ids": [f"{query.query_type.lower()}:{query.typed_id}"],
        }
    )


def append_evaluation(
    connection: Connection[Any],
    query: QueryRef,
    releases: ReleaseSet,
    candidates: list[Candidate],
    *,
    state: str = "REVIEW_REQUIRED",
    execution_mode: str = "SHADOW",
    target_recording_id: uuid.UUID | None = None,
    use_default_target: bool = True,
    hard_conflicts: list[JsonValue] | None = None,
    actor_type: str = "SYSTEM",
    actor_user_id: uuid.UUID | None = None,
    supersedes_decision_id: uuid.UUID | None = None,
    decided_at: datetime | None = None,
    project: bool = False,
    candidate_count: int | None = None,
    candidate_evidence_sha256: bytes | None = None,
    candidate_evidence_size_bytes: int | None = None,
    top2_confidence: Decimal | None = None,
    use_default_top2: bool = True,
    decision_id: uuid.UUID | None = None,
    value_overrides: Mapping[str, object] | None = None,
) -> StoredDecision:
    """Append an evaluation and candidate set; validation remains deferred."""

    if use_default_target:
        target = (
            candidates[0].recording_id
            if candidates and state in {"AUTO_MATCH", "REVIEW_REQUIRED"}
            else None
        )
    else:
        target = target_recording_id
    top1 = candidates[0] if candidates else None
    if use_default_top2:
        top2 = candidates[1].confidence if len(candidates) >= 2 else None
    else:
        top2 = top2_confidence
    margin = (
        top1.confidence - top2
        if top1 is not None and top1.confidence is not None and isinstance(top2, Decimal)
        else None
    )
    ranked_hashes = [(candidate.rank, candidate.document.sha256) for candidate in candidates]
    if candidate_evidence_sha256 is None:
        aggregate_hash, _ = candidate_aggregate_sha256(ranked_hashes)
    else:
        aggregate_hash = candidate_evidence_sha256
    aggregate_size = sum(candidate.document.byte_size for candidate in candidates)
    query_document = _query_document(query)
    decision_conflicts = (
        hard_conflicts
        if hard_conflicts is not None
        else (top1.hard_conflicts if top1 is not None else [])
    )
    values = query.persistence_values()
    decision_time = decided_at or datetime.now(UTC)
    persisted_decision_id = decision_id or new_id()
    suffix = new_id().hex
    values.update(
        {
            "query_snapshot": Jsonb(query_document.value),
            "query_snapshot_sha256": query_document.sha256,
            "decision_kind": "EVALUATION",
            "execution_mode": execution_mode,
            "candidate_recording_id": target,
            "decision_state": state,
            "candidate_count": len(candidates) if candidate_count is None else candidate_count,
            "candidate_evidence_sha256": aggregate_hash,
            "candidate_evidence_size_bytes": (
                aggregate_size
                if candidate_evidence_size_bytes is None
                else candidate_evidence_size_bytes
            ),
            "evidence_mode": releases.evidence_mode,
            "candidate_generation_version": releases.candidate_generation_version,
            "normalization_version": releases.normalization_version,
            "feature_extractor_versions": Jsonb(releases.feature_extractor_versions),
            "matcher_version": releases.matcher_version,
            "calibrator_version": releases.calibrator_version,
            "threshold_set_version": releases.threshold_set_version,
            "raw_score": top1.raw_score if top1 else None,
            "confidence": top1.confidence if top1 else None,
            "top2_confidence": top2,
            "margin": margin,
            "evidence_tier": top1.evidence_tier if top1 else releases.evidence_tier,
            "feature_scores": Jsonb(top1.feature_scores if top1 else []),
            "hard_conflicts": Jsonb(decision_conflicts),
            "candidate_origins": Jsonb(top1.candidate_origins if top1 else []),
            "actor_type": actor_type,
            "actor_user_id": actor_user_id,
            "idempotency_key": suffix,
            "request_sha256": hashlib.sha256(f"request:{suffix}".encode()).digest(),
            "supersedes_decision_id": supersedes_decision_id,
            "supersession_reason": (
                "P02 re-evaluation" if supersedes_decision_id is not None else None
            ),
            "decided_at": decision_time,
            "decision_id": persisted_decision_id,
        }
    )
    if value_overrides is not None:
        values.update(value_overrides)
    inserted_decision_id = returned_uuid(
        connection.execute(
            """
            INSERT INTO identity.match_decision (
                decision_id, query_type, owner_user_id, device_id, import_entry_id,
                user_track_ref_id, local_audio_id, external_reference_id,
                vault_object_id, audio_variant_id, query_snapshot,
                query_snapshot_schema_version, snapshot_canonicalization_version,
                query_snapshot_sha256, decision_kind, execution_mode,
                candidate_recording_id, decision_state, candidate_count,
                candidate_evidence_sha256, candidate_evidence_size_bytes,
                evidence_mode, candidate_generation_version, normalization_version,
                feature_extractor_versions, matcher_version, calibrator_version,
                threshold_set_version, raw_score, confidence, top2_confidence,
                margin, evidence_tier, feature_scores, hard_conflicts,
                candidate_origins, explanation_schema_version, actor_type,
                actor_user_id, idempotency_scope, idempotency_key, request_sha256,
                supersedes_decision_id, supersession_reason, decided_at
            ) VALUES (
                %(decision_id)s, %(query_type)s, %(owner_user_id)s,
                %(device_id)s, %(import_entry_id)s,
                %(user_track_ref_id)s, %(local_audio_id)s, %(external_reference_id)s,
                %(vault_object_id)s, %(audio_variant_id)s, %(query_snapshot)s,
                '1', 'RFC8785', %(query_snapshot_sha256)s,
                %(decision_kind)s, %(execution_mode)s, %(candidate_recording_id)s,
                %(decision_state)s, %(candidate_count)s,
                %(candidate_evidence_sha256)s, %(candidate_evidence_size_bytes)s,
                %(evidence_mode)s, %(candidate_generation_version)s,
                %(normalization_version)s, %(feature_extractor_versions)s,
                %(matcher_version)s, %(calibrator_version)s, %(threshold_set_version)s,
                %(raw_score)s, %(confidence)s, %(top2_confidence)s, %(margin)s,
                %(evidence_tier)s, %(feature_scores)s, %(hard_conflicts)s,
                %(candidate_origins)s, '1', %(actor_type)s, %(actor_user_id)s,
                'p02-identity', %(idempotency_key)s, %(request_sha256)s,
                %(supersedes_decision_id)s, %(supersession_reason)s, %(decided_at)s
            ) RETURNING decision_id
            """,
            values,
        )
    )
    insert_candidate_rows(connection, inserted_decision_id, candidates)
    stored = StoredDecision(
        decision_id=inserted_decision_id,
        query=query,
        releases=releases,
        candidates=tuple(candidates),
        state=state,
        execution_mode=execution_mode,
        decided_at=decision_time,
    )
    if project:
        project_decision(connection, stored)
    return stored


def insert_candidate_rows(
    connection: Connection[Any], decision_id: uuid.UUID, candidates: list[Candidate]
) -> list[uuid.UUID]:
    """Insert the exact candidate rows for a decision."""

    evidence_ids: list[uuid.UUID] = []
    for candidate in candidates:
        evidence_ids.append(
            returned_uuid(
                connection.execute(
                    """
                    INSERT INTO identity.match_candidate_evidence (
                        decision_id, recording_id, rank, raw_score, confidence,
                        evidence_tier, feature_scores, hard_conflicts, candidate_origins,
                        extractor_versions, evidence_schema_version, evidence_sha256,
                        evidence_document_size_bytes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '1', %s, %s)
                    RETURNING match_candidate_evidence_id
                    """,
                    (
                        decision_id,
                        candidate.recording_id,
                        candidate.rank,
                        candidate.raw_score,
                        candidate.confidence,
                        candidate.evidence_tier,
                        Jsonb(candidate.feature_scores),
                        Jsonb(candidate.hard_conflicts),
                        Jsonb(candidate.candidate_origins),
                        Jsonb(candidate.extractor_versions),
                        candidate.document.sha256,
                        candidate.document.byte_size,
                    ),
                )
            )
        )
    return evidence_ids


def project_decision(connection: Connection[Any], decision: StoredDecision) -> None:
    """Atomically project an applied decision into an import or user-track query."""

    if decision.execution_mode != "APPLIED":
        raise ValueError("only APPLIED decisions have durable query projections")
    query = decision.query
    row = connection.execute(
        """
        SELECT decision_kind, review_action, candidate_recording_id,
               decision_state, confidence
        FROM identity.match_decision WHERE decision_id = %s
        """,
        (decision.decision_id,),
    ).fetchone()
    if row is None:
        raise AssertionError("decision disappeared before projection")
    decision_kind, review_action, recording_id, state, confidence = row
    if query.query_type == "USER_TRACK_REF":
        if decision_kind == "REVIEW_ACTION":
            status = {
                "ACCEPT": "RESOLVED",
                "CREATE_RECORDING": "RESOLVED",
                "REJECT": "CANDIDATES",
                "KEEP_UNRESOLVED": "UNRESOLVED",
            }[review_action]
        else:
            status = {
                "AUTO_MATCH": "RESOLVED",
                "REVIEW_REQUIRED": "CANDIDATES",
                "NO_MATCH": "NOT_FOUND",
                "INTEGRITY_CONFLICT": "AMBIGUOUS",
                "DEFERRED_EVIDENCE": "UNRESOLVED",
            }[state]
        projected_recording = recording_id if status == "RESOLVED" else None
        resolved_at = datetime.now(UTC) if status == "RESOLVED" else None
        connection.execute(
            """
            UPDATE library.user_track_ref
            SET recording_id = %s, resolution_status = %s, resolved_at = %s,
                resolution_confidence = %s, current_match_decision_id = %s
            WHERE user_track_ref_id = %s
            """,
            (
                projected_recording,
                status,
                resolved_at,
                confidence,
                decision.decision_id,
                query.typed_id,
            ),
        )
    elif query.query_type == "IMPORT_ENTRY":
        if decision_kind == "REVIEW_ACTION":
            status = {
                "ACCEPT": "MANUAL_MATCH",
                "CREATE_RECORDING": "MANUAL_MATCH",
                "REJECT": "REVIEW_REQUIRED",
                "KEEP_UNRESOLVED": "MANUAL_UNRESOLVED",
            }[review_action]
        else:
            status = state
        projected_recording = recording_id if status in {"AUTO_MATCH", "MANUAL_MATCH"} else None
        connection.execute(
            """
            UPDATE importing.import_entry
            SET match_status = %s, selected_recording_id = %s,
                current_match_decision_id = %s
            WHERE import_entry_id = %s
            """,
            (status, projected_recording, decision.decision_id, query.typed_id),
        )


def append_review(
    connection: Connection[Any],
    predecessor: StoredDecision,
    *,
    action: str,
    actor_user_id: uuid.UUID,
    actor_type: str = "USER",
    selected_rank: int = 1,
    created_recording_id: uuid.UUID | None = None,
    replacement_rank_one_recording_id: uuid.UUID | None = None,
    project: bool = False,
) -> StoredDecision:
    """Append a manual action and copy the predecessor evidence snapshot byte-for-byte."""

    selected = next(
        (candidate for candidate in predecessor.candidates if candidate.rank == selected_rank), None
    )
    if action in {"ACCEPT", "REJECT"} and selected is None:
        raise ValueError("accept/reject requires an existing selected candidate")
    target = (
        created_recording_id
        if action == "CREATE_RECORDING"
        else (selected.recording_id if action in {"ACCEPT", "REJECT"} and selected else None)
    )
    reviewed_id: uuid.UUID | None = None
    if action in {"ACCEPT", "REJECT"}:
        row = connection.execute(
            """
            SELECT match_candidate_evidence_id
            FROM identity.match_candidate_evidence
            WHERE decision_id = %s AND rank = %s
            """,
            (predecessor.decision_id, selected_rank),
        ).fetchone()
        if row is None or not isinstance(row[0], uuid.UUID):
            raise AssertionError("predecessor evidence row is missing")
        reviewed_id = row[0]
    suffix = new_id().hex
    decided_at = predecessor.decided_at + timedelta(seconds=1)
    decision_id = returned_uuid(
        connection.execute(
            """
            INSERT INTO identity.match_decision (
                query_type, owner_user_id, device_id, import_entry_id,
                user_track_ref_id, local_audio_id, external_reference_id,
                vault_object_id, audio_variant_id, query_snapshot,
                query_snapshot_schema_version, snapshot_canonicalization_version,
                query_snapshot_sha256, decision_kind, execution_mode, review_action,
                reviewed_candidate_evidence_id, candidate_recording_id, decision_state,
                candidate_count, candidate_evidence_sha256, candidate_evidence_size_bytes,
                evidence_mode, candidate_generation_version, normalization_version,
                feature_extractor_versions, matcher_version, calibrator_version,
                threshold_set_version, raw_score, confidence, top2_confidence, margin,
                evidence_tier, feature_scores, hard_conflicts, candidate_origins,
                explanation_schema_version, actor_type, actor_user_id,
                idempotency_scope, idempotency_key, request_sha256,
                supersedes_decision_id, supersession_reason, decided_at
            )
            SELECT
                query_type, owner_user_id, device_id, import_entry_id,
                user_track_ref_id, local_audio_id, external_reference_id,
                vault_object_id, audio_variant_id, query_snapshot,
                query_snapshot_schema_version, snapshot_canonicalization_version,
                query_snapshot_sha256, 'REVIEW_ACTION', 'APPLIED', %s,
                %s, %s, decision_state, candidate_count,
                candidate_evidence_sha256, candidate_evidence_size_bytes,
                evidence_mode, candidate_generation_version, normalization_version,
                feature_extractor_versions, matcher_version, calibrator_version,
                threshold_set_version, raw_score, confidence, top2_confidence, margin,
                evidence_tier, feature_scores, hard_conflicts, candidate_origins,
                explanation_schema_version, %s, %s,
                'p02-review', %s, %s, decision_id, 'P02 manual review', %s
            FROM identity.match_decision WHERE decision_id = %s
            RETURNING decision_id
            """,
            (
                action,
                reviewed_id,
                target,
                actor_type,
                actor_user_id,
                suffix,
                hashlib.sha256(f"review:{suffix}".encode()).digest(),
                decided_at,
                predecessor.decision_id,
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
        )
        SELECT %s,
               CASE
                   WHEN rank = 1 AND %s::uuid IS NOT NULL THEN %s::uuid
                   ELSE recording_id
               END,
               rank, raw_score, confidence,
               evidence_tier, feature_scores, hard_conflicts, candidate_origins,
               extractor_versions, evidence_schema_version, evidence_sha256,
               evidence_document_size_bytes
        FROM identity.match_candidate_evidence
        WHERE decision_id = %s ORDER BY rank
        """,
        (
            decision_id,
            replacement_rank_one_recording_id,
            replacement_rank_one_recording_id,
            predecessor.decision_id,
        ),
    )
    stored = StoredDecision(
        decision_id=decision_id,
        query=predecessor.query,
        releases=predecessor.releases,
        candidates=predecessor.candidates,
        state=predecessor.state,
        execution_mode="APPLIED",
        decided_at=decided_at,
    )
    if project:
        project_decision(connection, stored)
    return stored
