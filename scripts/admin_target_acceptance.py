"""Create and verify a closed evidence bundle for Admin target acceptance.

The utility never contacts a target or applies configuration.  It binds reviewed,
redacted files to one manifest and refuses to report PASS until every physical gate
from the release runbook is complete.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

MAX_MANIFEST_BYTES = 262_144
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_BYTES = 1024 * 1024 * 1024
MAX_ARTIFACTS = 256
EXPECTED_MIGRATION_HEAD = "0060_local_bridge_authority"
EXPECTED_ANDROID_APPLICATION_ID = "app.autplay"
EXPECTED_ANDROID_VERSION_CODE = 11
EXPECTED_ANDROID_VERSION_NAME = "0.3.7-metadata"

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_HEAD = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_MIGRATION = re.compile(r"[0-9]{4}_[a-z0-9_]{1,80}\Z")
_APPLICATION_ID = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+\Z")
_SAFE_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/+:-]{0,199}\Z")
_SAFE_PATH = re.compile(r"[A-Za-z0-9._/-]{1,240}\Z")
_STATUS = frozenset({"PENDING", "PASS", "FAIL"})
_TOP_LEVEL_FIELDS = frozenset({"schema_version", "status", "source", "targets", "gates"})
_SOURCE_FIELDS = frozenset({"branch", "head", "dirty_worktree"})
_TARGET_FIELDS = frozenset(
    {
        "server_build_sha256",
        "migration_head",
        "admin_origin_sha256",
        "rp_id_sha256",
        "tls_chain_sha256",
        "android_apk_sha256",
        "android_application_id",
        "android_version_code",
        "android_version_name",
        "android_signing_sha256",
        "a55_serial_sha256",
        "a55_build_sha256",
        "windows_build_sha256",
        "browser_build_sha256",
        "network_policy_sha256",
        "server_identity_sha256",
        "environment_sha256",
        "backup_sha256",
        "started_at",
        "ended_at",
    }
)
_GATE_FIELDS = frozenset({"status", "controls"})
_CONTROL_FIELDS = frozenset({"status", "evidence"})
_EVIDENCE_FIELDS = frozenset({"path", "sha256", "captured_at", "redaction_reviewed"})
_SIGNOFF_FIELDS = frozenset(
    {
        "schema_version",
        "gate",
        "status",
        "reviewer",
        "reviewed_at",
        "targets_sha256",
        "evidence_sha256",
    }
)
_RESTORED_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "migration_head",
        "server_identity_sha256",
        "environment_sha256",
        "backup_sha256",
        "isolated_environment",
        "production_direct_targeted",
        "normal_supervisors_stopped",
        "deletion_ledger_guard_passed",
        "consent_ledger_guard_passed",
        "offline_drain_passed",
        "vault_reconcile_drained",
        "upload_cleanup_drained",
        "cpu_ingest_drained",
        "metadata_cleanup_drained",
        "readiness_started_after_guards",
        "finally_deleted_accounts_absent",
        "withdrawn_training_inputs_unauthorized",
        "current_publication_tuple_served",
        "open_execution_capacity_released_without_host_empty",
    }
)
_LIVE_PID_ABORT_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "migration_head",
        "server_identity_sha256",
        "environment_sha256",
        "backup_sha256",
        "platform",
        "error_code",
        "live_process_identity_sha256",
        "database_state_before_sha256",
        "database_state_after_sha256",
        "process_was_live",
        "transaction_aborted",
        "production_direct_targeted",
    }
)
_REVIEWER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@+-]{0,199}\Z")

REQUIRED_CONTROLS: Mapping[str, tuple[str, ...]] = {
    "A": (
        "private_https_origin_rp",
        "third_device_network_denial",
        "windows_hello_ceremony",
        "a55_platform_ceremony",
        "admin_en_ru_desktop_narrow",
        "keyboard_navigation",
        "synced_passkey_network_denial",
        "credential_revocation_session_refusal",
        "operator_signoff",
    ),
    "B": (
        "a55_reviewed_install",
        "pairing_owner",
        "pairing_admin",
        "pairing_user",
        "encrypted_process_death_pending",
        "txt_manual_recovery",
        "recovery_rotation_total_revocation",
        "deletion_request_cancel",
        "consent_grant_withdrawal",
        "quota_refusal",
        "receipts_database_state",
        "operator_signoff",
    ),
    "C": (
        "joint_workload_recipe",
        "resource_report_v1",
        "internal_report_v3",
        "all_audio_paths",
        "all_internal_paths",
        "metrics_outcomes",
        "operator_signoff",
    ),
    "D": (
        "isolated_restore_identity",
        "normal_supervisors_stopped",
        "deletion_ledger_guard",
        "consent_ledger_guard",
        "trusted_offline_drain",
        "restored_state_assertions",
        "live_pid_abort",
        "operator_signoff",
    ),
}

_GATE_TARGETS: Mapping[str, tuple[str, ...]] = {
    "A": (
        "server_build_sha256",
        "migration_head",
        "admin_origin_sha256",
        "rp_id_sha256",
        "tls_chain_sha256",
        "a55_serial_sha256",
        "a55_build_sha256",
        "windows_build_sha256",
        "browser_build_sha256",
        "network_policy_sha256",
        "server_identity_sha256",
    ),
    "B": (
        "server_build_sha256",
        "migration_head",
        "android_apk_sha256",
        "android_application_id",
        "android_version_code",
        "android_version_name",
        "android_signing_sha256",
        "a55_serial_sha256",
        "a55_build_sha256",
        "server_identity_sha256",
    ),
    "C": (
        "server_build_sha256",
        "migration_head",
        "server_identity_sha256",
        "environment_sha256",
    ),
    "D": (
        "server_build_sha256",
        "migration_head",
        "server_identity_sha256",
        "environment_sha256",
        "backup_sha256",
    ),
}


class AcceptanceError(ValueError):
    """The evidence bundle is incomplete or unsafe to accept."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise AcceptanceError(code)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("manifest_duplicate_key")
        result[key] = value
    return result


def _object(value: object, fields: frozenset[str], code: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        _fail(code)
    return value


def _sha256(value: object, code: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail(code)
    return value


def _timestamp(value: object, code: str, *, nullable: bool = False) -> datetime | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _fail(code)
    offset = parsed.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        _fail(code)
    return parsed


def _status(value: object, code: str) -> str:
    if not isinstance(value, str) or value not in _STATUS:
        _fail(code)
    return value


def _version_one(value: object, code: str) -> None:
    if type(value) is not int or value != 1:
        _fail(code)


def _read_manifest(path: Path) -> tuple[bytes, dict[str, object]]:
    if path.is_symlink() or not path.is_file():
        _fail("manifest_not_regular")
    size = path.stat().st_size
    if not 1 <= size <= MAX_MANIFEST_BYTES:
        _fail("manifest_size_invalid")
    payload = path.read_bytes()
    try:
        document = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs)
    except UnicodeError, json.JSONDecodeError, RecursionError:
        _fail("manifest_json_invalid")
    return payload, _object(document, _TOP_LEVEL_FIELDS, "manifest_fields_invalid")


def _read_json_object(path: Path, fields: frozenset[str], code: str) -> dict[str, object]:
    size = path.stat().st_size
    if not 1 <= size <= MAX_MANIFEST_BYTES:
        _fail(code)
    try:
        document = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    except UnicodeError, json.JSONDecodeError, RecursionError:
        _fail(code)
    return _object(document, fields, code)


def _validate_source(value: object) -> None:
    source = _object(value, _SOURCE_FIELDS, "source_fields_invalid")
    branch = source["branch"]
    if not isinstance(branch, str) or _SAFE_TEXT.fullmatch(branch) is None:
        _fail("source_branch_invalid")
    head = source["head"]
    if not isinstance(head, str) or _HEAD.fullmatch(head) is None:
        _fail("source_head_invalid")
    if type(source["dirty_worktree"]) is not bool:
        _fail("source_dirty_invalid")


def _validate_targets(value: object) -> tuple[dict[str, object], datetime | None, datetime | None]:
    targets = _object(value, _TARGET_FIELDS, "target_fields_invalid")
    digest_fields = _TARGET_FIELDS - {
        "migration_head",
        "android_application_id",
        "android_version_code",
        "android_version_name",
        "started_at",
        "ended_at",
    }
    for field in digest_fields:
        _sha256(targets[field], f"target_{field}_invalid", nullable=True)
    migration = targets["migration_head"]
    if migration is not None and (
        not isinstance(migration, str) or _MIGRATION.fullmatch(migration) is None
    ):
        _fail("target_migration_head_invalid")
    application_id = targets["android_application_id"]
    if application_id is not None and (
        not isinstance(application_id, str) or _APPLICATION_ID.fullmatch(application_id) is None
    ):
        _fail("target_android_application_id_invalid")
    version_code = targets["android_version_code"]
    if version_code is not None and (
        type(version_code) is not int or not 1 <= version_code < 2**31
    ):
        _fail("target_android_version_code_invalid")
    version_name = targets["android_version_name"]
    if version_name is not None and (
        not isinstance(version_name, str) or _SAFE_TEXT.fullmatch(version_name) is None
    ):
        _fail("target_android_version_name_invalid")
    started = _timestamp(targets["started_at"], "target_started_at_invalid", nullable=True)
    ended = _timestamp(targets["ended_at"], "target_ended_at_invalid", nullable=True)
    if (started is None) != (ended is None):
        _fail("target_time_range_invalid")
    if started is not None and ended is not None and started > ended:
        _fail("target_time_range_invalid")
    return targets, started, ended


def _safe_artifact_path(root: Path, raw: object) -> tuple[str, Path]:
    if not isinstance(raw, str) or _SAFE_PATH.fullmatch(raw) is None or "\\" in raw:
        _fail("evidence_path_invalid")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        _fail("evidence_path_invalid")
    candidate = root.joinpath(*relative.parts)
    current = candidate
    while current != root:
        if current.is_symlink():
            _fail("evidence_symlink_forbidden")
        current = current.parent
    if not candidate.is_file():
        _fail("evidence_not_regular")
    try:
        if not candidate.resolve(strict=True).is_relative_to(root):
            _fail("evidence_path_escape")
    except OSError:
        _fail("evidence_not_regular")
    return raw, candidate


def _digest_file(path: Path) -> tuple[str, int]:
    before = path.stat()
    if not 1 <= before.st_size <= MAX_ARTIFACT_BYTES:
        _fail("evidence_size_invalid")
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    after = path.stat()
    signature_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
    signature_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if signature_before != signature_after:
        _fail("evidence_changed_during_validation")
    return digest, before.st_size


def _validate_evidence(
    root: Path,
    value: object,
    *,
    started: datetime | None,
    ended: datetime | None,
    seen_paths: set[str],
) -> tuple[str, Path, int]:
    evidence = _object(value, _EVIDENCE_FIELDS, "evidence_fields_invalid")
    raw_path, path = _safe_artifact_path(root, evidence["path"])
    if raw_path == "manifest.json" or raw_path in seen_paths:
        _fail("evidence_path_duplicate")
    seen_paths.add(raw_path)
    expected = _sha256(evidence["sha256"], "evidence_sha256_invalid")
    captured = _timestamp(evidence["captured_at"], "evidence_captured_at_invalid")
    if evidence["redaction_reviewed"] is not True:
        _fail("evidence_redaction_review_required")
    if started is None or ended is None or captured is None or not started <= captured <= ended:
        _fail("evidence_outside_acceptance_window")
    actual, size = _digest_file(path)
    if actual != expected:
        _fail("evidence_hash_mismatch")
    return raw_path, path, size


def _validate_resource_reports(
    root: Path,
    control_paths: Mapping[tuple[str, str], tuple[Path, ...]],
    targets: Mapping[str, object],
) -> None:
    resource_paths = control_paths[("C", "resource_report_v1")]
    internal_paths = control_paths[("C", "internal_report_v3")]
    if len(resource_paths) != 1 or len(internal_paths) != 1:
        _fail("gate_c_report_count_invalid")
    repository_source = Path(__file__).resolve().parents[1] / "server" / "src"
    sys.path.insert(0, str(repository_source))
    try:
        try:
            from autplay.domain.internal_io import InternalIoMeasurement
            from autplay.domain.resource_admission import ResourceAdmissionError
            from autplay.domain.resource_measurements import ResourceMeasurement
        except ImportError:
            _fail("gate_c_validator_unavailable")

        try:
            resource_payload = resource_paths[0].read_bytes()
            internal_payload = internal_paths[0].read_bytes()
            resource = ResourceMeasurement.parse(resource_payload)
            internal = InternalIoMeasurement.parse(internal_payload)
        except ResourceAdmissionError, UnicodeError, ValueError:
            _fail("gate_c_report_invalid")
    finally:
        sys.path.pop(0)
    try:
        resource_doc = json.loads(resource_payload.decode("utf-8"), object_pairs_hook=_pairs)
        internal_doc = json.loads(internal_payload.decode("utf-8"), object_pairs_hook=_pairs)
    except UnicodeError, json.JSONDecodeError, RecursionError:
        _fail("gate_c_report_invalid")
    if (
        internal.version != 3
        or not isinstance(internal_doc, dict)
        or internal_doc.get("resource_measurement") != resource_doc
        or resource.environment_sha256 != targets["environment_sha256"]
    ):
        _fail("gate_c_report_mismatch")
    identity = json.dumps(
        {
            "identity_epoch": resource.identity_epoch,
            "server_instance_id": str(resource.server_instance_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    if hashlib.sha256(identity).hexdigest() != targets["server_identity_sha256"]:
        _fail("gate_c_server_identity_mismatch")


def _validate_gate_d_reports(
    control_paths: Mapping[tuple[str, str], tuple[Path, ...]],
    targets: Mapping[str, object],
) -> None:
    restored_paths = control_paths[("D", "restored_state_assertions")]
    abort_paths = control_paths[("D", "live_pid_abort")]
    if len(restored_paths) != 1 or len(abort_paths) != 1:
        _fail("gate_d_report_count_invalid")

    restored = _read_json_object(
        restored_paths[0],
        _RESTORED_STATE_FIELDS,
        "gate_d_restored_state_invalid",
    )
    expected_identity = {
        "migration_head": targets["migration_head"],
        "server_identity_sha256": targets["server_identity_sha256"],
        "environment_sha256": targets["environment_sha256"],
        "backup_sha256": targets["backup_sha256"],
    }
    if any(restored[field] != expected for field, expected in expected_identity.items()):
        _fail("gate_d_restored_state_target_mismatch")
    required_true = (
        "isolated_environment",
        "normal_supervisors_stopped",
        "deletion_ledger_guard_passed",
        "consent_ledger_guard_passed",
        "offline_drain_passed",
        "vault_reconcile_drained",
        "upload_cleanup_drained",
        "cpu_ingest_drained",
        "metadata_cleanup_drained",
        "readiness_started_after_guards",
        "finally_deleted_accounts_absent",
        "withdrawn_training_inputs_unauthorized",
        "current_publication_tuple_served",
    )
    _version_one(restored["schema_version"], "gate_d_restored_state_invalid")
    if (
        restored["status"] != "PASS"
        or any(restored[field] is not True for field in required_true)
        or restored["production_direct_targeted"] is not False
        or restored["open_execution_capacity_released_without_host_empty"] is not False
    ):
        _fail("gate_d_restored_state_invalid")

    abort = _read_json_object(
        abort_paths[0],
        _LIVE_PID_ABORT_FIELDS,
        "gate_d_live_pid_abort_invalid",
    )
    if any(abort[field] != expected for field, expected in expected_identity.items()):
        _fail("gate_d_live_pid_abort_target_mismatch")
    for field in (
        "live_process_identity_sha256",
        "database_state_before_sha256",
        "database_state_after_sha256",
    ):
        _sha256(abort[field], "gate_d_live_pid_abort_invalid")
    _version_one(abort["schema_version"], "gate_d_live_pid_abort_invalid")
    if (
        abort["status"] != "PASS"
        or abort["platform"] not in {"windows", "linux"}
        or abort["error_code"] != "offline_process_still_running"
        or abort["process_was_live"] is not True
        or abort["transaction_aborted"] is not True
        or abort["production_direct_targeted"] is not False
        or abort["database_state_before_sha256"] != abort["database_state_after_sha256"]
    ):
        _fail("gate_d_live_pid_abort_invalid")


def _validate_reviewed_target(
    targets: Mapping[str, object], gate_statuses: Mapping[str, str]
) -> None:
    if any(status == "PASS" for status in gate_statuses.values()) and (
        targets["migration_head"] != EXPECTED_MIGRATION_HEAD
    ):
        _fail("target_migration_head_unreviewed")
    if gate_statuses["B"] != "PASS":
        return
    expected_android = {
        "android_application_id": EXPECTED_ANDROID_APPLICATION_ID,
        "android_version_code": EXPECTED_ANDROID_VERSION_CODE,
        "android_version_name": EXPECTED_ANDROID_VERSION_NAME,
    }
    if any(targets[field] != expected for field, expected in expected_android.items()):
        _fail("gate_b_android_build_unreviewed")


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")
    ).hexdigest()


def _gate_targets_sha256(gate_name: str, targets: Mapping[str, object]) -> str:
    return _canonical_sha256({field: targets[field] for field in sorted(_GATE_TARGETS[gate_name])})


def _gate_evidence_sha256(gate_name: str, controls: Mapping[str, object]) -> str:
    snapshot: dict[str, object] = {}
    for control_name in REQUIRED_CONTROLS[gate_name]:
        if control_name == "operator_signoff":
            continue
        snapshot[control_name] = _object(
            controls[control_name],
            _CONTROL_FIELDS,
            "gate_control_fields_invalid",
        )
    return _canonical_sha256(snapshot)


def gate_signoff_document(
    gate_name: str,
    targets: Mapping[str, object],
    controls: Mapping[str, object],
    *,
    reviewer: str,
    reviewed_at: str,
) -> dict[str, object]:
    if gate_name not in REQUIRED_CONTROLS or _REVIEWER.fullmatch(reviewer) is None:
        _fail("gate_signoff_identity_invalid")
    _timestamp(reviewed_at, "gate_signoff_time_invalid")
    return {
        "schema_version": 1,
        "gate": gate_name,
        "status": "PASS",
        "reviewer": reviewer,
        "reviewed_at": reviewed_at,
        "targets_sha256": _gate_targets_sha256(gate_name, targets),
        "evidence_sha256": _gate_evidence_sha256(gate_name, controls),
    }


def _validate_gate_signoff(
    gate_name: str,
    controls: Mapping[str, object],
    paths: Mapping[tuple[str, str], tuple[Path, ...]],
    targets: Mapping[str, object],
    *,
    started: datetime | None,
    ended: datetime | None,
) -> None:
    signoff_paths = paths[(gate_name, "operator_signoff")]
    if len(signoff_paths) != 1:
        _fail("gate_signoff_count_invalid")
    signoff = _read_json_object(signoff_paths[0], _SIGNOFF_FIELDS, "gate_signoff_invalid")
    reviewer = signoff["reviewer"]
    _version_one(signoff["schema_version"], "gate_signoff_invalid")
    if (
        signoff["gate"] != gate_name
        or signoff["status"] != "PASS"
        or not isinstance(reviewer, str)
        or _REVIEWER.fullmatch(reviewer) is None
    ):
        _fail("gate_signoff_invalid")
    reviewed = _timestamp(signoff["reviewed_at"], "gate_signoff_time_invalid")
    if started is None or ended is None or reviewed is None or not started <= reviewed <= ended:
        _fail("gate_signoff_time_invalid")
    latest_evidence = started
    for control_name in REQUIRED_CONTROLS[gate_name]:
        if control_name == "operator_signoff":
            continue
        control = _object(
            controls[control_name],
            _CONTROL_FIELDS,
            "gate_control_fields_invalid",
        )
        evidence = control["evidence"]
        if not isinstance(evidence, list):
            _fail("gate_control_evidence_invalid")
        for item in evidence:
            descriptor = _object(item, _EVIDENCE_FIELDS, "evidence_fields_invalid")
            captured = _timestamp(descriptor["captured_at"], "evidence_captured_at_invalid")
            if captured is not None and captured > latest_evidence:
                latest_evidence = captured
    if reviewed < latest_evidence:
        _fail("gate_signoff_precedes_evidence")
    signoff_control = _object(
        controls["operator_signoff"],
        _CONTROL_FIELDS,
        "gate_control_fields_invalid",
    )
    signoff_evidence = signoff_control["evidence"]
    if not isinstance(signoff_evidence, list) or len(signoff_evidence) != 1:
        _fail("gate_signoff_count_invalid")
    descriptor = _object(signoff_evidence[0], _EVIDENCE_FIELDS, "evidence_fields_invalid")
    captured = _timestamp(descriptor["captured_at"], "evidence_captured_at_invalid")
    if captured is None or reviewed > captured:
        _fail("gate_signoff_time_invalid")
    if signoff["targets_sha256"] != _gate_targets_sha256(gate_name, targets):
        _fail("gate_signoff_target_mismatch")
    if signoff["evidence_sha256"] != _gate_evidence_sha256(gate_name, controls):
        _fail("gate_signoff_evidence_mismatch")


def _validate_closed_inventory(root: Path, referenced: set[str]) -> None:
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            _fail("bundle_symlink_forbidden")
        if path.is_file():
            actual.add(path.relative_to(root).as_posix())
    if actual != referenced | {"manifest.json"}:
        _fail("bundle_inventory_mismatch")


def validate_bundle(directory: Path) -> dict[str, object]:
    root = directory.resolve(strict=True)
    if not root.is_dir() or directory.is_symlink():
        _fail("bundle_root_invalid")
    manifest_payload, document = _read_manifest(root / "manifest.json")
    _version_one(document["schema_version"], "manifest_version_invalid")
    declared_status = _status(document["status"], "manifest_status_invalid")
    _validate_source(document["source"])
    targets, started, ended = _validate_targets(document["targets"])
    gates = document["gates"]
    if not isinstance(gates, dict) or set(gates) != set(REQUIRED_CONTROLS):
        _fail("gates_invalid")

    seen_paths: set[str] = set()
    total_bytes = 0
    control_paths: dict[tuple[str, str], tuple[Path, ...]] = {}
    gate_statuses: dict[str, str] = {}
    for gate_name, expected_controls in REQUIRED_CONTROLS.items():
        gate = _object(gates[gate_name], _GATE_FIELDS, "gate_fields_invalid")
        gate_status = _status(gate["status"], "gate_status_invalid")
        controls = gate["controls"]
        if not isinstance(controls, dict) or set(controls) != set(expected_controls):
            _fail("gate_controls_invalid")
        controls_pass = True
        for control_name in expected_controls:
            control = _object(
                controls[control_name], _CONTROL_FIELDS, "gate_control_fields_invalid"
            )
            control_status = _status(control["status"], "gate_control_status_invalid")
            raw_evidence = control["evidence"]
            if not isinstance(raw_evidence, list) or len(raw_evidence) > MAX_ARTIFACTS:
                _fail("gate_control_evidence_invalid")
            paths: list[Path] = []
            for item in raw_evidence:
                _, path, size = _validate_evidence(
                    root,
                    item,
                    started=started,
                    ended=ended,
                    seen_paths=seen_paths,
                )
                paths.append(path)
                total_bytes += size
                if len(seen_paths) > MAX_ARTIFACTS or total_bytes > MAX_BUNDLE_BYTES:
                    _fail("bundle_size_invalid")
            if control_status == "PASS" and not paths:
                _fail("gate_control_pass_without_evidence")
            controls_pass = controls_pass and control_status == "PASS"
            control_paths[(gate_name, control_name)] = tuple(paths)
        targets_present = all(targets[field] is not None for field in _GATE_TARGETS[gate_name])
        computed = "PASS" if controls_pass and targets_present else "PENDING"
        if gate_status == "PASS" and computed != "PASS":
            _fail("gate_false_pass")
        if gate_status == "PENDING" and computed == "PASS":
            _fail("gate_stale_pending")
        gate_statuses[gate_name] = gate_status

    _validate_closed_inventory(root, seen_paths)
    _validate_reviewed_target(targets, gate_statuses)
    if gate_statuses["C"] == "PASS":
        _validate_resource_reports(root, control_paths, targets)
    if gate_statuses["D"] == "PASS":
        _validate_gate_d_reports(control_paths, targets)
    for gate_name, gate_status in gate_statuses.items():
        if gate_status == "PASS":
            gate = _object(gates[gate_name], _GATE_FIELDS, "gate_fields_invalid")
            controls = gate["controls"]
            if not isinstance(controls, dict):
                _fail("gate_controls_invalid")
            _validate_gate_signoff(
                gate_name,
                controls,
                control_paths,
                targets,
                started=started,
                ended=ended,
            )
    all_pass = all(status == "PASS" for status in gate_statuses.values())
    computed_status = "PASS" if all_pass else "PENDING"
    if declared_status == "PASS" and computed_status != "PASS":
        _fail("manifest_false_pass")
    if declared_status == "PENDING" and computed_status == "PASS":
        _fail("manifest_stale_pending")
    if all_pass and (started is None or ended is None):
        _fail("target_time_range_required")
    return {
        "status": declared_status,
        "manifest_sha256": hashlib.sha256(manifest_payload).hexdigest(),
        "artifact_count": len(seen_paths),
        "artifact_bytes": total_bytes,
        "gates": gate_statuses,
    }


def inspect_bundle(directory: Path) -> dict[str, object]:
    validation = validate_bundle(directory)
    root = directory.resolve(strict=True)
    _, document = _read_manifest(root / "manifest.json")
    targets = _object(document["targets"], _TARGET_FIELDS, "target_fields_invalid")
    gates = document["gates"]
    if not isinstance(gates, dict):
        _fail("gates_invalid")
    gate_summaries: dict[str, object] = {}
    for gate_name, expected_controls in REQUIRED_CONTROLS.items():
        gate = _object(gates[gate_name], _GATE_FIELDS, "gate_fields_invalid")
        controls = gate["controls"]
        if not isinstance(controls, dict):
            _fail("gate_controls_invalid")
        incomplete_controls: list[str] = []
        artifact_count = 0
        for control_name in expected_controls:
            control = _object(
                controls[control_name],
                _CONTROL_FIELDS,
                "gate_control_fields_invalid",
            )
            if control["status"] != "PASS":
                incomplete_controls.append(control_name)
            evidence = control["evidence"]
            if not isinstance(evidence, list):
                _fail("gate_control_evidence_invalid")
            artifact_count += len(evidence)
        gate_summaries[gate_name] = {
            "status": gate["status"],
            "missing_targets": sorted(
                field for field in _GATE_TARGETS[gate_name] if targets[field] is None
            ),
            "incomplete_controls": incomplete_controls,
            "artifact_count": artifact_count,
        }
    return {
        "status": validation["status"],
        "ready": validation["status"] == "PASS",
        "manifest_sha256": validation["manifest_sha256"],
        "acceptance_window_ready": (
            targets["started_at"] is not None and targets["ended_at"] is not None
        ),
        "gates": gate_summaries,
    }


def scaffold(directory: Path, *, branch: str, head: str, dirty_worktree: bool) -> Path:
    if _SAFE_TEXT.fullmatch(branch) is None or _HEAD.fullmatch(head) is None:
        _fail("source_identity_invalid")
    if directory.exists():
        if not directory.is_dir() or any(directory.iterdir()):
            _fail("scaffold_output_not_empty")
    else:
        directory.mkdir(parents=True)
    document: dict[str, Any] = {
        "schema_version": 1,
        "status": "PENDING",
        "source": {"branch": branch, "head": head, "dirty_worktree": dirty_worktree},
        "targets": {field: None for field in sorted(_TARGET_FIELDS)},
        "gates": {
            gate: {
                "status": "PENDING",
                "controls": {
                    control: {"status": "PENDING", "evidence": []} for control in controls
                },
            }
            for gate, controls in REQUIRED_CONTROLS.items()
        },
    }
    path = directory / "manifest.json"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")
    return path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scaffold or validate a closed Admin target-acceptance evidence bundle."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("scaffold")
    create.add_argument("--output-directory", type=Path, required=True)
    create.add_argument("--branch", required=True)
    create.add_argument("--head", required=True)
    create.add_argument("--dirty-worktree", action="store_true")
    validate = commands.add_parser("validate")
    validate.add_argument("--bundle", type=Path, required=True)
    inspect = commands.add_parser("inspect")
    inspect.add_argument("--bundle", type=Path, required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "scaffold":
            path = scaffold(
                arguments.output_directory,
                branch=arguments.branch,
                head=arguments.head,
                dirty_worktree=arguments.dirty_worktree,
            )
            print(json.dumps({"status": "SCAFFOLDED", "manifest": path.name}, sort_keys=True))
            return 0
        if arguments.command == "inspect":
            print(
                json.dumps(inspect_bundle(arguments.bundle), sort_keys=True, separators=(",", ":"))
            )
            return 0
        result = validate_bundle(arguments.bundle)
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 0 if result["status"] == "PASS" else 1
    except (AcceptanceError, OSError) as error:
        code = error.code if isinstance(error, AcceptanceError) else "evidence_io_error"
        print(json.dumps({"status": "INVALID", "error": code}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
