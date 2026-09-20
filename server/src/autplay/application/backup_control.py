"""Owner-scoped file handoff between Admin Web and a privileged backup agent."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor, WebAdminError

_TARGET_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_MINIMUM_MAX_BYTES = 1024**3
_MAXIMUM_MAX_BYTES = 1024**5
_STATUS_STATES = frozenset(
    {"IDLE", "REQUESTED", "RUNNING", "COMPLETED", "FAILED", "LIMIT_EXCEEDED"}
)


@dataclass(frozen=True, slots=True)
class BackupTarget:
    target_id: str
    label: str
    kind: str


@dataclass(frozen=True, slots=True)
class BackupPolicy:
    target_id: str
    max_backup_bytes: int
    warning_percent: int
    revision: int
    updated_at: str


@dataclass(frozen=True, slots=True)
class BackupStatus:
    state: str
    target_id: str | None
    backup_id: str | None
    bytes_written: int
    available_bytes: int | None
    max_backup_bytes: int | None
    warning_percent: int | None
    message_code: str | None
    updated_at: str


@dataclass(frozen=True, slots=True)
class BackupSnapshot:
    targets: tuple[BackupTarget, ...]
    policy: BackupPolicy | None
    status: BackupStatus
    alert: bool


def parse_backup_targets(raw: str) -> tuple[BackupTarget, ...]:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("backup target registry is invalid") from error
    if not isinstance(document, list) or not 1 <= len(document) <= 32:
        raise ValueError("backup target registry must contain one to 32 targets")
    targets: list[BackupTarget] = []
    seen: set[str] = set()
    for item in document:
        if not isinstance(item, dict) or set(item) != {"id", "label", "kind"}:
            raise ValueError("backup target entry is invalid")
        target_id = item["id"]
        label = item["label"]
        kind = item["kind"]
        if (
            not isinstance(target_id, str)
            or _TARGET_ID.fullmatch(target_id) is None
            or target_id in seen
        ):
            raise ValueError("backup target identifier is invalid")
        if not isinstance(label, str) or not 1 <= len(label) <= 120 or label.strip() != label:
            raise ValueError("backup target label is invalid")
        if kind not in {"mounted", "external-agent"}:
            raise ValueError("backup target kind is invalid")
        seen.add(target_id)
        targets.append(BackupTarget(target_id, label, kind))
    return tuple(targets)


class BackupControlService:
    """Persist non-secret policy/request/status documents in a private spool."""

    def __init__(self, root: Path, targets: tuple[BackupTarget, ...]) -> None:
        if not root.is_absolute() or root == Path(root.anchor):
            raise ValueError("backup control root must be a bounded absolute path")
        if not targets:
            raise ValueError("backup targets are required")
        self._root = root
        self._targets = targets
        self._target_ids = frozenset(target.target_id for target in targets)
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self._root.chmod(0o700)

    @property
    def root(self) -> Path:
        return self._root

    def snapshot(self, actor: WebActor) -> BackupSnapshot:
        self._owner(actor)
        policy = self._read_policy()
        status = self._read_status()
        maximum = status.max_backup_bytes or (policy.max_backup_bytes if policy else None)
        warning = status.warning_percent or (policy.warning_percent if policy else None)
        alert = status.state == "LIMIT_EXCEEDED"
        if maximum is not None and warning is not None and maximum > 0:
            alert = alert or status.bytes_written * 100 >= maximum * warning
        return BackupSnapshot(self._targets, policy, status, alert)

    def configure(
        self,
        actor: WebActor,
        *,
        target_id: str,
        max_backup_bytes: int,
        warning_percent: int,
        expected_revision: int,
    ) -> BackupPolicy:
        self._owner(actor)
        if target_id not in self._target_ids:
            raise WebAdminError("backup_target_invalid")
        if not _MINIMUM_MAX_BYTES <= max_backup_bytes <= _MAXIMUM_MAX_BYTES:
            raise WebAdminError("backup_size_invalid")
        if not 50 <= warning_percent <= 99:
            raise WebAdminError("backup_warning_invalid")
        current = self._read_policy()
        revision = current.revision if current is not None else 0
        if expected_revision != revision:
            raise WebAdminError("backup_policy_stale")
        policy = BackupPolicy(
            target_id,
            max_backup_bytes,
            warning_percent,
            revision + 1,
            _utc_now(),
        )
        self._write("policy.json", asdict(policy))
        return policy

    def request(self, actor: WebActor, operation_id: UUID, expected_revision: int) -> None:
        self._owner(actor)
        policy = self._read_policy()
        if policy is None:
            raise WebAdminError("backup_policy_missing")
        if policy.revision != expected_revision:
            raise WebAdminError("backup_policy_stale")
        previous = self._read_optional("request.json")
        if previous is not None and previous.get("operation_id") == str(operation_id):
            return
        status = self._read_status()
        if status.state in {"REQUESTED", "RUNNING"}:
            raise WebAdminError("backup_already_active")
        requested_at = _utc_now()
        self._write(
            "request.json",
            {
                "schema_version": 1,
                "operation_id": str(operation_id),
                "requested_at": requested_at,
                "requested_by": str(actor.user_id),
                "target_id": policy.target_id,
                "max_backup_bytes": policy.max_backup_bytes,
                "warning_percent": policy.warning_percent,
                "policy_revision": policy.revision,
            },
        )
        self._write(
            "status.json",
            {
                "schema_version": 1,
                "state": "REQUESTED",
                "target_id": policy.target_id,
                "backup_id": None,
                "bytes_written": 0,
                "available_bytes": None,
                "max_backup_bytes": policy.max_backup_bytes,
                "warning_percent": policy.warning_percent,
                "message_code": "backup_waiting_for_agent",
                "updated_at": requested_at,
            },
        )

    def _read_policy(self) -> BackupPolicy | None:
        value = self._read_optional("policy.json")
        if value is None:
            return None
        try:
            policy = BackupPolicy(
                target_id=str(value["target_id"]),
                max_backup_bytes=_json_integer(value["max_backup_bytes"]),
                warning_percent=_json_integer(value["warning_percent"]),
                revision=_json_integer(value["revision"]),
                updated_at=str(value["updated_at"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WebAdminError("backup_control_unavailable") from error
        if (
            policy.target_id not in self._target_ids
            or not _MINIMUM_MAX_BYTES <= policy.max_backup_bytes <= _MAXIMUM_MAX_BYTES
            or not 50 <= policy.warning_percent <= 99
            or policy.revision < 1
        ):
            raise WebAdminError("backup_control_unavailable")
        return policy

    def _read_status(self) -> BackupStatus:
        value = self._read_optional("status.json")
        if value is None:
            return BackupStatus("IDLE", None, None, 0, None, None, None, None, _utc_now())
        try:
            state = str(value["state"])
            target = value.get("target_id")
            backup = value.get("backup_id")
            status = BackupStatus(
                state=state,
                target_id=str(target) if target is not None else None,
                backup_id=str(backup) if backup is not None else None,
                bytes_written=_json_integer(value["bytes_written"]),
                available_bytes=(
                    _json_integer(value["available_bytes"])
                    if value.get("available_bytes") is not None
                    else None
                ),
                max_backup_bytes=(
                    _json_integer(value["max_backup_bytes"])
                    if value.get("max_backup_bytes") is not None
                    else None
                ),
                warning_percent=(
                    _json_integer(value["warning_percent"])
                    if value.get("warning_percent") is not None
                    else None
                ),
                message_code=(
                    str(value["message_code"])
                    if value.get("message_code") is not None
                    else None
                ),
                updated_at=str(value["updated_at"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WebAdminError("backup_control_unavailable") from error
        if (
            status.state not in _STATUS_STATES
            or status.bytes_written < 0
            or (
                status.target_id is not None
                and status.target_id not in self._target_ids
            )
            or (
                status.max_backup_bytes is not None
                and not _MINIMUM_MAX_BYTES
                <= status.max_backup_bytes
                <= _MAXIMUM_MAX_BYTES
            )
            or (
                status.warning_percent is not None
                and not 50 <= status.warning_percent <= 99
            )
        ):
            raise WebAdminError("backup_control_unavailable")
        return status

    def _read_optional(self, name: str) -> dict[str, object] | None:
        path = self._root / name
        if not path.exists():
            return None
        try:
            if path.stat().st_size > 65_536:
                raise ValueError("control document too large")
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise WebAdminError("backup_control_unavailable") from error
        if not isinstance(value, dict):
            raise WebAdminError("backup_control_unavailable")
        return value

    def _write(self, name: str, value: dict[str, object]) -> None:
        destination = self._root / name
        temporary = self._root / f".{name}.{os.getpid()}.tmp"
        payload = (
            json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        try:
            with temporary.open("xb") as handle:
                if os.name != "nt":
                    os.chmod(temporary, 0o600)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        except OSError as error:
            raise WebAdminError("backup_control_unavailable") from error
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _owner(actor: WebActor) -> None:
        if actor.role is not AccountRole.OWNER:
            raise WebAdminError("forbidden")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json_integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("expected JSON integer")
    return value


__all__ = (
    "BackupControlService",
    "BackupPolicy",
    "BackupSnapshot",
    "BackupStatus",
    "BackupTarget",
    "parse_backup_targets",
)
