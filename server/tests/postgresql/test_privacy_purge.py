"""Restricted final owner purge on real PostgreSQL, preserving shared metadata."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import autplay.adapters.postgresql.import_runtime as import_runtime
import pytest
from autplay.adapters.postgresql.catalog_changes import PostgresCatalogChangeRepository
from autplay.adapters.postgresql.enrichment import SqlAlchemyEnrichmentRuntime
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.adapters.postgresql.models import (
    CatalogChangeItemRow,
    CatalogChangeSetRow,
    LibraryEntryRow,
    MatchDecisionRow,
    RecommendationInputSnapshotRow,
    UserAccountRow,
    UserTrackRefRow,
)
from autplay.adapters.postgresql.models.account_deletion import AccountDeletionRequestRow
from autplay.adapters.postgresql.provider_maintenance import PostgresProviderMaintenanceRepository
from autplay.adapters.postgresql.upload_cleanup import PostgresUploadCleanupRepository
from autplay.application.deletion_request_evidence import initial_receipt_sha256
from autplay.application.imports import ImportService
from autplay.application.job_worker import WorkerOutcome
from autplay.application.privacy_deletion import PrivacyDeletionService
from autplay.application.track_metadata import TrackMetadataService
from autplay.domain.auth import AccountRole
from autplay.domain.privacy_deletion import DeletionEvidenceError, DeletionRequestEvidence
from autplay.domain.provider_maintenance import MaintenanceAction, MaintenanceTicket
from autplay.domain.resource_execution import ExitKind, ProcessExitEvidence, ProcessIdentity
from autplay.ports.jobs import EnqueueJob
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from .conftest import DatabaseHarness
from .test_account_deletion import PairingHarness, prepared
from .test_account_deletion import base_pair as base_pair
from .test_account_deletion import pair as pair
from .test_account_recovery import required, seed_authority
from .test_enrichment_runtime import JOB_KEY, _model, _vault_recording
from .test_import_identity_runtime import _insert_recording, _run_import_worker, _runtime
from .test_resource_admission_runtime import AdmissionHarness
from .test_resource_admission_runtime import admission as admission
from .test_upload_cleanup import cancel
from .test_vault_ingest_fence import IngestFixture
from .test_vault_ingest_fence import ingest as ingest
from .test_web_passkeys import SECRET, Harness, _assertion, _finish, _register


def test_consumed_passkey_login_and_system_browser_audit_are_removed(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autplay.adapters.postgresql.web_admin_uow import SqlAlchemyWebAdminUnitOfWorkFactory
    from autplay.adapters.postgresql.web_passkeys import SqlAlchemyWebPasskeyUnitOfWorkFactory
    from autplay.adapters.webauthn import DuoWebPasskeyVerifier
    from autplay.application.web_admin import WebAdminService
    from autplay.application.web_passkeys import WebPasskeyService
    from test_webauthn import ORIGIN

    with Session(pair.engine) as session, session.begin():
        required(session.get(UserAccountRow, pair.actor.user_id)).role = "ADMIN"
    pair.actor = replace(pair.actor, role=AccountRole.ADMIN)
    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    web = WebAdminService(SqlAlchemyWebAdminUnitOfWorkFactory(sessions), SECRET)
    invitation = web.issue_invitation(pair.actor.user_id)
    browser = web.login(web.begin_login(), invitation.bearer, b"b" * 32)
    harness = Harness(
        pair.engine,
        web,
        WebPasskeyService(
            SqlAlchemyWebPasskeyUnitOfWorkFactory(sessions), DuoWebPasskeyVerifier(ORIGIN), SECRET
        ),
        browser,
    )
    _, key, handle = _register(harness)
    options, assertion = _assertion(harness, key, handle)
    logged_in = _finish(harness, options, assertion)
    web.revoke_browser_session_local(pair.actor.user_id, logged_in.actor.web_session_id, uuid4())
    operation = overdue(pair, monkeypatch)
    with pair.engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM account.web_passkey_ceremony")) == 2
        assert connection.scalar(text("SELECT count(*) FROM audit.audit_event")) > 0
        connection.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
        for table in ("account.web_passkey_ceremony", "audit.audit_event", "account.web_session"):
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def test_other_hosts_invitation_receipt_is_erased_without_removing_host_room(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autplay.adapters.postgresql.models.social import FriendshipRow
    from autplay.adapters.postgresql.wave import SqlAlchemyWaveService
    from autplay.application.social import SocialService

    from .test_social_s1c import _principal

    now = datetime.now(UTC)
    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    with sessions.begin() as session:
        host = _principal(session, "Host", now)
        lower, higher = sorted((host.user_id, pair.actor.user_id))
        session.add(FriendshipRow(lower_user_id=lower, higher_user_id=higher))
    social = SocialService(sessions, pair.key)
    social.set_settings(
        pair.actor,
        {
            "operation_id": str(uuid4()),
            "friend_presence_visibility_enabled": True,
            "room_activity_sharing_enabled": True,
            "invite_availability_enabled": True,
        },
        now,
    )
    social.heartbeat(pair.actor, uuid4(), now)
    room = SqlAlchemyWaveService(sessions).create(host, now)
    invitation = social.create_invitation(host, room.room_id, pair.actor.user_id, uuid4(), now)
    operation = overdue(pair, monkeypatch)
    with pair.engine.begin() as connection:
        connection.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
        assert connection.scalar(text("SELECT count(*) FROM wave.room")) == 1
        assert connection.scalar(text("SELECT count(*) FROM social.friend_room_invitation")) == 0
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM social.operation_receipt "
                    "WHERE result_target_id=:invitation"
                ),
                {"invitation": UUID(str(invitation["invitation_id"]))},
            )
            == 0
        )


def test_purge_receipts_cannot_be_forged_changed_or_removed(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    with (
        pytest.raises(DBAPIError, match="privacy_purge_receipt_immutable"),
        pair.engine.begin() as c,
    ):
        c.execute(
            text("INSERT INTO account.account_purge_receipt VALUES(:id,now(),1)"), {"id": uuid4()}
        )
    operation = overdue(pair, monkeypatch)
    with pair.engine.begin() as connection:
        connection.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
    for statement in (
        "UPDATE account.account_purge_receipt SET removed_rows=999",
        "DELETE FROM account.account_purge_receipt",
    ):
        with (
            pytest.raises(DBAPIError, match="privacy_purge_receipt_immutable"),
            pair.engine.begin() as c,
        ):
            c.execute(text(statement))
    with pytest.raises(DBAPIError, match="Refusing to discard privacy purge evidence"):
        database_harness.downgrade(database_name, "0052_account_deletion")


def overdue(pair: PairingHarness, monkeypatch: pytest.MonkeyPatch) -> UUID:
    service, code, _, body = prepared(pair)
    now = datetime.now(UTC) - timedelta(days=31)
    # The signed operation is fresh at submission; only authoritative acceptance time is moved.
    monkeypatch.setattr("autplay.application.account_deletion.database_now", lambda session: now)
    monkeypatch.setattr(
        "autplay.application.account_deletion.require_fresh", lambda request, at: None
    )
    result = service.request(pair.actor, code, body)
    identifier = UUID(result["deletion_request_id"])
    with Session(pair.engine) as session, session.begin():
        row = required(session.get(AccountDeletionRequestRow, identifier))
        row.state, row.revision, row.purge_started_at = "PURGING", 2, datetime.now(UTC)
    return identifier


def test_absent_owner_without_sql_receipt_cannot_manufacture_purge_completion(
    pair: PairingHarness,
) -> None:
    owner, operation = uuid4(), uuid4()
    ledger = pair.deletion_ledger
    prepared_evidence = ledger.prepare(owner, operation, datetime.now(UTC))
    service = PrivacyDeletionService(sessionmaker(pair.engine, expire_on_commit=False), ledger)
    with pytest.raises(DeletionEvidenceError):
        service.purge(owner, operation)
    with pytest.raises(DeletionEvidenceError):
        service.restore_guard()
    assert ledger.read() == (prepared_evidence,)


def copy_pending_fixture(
    pair: PairingHarness, source_id: UUID, user_id: UUID, device_id: UUID
) -> UUID:
    """Seed another accepted DB intent; this fixture does not exercise request authentication."""
    identifier = uuid4()
    with Session(pair.engine) as session, session.begin():
        original = required(session.get(AccountDeletionRequestRow, source_id))
        fields = {
            column.name: getattr(original, column.name) for column in original.__table__.columns
        }
        fields.update(
            deletion_request_id=identifier,
            user_id=user_id,
            actor_device_id=device_id,
            state="PENDING",
            revision=1,
            purge_started_at=None,
            requested_at=original.requested_at + timedelta(seconds=1),
            cancel_before=original.cancel_before + timedelta(seconds=1),
        )
        account = required(session.get(UserAccountRow, user_id))
        account.status, account.authority_generation = "DELETION_PENDING", 2
        copied = AccountDeletionRequestRow(**fields)
        session.add(copied)
        pair.deletion_ledger.request_record(
            DeletionRequestEvidence(
                pair.deletion_ledger.owner_tag(user_id),
                identifier,
                copied.request_sha256.hex(),
                initial_receipt_sha256(copied),
                "ATTEMPTED",
                copied.requested_at,
            )
        )
    return identifier


def test_held_oldest_request_does_not_starve_later_account(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from .test_social_s1c import _principal

    with Session(pair.engine) as session, session.begin():
        other = _principal(session, "Next account", datetime.now(UTC))
    original = overdue(pair, monkeypatch)
    copy_pending_fixture(pair, original, other.user_id, other.device_id)
    with pair.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO account.account_deletion_hold(user_id,reason_code) "
                "VALUES(:owner,'TEST_HOLD')"
            ),
            {"owner": pair.actor.user_id},
        )
    ledger = pair.deletion_ledger
    service = PrivacyDeletionService(sessionmaker(pair.engine, expire_on_commit=False), ledger)
    assert service.run_due(limit=1) == 0
    assert service.run_due(limit=1) == 1
    assert service.run_due(limit=1) == 0
    assert service.run_due(limit=1) == 0
    with Session(pair.engine) as session:
        assert session.get(UserAccountRow, other.user_id) is None
        assert session.get(UserAccountRow, pair.actor.user_id) is not None


def test_unexpired_personal_snapshot_is_removed_only_by_accepted_purge(
    pair: PairingHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Session(pair.engine) as session, session.begin():
        session.add(
            RecommendationInputSnapshotRow(
                recommendation_input_snapshot_id=uuid4(),
                user_id=pair.actor.user_id,
                input_snapshot_sha256=b"s" * 32,
                interaction_watermark=0,
                catalog_snapshot=1,
                availability_snapshot="a" * 64,
                policy_snapshot_sha256=b"p" * 32,
                snapshot_document={"user_id": str(pair.actor.user_id)},
                created_at=datetime.now(UTC),
                retained_until=datetime.now(UTC) + timedelta(days=30),
            )
        )
    with pytest.raises(DBAPIError), pair.engine.begin() as connection:
        connection.execute(text("DELETE FROM ml.recommendation_input_snapshot"))
    operation = overdue(pair, monkeypatch)
    with pair.engine.begin() as connection:
        connection.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
        assert connection.scalar(text("SELECT count(*) FROM ml.recommendation_input_snapshot")) == 0


def test_purge_removes_owner_and_preserves_another_account(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other = uuid4()
    with Session(pair.engine) as session, session.begin():
        session.add(UserAccountRow(user_id=other, display_name="Survivor", role="USER"))
    operation = overdue(pair, monkeypatch)
    with pair.engine.begin() as connection:
        count = connection.scalar(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
        assert count > 1
    with Session(pair.engine) as session:
        assert session.get(UserAccountRow, pair.actor.user_id) is None
        assert required(session.get(UserAccountRow, other)).display_name == "Survivor"
        assert session.scalar(text("SELECT count(*) FROM app_private.privacy_purge_context")) == 0
        assert session.scalar(text("SELECT count(*) FROM account.account_purge_receipt")) == 1


def test_purge_requires_accepted_intent_and_hold_blocks_without_partial_changes(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(DBAPIError, match="privacy_purge_not_authorized"), pair.engine.begin() as c:
        c.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": uuid4()},
        )
    operation = overdue(pair, monkeypatch)
    with pair.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO account.account_deletion_hold(user_id,reason_code) "
                "VALUES(:owner,'TEST_HOLD')"
            ),
            {"owner": pair.actor.user_id},
        )
    with pytest.raises(DBAPIError, match="privacy_deletion_held"), pair.engine.begin() as c:
        c.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
    with Session(pair.engine) as session:
        assert session.get(UserAccountRow, pair.actor.user_id) is not None
        assert session.get(AccountDeletionRequestRow, operation) is not None
        assert session.scalar(text("SELECT count(*) FROM account.account_purge_receipt")) == 0


def test_independent_evidence_reapplies_deletion_to_a_pre_request_database_copy(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    database_harness: DatabaseHarness,
    database_name: str,
) -> None:
    pair.engine.dispose()
    copy_name = database_harness.create_database(template=database_name)
    restored_engine = create_engine(database_harness.database_url(copy_name))
    try:
        ledger = pair.deletion_ledger
        operation = overdue(pair, monkeypatch)
        service = PrivacyDeletionService(sessionmaker(pair.engine, expire_on_commit=False), ledger)
        result = service.purge(pair.actor.user_id, operation)
        assert result.completed_at is not None
        assert service.purge(pair.actor.user_id, operation) == result
        with Session(restored_engine) as session:
            assert required(session.get(UserAccountRow, pair.actor.user_id)).status == "ACTIVE"
            assert session.get(AccountDeletionRequestRow, operation) is None
        restored = PrivacyDeletionService(
            sessionmaker(restored_engine, expire_on_commit=False), ledger
        )
        assert restored.restore_guard() == 1
        assert restored.restore_guard() == 0
        with Session(restored_engine) as session:
            assert session.get(UserAccountRow, pair.actor.user_id) is None
            assert (
                session.scalar(
                    text("SELECT count(*) FROM app_private.privacy_restore_authorization")
                )
                == 0
            )
        assert ledger.read() == (result,)
    finally:
        restored_engine.dispose()
        database_harness.drop_database(copy_name)


def test_external_completion_failure_leaves_durable_prepared_and_resumes(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    operation = overdue(pair, monkeypatch)
    ledger = pair.deletion_ledger
    service = PrivacyDeletionService(sessionmaker(pair.engine, expire_on_commit=False), ledger)
    original = ledger.complete

    def failed(*args: object, **kwargs: object) -> object:
        raise DeletionEvidenceError()

    monkeypatch.setattr(ledger, "complete", failed)
    with pytest.raises(DeletionEvidenceError):
        service.purge(pair.actor.user_id, operation)
    assert ledger.read()[0].completed_at is None
    with Session(pair.engine) as session:
        assert session.get(UserAccountRow, pair.actor.user_id) is None
    monkeypatch.setattr(ledger, "complete", original)
    assert service.run_due() == 0
    assert ledger.read()[0].completed_at is not None


def test_purge_clears_revision_evidence_and_cancelled_browser_admissions(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed_authority(pair)
    with Session(pair.engine) as session, session.begin():
        ref = UserTrackRefRow(
            user_id=pair.actor.user_id,
            raw_title="Private title",
            raw_artist="Artist",
            resolution_status="UNRESOLVED",
        )
        session.add(ref)
        session.flush()
        identifier = ref.user_track_ref_id
        session.add(
            LibraryEntryRow(
                user_id=pair.actor.user_id,
                user_track_ref_id=identifier,
                source="IMPORT",
                availability_status="LOCAL",
            )
        )
    TrackMetadataService(sessionmaker(pair.engine, expire_on_commit=False)).command(
        pair.actor,
        identifier,
        operation_id=uuid4(),
        expected_revision=0,
        action="EDIT",
        fields={"album": "Private"},
    )
    operation = overdue(pair, monkeypatch)
    with pair.engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM library.track_metadata_revision")) == 1
        connection.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
    with pair.engine.connect() as connection:
        for table in (
            "account.device_admission",
            "account.web_session",
            "library.track_metadata_revision",
            "library.user_track_ref",
            "sync.sync_event",
        ):
            assert connection.scalar(text(f"SELECT count(*) FROM {table}")) == 0


def test_reviewed_import_cycles_are_deleted_but_other_owner_admin_history_is_redacted(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    with Session(pair.engine) as session, session.begin():
        required(session.get(UserAccountRow, pair.actor.user_id)).role = "ADMIN"
    pair.actor = replace(pair.actor, role=AccountRole.ADMIN)
    _insert_recording(pair.engine, "Song", "Artist")
    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    imports = ImportService(sessions)
    own = imports.start(
        pair.actor,
        payload=b"title,artist,duration_ms\nSong,Artist,180000\n",
        format_name="CSV",
        schema_version="1",
        mode="LIBRARY_ONLY",
    )
    assert _run_import_worker(sessions) is WorkerOutcome.COMPLETED
    own_entry = imports.report(pair.actor, own.import_job_id).entries[0]
    imports.review(
        pair.actor,
        own.import_job_id,
        own_entry.import_entry_id,
        predecessor_decision_id=required(own_entry.decision_id),
        action="ACCEPT",
        selected_rank=1,
        idempotency_key="owned-review",
    )
    other_engine, other_sessions, other = _runtime(database_url)
    try:
        service = ImportService(other_sessions)
        created = service.start(
            other,
            payload=b"title,artist,duration_ms\nSong,Artist,180000\n",
            format_name="CSV",
            schema_version="1",
            mode="LIBRARY_ONLY",
        )
        assert _run_import_worker(other_sessions) is WorkerOutcome.COMPLETED
        entry = service.report(other, created.import_job_id).entries[0]
        original = import_runtime._review_decision

        def admin_review(predecessor: MatchDecisionRow, **kwargs: Any) -> MatchDecisionRow:
            row = original(predecessor, **kwargs)
            row.actor_type = "ADMIN"
            row.actor_user_id = pair.actor.user_id
            return row

        # Exercise a database-supported cross-owner ADMIN actor without weakening its validators.
        with monkeypatch.context() as patch:
            patch.setattr(import_runtime, "_review_decision", admin_review)
            service.review(
                other,
                created.import_job_id,
                entry.import_entry_id,
                predecessor_decision_id=required(entry.decision_id),
                action="ACCEPT",
                selected_rank=1,
                idempotency_key=f"admin-review:{pair.actor.user_id}",
            )
        operation = overdue(pair, monkeypatch)
        with pair.engine.begin() as connection:
            connection.execute(
                text("SELECT account.purge_account(:owner,:request)"),
                {"owner": pair.actor.user_id, "request": operation},
            )
        with Session(pair.engine) as session:
            assert (
                session.scalar(
                    select(MatchDecisionRow).where(
                        MatchDecisionRow.owner_user_id == pair.actor.user_id
                    )
                )
                is None
            )
            row = required(
                session.scalar(
                    select(MatchDecisionRow).where(
                        MatchDecisionRow.owner_user_id == other.user_id,
                        MatchDecisionRow.actor_type == "ADMIN",
                    )
                )
            )
            assert row.actor_user_id is None and row.actor_erased_at is not None
            assert str(pair.actor.user_id) not in row.idempotency_scope
            assert str(pair.actor.user_id) not in row.idempotency_key
        assert service.report(other, created.import_job_id).entries[0].status == "MANUAL_MATCH"
    finally:
        other_engine.dispose()


def test_shared_planned_catalog_change_remains_applicable_after_actor_erasure(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
    database_url: str,
) -> None:
    with Session(pair.engine) as session, session.begin():
        required(session.get(UserAccountRow, pair.actor.user_id)).role = "ADMIN"
    pair.actor = replace(pair.actor, role=AccountRole.ADMIN)
    source = _insert_recording(pair.engine, "Source", "Artist")
    target = _insert_recording(pair.engine, "Target", "Artist")
    with Session(pair.engine) as session, session.begin():
        proposed = PostgresCatalogChangeRepository(session).propose_recording_change(
            principal=pair.actor,
            operation_type="MERGE",
            source_recording_id=source,
            target_recording_id=target,
            reason="Private administrator comment",
            now=datetime.now(UTC),
        )
    with Session(pair.engine) as session:
        item = required(
            session.scalar(
                select(CatalogChangeItemRow).where(
                    CatalogChangeItemRow.change_set_id == proposed.change_set_id
                )
            )
        )
        original = (item.from_snapshot, item.to_snapshot)
    operation = overdue(pair, monkeypatch)
    with pair.engine.begin() as connection:
        connection.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
    other_engine, _, other = _runtime(database_url)
    try:
        with Session(pair.engine) as session, session.begin():
            row = required(session.get(CatalogChangeSetRow, proposed.change_set_id))
            assert row.actor_erased_at is not None and row.actor_user_id is None
            item = required(
                session.scalar(
                    select(CatalogChangeItemRow).where(
                        CatalogChangeItemRow.change_set_id == proposed.change_set_id
                    )
                )
            )
            assert (item.from_snapshot, item.to_snapshot) == original
            applied = PostgresCatalogChangeRepository(session).apply(
                principal=other, change_set_id=proposed.change_set_id, now=datetime.now(UTC)
            )
            assert applied.status == "APPLIED"
    finally:
        other_engine.dispose()


def test_purge_fences_competing_job_claim_and_preserves_shared_vault_and_model(
    pair: PairingHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = sessionmaker(pair.engine, expire_on_commit=False)
    runtime = SqlAlchemyEnrichmentRuntime(sessions)
    model, artifact, preprocessing = _model("privacy")
    runtime.register(
        model,
        artifact_manifest=artifact,
        preprocessing_manifest=preprocessing,
        license_review_reference="review://fixture/privacy",
    )
    provider = uuid4()
    with sessions.begin() as session:
        session.execute(
            text(
                "INSERT INTO identity.source_provider "
                "(provider_id,provider_key,display_name,adapter_id,adapter_version) "
                "VALUES(:id,'privacy.fixture','Fixture','fixture','1')"
            ),
            {"id": provider},
        )
        recording, variant = _vault_recording(
            session, pair.actor.user_id, provider, label="privacy"
        )
    enrichment_id = uuid4()
    jobs = SqlAlchemyJobUnitOfWorkFactory(sessions)
    with jobs() as unit:
        job = unit.jobs.enqueue(
            EnqueueJob(
                key=JOB_KEY,
                user_id=pair.actor.user_id,
                payload={"enrichment_job_id": str(enrichment_id)},
            )
        )
        unit.commit()
    with sessions.begin() as session:
        session.execute(
            text(
                "INSERT INTO ml.enrichment_job (enrichment_job_id,job_id,job_kind,"
                "recording_id,audio_variant_id,embedding_model_id,expected_weights_sha256,"
                "expected_preprocessing_sha256) VALUES(:id,:job,'AUDIO_EMBEDDING',:recording,"
                ":variant,:model,:weights,:preprocessing)"
            ),
            {
                "id": enrichment_id,
                "job": job.job_id,
                "recording": recording,
                "variant": variant,
                "model": model.embedding_model_id,
                "weights": model.weights_sha256,
                "preprocessing": model.preprocessing_sha256,
            },
        )
    operation = overdue(pair, monkeypatch)
    with sessions.begin() as session:
        session.execute(
            text("SELECT account.verify_account_purge_ready(:owner)"), {"owner": pair.actor.user_id}
        )
        with jobs() as competitor:
            assert not competitor.jobs.claim(
                worker_id="competing-worker",
                supported=(JOB_KEY,),
                lease_interval=timedelta(seconds=30),
                limit=1,
            )
            competitor.commit()
        session.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
    with sessions() as session:
        row = session.execute(
            text("SELECT user_id,state,payload FROM jobs.job WHERE job_id=:job"),
            {"job": job.job_id},
        ).one()
        assert tuple(row) == (None, "CANCELLED", {})
        assert session.scalar(text("SELECT count(*) FROM ml.enrichment_job")) == 1
        assert session.scalar(text("SELECT count(*) FROM ml.embedding_model")) == 1
        assert session.scalar(text("SELECT count(*) FROM vault.vault_object")) == 1
        assert session.scalar(text("SELECT count(*) FROM vault.audio_variant")) == 1
        assert (
            session.scalar(text("SELECT authorized_by_user_id FROM vault.acquisition_record"))
            is None
        )
    with jobs() as competitor:
        assert not competitor.jobs.claim(
            worker_id="later-worker",
            supported=(JOB_KEY,),
            lease_interval=timedelta(seconds=30),
            limit=1,
        )
        competitor.commit()


def synthetic_io_budget(pair: PairingHarness) -> None:
    with pair.engine.begin() as connection:
        connection.execute(
            text("""
            UPDATE account.resource_quota_policy SET global_playbacks=16, global_transfers=16,
              playback_ceiling=16, transfer_ceiling=16, budget_evidence='synthetic-not-deployment'
            WHERE global_playbacks IS NULL
        """)
        )
        connection.execute(
            text("""
                UPDATE account.internal_io_policy SET active_limit=100, measured_ceiling=100,
                  server_instance_id=:server, identity_epoch=1, environment_sha256=repeat('a',64),
                  workload_sha256=repeat('b',64), report_sha256=repeat('c',64),
                  playback_ceiling=1000000, transfer_ceiling=1000000,
                  initialized_at=now(), updated_at=now(), revision=2 WHERE singleton_id=1
            """),
            {"server": UUID(pair.identity["expected_server_instance_id"])},
        )


def test_unconfirmed_cleanup_blocks_purge_then_completed_upload_cycle_is_removed(
    pair: PairingHarness,
    admission: AdmissionHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    synthetic_io_budget(pair)
    upload = admission.upload(pair.actor)
    cancel(admission, upload)
    claims = PostgresUploadCleanupRepository(admission.sessions)
    claim = required(claims.claim(upload))
    maintenance = PostgresProviderMaintenanceRepository(admission.sessions)
    ticket = MaintenanceTicket(
        uuid4(), uuid4(), None, claim.claim_id, MaintenanceAction.UPLOAD_CLEANUP, claim.storage_key
    )
    identity = ProcessIdentity(12345, b"i" * 32)
    maintenance.prepare(ticket)
    maintenance.start(ticket, identity)
    operation = overdue(pair, monkeypatch)
    with (
        pytest.raises(DBAPIError, match="privacy_process_closure_required"),
        pair.engine.begin() as c,
    ):
        c.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
    with Session(pair.engine) as session:
        assert session.get(UserAccountRow, pair.actor.user_id) is not None
        assert session.scalar(text("SELECT count(*) FROM vault.upload_cleanup_claim")) == 1
    maintenance.confirm(ticket, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"p" * 32, 0, identity))
    claims.complete(claim, ticket.execution_id)
    with pair.engine.begin() as connection:
        connection.execute(
            text("SELECT account.purge_account(:owner,:request)"),
            {"owner": pair.actor.user_id, "request": operation},
        )
        assert connection.scalar(text("SELECT count(*) FROM vault.upload_session")) == 0
        assert connection.scalar(text("SELECT count(*) FROM vault.upload_cleanup_claim")) == 0
        assert connection.scalar(text("SELECT count(*) FROM vault.provider_maintenance")) == 0


def test_completed_ingest_cleanup_cycle_can_be_purged_without_deleting_shared_bytes(
    pair: PairingHarness, ingest: IngestFixture, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from autplay.adapters.postgresql.ingest_cleanup import PostgresIngestCleanupRepository
    from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
    from autplay.adapters.postgresql.models import UploadSessionRow

    from .test_ingest_cleanup import IDENTITY, cleanup_ticket, finalized

    synthetic_io_budget(pair)
    _, claim = finalized(ingest)
    with ingest.sessions.begin() as session:
        PostgresJobRepository(session).complete(ingest.lease.fence)
        upload = required(session.get(UploadSessionRow, ingest.upload_id))
        owner_id, device_id = upload.user_id, required(upload.device_id)
    original = overdue(pair, monkeypatch)
    operation = copy_pending_fixture(pair, original, owner_id, device_id)
    ledger = pair.deletion_ledger
    service = PrivacyDeletionService(ingest.sessions, ledger)
    with pytest.raises(DBAPIError, match="privacy_staging_cleanup_required"):
        service.purge(owner_id, operation)
    assert ledger.read() == ()
    repository = PostgresIngestCleanupRepository(ingest.sessions)
    cleanup = cleanup_ticket(claim)
    repository.prepare(cleanup)
    repository.start(cleanup, IDENTITY)
    repository.confirm(cleanup, ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, 0, IDENTITY))
    repository.complete(claim, cleanup.execution_id)
    assert service.purge(owner_id, operation).completed_at is not None
    with ingest.sessions() as session:
        assert session.scalar(text("SELECT count(*) FROM vault.ingest_cleanup_claim")) == 0
        assert session.scalar(text("SELECT count(*) FROM vault.ingest_cleanup_execution")) == 0
        assert session.scalar(text("SELECT count(*) FROM vault.ingest_execution")) == 0
        assert session.scalar(text("SELECT count(*) FROM vault.upload_session")) == 0
        assert session.scalar(text("SELECT count(*) FROM vault.vault_object")) == 1
        assert session.get(UserAccountRow, pair.actor.user_id) is not None
