"""Create the private, hash-bound Admin target backup on a mounted NAS.

The command is read-only unless ``--execute`` is supplied.  It deliberately leaves the
standalone acquisition shards alone: they write to their own download roots and are not
PostgreSQL/Vault supervisors.  Production API, stream, metadata, bridge and worker processes
are stopped before the database and Vault snapshots are taken.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

EXPECTED_MIGRATION_HEAD = "0032_track_metadata"
EXPECTED_LIVE_IMAGE_SHA256 = "c6518996ff1b0cdfaa4df24dcabe8a574b3019fefe2c31ebae8bcc08e09adb58"
EXPECTED_CONTAINER_SET_SHA256 = "70b8f44d79f9e29b0d12837aef5c3123ffdb6abd7568bba28dffa5288acdd170"
EXPECTED_CANDIDATE_ARCHIVE_SHA256 = (
    "504cf8310f3f197db11ea856cf4200703215cd366647ccb306bb60baa2194aed"
)
EXPECTED_CANDIDATE_IMAGE_SHA256 = "813f13bd270f96ba719716e4834e59cbccd4f78a5ea8eb23d67037523572c65d"

POSTGRES_CONTAINER = "autplay-production-postgres-1"
CANDIDATE_IMAGE = "autplay-admin-acceptance:504cf8310f3f"
ROLLBACK_IMAGE = "autplay-metadata:20260916-r2"
CANDIDATE_ARCHIVE = Path(
    "/srv/autplay/candidates/admin-target-504cf8310f3f/admin-target-candidate-20260919-r3.tar.gz"
)

# These processes can access the production database or Vault, or accept private traffic.  The
# independent music download shards intentionally are not in this list.
STATEFUL_CONTAINERS = (
    "autplay-acquisition-vault-bridge",
    "autplay-metadata-worker",
    "autplay-music-worker",
    "autplay-production-worker-cpu-1",
    "autplay-production-stream-1",
    "autplay-production-operator",
    "autplay-production-tailnet-admin",
    "autplay-production-tailnet-mobile-edge",
    "autplay-production-mobile-api-1",
    "autplay-production-api-1",
)
CORE_RESTART_ORDER = (
    "autplay-production-api-1",
    "autplay-production-mobile-api-1",
    "autplay-production-stream-1",
    "autplay-production-worker-cpu-1",
)
AUXILIARY_RESTART_ORDER = (
    "autplay-production-operator",
    "autplay-metadata-worker",
    "autplay-music-worker",
    "autplay-acquisition-vault-bridge",
    "autplay-production-tailnet-mobile-edge",
    "autplay-production-tailnet-admin",
)
CORE_HEALTH_CONTAINERS = (
    POSTGRES_CONTAINER,
    "autplay-production-api-1",
    "autplay-production-mobile-api-1",
    "autplay-production-stream-1",
    "autplay-production-worker-cpu-1",
)
VOLUMES = {
    "postgres-volume.tar.gz": "autplay-production_postgres-data",
    "vault-volume.tar.gz": "autplay-production_vault-data",
    "privacy-ledger-volume.tar.gz": "autplay-production_privacy-ledger-data",
    "training-consent-ledger-volume.tar.gz": ("autplay-production_training-consent-ledger-data"),
}
CONFIG_PATHS = (
    "srv/autplay/releases/pa3-8e52fbb",
    "srv/autplay/production/server.env",
    "srv/autplay/production/rendered-closed-compose.json",
    "srv/autplay/production/phone-tailnet-20260911",
    "srv/autplay/production/private-endpoint-20260911.py",
    "srv/autplay/production/metadata-20260916/runtime-specs-r2.json",
    "srv/autplay/production/music-library-20260916/final-runtime-specs.json",
    "srv/autplay/production/sync-vault-20260916/bridge-running-spec.json",
    "srv/autplay/secrets/production",
)
BACKUP_ID = re.compile(r"^admin-target-[0-9]{8}T[0-9]{6}Z$")
DEFAULT_MINIMUM_FREE_BYTES = 128 * 1024**3


class BackupError(RuntimeError):
    """A fail-closed backup precondition or operation failed."""


@dataclass(frozen=True, slots=True)
class MountRecord:
    target: str
    source: str
    filesystem: str
    options: str


@dataclass(frozen=True, slots=True)
class PreflightRecord:
    schema_version: int
    checked_at: str
    destination_root: str
    destination_mount: MountRecord
    root_mount: MountRecord
    available_bytes: int
    minimum_free_bytes: int
    migration_head: str
    live_image_sha256: str
    container_set_sha256: str
    candidate_archive_sha256: str
    candidate_image_sha256: str
    public_edge_active: bool
    core_health: dict[str, str]
    acquisition_shards_untouched: bool
    status: str


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _default_backup_id() -> str:
    return datetime.now(UTC).strftime("admin-target-%Y%m%dT%H%M%SZ")


def _run(
    arguments: Sequence[str],
    *,
    check: bool = True,
    capture_output: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        check=check,
        text=True,
        encoding="utf-8",
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
    )


def _output(*arguments: str) -> str:
    return _run(arguments).stdout.strip()


def _docker(*arguments: str) -> str:
    return _output("docker", *arguments)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _container_set_sha256() -> str:
    identifiers = _docker(
        "ps",
        "--filter",
        "label=com.docker.compose.project=autplay-production",
        "--format",
        "{{.ID}}",
    ).splitlines()
    payload = "".join(f"{identifier}\n" for identifier in sorted(identifiers)).encode()
    return hashlib.sha256(payload).hexdigest()


def _mount_for(path: Path) -> MountRecord:
    document = json.loads(
        _output(
            "findmnt",
            "--json",
            "--target",
            str(path),
            "--output",
            "TARGET,SOURCE,FSTYPE,OPTIONS",
        )
    )
    filesystems = document.get("filesystems", [])
    if len(filesystems) != 1:
        raise BackupError("destination_mount_unavailable")
    row = filesystems[0]
    return MountRecord(
        target=str(row["target"]),
        source=str(row["source"]),
        filesystem=str(row["fstype"]),
        options=str(row["options"]),
    )


def _validate_destination(
    destination_root: Path, minimum_free_bytes: int
) -> tuple[Path, MountRecord, MountRecord, int]:
    if not destination_root.is_absolute():
        raise BackupError("destination_must_be_absolute")
    if destination_root.is_symlink():
        raise BackupError("destination_symlink_refused")
    resolved = destination_root.resolve(strict=True)
    if not resolved.is_dir():
        raise BackupError("destination_must_be_directory")
    if resolved in {Path("/"), Path("/srv"), Path("/srv/autplay")}:
        raise BackupError("destination_too_broad")
    if not os.access(resolved, os.W_OK | os.X_OK):
        raise BackupError("destination_not_writable")
    destination_mount = _mount_for(resolved)
    root_mount = _mount_for(Path("/"))
    if destination_mount.target == root_mount.target:
        raise BackupError("destination_is_not_a_separate_mount")
    if destination_mount.source == root_mount.source:
        raise BackupError("destination_uses_root_filesystem_source")
    available_bytes = shutil.disk_usage(resolved).free
    if available_bytes < minimum_free_bytes:
        raise BackupError("destination_free_space_below_minimum")
    return resolved, destination_mount, root_mount, available_bytes


def _health(container: str) -> str:
    return _docker(
        "inspect",
        container,
        "--format",
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
    )


def preflight(destination_root: Path, minimum_free_bytes: int) -> PreflightRecord:
    for command in ("docker", "findmnt", "tar"):
        if shutil.which(command) is None:
            raise BackupError(f"required_command_missing:{command}")
    resolved, destination_mount, root_mount, available_bytes = _validate_destination(
        destination_root, minimum_free_bytes
    )
    migration_head = _docker(
        "exec",
        POSTGRES_CONTAINER,
        "psql",
        "-U",
        "autplay",
        "-d",
        "autplay",
        "-Atqc",
        "SELECT version_num FROM alembic_version",
    )
    live_image = _docker(
        "inspect", "autplay-production-api-1", "--format", "{{.Image}}"
    ).removeprefix("sha256:")
    container_set = _container_set_sha256()
    candidate_archive = _sha256_file(CANDIDATE_ARCHIVE)
    candidate_image = _docker(
        "image", "inspect", CANDIDATE_IMAGE, "--format", "{{.Id}}"
    ).removeprefix("sha256:")
    values = {
        "migration_head": (migration_head, EXPECTED_MIGRATION_HEAD),
        "live_image": (live_image, EXPECTED_LIVE_IMAGE_SHA256),
        "container_set": (container_set, EXPECTED_CONTAINER_SET_SHA256),
        "candidate_archive": (
            candidate_archive,
            EXPECTED_CANDIDATE_ARCHIVE_SHA256,
        ),
        "candidate_image": (candidate_image, EXPECTED_CANDIDATE_IMAGE_SHA256),
    }
    for name, (actual, expected) in values.items():
        if actual != expected:
            raise BackupError(f"baseline_mismatch:{name}")
    running = set(_docker("ps", "--format", "{{.Names}}").splitlines())
    public_edge_active = "autplay-production-edge-1" in running
    if public_edge_active:
        raise BackupError("public_edge_must_remain_inactive")
    core_health = {container: _health(container) for container in CORE_HEALTH_CONTAINERS}
    unhealthy = sorted(name for name, health in core_health.items() if health != "healthy")
    if unhealthy:
        raise BackupError("core_services_unhealthy:" + ",".join(unhealthy))
    return PreflightRecord(
        schema_version=1,
        checked_at=_utc_now(),
        destination_root=str(resolved),
        destination_mount=destination_mount,
        root_mount=root_mount,
        available_bytes=available_bytes,
        minimum_free_bytes=minimum_free_bytes,
        migration_head=migration_head,
        live_image_sha256=live_image,
        container_set_sha256=container_set,
        candidate_archive_sha256=candidate_archive,
        candidate_image_sha256=candidate_image,
        public_edge_active=False,
        core_health=core_health,
        acquisition_shards_untouched=True,
        status="PASS",
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _run_to_file(arguments: Sequence[str], destination: Path) -> None:
    with destination.open("wb") as handle:
        completed = subprocess.run(list(arguments), check=False, stdout=handle)
    if completed.returncode != 0:
        raise BackupError(f"command_failed:{arguments[0]}:{completed.returncode}")


def _archive_volume(volume: str, destination: Path) -> None:
    _run_to_file(
        (
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--user",
            "0:0",
            "--volume",
            f"{volume}:/source:ro",
            "--entrypoint",
            "tar",
            CANDIDATE_IMAGE,
            "-C",
            "/source",
            "-czf",
            "-",
            ".",
        ),
        destination,
    )


def _save_image(destination: Path) -> None:
    process = subprocess.Popen(["docker", "image", "save", ROLLBACK_IMAGE], stdout=subprocess.PIPE)
    if process.stdout is None:
        raise BackupError("rollback_image_stream_unavailable")
    try:
        with (
            destination.open("wb") as raw,
            gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=1, mtime=0) as packed,
        ):
            shutil.copyfileobj(process.stdout, packed, length=1024 * 1024)
    finally:
        process.stdout.close()
    if process.wait() != 0:
        raise BackupError("rollback_image_save_failed")


def _running_names() -> set[str]:
    return set(_docker("ps", "--format", "{{.Names}}").splitlines())


def _wait_for_healthy(container: str, timeout_seconds: int = 120) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if _health(container) == "healthy":
            return
        time.sleep(2)
    raise BackupError(f"container_health_timeout:{container}")


def _restart_original_runtime(stopped: Sequence[str], postgres_was_running: bool) -> None:
    stopped_set = set(stopped)
    if postgres_was_running:
        _run(("docker", "start", POSTGRES_CONTAINER), capture_output=True)
        _wait_for_healthy(POSTGRES_CONTAINER)
    for container in CORE_RESTART_ORDER:
        if container in stopped_set:
            _run(("docker", "start", container), capture_output=True)
    for container in CORE_RESTART_ORDER:
        if container in stopped_set:
            _wait_for_healthy(container)
    for container in AUXILIARY_RESTART_ORDER:
        if container in stopped_set:
            _run(("docker", "start", container), capture_output=True)

    expected = stopped_set | ({POSTGRES_CONTAINER} if postgres_was_running else set())
    missing = sorted(expected - _running_names())
    if missing:
        raise BackupError("runtime_restart_incomplete:" + ",".join(missing))


def _sync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())


def _write_hash_manifest(directory: Path) -> str:
    excluded = {"SHA256SUMS", "BACKUP_SHA256", "COMPLETED.json"}
    records: list[tuple[Path, str]] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.name not in excluded:
            _sync_file(path)
            records.append((path, _sha256_file(path)))
    payload = "".join(f"{digest}  {path.name}\n" for path, digest in records).encode("ascii")
    (directory / "SHA256SUMS").write_bytes(payload)
    _sync_file(directory / "SHA256SUMS")
    digest = hashlib.sha256(payload).hexdigest()
    (directory / "BACKUP_SHA256").write_text(digest + "\n", encoding="ascii")
    _sync_file(directory / "BACKUP_SHA256")
    if (directory / "SHA256SUMS").read_bytes() != payload:
        raise BackupError("hash_manifest_readback_mismatch")
    for path, expected in records:
        if _sha256_file(path) != expected:
            raise BackupError(f"backup_file_readback_mismatch:{path.name}")
    return digest


def execute_backup(
    destination_root: Path,
    backup_id: str,
    minimum_free_bytes: int,
    *,
    leave_stopped: bool,
) -> Path:
    if BACKUP_ID.fullmatch(backup_id) is None:
        raise BackupError("backup_id_invalid")
    preflight_record = preflight(destination_root, minimum_free_bytes)
    previous_umask = os.umask(0o077)
    try:
        return _execute_backup_after_preflight(
            preflight_record,
            backup_id,
            leave_stopped=leave_stopped,
        )
    finally:
        os.umask(previous_umask)


def _execute_backup_after_preflight(
    preflight_record: PreflightRecord,
    backup_id: str,
    *,
    leave_stopped: bool,
) -> Path:
    root = Path(preflight_record.destination_root)
    incomplete = root / f".{backup_id}.incomplete"
    completed = root / backup_id
    if incomplete.exists() or completed.exists():
        raise BackupError("backup_target_already_exists")
    incomplete.mkdir(mode=0o700)
    _write_json(incomplete / "PREFLIGHT.json", asdict(preflight_record))

    running_before = _running_names()
    postgres_was_running = POSTGRES_CONTAINER in running_before
    stopped = [name for name in STATEFUL_CONTAINERS if name in running_before]
    if not postgres_was_running:
        raise BackupError("production_postgres_not_running")
    inspect = json.loads(_docker("inspect", *sorted(running_before)))
    _write_json(incomplete / "RUNNING_CONTAINERS_INSPECT.json", inspect)
    (incomplete / "RUNNING_CONTAINERS.txt").write_text(
        "".join(f"{name}\n" for name in sorted(running_before)), encoding="utf-8"
    )

    backup_complete = False
    runtime_mutated = False
    try:
        runtime_mutated = True
        for container in stopped:
            _run(("docker", "stop", "--time", "45", container))
        still_running = sorted(set(stopped) & _running_names())
        if still_running:
            raise BackupError("stateful_containers_still_running:" + ",".join(still_running))

        _run_to_file(
            (
                "docker",
                "exec",
                POSTGRES_CONTAINER,
                "pg_dump",
                "-U",
                "autplay",
                "-d",
                "autplay",
                "-Fc",
            ),
            incomplete / "postgres.dump",
        )
        _run_to_file(
            (
                "docker",
                "exec",
                POSTGRES_CONTAINER,
                "pg_dumpall",
                "-U",
                "autplay",
                "--globals-only",
            ),
            incomplete / "postgres-globals.sql",
        )
        if (incomplete / "postgres.dump").stat().st_size <= 10_000:
            raise BackupError("postgres_dump_too_small")
        _run(("docker", "stop", "--time", "30", POSTGRES_CONTAINER))
        if {POSTGRES_CONTAINER, *stopped} & _running_names():
            raise BackupError("production_stateful_process_remained_running")

        for filename, volume in VOLUMES.items():
            exists = _run(("docker", "volume", "inspect", volume), check=False).returncode == 0
            if filename in {"postgres-volume.tar.gz", "vault-volume.tar.gz"} and not exists:
                raise BackupError(f"required_volume_missing:{volume}")
            if exists:
                _archive_volume(volume, incomplete / filename)

        _run(
            (
                "tar",
                "-C",
                "/",
                "-czf",
                str(incomplete / "config-and-secrets.tar.gz"),
                *CONFIG_PATHS,
            )
        )
        _save_image(incomplete / "autplay-metadata-20260916-r2.docker.tar.gz")
        _write_json(
            incomplete / "BACKUP_MANIFEST.json",
            {
                "schema_version": 1,
                "backup_id": backup_id,
                "created_at": _utc_now(),
                "source_migration_head": EXPECTED_MIGRATION_HEAD,
                "source_server_build_sha256": EXPECTED_LIVE_IMAGE_SHA256,
                "candidate_archive_sha256": EXPECTED_CANDIDATE_ARCHIVE_SHA256,
                "candidate_image_sha256": EXPECTED_CANDIDATE_IMAGE_SHA256,
                "acquisition_shards_stopped": False,
                "production_direct_targeted": True,
                "restore_rehearsal_completed": False,
            },
        )
        backup_sha256 = _write_hash_manifest(incomplete)
        _write_json(
            incomplete / "COMPLETED.json",
            {
                "schema_version": 1,
                "status": "PASS",
                "backup_sha256": backup_sha256,
                "completed_at": _utc_now(),
            },
        )
        _sync_file(incomplete / "COMPLETED.json")
        os.replace(incomplete, completed)
        backup_complete = True
        return completed
    finally:
        if runtime_mutated and (not leave_stopped or not backup_complete):
            _restart_original_runtime(stopped, postgres_was_running)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preflight or create the hash-bound Admin target backup on a mounted NAS."
    )
    parser.add_argument("--destination-root", required=True, type=Path)
    parser.add_argument("--backup-id", default=_default_backup_id())
    parser.add_argument("--minimum-free-bytes", type=int, default=DEFAULT_MINIMUM_FREE_BYTES)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the stop and backup; without this flag the command is read-only",
    )
    parser.add_argument(
        "--leave-stopped",
        action="store_true",
        help="after a successful backup, keep production stopped for Gate D/deployment",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(argv)
    if options.minimum_free_bytes <= 0:
        raise BackupError("minimum_free_bytes_must_be_positive")
    if options.leave_stopped and not options.execute:
        raise BackupError("leave_stopped_requires_execute")
    if options.execute:
        path = execute_backup(
            options.destination_root,
            options.backup_id,
            options.minimum_free_bytes,
            leave_stopped=options.leave_stopped,
        )
        print(json.dumps({"status": "PASS", "backup": str(path)}, sort_keys=True))
    else:
        record = preflight(options.destination_root, options.minimum_free_bytes)
        print(json.dumps(asdict(record), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BackupError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}), file=sys.stderr)
        raise SystemExit(1) from error
