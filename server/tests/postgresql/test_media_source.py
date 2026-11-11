"""Future real-PG M1 gates. SOURCE ONLY / UNVERIFIED; no execution authorized.

Uses the existing disposable database_url fixture. Crypto/fixture construction
below is a future test statement, never a calculated authoring-time vector.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from autplay.adapters.postgresql.auth_runtime import SqlAlchemyAuthUnitOfWorkFactory
from autplay.adapters.postgresql.identity_decisions import execute_identity_decision_command
from autplay.adapters.postgresql.media_source import (
    PostgresMediaSourceReader,
    SystemElapsedClock,
    create_media_source_engine,
)
from autplay.adapters.postgresql.models import (
    AudioVariantRow,
    DeviceRow,
    LibraryEntryRow,
    MatchCandidateEvidenceRow,
    MatchDecisionRow,
    RecordingCanonicalVariantRow,
    ServerInstanceRow,
    UserAccountRow,
    UserSessionRow,
    UserTrackRefRow,
    VaultObjectRow,
    VaultReplicaRow,
)
from autplay.adapters.security.tokens import Hs256AccessTokenCodec, OpaqueRefreshTokenCodec
from autplay.adapters.system import SystemClock, Uuid7Generator
from autplay.application.auth import AuthService
from autplay.application.identity_evidence import (
    candidate_aggregate_sha256,
    canonical_candidate_evidence,
    canonical_query_snapshot,
)
from autplay.application.media_source import MediaSourceService
from autplay.application.music_library import MusicLibraryService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.import_identity import (
    CANDIDATE_GENERATION_VERSION,
    FEATURE_EXTRACTOR_VERSIONS,
    MATCHER_VERSION,
    NORMALIZATION_VERSION,
    normalize_text,
)
from autplay.domain.media_source import (
    MediaSourceError,
    MediaSourceSnapshotRequestV1,
    parse_snapshot,
)
from autplay.domain.profile_pairing import public_key_thumbprint, public_spki
from autplay.entrypoints.media_source_http import (
    MediaSourceIngressMiddleware,
    create_media_source_router,
)
from autplay.ports.media_source import ConfiguredM5Identity, MediaSourcePolicy
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, event, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker
from typed_assertions import present


@dataclass
class Harness:
    engine: Engine
    read_engine: Engine
    sessions: sessionmaker[Session]
    principal: Principal
    policy: MediaSourcePolicy
    source: UUID
    recording: UUID
    variant: UUID
    object_id: UUID
    auth: AuthService
    codec: Hs256AccessTokenCodec
    service: MediaSourceService
    clock: SystemElapsedClock
    identity_key: ec.EllipticCurvePrivateKey

    def token(self, *, expires_in: int = 60) -> str:
        with self.engine.connect() as connection:
            now = connection.scalar(text("SELECT clock_timestamp()"))
        return self.codec.issue(
            self.principal,
            token_id=uuid4(),
            issued_at=now.replace(microsecond=0),
            expires_at=now + timedelta(seconds=expires_in),
        )

    def reply(self, target: UUID | None = None) -> bytes:
        started = self.clock.mark()
        principal, claims = self.auth.authenticate_access_with_claims(self.token())
        return self.service.prepare(
            principal,
            claims,
            MediaSourceSnapshotRequestV1(str(target or self.source)),
            started=started,
        ).admit()

    def client(self) -> TestClient:
        app = FastAPI()
        app.include_router(
            create_media_source_router(
                self.service,
                authenticate=self.auth.authenticate_access_with_claims,
                clock=self.clock,
                isolated_fixture=True,
            ),
            prefix="/api/v1",
        )
        app.add_middleware(MediaSourceIngressMiddleware, clock=self.clock)
        return TestClient(app, follow_redirects=False)


@pytest.fixture(params=["ANDROID", "MACOS"])
def media(database_url: str, request: pytest.FixtureRequest) -> Iterator[Harness]:
    engine = create_engine(database_url, hide_parameters=True)
    read_engine = create_media_source_engine(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    clock = SystemElapsedClock()
    codec = Hs256AccessTokenCodec(
        "synthetic-M1-access-secret-at-least-32-bytes", issuer="M1", audience="M1"
    )
    auth = AuthService(
        unit_of_work_factory=SqlAlchemyAuthUnitOfWorkFactory(sessions),
        clock=SystemClock(),
        ids=Uuid7Generator(),
        access_tokens=codec,
        refresh_tokens=OpaqueRefreshTokenCodec(),
        access_token_ttl=timedelta(seconds=900),
        refresh_token_ttl=timedelta(days=90),
    )
    user, device, session_id, ref, variant, obj = (uuid4() for _ in range(6))
    principal = Principal(user, device, session_id, AccountRole.USER)
    key = ec.generate_private_key(ec.SECP256R1())
    spki = public_spki(key)
    thumb = public_key_thumbprint(spki)
    identity = ConfiguredM5Identity(
        uuid4(), 1, spki, thumb, "https://api.invalid", "https://stream.invalid"
    )
    policy = MediaSourcePolicy(True, identity, m5_capability_revision=3)
    try:
        with sessions.begin() as s:
            now = s.scalar(select(text("clock_timestamp()"))).replace(microsecond=0)
            s.add(
                UserAccountRow(
                    user_id=user, display_name="Synthetic M1", role="USER", status="ACTIVE"
                )
            )
            s.flush()
            s.add(
                DeviceRow(
                    device_id=device,
                    user_id=user,
                    device_name="Synthetic M1",
                    platform=request.param,
                    app_version="M1",
                    public_key=spki,
                    public_key_thumbprint_sha256=thumb,
                    created_at=now - timedelta(seconds=1),
                )
            )
            s.flush()
            s.add(
                UserSessionRow(
                    session_id=session_id,
                    user_id=user,
                    device_id=device,
                    refresh_token_hash=hashlib.sha256(session_id.bytes).digest(),
                    issued_at=now,
                    expires_at=now + timedelta(minutes=5),
                    family_id=session_id,
                    generation=0,
                    session_mode="V2",
                )
            )
            s.add(
                ServerInstanceRow(
                    server_instance_id=identity.server_instance_id,
                    identity_epoch=1,
                    identity_public_key_spki=spki,
                    identity_thumbprint_sha256=thumb,
                    label_hint="Synthetic M1",
                    api_origin=identity.api_origin,
                    stream_origin=identity.stream_origin,
                    capability_revision=3,
                    created_at=now,
                    updated_at=now,
                )
            )
            s.add(
                UserTrackRefRow(
                    user_track_ref_id=ref,
                    user_id=user,
                    raw_title="Synthetic M1",
                    raw_artist="Synthetic artist",
                    resolution_status="UNRESOLVED",
                )
            )
            s.flush()
            s.add(
                LibraryEntryRow(
                    user_id=user,
                    user_track_ref_id=ref,
                    source="IMPORT",
                    availability_status="VAULT",
                )
            )
        # Use the real explicit identity owner to create a valid resolved ref and
        # its decision evidence. Never disable constraints to fabricate a case.
        recording = MusicLibraryService(sessions, None).prepare(principal, ref)
        payload = b"synthetic M1 immutable object metadata fixture"
        digest = hashlib.sha256(payload).digest()
        with sessions.begin() as s:
            s.add(
                VaultObjectRow(
                    vault_object_id=obj,
                    sha256=digest,
                    byte_size=len(payload),
                    detected_mime_type="audio/flac",
                    commit_status="COMMITTED",
                    committed_at=now,
                )
            )
            s.flush()
            s.add(
                AudioVariantRow(
                    audio_variant_id=variant,
                    recording_id=recording,
                    vault_object_id=obj,
                    codec="flac",
                    container="flac",
                    sample_rate_hz=48000,
                    channels=2,
                    duration_ms=1000,
                    validation_status="VALID",
                    row_version=11,
                )
            )
            s.flush()
            s.add(
                RecordingCanonicalVariantRow(
                    recording_id=recording, audio_variant_id=variant, policy_version="M1", reason={}
                )
            )
            s.add(
                VaultReplicaRow(
                    vault_object_id=obj,
                    storage_backend="LOCAL_FILESYSTEM",
                    storage_key=digest.hex(),
                    replica_status="AVAILABLE",
                    verified_at=now,
                )
            )
        h: Harness
        service = MediaSourceService(
            PostgresMediaSourceReader(read_engine, clock=clock),
            policy=lambda: h.policy,
            clock=clock,
        )
        h = Harness(
            engine,
            read_engine,
            sessions,
            principal,
            policy,
            ref,
            recording,
            variant,
            obj,
            auth,
            codec,
            service,
            clock,
            key,
        )
        yield h
    finally:
        read_engine.dispose()
        engine.dispose()


def _database_image(h: Harness) -> list[tuple[str, object]]:
    """Exact synthetic row/sequence observations, without calculating a digest."""
    with h.engine.connect() as connection:
        tables = connection.execute(
            text(
                "SELECT schemaname, tablename FROM pg_tables "
                "WHERE schemaname IN ('account','library','catalog','identity','"
                "vault','audit','sync','resource') "
                "ORDER BY schemaname,tablename"
            )
        ).all()
        result: list[tuple[str, object]] = []
        for schema, table in tables:
            # Names come only from PostgreSQL catalog, quote as identifiers.
            quote = h.engine.dialect.identifier_preparer.quote
            rows = connection.execute(
                text(
                    f"SELECT to_jsonb(t)::text FROM {quote(schema)}.{quote(table)} t "
                    "ORDER BY to_jsonb(t)::text"
                )
            ).all()
            result.append((f"{schema}.{table}", rows))
        result.append(
            (
                "sequences",
                connection.execute(
                    text("SELECT schemaname,sequencename,last_value FROM pg_sequences ORDER BY 1,2")
                ).all(),
            )
        )
        return result


def _resolved_identity(
    h: Harness, ref_id: UUID, recording_id: UUID, *, active: bool = True
) -> tuple[UUID, int]:
    """Positive control on the committed owner projection, before any snapshot assertion."""
    with h.sessions() as s:
        ref = s.get(UserTrackRefRow, ref_id)
        assert ref is not None and ref.user_id == h.principal.user_id
        assert (ref.deleted_at is None) is active
        assert ref.resolution_status == "RESOLVED" and ref.recording_id == recording_id
        decision = s.get(MatchDecisionRow, ref.current_match_decision_id)
        assert decision is not None and decision.query_type == "USER_TRACK_REF"
        assert decision.user_track_ref_id == ref_id and decision.owner_user_id == ref.user_id
        assert decision.execution_mode == "APPLIED" and decision.decision_kind == "REVIEW_ACTION"
        assert decision.candidate_recording_id == recording_id
        assert (
            ref.resolved_at == decision.decided_at
            and ref.resolution_confidence == decision.confidence
        )
        predecessor = s.get(MatchDecisionRow, decision.supersedes_decision_id)
        assert predecessor is not None and predecessor.query_type == "USER_TRACK_REF"
        assert predecessor.user_track_ref_id == ref_id and predecessor.owner_user_id == ref.user_id
        if decision.review_action == "ACCEPT":
            selected = s.get(MatchCandidateEvidenceRow, decision.reviewed_candidate_evidence_id)
            assert selected is not None and selected.decision_id == predecessor.decision_id
            assert selected.recording_id == recording_id
            copied = s.scalar(
                select(MatchCandidateEvidenceRow).where(
                    MatchCandidateEvidenceRow.decision_id == decision.decision_id,
                    MatchCandidateEvidenceRow.rank == selected.rank,
                )
            )
            assert (
                copied is not None
                and copied.match_candidate_evidence_id != selected.match_candidate_evidence_id
            )
            assert (
                copied.recording_id == recording_id
                and copied.evidence_sha256 == selected.evidence_sha256
            )
            assert decision.candidate_evidence_sha256 == predecessor.candidate_evidence_sha256
        assert (
            s.scalar(
                select(MatchDecisionRow.decision_id).where(
                    MatchDecisionRow.supersedes_decision_id == decision.decision_id
                )
            )
            is None
        )
        assert (
            s.scalar(
                select(LibraryEntryRow.library_entry_id).where(
                    LibraryEntryRow.user_id == ref.user_id,
                    LibraryEntryRow.user_track_ref_id == ref_id,
                    LibraryEntryRow.removed_at.is_(None)
                    if active
                    else LibraryEntryRow.removed_at.is_not(None),
                )
            )
            is not None
        )
        return decision.decision_id, ref.row_version


def _accept_recording(h: Harness, ref_id: UUID, recording_id: UUID, *, base_version: int) -> UUID:
    """Future fixture: append/project an evaluation, commit, then append/project its ACCEPT.

    Both phases use the production identity command, including its canonical graph
    and immediate constraint checks. Separate commits preserve the APPLIED projection
    invariant; a re-evaluation supersedes this UTR's own current decision, never R2's UTR.
    """
    scope, key = f"M1-identity:{h.principal.user_id}", str(uuid4())
    with h.sessions.begin() as s:
        ref = s.scalar(
            select(UserTrackRefRow)
            .where(
                UserTrackRefRow.user_track_ref_id == ref_id,
                UserTrackRefRow.user_id == h.principal.user_id,
                UserTrackRefRow.deleted_at.is_(None),
            )
            .with_for_update()
        )
        assert ref is not None and ref.row_version == base_version
        assert (
            s.scalar(
                select(LibraryEntryRow.library_entry_id).where(
                    LibraryEntryRow.user_id == ref.user_id,
                    LibraryEntryRow.user_track_ref_id == ref_id,
                    LibraryEntryRow.removed_at.is_(None),
                )
            )
            is not None
        )
        previous_id = ref.current_match_decision_id
        previous = s.get(MatchDecisionRow, previous_id) if previous_id else None
        assert previous_id is None or previous is not None
        if previous is not None:
            assert previous.user_track_ref_id == ref_id and previous.owner_user_id == ref.user_id
            assert previous.execution_mode == "APPLIED"
        snapshot = canonical_query_snapshot(
            {
                "normalized_title": normalize_text(ref.raw_title or "Audio"),
                "normalized_artists": [normalize_text(ref.raw_artist or "Unknown artist")],
                "duration_ms": ref.raw_duration_ms,
                "version_markers": [],
                "market_scope": "GLOBAL",
                "evidence_ids": [f"user-track-ref:{ref_id}"],
            }
        )
        document = canonical_candidate_evidence(
            {
                "recording_id": str(recording_id),
                "raw_score": 0.8,
                "confidence": 0.8,
                "evidence_tier": "T0",
                "feature_scores": [],
                "hard_conflicts": [],
                "candidate_origins": [],
                "extractor_versions": dict(FEATURE_EXTRACTOR_VERSIONS),
            }
        )
        aggregate, _ = candidate_aggregate_sha256([(1, document.sha256)])
        request_hash = hashlib.sha256(
            snapshot.canonical_bytes + recording_id.bytes + key.encode()
        ).digest()
        now = s.scalar(text("SELECT clock_timestamp()"))
        evaluation = MatchDecisionRow(
            query_type="USER_TRACK_REF",
            owner_user_id=ref.user_id,
            user_track_ref_id=ref_id,
            query_snapshot=snapshot.value,
            query_snapshot_schema_version="1",
            snapshot_canonicalization_version="RFC8785",
            query_snapshot_sha256=snapshot.sha256,
            decision_kind="EVALUATION",
            execution_mode="APPLIED",
            decision_state="REVIEW_REQUIRED",
            candidate_recording_id=recording_id,
            candidate_count=1,
            candidate_evidence_sha256=aggregate,
            candidate_evidence_size_bytes=document.byte_size,
            evidence_mode="METADATA_ONLY",
            candidate_generation_version=CANDIDATE_GENERATION_VERSION,
            normalization_version=NORMALIZATION_VERSION,
            feature_extractor_versions=dict(FEATURE_EXTRACTOR_VERSIONS),
            matcher_version=MATCHER_VERSION,
            raw_score=Decimal("0.8"),
            confidence=Decimal("0.8"),
            evidence_tier="T0",
            feature_scores=[],
            hard_conflicts=[],
            candidate_origins=[],
            explanation_schema_version="1",
            actor_type="SYSTEM",
            idempotency_scope=scope,
            idempotency_key=key,
            request_sha256=request_hash,
            supersedes_decision_id=previous_id,
            supersession_reason="M1 explicit identity re-evaluation" if previous_id else None,
            decided_at=max(now, previous.decided_at + timedelta(microseconds=1))
            if previous
            else now,
        )

        def write_evaluation(active: Session) -> MatchDecisionRow:
            active.add(evaluation)
            active.flush([evaluation])
            active.add(
                MatchCandidateEvidenceRow(
                    decision_id=evaluation.decision_id,
                    recording_id=recording_id,
                    rank=1,
                    raw_score=Decimal("0.8"),
                    confidence=Decimal("0.8"),
                    evidence_tier="T0",
                    feature_scores=[],
                    hard_conflicts=[],
                    candidate_origins=[],
                    extractor_versions=dict(FEATURE_EXTRACTOR_VERSIONS),
                    evidence_schema_version="1",
                    evidence_sha256=document.sha256,
                    evidence_document_size_bytes=document.byte_size,
                )
            )
            active.flush()
            version = active.scalar(
                update(UserTrackRefRow)
                .where(
                    UserTrackRefRow.user_track_ref_id == ref_id,
                    present(UserTrackRefRow).user_id == present(ref).user_id,
                    UserTrackRefRow.row_version == base_version,
                    UserTrackRefRow.current_match_decision_id == previous_id,
                )
                .values(
                    recording_id=None,
                    resolution_status="CANDIDATES",
                    resolved_at=None,
                    resolution_confidence=evaluation.confidence,
                    current_match_decision_id=evaluation.decision_id,
                )
                .returning(UserTrackRefRow.row_version)
                .execution_options(synchronize_session=False)
            )
            assert version == base_version + 1
            return evaluation

        result = execute_identity_decision_command(
            s,
            idempotency_scope=scope,
            idempotency_key=key,
            request_sha256=request_hash,
            write=write_evaluation,
        )
        assert not result.replayed
        evaluation_id = result.decision.decision_id
    # The committed CANDIDATES row is the positive control for the first phase.
    with h.sessions.begin() as s:
        ref = s.scalar(
            select(UserTrackRefRow)
            .where(
                UserTrackRefRow.user_track_ref_id == ref_id,
                UserTrackRefRow.user_id == h.principal.user_id,
            )
            .with_for_update()
        )
        assert ref is not None and ref.row_version == base_version + 1
        assert ref.current_match_decision_id == evaluation_id
        assert ref.resolution_status == "CANDIDATES" and ref.recording_id is None
        evaluation = present(s.get(MatchDecisionRow, evaluation_id))
        assert evaluation is not None and evaluation.supersedes_decision_id == previous_id
        evidence = s.scalar(
            select(MatchCandidateEvidenceRow).where(
                MatchCandidateEvidenceRow.decision_id == evaluation_id,
                MatchCandidateEvidenceRow.rank == 1,
            )
        )
        assert evidence is not None and evidence.recording_id == recording_id
        values = {
            c.name: deepcopy(getattr(evaluation, c.name))
            for c in MatchDecisionRow.__table__.columns
            if c.name not in {"decision_id", "created_at"}
        }
        review_hash = hashlib.sha256(evaluation.request_sha256 + b"\0M1-ACCEPT").digest()
        values.update(
            decision_kind="REVIEW_ACTION",
            review_action="ACCEPT",
            actor_type="USER",
            actor_user_id=ref.user_id,
            reviewed_candidate_evidence_id=evidence.match_candidate_evidence_id,
            idempotency_key=key + ":accept",
            request_sha256=review_hash,
            supersedes_decision_id=evaluation_id,
            supersession_reason="M1 explicit owner acceptance",
            decided_at=max(
                s.scalar(text("SELECT clock_timestamp()")),
                evaluation.decided_at + timedelta(microseconds=1),
            ),
        )
        review = MatchDecisionRow(**values)

        def write_review(active: Session) -> MatchDecisionRow:
            active.add(review)
            active.flush([review])
            active.add(
                MatchCandidateEvidenceRow(
                    decision_id=review.decision_id,
                    **{
                        c.name: deepcopy(getattr(evidence, c.name))
                        for c in MatchCandidateEvidenceRow.__table__.columns
                        if c.name
                        not in {"match_candidate_evidence_id", "decision_id", "created_at"}
                    },
                )
            )
            active.flush()
            version = active.scalar(
                update(UserTrackRefRow)
                .where(
                    UserTrackRefRow.user_track_ref_id == ref_id,
                    UserTrackRefRow.user_id == ref.user_id,
                    UserTrackRefRow.row_version == base_version + 1,
                    UserTrackRefRow.current_match_decision_id == evaluation_id,
                )
                .values(
                    recording_id=recording_id,
                    resolution_status="RESOLVED",
                    resolved_at=review.decided_at,
                    resolution_confidence=review.confidence,
                    current_match_decision_id=review.decision_id,
                )
                .returning(UserTrackRefRow.row_version)
                .execution_options(synchronize_session=False)
            )
            assert version == base_version + 2
            return review

        result = execute_identity_decision_command(
            s,
            idempotency_scope=scope,
            idempotency_key=key + ":accept",
            request_sha256=review_hash,
            write=write_review,
        )
        assert not result.replayed
        review_id = result.decision.decision_id
    assert _resolved_identity(h, ref_id, recording_id) == (review_id, base_version + 2)
    return review_id


def test_real_auth_exact_tuple_readonly_and_no_database_mutation(media: Harness) -> None:
    before = _database_image(media)
    statements: list[str] = []

    def observe(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    event.listen(media.read_engine, "before_cursor_execute", observe)
    try:
        with media.client() as client:
            response = client.get(
                f"/api/v1/media-source/user-tracks/{media.source}/snapshot",
                headers={"Authorization": "Bearer " + media.token()},
            )
        assert response.status_code == 200
        value = parse_snapshot(response.content)
        assert (value.user_track_ref_id, value.recording_id, value.audio_variant_id) == tuple(
            str(item) for item in (media.source, media.recording, media.variant)
        )
        assert value.audio_variant_row_version == 11 and value.capability_revision == 3
        assert _database_image(media) == before
        selects = [item for item in statements if "FROM (SELECT 1) AS anchor" in item]
        assert len(selects) == 1 and "FOR UPDATE" not in selects[0] and "advisory" not in selects[0]
        assert sum(item == "SELECT clock_timestamp()" for item in statements) == 1
        assert "SET TRANSACTION READ ONLY" in statements
    finally:
        event.remove(media.read_engine, "before_cursor_execute", observe)
    with media.read_engine.connect() as connection:
        assert connection.scalar(text("SHOW transaction_read_only")) == "on"
        with pytest.raises(DBAPIError):
            connection.execute(text("UPDATE account.user_account SET display_name=display_name"))


@pytest.mark.parametrize(
    "mutation",
    [
        (
            "UPDATE library.user_track_ref SET deleted_at=clock_timestamp() WHERE "
            "user_track_ref_id=:ref"
        ),
        (
            "UPDATE library.library_entry SET removed_at=clock_timestamp() WHERE "
            "user_track_ref_id=:ref"
        ),
        "UPDATE catalog.recording SET deleted_at=clock_timestamp() WHERE recording_id=:recording",
        "DELETE FROM vault.recording_canonical_variant WHERE recording_id=:recording",
        (
            "UPDATE vault.audio_variant SET validation_status='INVALID' WHERE "
            "audio_variant_id=:variant"
        ),
        (
            "UPDATE vault.audio_variant SET deleted_at=clock_timestamp() WHERE "
            "audio_variant_id=:variant"
        ),
        "UPDATE vault.vault_object SET commit_status='QUARANTINED' WHERE vault_object_id=:obj",
        "UPDATE vault.vault_replica SET verified_at=NULL WHERE vault_object_id=:obj",
        "UPDATE vault.vault_replica SET replica_status='MISSING' WHERE vault_object_id=:obj",
        "UPDATE vault.vault_replica SET storage_backend='OTHER' WHERE vault_object_id=:obj",
    ],
)
def test_every_relationship_negative_is_the_same_masked_miss(media: Harness, mutation: str) -> None:
    assert parse_snapshot(media.reply()).audio_variant_id == str(media.variant)
    with media.engine.begin() as connection:
        connection.execute(
            text(mutation),
            {
                "ref": media.source,
                "recording": media.recording,
                "variant": media.variant,
                "obj": media.object_id,
            },
        )
    before = _database_image(media)
    with pytest.raises(MediaSourceError, match="not_found"):
        media.reply()
    assert _database_image(media) == before


def test_foreign_missing_unresolved_and_same_recording_other_entry_do_not_authorize(
    media: Harness,
) -> None:
    original_decision, original_version = _resolved_identity(media, media.source, media.recording)
    other_user, unresolved, foreign, other_ref = (uuid4() for _ in range(4))
    with media.sessions.begin() as s:
        s.add(
            UserAccountRow(user_id=other_user, display_name="Other", role="ADMIN", status="ACTIVE")
        )
        s.flush()
        s.add_all(
            [
                UserTrackRefRow(user_track_ref_id=unresolved, user_id=media.principal.user_id),
                UserTrackRefRow(user_track_ref_id=foreign, user_id=other_user),
            ]
        )
        other = UserTrackRefRow(
            user_track_ref_id=other_ref,
            user_id=media.principal.user_id,
            raw_title="Independent M1 same-recording ref",
            resolution_status="UNRESOLVED",
        )
        s.add(other)
        s.flush()
        other_version = other.row_version
        s.add(
            LibraryEntryRow(
                user_id=media.principal.user_id,
                user_track_ref_id=other_ref,
                source="IMPORT",
                availability_status="VAULT",
            )
        )
    # Retire the original reference before resolving another active reference
    # to the same recording; the user's active-recording uniqueness is real.
    with media.sessions.begin() as s:
        s.execute(
            text(
                "UPDATE library.user_track_ref SET deleted_at=clock_timestamp() "
                "WHERE user_track_ref_id=:ref"
            ),
            {"ref": media.source},
        )
        s.execute(
            text(
                "UPDATE library.library_entry SET removed_at=clock_timestamp() "
                "WHERE user_track_ref_id=:ref"
            ),
            {"ref": media.source},
        )
    other_decision = _accept_recording(
        media, other_ref, media.recording, base_version=other_version
    )
    assert other_decision != original_decision
    assert _resolved_identity(media, media.source, media.recording, active=False) == (
        original_decision,
        original_version + 1,
    )
    before = _database_image(media)
    for target in (other_ref,):
        value = parse_snapshot(media.reply(target))
        assert (value.user_track_ref_id, value.recording_id, value.audio_variant_id) == (
            str(target),
            str(media.recording),
            str(media.variant),
        )
    assert _database_image(media) == before
    with media.sessions.begin() as s:
        s.execute(
            text(
                "UPDATE library.library_entry SET removed_at=clock_timestamp() WHERE "
                "user_track_ref_id=:ref"
            ),
            {"ref": media.source},
        )
    before = _database_image(media)
    for target in (uuid4(), unresolved, foreign, media.source):
        with pytest.raises(MediaSourceError, match="not_found"):
            media.reply(target)
    value = parse_snapshot(media.reply(other_ref))
    assert (value.user_track_ref_id, value.recording_id, value.audio_variant_id) == (
        str(other_ref),
        str(media.recording),
        str(media.variant),
    )
    assert _database_image(media) == before


@pytest.mark.parametrize("owner", ["session", "jwt", "neither"])
def test_actual_slow_last_select_expires_current_valid_claims(media: Harness, owner: str) -> None:
    token = media.token(expires_in=2 if owner == "jwt" else 60)
    principal, claims = media.auth.authenticate_access_with_claims(token)
    if owner == "session":
        with media.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE account.user_session SET expires_at=clock_timestamp()+interval '2 "
                    "seconds' WHERE session_id=:sid"
                ),
                {"sid": principal.session_id},
            )
    before = _database_image(media)

    def delay(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> tuple[str, Any]:
        if "FROM (SELECT 1) AS anchor" in statement:
            statement = statement.replace(
                "FROM (SELECT 1) AS anchor", "FROM (SELECT 1, pg_sleep(5)) AS anchor"
            )
        return statement, parameters

    event.listen(media.read_engine, "before_cursor_execute", delay, retval=True)
    try:
        prepared = media.service.prepare(
            principal,
            claims,
            MediaSourceSnapshotRequestV1(str(media.source)),
            started=media.clock.mark(),
        )
        if owner == "neither":
            assert parse_snapshot(prepared.admit()).audio_variant_id == str(media.variant)
        else:
            with pytest.raises(
                MediaSourceError,
                match="authentication_required" if owner == "jwt" else "session_revoked",
            ):
                prepared.admit()
        assert _database_image(media) == before
    finally:
        event.remove(media.read_engine, "before_cursor_execute", delay)


def test_config_gate_and_database_fault_never_create_or_repair_identity(media: Harness) -> None:
    for policy in (
        replace(media.policy, media_source_snapshot_v1_enabled=False),
        replace(
            present(media.policy),
            identity=replace(present(media.policy.identity), identity_epoch=2),
        ),
    ):
        old, media.policy = media.policy, policy
        before = _database_image(media)
        with pytest.raises(MediaSourceError, match="media_source_unavailable"):
            media.reply()
        assert _database_image(media) == before
        media.policy = old
    with media.engine.begin() as connection:
        connection.execute(text("DELETE FROM account.server_instance"))
    before = _database_image(media)
    with pytest.raises(MediaSourceError, match="media_source_unavailable"):
        media.reply()
    assert _database_image(media) == before


def test_canonical_variant_cannot_cross_recordings_even_with_owner_access(media: Harness) -> None:
    with media.sessions.begin() as s:
        # A second recording legitimately created by the existing identity owner.
        ref = UserTrackRefRow(user_id=media.principal.user_id, raw_title="Different recording")
        s.add(ref)
        s.flush()
        second = ref.user_track_ref_id
        s.add(
            LibraryEntryRow(
                user_id=media.principal.user_id,
                user_track_ref_id=second,
                source="IMPORT",
                availability_status="PENDING",
            )
        )
    other_recording = MusicLibraryService(media.sessions, None).prepare(media.principal, second)
    with media.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE vault.audio_variant SET recording_id=:other WHERE audio_variant_id=:variant"
            ),
            {"other": other_recording, "variant": media.variant},
        )
    with pytest.raises(MediaSourceError, match="not_found"):
        media.reply()


@pytest.mark.parametrize(
    "mutation,code",
    [
        (
            "UPDATE account.user_account SET status='DISABLED' WHERE user_id=:uid",
            "authentication_required",
        ),
        (
            "UPDATE account.user_account SET role='ADMIN' WHERE user_id=:uid",
            "authentication_required",
        ),
        (
            "UPDATE account.user_account SET deleted_at=clock_timestamp() WHERE user_id=:uid",
            "authentication_required",
        ),
        (
            "UPDATE account.device SET revoked_at=clock_timestamp() WHERE device_id=:did",
            "authentication_required",
        ),
        (
            (
                "UPDATE account.device SET created_at=clock_timestamp()+interval '10 "
                "seconds' WHERE device_id=:did"
            ),
            "authentication_required",
        ),
        (
            "UPDATE account.user_session SET revoked_at=clock_timestamp() WHERE session_id=:sid",
            "session_revoked",
        ),
        ("DELETE FROM account.user_session WHERE session_id=:sid", "authentication_required"),
        (
            "INSERT INTO account.device_key_block(user_id,device_key_thumbprint_sha256) "
            "SELECT user_id,public_key_thumbprint_sha256 FROM account.device WHERE device_id=:did",
            "authentication_required",
        ),
    ],
)
def test_single_snapshot_rechecks_mutable_actor_after_actual_authentication(
    media: Harness, mutation: str, code: str
) -> None:
    principal, claims = media.auth.authenticate_access_with_claims(media.token())
    with media.engine.begin() as connection:
        connection.execute(
            text(mutation),
            {"uid": principal.user_id, "did": principal.device_id, "sid": principal.session_id},
        )
    before = _database_image(media)
    with pytest.raises(MediaSourceError, match=code):
        media.service.prepare(
            principal,
            claims,
            MediaSourceSnapshotRequestV1(str(media.source)),
            started=media.clock.mark(),
        ).admit()
    assert _database_image(media) == before


def test_m5_singleton_revision_never_uses_scoped_d1_counter(media: Harness) -> None:
    from autplay.adapters.postgresql.models.device_compatibility import DeviceCapabilityRevisionRow

    identity = media.policy.identity
    assert identity is not None
    scope = (
        identity.server_instance_id,
        identity.identity_epoch,
        media.principal.user_id,
        media.principal.device_id,
    )
    with media.sessions.begin() as s:
        now = s.scalar(text("SELECT clock_timestamp()"))
        device = s.get(DeviceRow, media.principal.device_id)
        s.add(
            DeviceCapabilityRevisionRow(
                server_instance_id=scope[0],
                identity_epoch=scope[1],
                user_id=scope[2],
                device_id=scope[3],
                capability_revision=90,
                effective_content_sha256=hashlib.sha256(b"synthetic D1 effective state").digest(),
                identity_thumbprint_sha256=identity.thumbprint_sha256,
                device_key_thumbprint_sha256=present(device).public_key_thumbprint_sha256,
                created_at=now,
                updated_at=now,
            )
        )
    assert parse_snapshot(media.reply()).capability_revision == 3
    with media.sessions.begin() as s:
        revision = present(s.get(DeviceCapabilityRevisionRow, scope))
        revision.capability_revision = 91
        revision.effective_content_sha256 = hashlib.sha256(
            b"changed synthetic D1 effective state"
        ).digest()
    assert parse_snapshot(media.reply()).capability_revision == 3
    with media.sessions.begin() as s:
        present(s.get(ServerInstanceRow, identity.server_instance_id)).capability_revision = 4
    media.policy = replace(media.policy, m5_capability_revision=4)
    assert parse_snapshot(media.reply()).capability_revision == 4
    with media.sessions() as s:
        assert present(s.get(DeviceCapabilityRevisionRow, scope)).capability_revision == 91


def _second_source(h: Harness) -> tuple[UUID, UUID, UUID]:
    with h.sessions.begin() as s:
        ref = UserTrackRefRow(user_id=h.principal.user_id, raw_title="Second M1 target")
        s.add(ref)
        s.flush()
        ref_id = ref.user_track_ref_id
        s.add(
            LibraryEntryRow(
                user_id=h.principal.user_id,
                user_track_ref_id=ref_id,
                source="IMPORT",
                availability_status="PENDING",
            )
        )
    recording = MusicLibraryService(h.sessions, None).prepare(h.principal, ref_id)
    with h.sessions.begin() as s:
        now = s.scalar(text("SELECT clock_timestamp()"))
        payload = b"a distinct synthetic M1 representation"
        digest = hashlib.sha256(payload).digest()
        obj = VaultObjectRow(
            sha256=digest,
            byte_size=len(payload),
            detected_mime_type="audio/flac",
            commit_status="COMMITTED",
            committed_at=now,
        )
        s.add(obj)
        s.flush()
        variant = AudioVariantRow(
            recording_id=recording,
            vault_object_id=obj.vault_object_id,
            codec="flac",
            container="flac",
            sample_rate_hz=48000,
            channels=2,
            duration_ms=1000,
            validation_status="VALID",
        )
        s.add(variant)
        s.flush()
        variant_id = variant.audio_variant_id
        s.add(
            RecordingCanonicalVariantRow(
                recording_id=recording, audio_variant_id=variant_id, policy_version="M1", reason={}
            )
        )
        s.add(
            VaultReplicaRow(
                vault_object_id=obj.vault_object_id,
                storage_backend="LOCAL_FILESYSTEM",
                storage_key=digest.hex(),
                replica_status="AVAILABLE",
                verified_at=now,
            )
        )
    return ref_id, recording, variant_id


def test_reassignment_returns_real_new_utr_version_and_never_old_recording_new_variant(
    media: Harness,
) -> None:
    original_decision, original_version = _resolved_identity(media, media.source, media.recording)
    original = parse_snapshot(media.reply())
    assert original.user_track_ref_row_version == original_version
    assert (original.recording_id, original.audio_variant_id) == (
        str(media.recording),
        str(media.variant),
    )
    second, recording, variant = _second_source(media)
    second_identity = _resolved_identity(media, second, recording)
    before = _database_image(media)
    second_snapshot = parse_snapshot(media.reply(second))
    assert (
        second_snapshot.user_track_ref_id,
        second_snapshot.recording_id,
        second_snapshot.audio_variant_id,
    ) == (str(second), str(recording), str(variant))
    assert _database_image(media) == before
    with media.sessions.begin() as s:
        s.execute(
            text(
                "UPDATE library.user_track_ref SET deleted_at=clock_timestamp() "
                "WHERE user_track_ref_id=:ref"
            ),
            {"ref": second},
        )
        s.execute(
            text(
                "UPDATE library.library_entry SET removed_at=clock_timestamp() "
                "WHERE user_track_ref_id=:ref"
            ),
            {"ref": second},
        )
    new_decision = _accept_recording(media, media.source, recording, base_version=original_version)
    assert new_decision not in (original_decision, second_identity[0])
    assert _resolved_identity(media, second, recording, active=False) == (
        second_identity[0],
        second_identity[1] + 1,
    )
    with media.sessions() as s:
        old = s.get(MatchDecisionRow, original_decision)
        assert old is not None and old.user_track_ref_id == media.source
        assert old.candidate_recording_id == media.recording
    before = _database_image(media)
    current = parse_snapshot(media.reply())
    assert current.user_track_ref_id == original.user_track_ref_id
    # The real CANDIDATES -> RESOLVED lifecycle advances the UTR twice.
    assert current.user_track_ref_row_version == original.user_track_ref_row_version + 2
    assert (current.recording_id, current.audio_variant_id) == (str(recording), str(variant))
    assert current.recording_id != original.recording_id
    assert current.audio_variant_id != original.audio_variant_id
    assert _database_image(media) == before


def test_outgoing_redirect_is_masked_without_chasing_or_repair(media: Harness) -> None:
    from autplay.adapters.postgresql.models.audit import CatalogChangeSetRow
    from autplay.adapters.postgresql.models.identity import RecordingRedirectRow

    _, recording, _ = _second_source(media)
    with media.sessions.begin() as s:
        change = CatalogChangeSetRow(
            operation_type="MERGE",
            actor_type="USER",
            actor_user_id=media.principal.user_id,
            reason="Synthetic M1 redirect",
            status="APPLIED",
        )
        s.add(change)
        s.flush()
        s.add(
            RecordingRedirectRow(
                source_recording_id=media.recording,
                target_recording_id=recording,
                change_set_id=change.change_set_id,
                reason="Synthetic M1 redirect",
            )
        )
    before = _database_image(media)
    with pytest.raises(MediaSourceError, match="not_found"):
        media.reply()
    assert _database_image(media) == before


@pytest.mark.parametrize("stage", ["relationship", "clock"])
def test_actual_adapter_database_exception_is_safe503_without_echo(
    media: Harness, stage: str
) -> None:
    before = _database_image(media)

    def fail(
        connection: Any,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        if (stage == "relationship" and "FROM (SELECT 1) AS anchor" in statement) or (
            stage == "clock" and statement == "SELECT clock_timestamp()"
        ):
            raise DBAPIError(statement, parameters, Exception("synthetic private DB detail"))

    event.listen(media.read_engine, "before_cursor_execute", fail)
    try:
        with media.client() as client:
            response = client.get(
                f"/api/v1/media-source/user-tracks/{media.source}/snapshot",
                headers={"Authorization": "Bearer " + media.token()},
            )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "media_source_unavailable"
        assert "private DB detail" not in response.text
        assert _database_image(media) == before
    finally:
        event.remove(media.read_engine, "before_cursor_execute", fail)


def test_multiple_available_replicas_are_one_existence_fact_not_duplicate_source_rows(
    media: Harness,
) -> None:
    with media.sessions.begin() as s:
        now = s.scalar(text("SELECT clock_timestamp()"))
        s.add(
            VaultReplicaRow(
                vault_object_id=media.object_id,
                storage_backend="LOCAL_FILESYSTEM",
                storage_key="synthetic-second-verified-replica",
                replica_status="AVAILABLE",
                verified_at=now,
            )
        )
        s.add(
            VaultReplicaRow(
                vault_object_id=media.object_id,
                storage_backend="LOCAL_FILESYSTEM",
                storage_key="synthetic-unverified-replica",
                replica_status="AVAILABLE",
                verified_at=None,
            )
        )
    value = parse_snapshot(media.reply())
    assert value.audio_variant_id == str(media.variant)
