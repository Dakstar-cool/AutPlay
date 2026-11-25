import hashlib
import io
import json
import wave
from uuid import uuid4

import pytest
from autplay.adapters.preservation_sources import archive
from autplay.application.music_preservation import PreservationWatcher, reports, request_run
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .identity_factory import insert_world


def test_scan_deduplicates_jobs_and_requires_exact_original(
    database_url, database_connection, tmp_path
):
    world = insert_world(database_connection)
    database_connection.execute(
        "UPDATE vault.audio_variant SET validation_status='VALID' WHERE audio_variant_id=%s",
        (world.audio_variant_id,),
    )
    digest = database_connection.execute(
        "SELECT encode(sha256,'hex') FROM vault.vault_object WHERE vault_object_id=%s",
        (world.vault_object_id,),
    ).fetchone()[0]
    database_connection.commit()
    engine = create_engine(database_url)
    sessions = sessionmaker(engine)
    owner = world.owner_user_id
    watcher = PreservationWatcher(sessions, owner, tmp_path)
    track = next(iter(archive()))

    def case(key, **changes):
        return dict(track, event=key, **changes)

    rows = [
        case("present"),
        case("missing"),
        case("unavailable"),
        case("lead", confidence="review"),
    ]
    grant = dict(
        artist=track["artist"],
        title=track["title"],
        version=track["version"],
        sha256=digest,
        duration_ms=180000,
        allow_vault=True,
        candidate_id="abcdef12345",
        rights_url="https://example.org/grant",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    (tmp_path / "originals.json").write_text(
        json.dumps(
            {
                "present": grant,
                "missing": dict(grant, sha256="b" * 64),
                "same-original": dict(grant, sha256="b" * 64),
            }
        )
    )

    def broken():
        raise TimeoutError()

    sources = [("fixture", lambda: rows), ("broken", broken)]
    try:
        watcher.tick(sources)
        watcher.tick(sources)
        with sessions.begin() as session:
            [first] = reports(session, owner)
            assert first["counts"] == dict(new=4, present=1, queued=1, unavailable=1, review=1)
            assert first["sources"]["broken"] == "TimeoutError"
            assert reports(session, world.other_user_id) == []
            request = uuid4()
            request_run(session, owner, request)
            request_run(session, owner, request)
        rows.append(case("same-original"))
        watcher.tick(sources)
        with sessions.begin() as session:
            second, first = reports(session, owner)
            assert second["counts"] == dict(new=1, present=1, pending=2, unavailable=1, review=1)
            assert session.execute(
                text(
                    "SELECT priority,payload->>'event' FROM jobs.job "
                    "WHERE job_type='music.preservation.original'"
                )
            ).one() == (1, "missing")
            request_run(session, owner, uuid4())
        rows[1] = case("missing", status="retracted")
        watcher.tick(sources)
        with sessions() as session:
            latest, previous, first = reports(session, owner)
            assert latest["counts"]["historical"] == 1
            assert previous["counts"]["pending"] == 2
    finally:
        engine.dispose()


@pytest.mark.usefixtures("internal_io_budget")
@pytest.mark.parametrize("wrong_edition", [True, False])
def test_authorized_source_through_real_queue_and_vault(
    database_url, tmp_path, monkeypatch, wrong_edition
):
    from autplay.adapters.filesystem.provider_media import SOURCE_METADATA_FILE
    from autplay.adapters.filesystem.vault import FilesystemVaultStorage
    from autplay.adapters.postgresql.jobs_uow import SqlAlchemyJobUnitOfWorkFactory
    from autplay.adapters.postgresql.vault_uow import (
        SqlAlchemyVaultUnitOfWorkFactory,
        TransactionalIngestRepository,
    )
    from autplay.application.imports import ImportJobHandler
    from autplay.application.job_worker import JobHandlerRegistry, JobWorker
    from autplay.application.music_preservation import PRESERVE, original_in_vault
    from autplay.application.vault_ingest import VaultIngestHandler
    from autplay.domain.jobs import JobKey
    from autplay.domain.vault import AudioTechnicalMetadata, ChromaprintEvidence

    from .test_acquisition_bridge import bridge

    monkeypatch.setenv("AUTPLAY_DATABASE_URL", database_url)
    monkeypatch.setenv("AUTPLAY_AUTH_SIGNING_SECRET", "test-only-auth-" * 4)
    monkeypatch.setenv("AUTPLAY_PUBLIC_ACCESS_SOURCE_HMAC_SECRET", "test-only-source-" * 4)
    monkeypatch.setenv("AUTPLAY_VAULT_ROOT", str(tmp_path / "vault"))
    monkeypatch.setenv("AUTPLAY_VAULT_LOW_DISK_BYTES", "0")
    engine = create_engine(database_url)
    sessions = sessionmaker(engine)
    with sessions.begin() as session:
        identity = session.scalar(
            text(
                "INSERT INTO account.user_account(display_name,role) "
                "VALUES ('Preservation test','OWNER') RETURNING user_id"
            )
        )
    backend = bridge.Backend(identity, provision=True)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", "NONE"))
        output.writeframes(b"\x01\x02" * 16000)
    audio = buf.getvalue()
    digest = hashlib.sha256(audio).hexdigest()
    row = dict(
        next(iter(archive())),
        event="fixture",
        artist="Fixture Artist",
        title="Fixture Song",
        version="original studio master",
        status="active",
    )
    entry = dict(
        artist=row["artist"],
        title=row["title"],
        version=row["version"],
        duration_ms=2000,
        sha256=digest,
        candidate_id="abcdef12345",
        allow_vault=True,
        rights_url="https://example.org/grant",
        expires_at="2099-01-01T00:00:00+00:00",
    )
    (tmp_path / "originals.json").write_text(json.dumps({"fixture": entry}))
    watcher = PreservationWatcher(sessions, identity, tmp_path)

    class Provider:
        def download(self, candidate_id, path):
            path.mkdir(parents=True)
            payload = audio[:-2] + b"xx" if wrong_edition else audio
            (path / "audio.media").write_bytes(payload)
            (path / SOURCE_METADATA_FILE).write_text(
                json.dumps(
                    dict(
                        schema_version=1,
                        provider="YOUTUBE",
                        source_id=candidate_id,
                        fields=dict(
                            title=row["title"], artist=row["artist"], album="Fixture Album"
                        ),
                        external_ids={},
                        artwork=[],
                    )
                )
            )
            return path / "audio.media"

    class Media:
        def inspect(self, path):
            assert path.read_bytes() == audio
            return AudioTechnicalMetadata("pcm_s16le", "wav", 8000, 1, 2000, None, 16)

    class Fingerprints:
        def fingerprint(self, path):
            return ChromaprintEvidence("chromaprint", "1.6.1", 2000, b"fixture")

    uow = SqlAlchemyJobUnitOfWorkFactory(sessions)
    ingest = VaultIngestHandler(
        repository=TransactionalIngestRepository(SqlAlchemyVaultUnitOfWorkFactory(sessions)),
        storage=FilesystemVaultStorage(tmp_path / "vault"),
        media=Media(),
        fingerprints=Fingerprints(),
    )
    imports = JobWorker(
        uow_factory=uow,
        worker_id="fixture-imports",
        registry=JobHandlerRegistry(
            {
                JobKey("library.import", 1): ImportJobHandler(sessions),
                JobKey("vault.ingest", 1): ingest,
            }
        ),
    )

    class WorkerLoop:
        def is_set(self):
            return False

        def wait(self, seconds):
            imports.run_once()

    publisher = bridge.PlaylistCatalogPublisher(backend, tmp_path / "audio", WorkerLoop())
    publisher.provider = Provider()
    originals = JobWorker(
        uow_factory=SqlAlchemyJobUnitOfWorkFactory(sessions, claim_owner=identity),
        worker_id="fixture-originals",
        registry=JobHandlerRegistry(
            {PRESERVE: lambda context, lease: watcher.acquire(context, lease, publisher)}
        ),
    )
    try:
        # The same path accepts a track directly or as a member of a withdrawn album.
        album = {
            **row,
            "kind": "album",
            "event": "fixture-album",
            "title": "Withdrawn album",
            "tracks": [{key: row[key] for key in ("event", "artist", "title", "version")}],
        }
        source = [("fixture", lambda: [row if wrong_edition else album])]
        watcher.tick(source)
        with sessions.begin() as session:
            foreign = session.scalar(
                text(
                    "INSERT INTO account.user_account(display_name,role) "
                    "VALUES ('Other bridge','OWNER') RETURNING user_id"
                )
            )
            session.execute(
                text(
                    "INSERT INTO jobs.job(job_type,schema_version,user_id,priority) "
                    "VALUES (:kind,1,:owner,0)"
                ),
                {"kind": PRESERVE.job_type, "owner": foreign},
            )
        originals.run_once()
        with sessions.begin() as session:
            job = session.execute(
                text(
                    "SELECT state,error_code FROM jobs.job WHERE job_type=:kind AND user_id=:owner"
                ),
                {"kind": PRESERVE.job_type, "owner": identity},
            ).one()
            assert job == (
                ("FAILED", "preservation.original_mismatch")
                if wrong_edition
                else ("COMPLETED", None)
            )
            assert original_in_vault(session, digest) == (not wrong_edition)
            assert reports(session, identity)[0]["counts"].get("downloaded", 0) == int(
                not wrong_edition
            )
            if not wrong_edition:
                request_run(session, identity, uuid4())
        watcher.tick(source)
        with sessions() as session:
            assert (
                session.scalar(
                    text("SELECT count(*) FROM jobs.job WHERE job_type=:kind AND user_id=:owner"),
                    {"kind": PRESERVE.job_type, "owner": identity},
                )
                == 1
            )
        if not wrong_edition:
            with sessions.begin() as session:
                request_run(session, identity, uuid4())
            album["tracks"] = []
            watcher.tick(source)
            with sessions() as session:
                latest = reports(session, identity)[0]
                assert latest["counts"] == {"new": 0, "historical": 2}
    finally:
        backend.close()
        engine.dispose()


def test_owner_manual_scan_and_report(database_url, tmp_path):
    from autplay.application.acquisition_control import AcquisitionControlService
    from autplay.domain.auth import AccountRole
    from autplay.domain.web_admin import AuthenticatedWebSession, WebActor
    from autplay.entrypoints.acquisition_control_http import create_acquisition_control_router
    from autplay.runtime.web_security import encode_request_integrity_token
    from autplay.web.renderer import AdminTemplateRenderer
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    engine = create_engine(database_url)
    sessions = sessionmaker(engine)
    with sessions.begin() as session:
        owner = session.scalar(
            text(
                "INSERT INTO account.user_account(display_name,role) "
                "VALUES ('Test operator','OWNER') RETURNING user_id"
            )
        )
    actor = WebActor(uuid4(), owner, uuid4(), AccountRole.OWNER, 0)
    token = b"x" * 32

    class Web:
        def authenticate_safe_get(self, bearer):
            return AuthenticatedWebSession(actor, token)

        def authenticate(self, bearer, *, mutation):
            assert mutation
            return AuthenticatedWebSession(actor, token)

        def validate_csrf(self, current, csrf, operation):
            assert current == actor and csrf == token

    app = FastAPI()
    origin = "http://127.0.0.1:8787"
    app.include_router(
        create_acquisition_control_router(
            web=Web(),
            renderer=AdminTemplateRenderer(),
            acquisition=AcquisitionControlService(tmp_path / "spool"),
            origin=origin,
            preservation_sessions=sessions,
        )
    )
    try:
        with TestClient(app, base_url=origin) as client:
            data = dict(csrf_token=encode_request_integrity_token(token), operation_id=str(uuid4()))
            for _ in range(2):
                assert (
                    client.post(
                        "/admin/acquisition/preservation/scan",
                        data=data,
                        headers={"origin": origin},
                        follow_redirects=False,
                    ).status_code
                    == 303
                )
            PreservationWatcher(sessions, owner, tmp_path).tick([("archive", archive)])
            page = client.get("/admin/acquisition?lang=ru")
            assert page.status_code == 200
            assert (
                "Music Preservation Watcher" in page.text and "Fairytale of New York" in page.text
            )
            assert "Обнаружено" in page.text and "Запустить проверку" in page.text
    finally:
        engine.dispose()
