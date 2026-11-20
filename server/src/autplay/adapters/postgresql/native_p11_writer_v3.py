"""Create the Source3 origin at the ordinary P11 insert, on its original RR connection."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial
from hashlib import sha256
from uuid import UUID, uuid7

from sqlalchemy import text
from sqlalchemy.orm import Session

from autplay.application.native_atomic_capture_v3 import prepare_native_atomic_capture_v3
from autplay.application.native_original_capture_v3 import (
    _encode,
    prepare_original_native_capture_v3,
)
from autplay.application.recommendations import RecommendationPipelineRunner
from autplay.application.training_consent import TrainingCaptureGrant
from autplay.application.training_consent_v3 import NativeTrainingConsentServiceV3
from autplay.domain.adaptive_recommendations import (
    DEFAULT_ADAPTIVE_FEATURE_POLICY,
    MAX_SOURCE_EVENTS,
)
from autplay.domain.auth import Principal
from autplay.domain.native_capture_creation_budget_v3 import (
    NativeCaptureCreationBudgetV3,
    NativeCaptureCreationLeaseV3,
)
from autplay.domain.native_original_capture_v3 import (
    NativeCaptureServerIdentityV3,
    NativeCausalAuthorityV3,
    SonaCaptureBundleV3,
)
from autplay.domain.native_served_authority_values import NativeServedIdentity
from autplay.domain.recommendations import (
    PipelineDefinition,
    RecommendationInputSnapshot,
    RecommendationQuery,
    RecommendationRequestTrace,
    RecommendationResponse,
)
from autplay.domain.training_consent_v3 import NativeConsentSnapshotPreludeV3

from .models.ml import RecommendationTemporalSnapshotRow
from .native_capture import discover_native_capture, lock_native_capture_owner, read_capture_facts
from .native_temporal_source import milliseconds
from .recommendations import _SNAPSHOT_SQL, _SNAPSHOT_SQL_V2, SqlAlchemyRecommendationRuntime
from .sona_native_capture import SqlAlchemySonaNativeCapture


class _LedgerRead:
    def __init__(
        self, lease: NativeCaptureCreationLeaseV3, prelude: NativeConsentSnapshotPreludeV3
    ):
        lease.admit_rows(prelude.byte_length)
        self.lease = lease
        self.remaining = prelude.byte_length
        self.digest = sha256()

    def update_bytes(self, data: bytes) -> None:
        if len(data) > self.remaining or self.remaining < 0:
            raise ValueError("NATIVE_V3_LEDGER_OVERFLOW")
        self.lease.charge(sha_input_bytes=len(data), node_visits=1)
        self.digest.update(data)
        self.remaining -= len(data)

    def finish_stream(self) -> bytes:
        if self.remaining != 0:
            raise ValueError("NATIVE_V3_LEDGER_INCOMPLETE")
        self.remaining = -1
        return self.digest.digest()

    def abort(self) -> None:
        self.remaining = -1


def _causal(session: Session, owner: UUID, request: UUID) -> NativeCausalAuthorityV3:
    session.execute(
        text("SELECT app_private.assert_native_served_authority(:request)"), {"request": request}
    )
    row = session.execute(
        text("""
        SELECT q.serving_contract_version,f.request_hash_format_version,
            CASE f.request_hash_format_version WHEN 2 THEN p.document_sha256
                WHEN 3 THEN r.root_proof_sha256 END,
            d.decision_sha256,d.served_items_sha256,q.pipeline_manifest_sha256
        FROM ml.recommendation_request q
        JOIN ml.native_served_request_hash_format f
          ON f.served_request_id=q.recommendation_request_id AND f.owner_user_id=q.user_id
        JOIN ml.recommendation_serving_decision d USING(recommendation_request_id)
        LEFT JOIN ml.recommendation_request_replay_payload p
          ON p.recommendation_request_id=q.recommendation_request_id AND p.owner_user_id=q.user_id
        LEFT JOIN ml.recommendation_request_root_v3 r
          ON r.served_request_id=q.recommendation_request_id AND r.owner_user_id=q.user_id
        WHERE q.user_id=:owner AND q.recommendation_request_id=:request AND d.state='COMMITTED_P11'
          AND d.serving_provenance='P11' AND d.inference_evidence_sha256 IS NULL
          AND d.fallback_reason IS NULL
          AND d.served_truth_expires_at>clock_timestamp()
          AND ((f.request_hash_format_version=2 AND p.replay_expires_at>clock_timestamp())
           OR (f.request_hash_format_version=3 AND r.expires_at>clock_timestamp()))
    """),
        {"owner": owner, "request": request},
    ).one()
    return NativeCausalAuthorityV3(request, row[0], row[1], *(value.hex() for value in row[2:]))


def _insert_temporal(
    session: Session,
    response: RecommendationResponse,
    temporal_id: UUID,
    capture: SonaCaptureBundleV3,
    retained_until: datetime,
    lease: NativeCaptureCreationLeaseV3,
) -> tuple[bytes, bytes]:
    trace = response.request
    request_time, user_id = trace.created_at, trace.query.user_id
    with capture.projection(meter=lease) as doc:
        temporal = doc["temporal_snapshot"]
        profile = temporal["adaptive_profile"]
        policy = DEFAULT_ADAPTIVE_FEATURE_POLICY

        def digest(value: object) -> bytes:
            encoded = _encode(value, lease)
            lease.charge(
                sha_input_bytes=len(encoded),
                node_visits=1,
            )
            return sha256(encoded).digest()

        temporal_sha = bytes.fromhex(doc["temporal_snapshot_sha256"])
        feature_sha = bytes.fromhex(temporal["feature_policy_sha256"])
        session.add(
            RecommendationTemporalSnapshotRow(
                recommendation_temporal_snapshot_id=temporal_id,
                user_id=user_id,
                recommendation_input_snapshot_id=trace.snapshot.snapshot_id,
                cutoff_at_ms=capture.cutoff_at_ms,
                interaction_watermark=capture.native_interaction_watermark,
                catalog_snapshot=trace.snapshot.catalog_snapshot,
                availability_snapshot_sha256=bytes.fromhex(trace.snapshot.availability_snapshot),
                baseline_input_snapshot_sha256=bytes.fromhex(trace.snapshot.input_snapshot_sha256),
                feature_policy_key=policy.key,
                feature_policy_version=policy.version,
                feature_policy_sha256=feature_sha,
                event_time_policy_sha256=digest(
                    {
                        "episode_inactivity_gap_ms": policy.episode_inactivity_gap_ms,
                        "future_clock_tolerance_ms": policy.future_clock_tolerance_ms,
                        "maximum_recent_backfill_ms": policy.maximum_recent_backfill_ms,
                        "stable_order": [
                            "effective_at_ms",
                            "received_at_ms",
                            "server_sequence",
                            "evidence_id",
                        ],
                    }
                ),
                derived_features_sha256=digest({k: profile[k] for k in ("maturity", "dimensions")}),
                source_evidence_sha256=digest(temporal["source_evidence"]),
                snapshot_sha256=temporal_sha,
                source_event_count=len(temporal["facts"]),
                dimension_count=len(profile["dimensions"]),
                snapshot_document=temporal,
                retained_until=retained_until,
                created_at=request_time,
            )
        )
        session.flush()
    return temporal_sha, feature_sha


class SqlAlchemyNativeP11WriterV3(SqlAlchemyRecommendationRuntime):
    def __init__(
        self,
        sessions: Callable[[], Session],
        *,
        identity: NativeServedIdentity | Principal,
        consent: NativeTrainingConsentServiceV3,
        snapshot_policy_version: str = "1",
        catalogue_capture: SqlAlchemySonaNativeCapture | None = None,
    ):
        super().__init__(sessions, snapshot_policy_version=snapshot_policy_version, native_capture=catalogue_capture)
        if isinstance(identity, NativeServedIdentity):
            identity.validate()
        self.identity = identity
        self.consent = consent

    def _served_identity(self, session: Session) -> NativeServedIdentity:
        if isinstance(self.identity, NativeServedIdentity):
            return self.identity
        actor = self.identity
        journal, server, epoch = session.execute(
            text("""SELECT c.journal_epoch, s.server_instance_id, s.identity_epoch
                    FROM sync.device_sync_cursor c CROSS JOIN account.server_instance s
                    WHERE c.user_id=:owner AND c.device_id=:device LIMIT 2"""),
            {"owner": actor.user_id, "device": actor.device_id},
        ).one()
        return NativeServedIdentity(actor.user_id, actor.device_id, journal, server, epoch)

    def _origin_for_replay(self, session: Session, owner: UUID, request: UUID) -> UUID:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        origin = session.scalar(
            text("""SELECT r.original_request_id FROM ml.recommendation_request_root_v3 r
                    JOIN ml.recommendation_serving_decision d
                      ON d.recommendation_request_id=r.served_request_id
                    WHERE r.owner_user_id=:owner AND r.served_request_id=:request
                      AND r.expires_at>clock_timestamp()
                      AND d.served_truth_expires_at>clock_timestamp()"""),
            {"owner": owner, "request": request},
        )
        if origin is None:
            return request
        if not isinstance(origin, UUID):
            raise ValueError("NATIVE_V3_REPLAY_ORIGIN")
        session.execute(
            text("SELECT app_private.assert_native_served_authority(:request)"),
            {"request": request},
        )
        return origin

    def exact(self, user_id: UUID, request_id: UUID) -> RecommendationResponse | None:
        with self._sessions() as session, session.begin():
            origin = self._origin_for_replay(session, user_id, request_id)
            response = self._exact_in_session(session, user_id, origin)
            if response is None or origin == request_id:
                return response
            return replace(
                response, request=replace(response.request, recommendation_request_id=request_id)
            )

    def request(self, user_id: UUID, request_id: UUID) -> RecommendationRequestTrace | None:
        with self._sessions() as session, session.begin():
            origin = self._origin_for_replay(session, user_id, request_id)
            trace = self._trace(session, user_id, origin)
            if trace is None or origin == request_id:
                return trace
            return replace(trace, recommendation_request_id=request_id)

    def capture_run_save_v3(
        self,
        *,
        original_request_id: UUID,
        query: RecommendationQuery,
        request_time: datetime,
        capture_eligible: bool,
        retained_until: datetime,
        pipeline: PipelineDefinition,
        build_response: Callable[[RecommendationInputSnapshot], RecommendationResponse],
    ) -> RecommendationResponse:
        user_id = query.user_id
        actor = self.identity
        owner = actor.owner if isinstance(actor, NativeServedIdentity) else actor.user_id
        if owner != user_id:
            raise ValueError("NATIVE_V3_OWNER_BINDING")
        if not capture_eligible:
            return super().serve_atomic(query, retained_until=retained_until, serve=build_response)
        if pipeline.version != self._snapshot_policy_version:
            raise ValueError("NATIVE_V3_SNAPSHOT_PIPELINE_BINDING")
        if pipeline.version == "2" and (self._native_capture is None or self._native_capture.catalogue_owner != owner):
            raise ValueError("NATIVE_V3_ORIGINAL_CATALOGUE_REQUIRED")
        self.ensure_pipeline(pipeline)
        with self._sessions() as session, session.begin():
            session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            pid, xid = session.execute(
                text("SELECT pg_backend_pid(), pg_current_xact_id()::text::bigint")
            ).one()
            budget = NativeCaptureCreationBudgetV3((pid, xid))
            served_id = uuid7()
            lease = budget.begin_request(
                owner_user_id=user_id,
                original_request_id=original_request_id,
                served_request_id=served_id,
            )
            success = False
            try:
                lock_native_capture_owner(session, user_id)

                with self.consent.capture_grant_v3(
                    session, user_id, meter=lease, admit_snapshot=partial(_LedgerRead, lease)
                ) as grant:
                    if grant is not None:
                        identity = self._served_identity(session)
                        session.execute(
                            text(
                                "SELECT app_private.arm_native_original_context_v3("
                                ":owner,:device,:journal,:server,:epoch)"
                            ),
                            {
                                "owner": user_id,
                                "device": identity.device,
                                "journal": identity.journal_epoch,
                                "server": identity.server_instance,
                                "epoch": identity.identity_epoch,
                            },
                        )
                        retained_until = max(retained_until, request_time + timedelta(days=180))
                        row_bytes = session.scalar(
                            text(
                                "SELECT coalesce(sum(pg_column_size(s)),0) FROM ("
                                + (_SNAPSHOT_SQL_V2 if pipeline.version == "2" else _SNAPSHOT_SQL)
                                + ") s"
                            ),
                            {"user_id": user_id, "limit": self._snapshot_limit},
                        )
                        lease.admit_rows(int(row_bytes or 0))
                    baseline = self._capture_in_session(
                        session, user_id, retained_until=retained_until, created_at=request_time
                    )
                    response = build_response(baseline)
                    trace = response.request
                    if (
                        trace.query.user_id,
                        trace.recommendation_request_id,
                        trace.created_at,
                        trace.pipeline,
                        trace.snapshot,
                    ) != (user_id, original_request_id, request_time, pipeline, baseline.reference):
                        raise ValueError("NATIVE_V3_RESPONSE_BINDING")
                    if grant is None:
                        self._save_in_session(session, response)
                    else:
                        self._write_original(session, identity, baseline, response, grant, lease)
                        response = replace(
                            response, request=replace(trace, recommendation_request_id=served_id)
                        )
                success = True
                return response
            finally:
                lease.close(success=success)
                budget.finish()

    def _write_original(
        self,
        session: Session,
        identity: NativeServedIdentity,
        baseline: RecommendationInputSnapshot,
        response: RecommendationResponse,
        grant: TrainingCaptureGrant,
        lease: NativeCaptureCreationLeaseV3,
    ) -> None:
        user_id, request_time = identity.owner, response.request.created_at
        pid, xid = session.execute(
            text("SELECT pg_backend_pid(), pg_current_xact_id()::text::bigint")
        ).one()
        session.flush()
        temporal_id = uuid7()
        lease.admit_rows(32 * (MAX_SOURCE_EVENTS + 1))
        boundary = discover_native_capture(
            session, owner=user_id, cutoff=milliseconds(request_time)
        )
        # A normalized event carries <=8192 payload and <=1024 acknowledgement
        # bytes. Reserve the full per-row projection, including fixed authority
        # and association fields, before any event or causal payload is fetched.
        lease.admit_rows(12288 * len(boundary.event_ids))
        facts = read_capture_facts(session, boundary, baseline)
        causal = tuple(
            _causal(session, user_id, rid)
            for rid in sorted({f.causality.request_id for f in facts if f.causality})
        )
        runner = RecommendationPipelineRunner()
        with (
            prepare_original_native_capture_v3(
                baseline=baseline,
                response=response,
                grant=grant,
                boundary=boundary,
                facts=facts,
                temporal_id=temporal_id,
                server_identity=NativeCaptureServerIdentityV3(
                    identity.server_instance, identity.identity_epoch
                ),
                causal_authority=causal,
                candidate_authority=runner,
                meter=lease,
            ) as source,
            prepare_native_atomic_capture_v3(
                capture=source.capture,
                original_profile=source.original_profile,
                baseline=baseline,
                response=response,
                identity=identity,
                served_request_id=lease.served_request_id,
                candidate_authority=runner,
                root_budget=lease,
                source_meter=lease,
                transaction_binding=(pid, xid),
            ) as atomic,
        ):
            capture = source.capture
            temporal_sha, feature_sha = _insert_temporal(
                session, response, temporal_id, capture, baseline.retained_until, lease
            )
            self._save_in_session(
                session,
                response,
                temporal_id=temporal_id,
                temporal_digest=temporal_sha,
                feature_digest=feature_sha,
            )
            session.flush()
            if self._native_capture is not None and response.request.pipeline.version == "2":
                self._save_collection(
                    session, response, baseline, grant.revision, self._native_capture,
                    require_complete_catalogue=True,
                )
            orig = atomic.original
            params = {
                "original": lease.original_request_id,
                "served": lease.served_request_id,
                "capture": capture.document,
                "root": atomic.proof.document,
                "request": orig.request_document,
                "baseline": orig.baseline_document,
                "ranking": orig.ranking_document,
                "profile": orig.profile_document,
                "pipeline": orig.pipeline_document,
                "bits": bytes.fromhex(orig.profile_binary64_sha256),
            }
            for name, value in params.items():
                if name == "baseline":
                    lease.admit_original_baseline(len(orig.baseline_document))
                elif isinstance(value, bytes):
                    lease.admit_document(len(value))
            with self.consent.capture_grant_v3(
                session, user_id, meter=lease, admit_snapshot=partial(_LedgerRead, lease)
            ) as current:
                if current != grant:
                    raise ValueError("NATIVE_V3_CONSENT_CHANGED")
                session.execute(
                    text(
                        "SELECT app_private.commit_native_atomic_capture_v3("
                        ":original,:served,:capture,:root,:request,:baseline,"
                        ":ranking,:profile,:pipeline,:bits)"
                    ),
                    params,
                )
