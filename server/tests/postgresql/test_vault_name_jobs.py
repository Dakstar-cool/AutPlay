from uuid import uuid4

import pytest
from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
from autplay.application.job_worker import JobHandlerRegistry, JobWorker, WorkerOutcome
from autplay.application.vault_name_jobs import NORMALIZE_NAMES, VaultNameJobs
from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor, WebAdminError
from sqlalchemy import text

from .test_track_metadata import setup
from .test_track_metadata_acquisition import publish_audio


def test_panel_job_replays_and_updates_catalog_and_account(database_url: str) -> None:
    engine, sessions, person, ref, _ = setup(database_url)
    try:
        variant = publish_audio(sessions, person, ref)
        # Simulate a download made before normalization was part of ingestion.
        with sessions.begin() as session:
            session.execute(
                text("""
                UPDATE catalog.recording SET title='Song feat. Guest'
                WHERE recording_id=(SELECT recording_id FROM vault.audio_variant
                                    WHERE audio_variant_id=:variant)
            """),
                {"variant": variant},
            )
            session.execute(
                text("UPDATE account.user_account SET role='OWNER' WHERE user_id=:u"),
                {"u": person.user_id},
            )
            session.execute(
                text(
                    "UPDATE library.user_track_ref SET raw_title='Song feat. Guest' "
                    "WHERE user_track_ref_id=:r"
                ),
                {"r": ref},
            )
        jobs = VaultNameJobs(sessions)
        actor = WebActor(uuid4(), person.user_id, uuid4(), AccountRole.OWNER, 0)
        operation = uuid4()
        jobs.request(actor, operation)
        jobs.request(actor, operation)
        with pytest.raises(WebAdminError, match="normalization_active"):
            jobs.request(actor, uuid4())
        worker = JobWorker(
            uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions),
            worker_id="normalize-test",
            registry=JobHandlerRegistry({NORMALIZE_NAMES: jobs.run}),
        )
        assert worker.run_once().outcome == WorkerOutcome.COMPLETED
        assert jobs.latest() == {"state": "COMPLETED", "progress_current": 2, "progress_total": 2}
        jobs.request(actor, operation)
        assert worker.run_once().outcome == WorkerOutcome.IDLE
        with sessions() as session:
            fields = session.scalar(
                text(
                    "select document->'fields' from library.track_metadata "
                    "where user_track_ref_id=:r"
                ),
                {"r": ref},
            )
            assert fields["title"] == "Song" and fields["artist"] == "Artist feat. Guest"
            assert (
                session.scalar(
                    text(
                        "select count(*) from catalog.browse_page "
                        "where page_key like 'vault-display-v1:%' and document->>'title'='Song'"
                    )
                )
                == 1
            )
    finally:
        engine.dispose()
