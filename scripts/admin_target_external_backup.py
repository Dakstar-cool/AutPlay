"""Create the Admin target pre-deployment backup on a Windows external drive.

The target host is read through OpenSSH and every large artifact is streamed directly to
the external drive.  No database, Vault, secret archive, or rollback image is staged on
the workstation system drive or on the target server.  The command is read-only unless
``--execute`` is supplied.  On failure it makes a best-effort attempt to restore exactly
the containers that were running before the backup.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

baseline = importlib.import_module(
    "scripts.admin_target_nas_backup" if __package__ else "admin_target_nas_backup"
)

DEFAULT_SSH_TARGET = "user@100.111.141.119"
DEFAULT_MINIMUM_FREE_BYTES = baseline.DEFAULT_MINIMUM_FREE_BYTES
BACKUP_ID = baseline.BACKUP_ID
STREAM_BLOCK_BYTES = 4 * 1024 * 1024
PROGRESS_INTERVAL_BYTES = 256 * 1024**2


class ExternalBackupError(RuntimeError):
    """A fail-closed external backup precondition or operation failed."""


@dataclass(frozen=True, slots=True)
class BackupBaseline:
    schema_version: int
    migration_head: str
    live_image_sha256: str
    container_set_sha256: str
    candidate_archive: str
    candidate_archive_sha256: str
    candidate_image: str
    candidate_image_sha256: str
    rollback_image: str


@dataclass(frozen=True, slots=True)
class ExternalVolumeRecord:
    drive_letter: str
    disk_number: int
    bus_type: str
    filesystem: str
    is_boot: bool
    is_system: bool
    size_bytes: int
    available_bytes: int


@dataclass(frozen=True, slots=True)
class RemotePreflightRecord:
    schema_version: int
    checked_at: str
    ssh_target: str
    migration_head: str
    live_image_sha256: str
    container_set_sha256: str
    candidate_archive_sha256: str
    candidate_image_sha256: str
    public_edge_active: bool
    core_health: dict[str, str]
    status: str


@dataclass(frozen=True, slots=True)
class ExternalPreflightRecord:
    schema_version: int
    checked_at: str
    destination_root: str
    destination_volume: ExternalVolumeRecord
    minimum_free_bytes: int
    remote: RemotePreflightRecord
    status: str


class TransferBudget:
    """Hard generation cap with a one-shot warning and bounded progress callbacks."""

    def __init__(
        self,
        max_bytes: int,
        warning_percent: int,
        initial_bytes: int,
        callback: Callable[[int, bool], None] | None = None,
    ) -> None:
        if not 1024**3 <= max_bytes <= 1024**5:
            raise ExternalBackupError("max_backup_bytes_invalid")
        if not 50 <= warning_percent <= 99:
            raise ExternalBackupError("warning_percent_invalid")
        if not 0 <= initial_bytes <= max_bytes:
            raise ExternalBackupError("backup_size_limit_exceeded")
        self.max_bytes = max_bytes
        self.warning_percent = warning_percent
        self.total_bytes = initial_bytes
        self._callback = callback
        self._warned = initial_bytes * 100 >= max_bytes * warning_percent
        self._last_reported = initial_bytes

    @property
    def warned(self) -> bool:
        return self._warned

    def accept(self, size: int) -> None:
        if size < 0 or self.total_bytes + size > self.max_bytes:
            raise ExternalBackupError("backup_size_limit_exceeded")
        self.total_bytes += size
        warned_now = False
        if not self._warned and self.total_bytes * 100 >= self.max_bytes * self.warning_percent:
            self._warned = True
            warned_now = True
            print(
                json.dumps(
                    {
                        "status": "ALERT",
                        "code": "backup_limit_approaching",
                        "bytes_written": self.total_bytes,
                        "max_backup_bytes": self.max_bytes,
                        "warning_percent": self.warning_percent,
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
                flush=True,
            )
        if warned_now or self.total_bytes - self._last_reported >= PROGRESS_INTERVAL_BYTES:
            self.report()

    def report(self) -> None:
        self._last_reported = self.total_bytes
        if self._callback is not None:
            self._callback(self.total_bytes, self._warned)


class RemoteControlReporter:
    """Read one Admin Web request and atomically publish sanitized agent status."""

    def __init__(
        self,
        ssh_target: str,
        root: str,
        target_id: str,
        backup_id: str,
        helper_image: str | None = None,
    ) -> None:
        control_root = PurePosixPath(root)
        if not control_root.is_absolute() or control_root == PurePosixPath("/"):
            raise ExternalBackupError("remote_control_root_invalid")
        if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?", target_id) is None:
            raise ExternalBackupError("target_id_invalid")
        if (
            helper_image is not None
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}", helper_image) is None
        ):
            raise ExternalBackupError("remote_control_helper_image_invalid")
        self.ssh_target = ssh_target
        self.root = str(control_root)
        self.target_id = target_id
        self.backup_id = backup_id
        self.helper_image = helper_image

    def _read_text(self, path: str) -> str:
        arguments: tuple[str, ...] = ("cat", path)
        if self.helper_image is not None:
            arguments = (
                "docker",
                "run",
                "--rm",
                "--log-driver",
                "none",
                "--network",
                "none",
                "--user",
                "999:999",
                "--volume",
                f"{self.root}:{self.root}:rw",
                "--entrypoint",
                "cat",
                self.helper_image,
                path,
            )
        return _remote_output(self.ssh_target, shlex.join(arguments))

    def requested_policy(self) -> tuple[int, int]:
        request_path = str(PurePosixPath(self.root) / "request.json")
        try:
            value = json.loads(self._read_text(request_path))
            if not isinstance(value, dict) or value.get("target_id") != self.target_id:
                raise ValueError("target mismatch")
            maximum = value["max_backup_bytes"]
            warning = value["warning_percent"]
            if (
                not isinstance(maximum, int)
                or isinstance(maximum, bool)
                or not isinstance(warning, int)
                or isinstance(warning, bool)
            ):
                raise ValueError("invalid policy values")
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ExternalBackupError("backup_request_invalid") from error
        return maximum, warning

    def request_is_pending(self) -> bool:
        status_path = str(PurePosixPath(self.root) / "status.json")
        try:
            value = json.loads(self._read_text(status_path))
        except subprocess.CalledProcessError as error:
            if "No such file or directory" in (error.stderr or ""):
                return False
            raise ExternalBackupError("backup_status_unavailable") from error
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ExternalBackupError("backup_status_invalid") from error
        if not isinstance(value, dict):
            raise ExternalBackupError("backup_status_invalid")
        state = value.get("state")
        target_id = value.get("target_id")
        if state == "REQUESTED":
            if target_id != self.target_id:
                raise ExternalBackupError("backup_status_target_mismatch")
            return True
        if state not in {
            "IDLE",
            "RUNNING",
            "COMPLETED",
            "FAILED",
            "LIMIT_EXCEEDED",
        }:
            raise ExternalBackupError("backup_status_invalid")
        return False

    def update(
        self,
        state: str,
        *,
        bytes_written: int,
        available_bytes: int,
        max_backup_bytes: int,
        warning_percent: int,
        message_code: str | None,
    ) -> None:
        payload = {
            "schema_version": 1,
            "state": state,
            "target_id": self.target_id,
            "backup_id": self.backup_id,
            "bytes_written": bytes_written,
            "available_bytes": max(0, available_bytes),
            "max_backup_bytes": max_backup_bytes,
            "warning_percent": warning_percent,
            "message_code": message_code,
            "updated_at": _utc_now(),
        }
        encoded = base64.b64encode(
            (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        ).decode("ascii")
        root = shlex.quote(self.root)
        status = shlex.quote(str(PurePosixPath(self.root) / "status.json"))
        temporary = shlex.quote(str(PurePosixPath(self.root) / f".status.{self.backup_id}.tmp"))
        write_script = (
            f"umask 077; mkdir -p {root}; printf %s {shlex.quote(encoded)} "
            f"| base64 -d > {temporary}; mv -f {temporary} {status}"
        )
        command = write_script
        if self.helper_image is not None:
            command = shlex.join(
                (
                    "docker",
                    "run",
                    "--rm",
                    "--log-driver",
                    "none",
                    "--network",
                    "none",
                    "--user",
                    "999:999",
                    "--volume",
                    f"{self.root}:{self.root}:rw",
                    "--entrypoint",
                    "sh",
                    self.helper_image,
                    "-c",
                    write_script,
                )
            )
        _remote_run(self.ssh_target, command)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _default_baseline() -> BackupBaseline:
    return BackupBaseline(
        schema_version=1,
        migration_head=baseline.EXPECTED_MIGRATION_HEAD,
        live_image_sha256=baseline.EXPECTED_LIVE_IMAGE_SHA256,
        container_set_sha256=baseline.EXPECTED_CONTAINER_SET_SHA256,
        candidate_archive=baseline.CANDIDATE_ARCHIVE.as_posix(),
        candidate_archive_sha256=baseline.EXPECTED_CANDIDATE_ARCHIVE_SHA256,
        candidate_image=baseline.CANDIDATE_IMAGE,
        candidate_image_sha256=baseline.EXPECTED_CANDIDATE_IMAGE_SHA256,
        rollback_image=baseline.ROLLBACK_IMAGE,
    )


def _load_baseline(path: Path) -> BackupBaseline:
    try:
        raw = path.read_bytes()
        if len(raw) > 65_536:
            raise ValueError("baseline file too large")
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != set(BackupBaseline.__annotations__):
            raise ValueError("baseline fields are invalid")
        record = BackupBaseline(**value)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ExternalBackupError("backup_baseline_invalid") from error
    if record.schema_version != 1:
        raise ExternalBackupError("backup_baseline_invalid")
    for digest in (
        record.live_image_sha256,
        record.container_set_sha256,
        record.candidate_archive_sha256,
        record.candidate_image_sha256,
    ):
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ExternalBackupError("backup_baseline_invalid")
    candidate_path = PurePosixPath(record.candidate_archive)
    if not candidate_path.is_absolute() or candidate_path == PurePosixPath("/"):
        raise ExternalBackupError("backup_baseline_invalid")
    image_pattern = r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,255}"
    if any(
        re.fullmatch(image_pattern, image) is None
        for image in (record.candidate_image, record.rollback_image)
    ):
        raise ExternalBackupError("backup_baseline_invalid")
    if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z_.-]{0,127}", record.migration_head) is None:
        raise ExternalBackupError("backup_baseline_invalid")
    return record


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


def _powershell_json(script: str) -> object:
    result = _run(
        (
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        )
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ExternalBackupError("external_volume_inventory_invalid") from error


def _external_volume(path: Path) -> ExternalVolumeRecord:
    drive = path.drive.rstrip(":").upper()
    if not re.fullmatch(r"[A-Z]", drive):
        raise ExternalBackupError("destination_requires_windows_drive_letter")
    drive_literal = drive.replace("'", "''")
    document = _powershell_json(
        "$ErrorActionPreference='Stop';"
        f"$partition=Get-Partition -DriveLetter '{drive_literal}';"
        "$disk=$partition|Get-Disk;"
        f"$volume=Get-Volume -DriveLetter '{drive_literal}';"
        "[pscustomobject]@{"
        f"DriveLetter='{drive_literal}';"
        "DiskNumber=[int]$disk.Number;"
        "BusType=[string]$disk.BusType;"
        "FileSystem=[string]$volume.FileSystem;"
        "IsBoot=[bool]$disk.IsBoot;"
        "IsSystem=[bool]$disk.IsSystem;"
        "SizeBytes=[int64]$disk.Size;"
        "AvailableBytes=[int64]$volume.SizeRemaining"
        "}|ConvertTo-Json -Compress"
    )
    if not isinstance(document, dict):
        raise ExternalBackupError("external_volume_inventory_invalid")
    record = ExternalVolumeRecord(
        drive_letter=str(document["DriveLetter"]),
        disk_number=int(document["DiskNumber"]),
        bus_type=str(document["BusType"]),
        filesystem=str(document["FileSystem"]),
        is_boot=bool(document["IsBoot"]),
        is_system=bool(document["IsSystem"]),
        size_bytes=int(document["SizeBytes"]),
        available_bytes=int(document["AvailableBytes"]),
    )
    if record.is_boot or record.is_system:
        raise ExternalBackupError("destination_is_system_or_boot_disk")
    if record.bus_type.upper() != "USB":
        raise ExternalBackupError("destination_is_not_usb_external_disk")
    if record.filesystem.upper() != "NTFS":
        raise ExternalBackupError("destination_requires_ntfs")
    return record


def _validate_destination(
    destination_root: Path, minimum_free_bytes: int
) -> tuple[Path, ExternalVolumeRecord]:
    if os.name != "nt":
        raise ExternalBackupError("external_backup_requires_windows")
    if not destination_root.is_absolute():
        raise ExternalBackupError("destination_must_be_absolute")
    destination_root.mkdir(parents=True, exist_ok=True)
    if destination_root.is_symlink():
        raise ExternalBackupError("destination_symlink_refused")
    resolved = destination_root.resolve(strict=True)
    if not resolved.is_dir():
        raise ExternalBackupError("destination_must_be_directory")
    system_drive = os.environ.get("SYSTEMDRIVE", "C:").rstrip("\\/").upper()
    if resolved.drive.upper() == system_drive:
        raise ExternalBackupError("destination_is_system_drive")
    volume = _external_volume(resolved)
    available = shutil.disk_usage(resolved).free
    if min(available, volume.available_bytes) < minimum_free_bytes:
        raise ExternalBackupError("destination_free_space_below_minimum")
    probe = resolved / ".autplay-backup-write-probe"
    try:
        probe.write_bytes(b"autplay\n")
        with probe.open("rb+") as handle:
            os.fsync(handle.fileno())
        if probe.read_bytes() != b"autplay\n":
            raise ExternalBackupError("destination_readback_failed")
    finally:
        probe.unlink(missing_ok=True)
    return resolved, ExternalVolumeRecord(
        drive_letter=volume.drive_letter,
        disk_number=volume.disk_number,
        bus_type=volume.bus_type,
        filesystem=volume.filesystem,
        is_boot=volume.is_boot,
        is_system=volume.is_system,
        size_bytes=volume.size_bytes,
        available_bytes=available,
    )


def _ssh_arguments(ssh_target: str, remote_command: str) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=15",
        ssh_target,
        remote_command,
    ]


def _remote_shell(script: str) -> str:
    return "bash -o pipefail -c " + shlex.quote(script)


def _remote_run(
    ssh_target: str,
    script: str,
    *,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _run(
        _ssh_arguments(ssh_target, _remote_shell(script)),
        check=check,
    )


def _remote_output(ssh_target: str, script: str) -> str:
    return _remote_run(ssh_target, script).stdout.strip()


def _remote_docker(ssh_target: str, *arguments: str) -> str:
    return _remote_output(ssh_target, shlex.join(("docker", *arguments)))


def _remote_health(ssh_target: str, container: str) -> str:
    return _remote_docker(
        ssh_target,
        "inspect",
        container,
        "--format",
        "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
    )


def _remote_container_set_sha256(ssh_target: str) -> str:
    identifiers = _remote_docker(
        ssh_target,
        "ps",
        "--filter",
        "label=com.docker.compose.project=autplay-production",
        "--format",
        "{{.ID}}",
    ).splitlines()
    payload = "".join(f"{identifier}\n" for identifier in sorted(identifiers)).encode()
    return hashlib.sha256(payload).hexdigest()


def _remote_running_names(ssh_target: str) -> set[str]:
    return set(_remote_docker(ssh_target, "ps", "--format", "{{.Names}}").splitlines())


def _remote_preflight(ssh_target: str, target_baseline: BackupBaseline) -> RemotePreflightRecord:
    migration_head = _remote_docker(
        ssh_target,
        "exec",
        baseline.POSTGRES_CONTAINER,
        "psql",
        "-U",
        "autplay",
        "-d",
        "autplay",
        "-Atqc",
        "SELECT version_num FROM alembic_version",
    )
    live_image = _remote_docker(
        ssh_target,
        "inspect",
        "autplay-production-api-1",
        "--format",
        "{{.Image}}",
    ).removeprefix("sha256:")
    container_set = _remote_container_set_sha256(ssh_target)
    candidate_archive = _remote_output(
        ssh_target,
        shlex.join(("sha256sum", target_baseline.candidate_archive)) + " | cut -d' ' -f1",
    )
    candidate_image = _remote_docker(
        ssh_target,
        "image",
        "inspect",
        target_baseline.candidate_image,
        "--format",
        "{{.Id}}",
    ).removeprefix("sha256:")
    values = {
        "migration_head": (migration_head, target_baseline.migration_head),
        "live_image": (live_image, target_baseline.live_image_sha256),
        "container_set": (container_set, target_baseline.container_set_sha256),
        "candidate_archive": (
            candidate_archive,
            target_baseline.candidate_archive_sha256,
        ),
        "candidate_image": (
            candidate_image,
            target_baseline.candidate_image_sha256,
        ),
    }
    for name, (actual, expected) in values.items():
        if actual != expected:
            raise ExternalBackupError(f"baseline_mismatch:{name}")
    running = _remote_running_names(ssh_target)
    public_edge_active = "autplay-production-edge-1" in running
    if public_edge_active:
        raise ExternalBackupError("public_edge_must_remain_inactive")
    core_health = {
        container: _remote_health(ssh_target, container)
        for container in baseline.CORE_HEALTH_CONTAINERS
    }
    unhealthy = sorted(name for name, health in core_health.items() if health != "healthy")
    if unhealthy:
        raise ExternalBackupError("core_services_unhealthy:" + ",".join(unhealthy))
    return RemotePreflightRecord(
        schema_version=1,
        checked_at=_utc_now(),
        ssh_target=ssh_target,
        migration_head=migration_head,
        live_image_sha256=live_image,
        container_set_sha256=container_set,
        candidate_archive_sha256=candidate_archive,
        candidate_image_sha256=candidate_image,
        public_edge_active=False,
        core_health=core_health,
        status="PASS",
    )


def preflight(
    destination_root: Path,
    ssh_target: str,
    minimum_free_bytes: int,
    target_baseline: BackupBaseline | None = None,
) -> ExternalPreflightRecord:
    for command in ("ssh", "icacls", "powershell.exe"):
        if shutil.which(command) is None:
            raise ExternalBackupError(f"required_command_missing:{command}")
    if minimum_free_bytes <= 0:
        raise ExternalBackupError("minimum_free_bytes_must_be_positive")
    resolved, volume = _validate_destination(destination_root, minimum_free_bytes)
    remote = _remote_preflight(ssh_target, target_baseline or _default_baseline())
    return ExternalPreflightRecord(
        schema_version=1,
        checked_at=_utc_now(),
        destination_root=str(resolved),
        destination_volume=volume,
        minimum_free_bytes=minimum_free_bytes,
        remote=remote,
        status="PASS",
    )


def _write_json(
    path: Path,
    value: object,
    *,
    budget: TransferBudget | None = None,
) -> None:
    payload = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if budget is not None:
        budget.accept(len(payload))
    path.write_bytes(payload)


def _secure_directory(path: Path) -> None:
    identity = _run(("whoami",)).stdout.strip()
    result = _run(
        (
            "icacls",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{identity}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
            "*S-1-5-32-544:(OI)(CI)F",
        ),
        check=False,
    )
    if result.returncode != 0:
        raise ExternalBackupError("backup_directory_acl_failed")


def _remote_to_file(
    ssh_target: str,
    script: str,
    destination: Path,
    *,
    budget: TransferBudget,
    minimum_bytes: int = 1,
) -> None:
    error_path = destination.with_suffix(destination.suffix + ".stderr")
    print(f"START {destination.name}", file=sys.stderr, flush=True)
    with destination.open("wb") as output, error_path.open("wb") as errors:
        process = subprocess.Popen(
            _ssh_arguments(ssh_target, _remote_shell(script)),
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        if process.stdout is None:
            raise ExternalBackupError("remote_stream_unavailable")
        try:
            while block := process.stdout.read(STREAM_BLOCK_BYTES):
                budget.accept(len(block))
                output.write(block)
        except ExternalBackupError:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        finally:
            process.stdout.close()
        return_code = process.wait()
        output.flush()
        os.fsync(output.fileno())
    if return_code != 0:
        message = error_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        raise ExternalBackupError(
            f"remote_stream_failed:{destination.name}:{return_code}:{message.strip()}"
        )
    error_path.unlink(missing_ok=True)
    size = destination.stat().st_size
    if size < minimum_bytes:
        raise ExternalBackupError(f"remote_stream_too_small:{destination.name}:{size}")
    print(f"DONE {destination.name} {size}", file=sys.stderr, flush=True)


def _remote_volume_exists(ssh_target: str, volume: str) -> bool:
    command = shlex.join(("docker", "volume", "inspect", volume))
    return _remote_run(ssh_target, command, check=False).returncode == 0


def _archive_remote_volume(
    ssh_target: str,
    volume: str,
    destination: Path,
    budget: TransferBudget,
    *,
    compress: bool = True,
    helper_image: str | None = None,
) -> None:
    command = shlex.join(
        (
            "docker",
            "run",
            "--rm",
            "--log-driver",
            "none",
            "--network",
            "none",
            "--user",
            "0:0",
            "--volume",
            f"{volume}:/source:ro",
            "--entrypoint",
            "tar",
            helper_image or baseline.CANDIDATE_IMAGE,
            "-C",
            "/source",
            "-czf" if compress else "-cf",
            "-",
            ".",
        )
    )
    _remote_to_file(ssh_target, command, destination, budget=budget)


def _remote_stop(ssh_target: str, container: str, timeout: int) -> None:
    _remote_docker(ssh_target, "stop", "--time", str(timeout), container)


def _remote_start(ssh_target: str, container: str) -> None:
    _remote_docker(ssh_target, "start", container)


def _wait_for_remote_healthy(
    ssh_target: str,
    container: str,
    *,
    attempts: int = 60,
) -> None:
    command = (
        "for i in $(seq 1 "
        + str(attempts)
        + "); do status=$(docker inspect "
        + shlex.quote(container)
        + " --format '{{if .State.Health}}{{.State.Health.Status}}"
        + "{{else}}none{{end}}' 2>/dev/null || true); "
        + '[ "$status" = healthy ] && exit 0; sleep 2; done; exit 1'
    )
    if _remote_run(ssh_target, command, check=False).returncode != 0:
        raise ExternalBackupError(f"container_health_timeout:{container}")


def _restart_original_runtime(
    ssh_target: str,
    stopped: Sequence[str],
    postgres_was_running: bool,
) -> None:
    stopped_set = set(stopped)
    if postgres_was_running:
        _remote_start(ssh_target, baseline.POSTGRES_CONTAINER)
        _wait_for_remote_healthy(ssh_target, baseline.POSTGRES_CONTAINER)
    for container in baseline.CORE_RESTART_ORDER:
        if container in stopped_set:
            _remote_start(ssh_target, container)
    for container in baseline.CORE_RESTART_ORDER:
        if container in stopped_set:
            _wait_for_remote_healthy(ssh_target, container)
    for container in baseline.AUXILIARY_RESTART_ORDER:
        if container in stopped_set:
            _remote_start(ssh_target, container)
    expected = stopped_set | ({baseline.POSTGRES_CONTAINER} if postgres_was_running else set())
    missing = sorted(expected - _remote_running_names(ssh_target))
    if missing:
        raise ExternalBackupError("runtime_restart_incomplete:" + ",".join(missing))


def _sync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(STREAM_BLOCK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_hash_manifest(directory: Path, budget: TransferBudget) -> str:
    excluded = {"SHA256SUMS", "BACKUP_SHA256", "COMPLETED.json"}
    records: list[tuple[Path, str]] = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.name not in excluded:
            _sync_file(path)
            records.append((path, _sha256_file(path)))
    payload = "".join(f"{digest}  {path.name}\n" for path, digest in records).encode("ascii")
    budget.accept(len(payload) + 65)
    sums = directory / "SHA256SUMS"
    sums.write_bytes(payload)
    _sync_file(sums)
    manifest_digest = hashlib.sha256(payload).hexdigest()
    digest_path = directory / "BACKUP_SHA256"
    digest_path.write_text(manifest_digest + "\n", encoding="ascii")
    _sync_file(digest_path)
    if sums.read_bytes() != payload:
        raise ExternalBackupError("hash_manifest_readback_mismatch")
    for path, expected in records:
        if _sha256_file(path) != expected:
            raise ExternalBackupError(f"backup_file_readback_mismatch:{path.name}")
    return manifest_digest


def execute_backup(
    destination_root: Path,
    ssh_target: str,
    backup_id: str,
    minimum_free_bytes: int,
    max_backup_bytes: int,
    warning_percent: int,
    *,
    leave_stopped: bool,
    vault_compression: str = "gzip",
    target_baseline: BackupBaseline | None = None,
    reporter: RemoteControlReporter | None = None,
) -> Path:
    if BACKUP_ID.fullmatch(backup_id) is None:
        raise ExternalBackupError("backup_id_invalid")
    resolved_baseline = target_baseline or _default_baseline()
    record = preflight(
        destination_root,
        ssh_target,
        minimum_free_bytes,
        resolved_baseline,
    )
    if max_backup_bytes > record.destination_volume.available_bytes:
        raise ExternalBackupError("max_backup_bytes_exceeds_available_space")
    root = Path(record.destination_root)
    incomplete = root / f".{backup_id}.incomplete"
    completed = root / backup_id
    if incomplete.exists() or completed.exists():
        raise ExternalBackupError("backup_target_already_exists")
    incomplete.mkdir()
    _secure_directory(incomplete)
    _write_json(incomplete / "PREFLIGHT.json", asdict(record))

    running_before = _remote_running_names(ssh_target)
    postgres_was_running = baseline.POSTGRES_CONTAINER in running_before
    stopped = [name for name in baseline.STATEFUL_CONTAINERS if name in running_before]
    if not postgres_was_running:
        raise ExternalBackupError("production_postgres_not_running")
    inspect = json.loads(_remote_docker(ssh_target, "inspect", *sorted(running_before)))
    _write_json(incomplete / "RUNNING_CONTAINERS_INSPECT.json", inspect)
    (incomplete / "RUNNING_CONTAINERS.txt").write_text(
        "".join(f"{name}\n" for name in sorted(running_before)), encoding="utf-8"
    )

    initial_bytes = sum(path.stat().st_size for path in incomplete.iterdir() if path.is_file())

    def progress(bytes_written: int, warned: bool) -> None:
        if reporter is not None:
            reporter.update(
                "RUNNING",
                bytes_written=bytes_written,
                available_bytes=(record.destination_volume.available_bytes - bytes_written),
                max_backup_bytes=max_backup_bytes,
                warning_percent=warning_percent,
                message_code="backup_limit_approaching" if warned else None,
            )

    budget = TransferBudget(
        max_backup_bytes,
        warning_percent,
        initial_bytes,
        progress,
    )
    budget.report()

    backup_complete = False
    runtime_mutated = False
    try:
        runtime_mutated = True
        for container in stopped:
            _remote_stop(ssh_target, container, 45)
        still_running = sorted(set(stopped) & _remote_running_names(ssh_target))
        if still_running:
            raise ExternalBackupError(
                "stateful_containers_still_running:" + ",".join(still_running)
            )

        _remote_to_file(
            ssh_target,
            shlex.join(
                (
                    "docker",
                    "exec",
                    baseline.POSTGRES_CONTAINER,
                    "pg_dump",
                    "-U",
                    "autplay",
                    "-d",
                    "autplay",
                    "-Fc",
                )
            ),
            incomplete / "postgres.dump",
            budget=budget,
            minimum_bytes=10_001,
        )
        _remote_to_file(
            ssh_target,
            shlex.join(
                (
                    "docker",
                    "exec",
                    baseline.POSTGRES_CONTAINER,
                    "pg_dumpall",
                    "-U",
                    "autplay",
                    "--globals-only",
                )
            ),
            incomplete / "postgres-globals.sql",
            budget=budget,
        )
        _remote_stop(ssh_target, baseline.POSTGRES_CONTAINER, 30)
        if {baseline.POSTGRES_CONTAINER, *stopped} & _remote_running_names(ssh_target):
            raise ExternalBackupError("production_stateful_process_remained_running")

        for filename, volume in baseline.VOLUMES.items():
            exists = _remote_volume_exists(ssh_target, volume)
            if filename in {"postgres-volume.tar.gz", "vault-volume.tar.gz"} and not exists:
                raise ExternalBackupError(f"required_volume_missing:{volume}")
            if exists:
                compress = filename != "vault-volume.tar.gz" or vault_compression == "gzip"
                destination_name = filename if compress else filename.removesuffix(".gz")
                _archive_remote_volume(
                    ssh_target,
                    volume,
                    incomplete / destination_name,
                    budget,
                    compress=compress,
                    helper_image=resolved_baseline.candidate_image,
                )

        archive_command = shlex.join(
            (
                "tar",
                "-C",
                "/",
                "-czf",
                "-",
                *baseline.CONFIG_PATHS,
            )
        )
        _remote_to_file(
            ssh_target,
            archive_command,
            incomplete / "config-and-secrets.tar.gz",
            budget=budget,
        )
        image_command = (
            shlex.join(("docker", "image", "save", resolved_baseline.rollback_image)) + " | gzip -1"
        )
        _remote_to_file(
            ssh_target,
            image_command,
            incomplete / "rollback-image.docker.tar.gz",
            budget=budget,
        )
        _write_json(
            incomplete / "BACKUP_MANIFEST.json",
            {
                "schema_version": 1,
                "backup_id": backup_id,
                "created_at": _utc_now(),
                "transport": "openssh-direct-to-windows-external-ntfs",
                "source_migration_head": resolved_baseline.migration_head,
                "source_server_build_sha256": resolved_baseline.live_image_sha256,
                "candidate_archive_sha256": resolved_baseline.candidate_archive_sha256,
                "candidate_image_sha256": resolved_baseline.candidate_image_sha256,
                "rollback_image": resolved_baseline.rollback_image,
                "acquisition_shards_stopped_before_backup": True,
                "production_direct_targeted": True,
                "restore_rehearsal_completed": False,
                "vault_compression": vault_compression,
                "max_backup_bytes": max_backup_bytes,
                "warning_percent": warning_percent,
            },
            budget=budget,
        )
        backup_sha256 = _write_hash_manifest(incomplete, budget)
        _write_json(
            incomplete / "COMPLETED.json",
            {
                "schema_version": 1,
                "status": "PASS",
                "backup_sha256": backup_sha256,
                "completed_at": _utc_now(),
            },
            budget=budget,
        )
        _sync_file(incomplete / "COMPLETED.json")
        incomplete.rename(completed)
        backup_complete = True
        budget.report()
        if reporter is not None:
            reporter.update(
                "COMPLETED",
                bytes_written=budget.total_bytes,
                available_bytes=(record.destination_volume.available_bytes - budget.total_bytes),
                max_backup_bytes=max_backup_bytes,
                warning_percent=warning_percent,
                message_code=None,
            )
        return completed
    except Exception as error:
        if reporter is not None:
            state = "LIMIT_EXCEEDED" if str(error) == "backup_size_limit_exceeded" else "FAILED"
            try:
                reporter.update(
                    state,
                    bytes_written=budget.total_bytes,
                    available_bytes=(
                        record.destination_volume.available_bytes - budget.total_bytes
                    ),
                    max_backup_bytes=max_backup_bytes,
                    warning_percent=warning_percent,
                    message_code=str(error)[:120],
                )
            except Exception as report_error:
                print(
                    json.dumps(
                        {
                            "status": "FAIL",
                            "error": "backup_status_publish_failed",
                            "detail": str(report_error),
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
        raise
    finally:
        if runtime_mutated and (not leave_stopped or not backup_complete):
            try:
                _restart_original_runtime(ssh_target, stopped, postgres_was_running)
            except Exception as restart_error:
                print(
                    json.dumps(
                        {
                            "status": "FAIL",
                            "error": "runtime_restart_failed",
                            "detail": str(restart_error),
                        },
                        sort_keys=True,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                if backup_complete:
                    raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=("Preflight or stream the Admin target backup to a Windows external NTFS disk.")
    )
    parser.add_argument("--destination-root", required=True, type=Path)
    parser.add_argument("--ssh-target", default=DEFAULT_SSH_TARGET)
    parser.add_argument("--backup-id", default=_default_backup_id())
    parser.add_argument(
        "--baseline-file",
        type=Path,
        help="strict non-secret JSON pinning the approved deployed target and rollback image",
    )
    parser.add_argument("--minimum-free-bytes", type=int, default=DEFAULT_MINIMUM_FREE_BYTES)
    parser.add_argument(
        "--max-backup-bytes",
        type=int,
        help="hard cap for the complete backup generation (required with --execute)",
    )
    parser.add_argument(
        "--warning-percent",
        type=int,
        help="emit/publish the capacity alert at this percentage (default: 90)",
    )
    parser.add_argument(
        "--remote-control-root",
        help="optional target-host spool shared with Admin Web",
    )
    parser.add_argument(
        "--target-id",
        help="approved Admin Web target identifier for the external disk agent",
    )
    parser.add_argument(
        "--remote-control-helper-image",
        help="networkless image used with the Admin UID to access its owner-only control spool",
    )
    parser.add_argument(
        "--vault-compression",
        choices=("none", "gzip"),
        default="none",
        help=(
            "Vault stream compression (default: none; compressed audio normally makes gzip "
            "slower without meaningful space savings)"
        ),
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="perform the stop and backup; without this flag the command is read-only",
    )
    parser.add_argument(
        "--run-requested",
        action="store_true",
        help=(
            "execute only when Admin Web has published a REQUESTED status; intended for a "
            "periodic privilege-separated agent"
        ),
    )
    parser.add_argument(
        "--leave-stopped",
        action="store_true",
        help="after a successful backup, keep production stopped for Gate D/deployment",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(argv)
    target_baseline = (
        _load_baseline(options.baseline_file)
        if options.baseline_file is not None
        else _default_baseline()
    )
    execute = options.execute or options.run_requested
    if options.execute and options.run_requested:
        raise ExternalBackupError("execute_modes_mutually_exclusive")
    if options.run_requested and options.remote_control_root is None:
        raise ExternalBackupError("run_requested_requires_remote_control")
    if options.leave_stopped and not execute:
        raise ExternalBackupError("leave_stopped_requires_execute")
    if (options.remote_control_root is None) != (options.target_id is None):
        raise ExternalBackupError("remote_control_requires_target_id_and_root")
    if options.remote_control_helper_image is not None and options.remote_control_root is None:
        raise ExternalBackupError("remote_control_helper_image_requires_root")
    reporter = (
        RemoteControlReporter(
            options.ssh_target,
            options.remote_control_root,
            options.target_id,
            options.backup_id,
            options.remote_control_helper_image or target_baseline.candidate_image,
        )
        if options.remote_control_root is not None
        else None
    )
    if options.run_requested:
        assert reporter is not None
        if not reporter.request_is_pending():
            print(json.dumps({"status": "NOOP", "reason": "no_requested_backup"}))
            return 0
    max_backup_bytes = options.max_backup_bytes
    warning_percent = options.warning_percent
    if reporter is not None:
        requested_maximum, requested_warning = reporter.requested_policy()
        if max_backup_bytes is not None and max_backup_bytes != requested_maximum:
            raise ExternalBackupError("max_backup_bytes_policy_mismatch")
        if warning_percent is not None and warning_percent != requested_warning:
            raise ExternalBackupError("warning_percent_policy_mismatch")
        max_backup_bytes = requested_maximum
        warning_percent = requested_warning
    if execute:
        if max_backup_bytes is None:
            raise ExternalBackupError("max_backup_bytes_required")
        if warning_percent is None:
            warning_percent = 90
        path = execute_backup(
            options.destination_root,
            options.ssh_target,
            options.backup_id,
            options.minimum_free_bytes,
            max_backup_bytes,
            warning_percent,
            leave_stopped=options.leave_stopped,
            vault_compression=options.vault_compression,
            target_baseline=target_baseline,
            reporter=reporter,
        )
        print(json.dumps({"status": "PASS", "backup": str(path)}, sort_keys=True))
    else:
        record = preflight(
            options.destination_root,
            options.ssh_target,
            options.minimum_free_bytes,
            target_baseline,
        )
        print(json.dumps(asdict(record), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        ExternalBackupError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as error:
        print(json.dumps({"status": "FAIL", "error": str(error)}), file=sys.stderr)
        raise SystemExit(1) from error
