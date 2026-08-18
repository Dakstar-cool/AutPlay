"""Run the disposable P14 PostgreSQL/Vault backup and large-catalog drill.

The command is intentionally local-only. It creates two uniquely named Compose
projects, uses only the loopback test override, restores into a second container,
and removes both projects and their volumes in ``finally`` blocks.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import tempfile
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from alembic import command
from alembic.config import Config
from autplay.adapters.filesystem.vault import FilesystemVaultStorage
from autplay.adapters.postgresql.vault_uow import SqlAlchemyVaultUnitOfWorkFactory
from autplay.application.vault_reconciliation import ReconcileMode, VaultReconciliationService
from autplay.domain.vault import OpaqueStorageKey, Sha256Digest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_BASE = REPOSITORY_ROOT / "deploy" / "compose" / "compose.yaml"
COMPOSE_TEST = REPOSITORY_ROOT / "deploy" / "compose" / "compose.test.yaml"
ALEMBIC_CONFIG = REPOSITORY_ROOT / "server" / "alembic.ini"
EXPECTED_ALEMBIC_HEAD = "0015_wave_runtime"
EXPECTED_POSTGRES_PREFIX = "18.4"
EXPECTED_VECTOR_VERSION = "0.8.6"
CATALOG_ROWS = 100_000
SEARCH_ITERATIONS = 120
SEARCH_P95_TARGET_MS = 300.0


def _run(arguments: Sequence[str]) -> str:
    result = subprocess.run(
        arguments,
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _compose(project: str, *arguments: str) -> str:
    return _run(
        [
            "docker",
            "compose",
            "-p",
            project,
            "-f",
            str(COMPOSE_BASE),
            "-f",
            str(COMPOSE_TEST),
            *arguments,
        ]
    )


def _project_name(role: str) -> str:
    return f"autplay-p14-{role}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def _start(project: str) -> tuple[str, str]:
    _compose(project, "config", "--quiet")
    _compose(project, "up", "--detach", "--wait")
    endpoint = _compose(project, "port", "postgres", "5432").strip()
    host, separator, port_text = endpoint.rpartition(":")
    if separator != ":" or host not in {"127.0.0.1", "[::1]"}:
        raise RuntimeError(f"PostgreSQL was not published on loopback: {endpoint!r}")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise RuntimeError("PostgreSQL published an invalid port")
    dsn = f"postgresql://autplay:autplay_dev_only@127.0.0.1:{port}/autplay"
    sqlalchemy_url = f"postgresql+psycopg://autplay:autplay_dev_only@127.0.0.1:{port}/autplay"
    return dsn, sqlalchemy_url


def _stop(project: str) -> None:
    _compose(project, "down", "--volumes", "--remove-orphans")
    remaining_containers = _run(
        [
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.ID}}",
        ]
    ).strip()
    remaining_volumes = _run(
        [
            "docker",
            "volume",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.Name}}",
        ]
    ).strip()
    remaining_networks = _run(
        [
            "docker",
            "network",
            "ls",
            "--filter",
            f"label=com.docker.compose.project={project}",
            "--format",
            "{{.Name}}",
        ]
    ).strip()
    if remaining_containers:
        raise RuntimeError(f"disposable Compose project still has containers: {project}")
    if remaining_volumes:
        raise RuntimeError(f"disposable Compose project still has volumes: {project}")
    if remaining_networks:
        raise RuntimeError(f"disposable Compose project still has networks: {project}")


def _upgrade(sqlalchemy_url: str) -> None:
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", sqlalchemy_url.replace("%", "%%"))
    previous = os.environ.get("AUTPLAY_DATABASE_URL")
    os.environ["AUTPLAY_DATABASE_URL"] = sqlalchemy_url
    try:
        command.upgrade(config, "head")
    finally:
        if previous is None:
            os.environ.pop("AUTPLAY_DATABASE_URL", None)
        else:
            os.environ["AUTPLAY_DATABASE_URL"] = previous


def _percentile(values: Sequence[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile)))
    return round(ordered[rank], 3)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _seed_source(connection: psycopg.Connection[Any], vault_root: Path) -> dict[str, Any]:
    blob = (b"AutPlay P14 immutable Vault backup fixture\n" * 4096) + b"EOF"
    blob_sha256 = hashlib.sha256(blob).hexdigest()
    storage = FilesystemVaultStorage(vault_root)
    staging_key = OpaqueStorageKey(uuid.uuid4().hex)
    storage.create_staging(staging_key)
    storage.write_chunk(
        staging_key,
        offset=0,
        payload=blob,
        payload_sha256=Sha256Digest(bytes.fromhex(blob_sha256)),
    )
    committed = storage.commit_staging(staging_key, storage.verify_staging(staging_key))
    storage.cleanup_staging(staging_key)
    storage_key = committed.storage_key.value

    with connection.transaction():
        row = connection.execute(
            """
            INSERT INTO vault.vault_object (
                sha256, byte_size, detected_mime_type, commit_status, committed_at,
                last_verified_at
            )
            VALUES (decode(%s, 'hex'), %s, 'audio/flac', 'COMMITTED', now(), now())
            RETURNING vault_object_id
            """,
            (blob_sha256, len(blob)),
        ).fetchone()
        if row is None:
            raise RuntimeError("Vault fixture insert returned no identifier")
        vault_object_id = str(row[0])
        connection.execute(
            """
            INSERT INTO vault.vault_replica (
                vault_object_id, storage_backend, storage_key, replica_status, verified_at
            )
            VALUES (%s, 'LOCAL_FILESYSTEM', %s, 'AVAILABLE', now())
            """,
            (vault_object_id, storage_key),
        )
    return {
        "vault_object_id": vault_object_id,
        "sha256": blob_sha256,
        "byte_size": len(blob),
        "storage_backend": "LOCAL_FILESYSTEM",
        "storage_key": storage_key,
    }


def _reconcile(
    sqlalchemy_url: str,
    vault_root: Path,
    *,
    mode: ReconcileMode,
) -> dict[str, int]:
    engine = create_engine(sqlalchemy_url, pool_pre_ping=True)
    sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
    try:
        with SqlAlchemyVaultUnitOfWorkFactory(sessions)() as unit:
            report = VaultReconciliationService(
                repository=unit.vault,
                storage=FilesystemVaultStorage(vault_root),
            ).run(mode=mode, limit=100)
            unit.commit()
        return {
            "inspected": report.inspected,
            "repaired": report.repaired,
            "quarantined": report.quarantined,
            "remaining": report.remaining,
        }
    finally:
        engine.dispose()


def _benchmark_catalog(connection: psycopg.Connection[Any]) -> dict[str, Any]:
    with connection.transaction():
        credit_id = connection.execute(
            """
            INSERT INTO catalog.artist_credit (display_name, normalized_name)
            VALUES ('P14 Fixture Artist', 'p14 fixture artist')
            RETURNING artist_credit_id
            """
        ).fetchone()
        if credit_id is None:
            raise RuntimeError("catalog benchmark credit insert returned no identifier")
        connection.execute(
            """
            INSERT INTO catalog.recording (
                artist_credit_id, title, normalized_title, recording_kind, identity_status
            )
            SELECT %s,
                   'P14 Track ' || lpad(value::text, 6, '0'),
                   'p14 track ' || lpad(value::text, 6, '0'),
                   'STUDIO',
                   'ACTIVE'
            FROM generate_series(1, %s) AS value
            """,
            (credit_id[0], CATALOG_ROWS),
        )
        connection.execute("ANALYZE catalog.recording")

    durations_ms: list[float] = []
    for iteration in range(SEARCH_ITERATIONS):
        target = ((iteration * 7919) % CATALOG_ROWS) + 1
        needle = f"%track {target:06d}%"
        started = time.perf_counter_ns()
        rows = connection.execute(
            """
            SELECT recording_id
            FROM catalog.recording
            WHERE normalized_title ILIKE %s
              AND identity_status = 'ACTIVE'
              AND deleted_at IS NULL
            ORDER BY recording_id
            LIMIT 50
            """,
            (needle,),
        ).fetchall()
        durations_ms.append((time.perf_counter_ns() - started) / 1_000_000)
        if len(rows) != 1:
            raise RuntimeError(f"large-catalog search returned {len(rows)} rows for {needle!r}")

    p50 = _percentile(durations_ms, 0.50)
    p95 = _percentile(durations_ms, 0.95)
    p99 = _percentile(durations_ms, 0.99)
    if p95 > SEARCH_P95_TARGET_MS:
        raise RuntimeError(f"large-catalog search p95 {p95} ms exceeds {SEARCH_P95_TARGET_MS} ms")
    return {
        "name": "postgresql_catalog_trigram_search",
        "dataset_rows": CATALOG_ROWS,
        "iterations": SEARCH_ITERATIONS,
        "warmup": "first measured query retained conservatively",
        "query_limit": 50,
        "p50_ms": p50,
        "p95_ms": p95,
        "p99_ms": p99,
        "mean_ms": round(statistics.fmean(durations_ms), 3),
        "target_p95_ms": SEARCH_P95_TARGET_MS,
        "status": "PASS",
    }


def _database_state(connection: psycopg.Connection[Any]) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT current_setting('server_version'),
               (SELECT extversion FROM pg_extension WHERE extname = 'vector'),
               (SELECT version_num FROM alembic_version),
               (SELECT count(*) FROM vault.vault_object),
               (SELECT count(*) FROM vault.vault_replica),
               (SELECT count(*) FROM catalog.recording),
               pg_current_wal_lsn()::text
        """
    ).fetchone()
    if row is None:
        raise RuntimeError("database state query returned no row")
    state: dict[str, Any] = {
        "postgresql_version": str(row[0]),
        "pgvector_version": str(row[1]),
        "alembic_head": str(row[2]),
        "vault_object_count": int(row[3]),
        "vault_replica_count": int(row[4]),
        "recording_count": int(row[5]),
        "wal_lsn": str(row[6]),
    }
    if not state["postgresql_version"].startswith(EXPECTED_POSTGRES_PREFIX):
        raise RuntimeError("unexpected PostgreSQL version")
    if state["pgvector_version"] != EXPECTED_VECTOR_VERSION:
        raise RuntimeError("unexpected pgvector version")
    if state["alembic_head"] != EXPECTED_ALEMBIC_HEAD:
        raise RuntimeError("unexpected Alembic head")
    return state


def _dump(project: str, destination: Path) -> None:
    container_id = _compose(project, "ps", "-q", "postgres").strip()
    if not container_id:
        raise RuntimeError("source PostgreSQL container identifier is unavailable")
    with destination.open("wb") as stream:
        result = subprocess.run(
            [
                "docker",
                "exec",
                container_id,
                "pg_dump",
                "-U",
                "autplay",
                "-d",
                "autplay",
                "--format=custom",
                "--no-owner",
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            stdout=stream,
            stderr=subprocess.PIPE,
        )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))


def _restore(project: str, dump_path: Path) -> None:
    container_id = _compose(project, "ps", "-q", "postgres").strip()
    if not container_id:
        raise RuntimeError("restore PostgreSQL container identifier is unavailable")
    with dump_path.open("rb") as stream:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                container_id,
                "pg_restore",
                "-U",
                "autplay",
                "-d",
                "autplay",
                "--exit-on-error",
                "--no-owner",
            ],
            cwd=REPOSITORY_ROOT,
            check=False,
            stdin=stream,
            capture_output=True,
        )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))


def _host_inventory() -> dict[str, Any]:
    cpu = platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown")
    docker_version = _run(["docker", "version", "--format", "{{.Server.Version}}"]).strip()
    return {
        "os": platform.platform(),
        "machine": platform.machine(),
        "cpu": cpu,
        "python": platform.python_version(),
        "docker_engine": docker_version,
        "storage": "Docker Desktop Linux volume plus host temporary NTFS workspace",
    }


def run(output: Path) -> dict[str, Any]:
    output.unlink(missing_ok=True)
    generation_id = str(uuid.uuid4())
    source_project = _project_name("source")
    restore_project = _project_name("restore")
    started_at = datetime.now(UTC)
    source_started = False
    restore_started = False

    with tempfile.TemporaryDirectory(prefix="autplay-p14-") as temporary:
        temporary_root = Path(temporary).resolve()
        source_vault = temporary_root / "source-vault"
        restored_vault = temporary_root / "restored-vault"
        source_vault.mkdir()
        restored_vault.mkdir()
        dump_path = temporary_root / "autplay.dump"
        manifest_path = temporary_root / "manifest.json"

        try:
            source_started = True
            source_dsn, source_sqlalchemy_url = _start(source_project)
            _upgrade(source_sqlalchemy_url)
            with psycopg.connect(source_dsn) as source_connection:
                vault_entry = _seed_source(source_connection, source_vault)
                performance = _benchmark_catalog(source_connection)
                source_state = _database_state(source_connection)
            manifest = {
                "schema_version": 1,
                "generation_id": generation_id,
                "created_at": datetime.now(UTC).isoformat(),
                "backup_mode": "quiesced_disposable_generation",
                "database": source_state,
                "vault_objects": [vault_entry],
                "secrets_included": False,
                "derived_data_policy": (
                    "included for drill; production may rebuild declared derived data"
                ),
            }
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            manifest_sha256 = _sha256(manifest_path)
            _dump(source_project, dump_path)
            dump_sha256 = _sha256(dump_path)

            restore_started = True
            restore_dsn, restore_sqlalchemy_url = _start(restore_project)
            _restore(restore_project, dump_path)
            shutil.copytree(source_vault, restored_vault, dirs_exist_ok=True)
            restored_blob = (
                restored_vault
                / "objects"
                / str(vault_entry["storage_key"])[:2]
                / str(vault_entry["storage_key"])[2:4]
                / str(vault_entry["storage_key"])
            )
            restored_blob_sha256 = _sha256(restored_blob)
            if restored_blob_sha256 != vault_entry["sha256"]:
                raise RuntimeError("restored Vault blob checksum mismatch")
            healthy_reconciliation = _reconcile(
                restore_sqlalchemy_url,
                restored_vault,
                mode=ReconcileMode.APPLY,
            )
            if healthy_reconciliation != {
                "inspected": 1,
                "repaired": 0,
                "quarantined": 0,
                "remaining": 0,
            }:
                raise RuntimeError("healthy restored Vault reconciliation was not clean")
            with psycopg.connect(restore_dsn) as restored_connection:
                restored_state = _database_state(restored_connection)
                restored_row = restored_connection.execute(
                    """
                    SELECT encode(object.sha256, 'hex'), object.byte_size, replica.storage_key,
                           object.commit_status, replica.replica_status
                    FROM vault.vault_object AS object
                    JOIN vault.vault_replica AS replica USING (vault_object_id)
                    WHERE object.vault_object_id = %s
                    """,
                    (vault_entry["vault_object_id"],),
                ).fetchone()
            expected_row = (
                vault_entry["sha256"],
                vault_entry["byte_size"],
                vault_entry["storage_key"],
                "COMMITTED",
                "AVAILABLE",
            )
            if restored_row != expected_row:
                raise RuntimeError("restored database/Vault manifest row mismatch")
            for key in (
                "alembic_head",
                "vault_object_count",
                "vault_replica_count",
                "recording_count",
            ):
                if source_state[key] != restored_state[key]:
                    raise RuntimeError(f"restored database state mismatch for {key}")

            restored_blob.chmod(0o600)
            restored_blob.write_bytes(restored_blob.read_bytes() + b"corruption")
            corruption_reconciliation = _reconcile(
                restore_sqlalchemy_url,
                restored_vault,
                mode=ReconcileMode.APPLY,
            )
            if corruption_reconciliation != {
                "inspected": 1,
                "repaired": 1,
                "quarantined": 1,
                "remaining": 0,
            }:
                raise RuntimeError("corrupt restored Vault reconciliation was not quarantined")
            with psycopg.connect(restore_dsn) as restored_connection:
                corruption_row = restored_connection.execute(
                    """
                    SELECT object.commit_status, replica.replica_status
                    FROM vault.vault_object AS object
                    JOIN vault.vault_replica AS replica USING (vault_object_id)
                    WHERE object.vault_object_id = %s
                    """,
                    (vault_entry["vault_object_id"],),
                ).fetchone()
            if corruption_row != ("QUARANTINED", "QUARANTINED"):
                raise RuntimeError("corrupt restored Vault database state was not quarantined")

            finished_at = datetime.now(UTC)
            duration_seconds = round((finished_at - started_at).total_seconds(), 3)
            report = {
                "schema_version": 1,
                "status": "PASS",
                "generation_id": generation_id,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_seconds": duration_seconds,
                "rpo_seconds_for_quiesced_fixture": 0,
                "rto_target_seconds": 14_400,
                "rto_target_met": duration_seconds <= 14_400,
                "source_project": "unique disposable Compose project (identifier redacted)",
                "restore_project": (
                    "independent unique disposable Compose project (identifier redacted)"
                ),
                "host": _host_inventory(),
                "database_source": source_state,
                "database_restored": restored_state,
                "dump": {"sha256": dump_sha256, "format": "PostgreSQL custom"},
                "manifest": {"sha256": manifest_sha256, **manifest},
                "vault_restore": {
                    "restored_sha256": restored_blob_sha256,
                    "object_count": 1,
                    "database_manifest_consistent": True,
                    "healthy_reconciliation": healthy_reconciliation,
                    "corruption_reconciliation": corruption_reconciliation,
                    "corruption_injection_detected": True,
                    "corruption_state": {
                        "object": corruption_row[0],
                        "replica": corruption_row[1],
                    },
                },
                "performance": performance,
                "cleanup": "verified after report generation by process finalizers",
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return report
        finally:
            failures: list[str] = []
            if restore_started:
                try:
                    _stop(restore_project)
                except Exception as error:  # cleanup must report every scoped failure
                    failures.append(f"restore cleanup: {error}")
            if source_started:
                try:
                    _stop(source_project)
                except Exception as error:  # cleanup must report every scoped failure
                    failures.append(f"source cleanup: {error}")
            if failures:
                output.unlink(missing_ok=True)
                raise RuntimeError("; ".join(failures))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=REPOSITORY_ROOT
        / "docs"
        / "implementation"
        / "evidence"
        / "P14_BACKUP_RESTORE_2026-08-17.json",
    )
    arguments = parser.parse_args()
    report = run(arguments.output.resolve())
    print(
        "P14 backup/restore and 100k search drill PASS: "
        f"duration={report['duration_seconds']}s, "
        f"search_p95={report['performance']['p95_ms']}ms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
