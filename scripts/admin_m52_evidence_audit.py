"""Audit the complete supplemental M52 evidence set without replaying physical tests."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn

MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_BYTES = 256 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_PATH = re.compile(r"[A-Za-z0-9._/-]{1,240}\Z")
_PASS = "PASS"
_SUPPLEMENTAL_PASS = "PASS_SUPPLEMENTAL_M52_NOT_GATE"
_PRODUCTION_APPLICATION_ID = "app.autplay"
_QA_APPLICATION_ID = "app.autplay.qa"
_M52_MODEL = "SM-M526B"
_DISPOSABLE_MARKERS = (
    "autplay-p14-m5b-e2e-",
    "autplay-p14-android-e2e-",
    "autplay-p14-self-pairing-roles-e2e-",
    "autplay-p14-account-recovery-android-e2e-",
    "autplay-p14-account-deletion-android-e2e-",
    "autplay-p14-training-consent-android-e2e-",
    "autplay-p14-self-pairing-device-quota-e2e-",
)
_PRODUCTION_REPORTS = frozenset(
    {
        "self_pairing_roles",
        "account_recovery",
        "account_deletion",
        "training_consent",
        "self_pairing_device_quota",
    }
)
_KNOWN_EMPTY_PLACEHOLDERS = frozenset(
    {
        "step-001-launch.png",
        "step-007-profile.png",
        "step-007a-profile.png",
    }
)


class EvidenceAuditError(ValueError):
    """The private M52 evidence set is incomplete, changed or unsafe."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise EvidenceAuditError(code)


@dataclass(frozen=True, slots=True)
class EvidenceSpec:
    name: str
    manifest_path: str
    report_path: str
    report_status: str


@dataclass(frozen=True, slots=True)
class DeviceState:
    serial: str
    model: str
    production_package: dict[str, object]
    adb_reverse_entries: int
    qa_process_running: bool
    disposable_docker_resources: int


EVIDENCE_SPECS = (
    EvidenceSpec(
        "full_connected",
        "full-connected/SHA256.json",
        "full-connected/SUMMARY.json",
        _SUPPLEMENTAL_PASS,
    ),
    EvidenceSpec(
        "playback_process_death",
        "l1-process-death/SHA256.json",
        "l1-process-death/receipt.json",
        _PASS,
    ),
    EvidenceSpec(
        "sync_coordinator",
        "sync-coordinator-current/SHA256.json",
        "sync-coordinator-current/receipt.json",
        _SUPPLEMENTAL_PASS,
    ),
    EvidenceSpec(
        "m5b_profile_pairing",
        "M5B_PROFILE_PAIRING_E2E.sha256.json",
        "M5B_PROFILE_PAIRING_E2E.json",
        _PASS,
    ),
    EvidenceSpec(
        "p14_android_server",
        "P14_ANDROID_SERVER_E2E.sha256.json",
        "P14_ANDROID_SERVER_E2E.json",
        _PASS,
    ),
    EvidenceSpec(
        "self_pairing_roles",
        "SELF_PAIRING_ROLES_ANDROID_E2E.sha256.json",
        "SELF_PAIRING_ROLES_ANDROID_E2E.json",
        _PASS,
    ),
    EvidenceSpec(
        "account_recovery",
        "ACCOUNT_RECOVERY_ANDROID_E2E.sha256.json",
        "ACCOUNT_RECOVERY_ANDROID_E2E.json",
        _PASS,
    ),
    EvidenceSpec(
        "account_deletion",
        "ACCOUNT_DELETION_ANDROID_E2E.sha256.json",
        "ACCOUNT_DELETION_ANDROID_E2E.json",
        _PASS,
    ),
    EvidenceSpec(
        "training_consent",
        "TRAINING_CONSENT_ANDROID_E2E.sha256.json",
        "TRAINING_CONSENT_ANDROID_E2E.json",
        _PASS,
    ),
    EvidenceSpec(
        "self_pairing_device_quota",
        "SELF_PAIRING_DEVICE_QUOTA_ANDROID_E2E.sha256.json",
        "SELF_PAIRING_DEVICE_QUOTA_ANDROID_E2E.json",
        _PASS,
    ),
)


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("json_duplicate_key")
        result[key] = value
    return result


def _object(value: object, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(code)
    return value


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        _fail("json_not_regular")
    size = path.stat().st_size
    if not 1 <= size <= MAX_JSON_BYTES:
        _fail("json_size_invalid")
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    except UnicodeError, json.JSONDecodeError, RecursionError:
        _fail("json_invalid")
    return _object(value, "json_object_required")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(root).as_posix()
    except ValueError:
        _fail("evidence_path_escape")


def _safe_manifest_artifact(root: Path, manifest: Path, raw: object) -> tuple[str, Path]:
    if not isinstance(raw, str) or _SAFE_PATH.fullmatch(raw) is None or "\\" in raw:
        _fail("manifest_artifact_path_invalid")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        _fail("manifest_artifact_path_invalid")
    path = manifest.parent.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file():
        _fail("manifest_artifact_not_regular")
    return _relative_path(root, path), path


def _manifest_entries(document: dict[str, Any]) -> dict[str, str]:
    if set(document) == {"schema_version", "files"}:
        if type(document["schema_version"]) is not int or document["schema_version"] != 1:
            _fail("manifest_version_invalid")
        raw = _object(document["files"], "manifest_files_invalid")
    elif set(document) == {"schema_version", "status", "artifacts"}:
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != 1
            or document["status"] != _SUPPLEMENTAL_PASS
        ):
            _fail("manifest_header_invalid")
        raw = _object(document["artifacts"], "manifest_files_invalid")
    elif set(document) == {"schema_version", "artifact", "sha256", "status"}:
        if (
            type(document["schema_version"]) is not int
            or document["schema_version"] != 1
            or document["status"] != _SUPPLEMENTAL_PASS
        ):
            _fail("manifest_header_invalid")
        artifact = document["artifact"]
        digest = document["sha256"]
        if not isinstance(artifact, str) or not isinstance(digest, str):
            _fail("manifest_entry_invalid")
        raw = {artifact: digest}
    elif document and all(
        isinstance(key, str) and isinstance(value, str) for key, value in document.items()
    ):
        raw = document
    else:
        _fail("manifest_shape_invalid")
    entries: dict[str, str] = {}
    for path, digest in raw.items():
        if not isinstance(path, str) or not isinstance(digest, str):
            _fail("manifest_entry_invalid")
        if _SHA256.fullmatch(digest) is None:
            _fail("manifest_digest_invalid")
        entries[path] = digest
    if not entries:
        _fail("manifest_empty")
    return entries


def _utc_timestamp(raw: object, code: str) -> datetime:
    if not isinstance(raw, str):
        _fail(code)
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        _fail(code)
    return value


def _validate_duration(report: dict[str, Any]) -> None:
    if not {"started_at", "finished_at", "duration_seconds"} <= set(report):
        return
    started = _utc_timestamp(report["started_at"], "report_started_at_invalid")
    finished = _utc_timestamp(report["finished_at"], "report_finished_at_invalid")
    duration = report["duration_seconds"]
    if not isinstance(duration, (int, float)) or isinstance(duration, bool):
        _fail("report_duration_invalid")
    if finished < started or abs((finished - started).total_seconds() - float(duration)) > 0.01:
        _fail("report_duration_mismatch")


def _expect(report: dict[str, Any], path: tuple[str, ...], expected: object, code: str) -> None:
    value: object = report
    for field in path:
        value = _object(value, code).get(field)
    if not _exact_json_value(value, expected):
        _fail(code)


def _exact_json_value(value: object, expected: object) -> bool:
    if type(value) is not type(expected):
        return False
    if isinstance(expected, list):
        if not isinstance(value, list) or len(value) != len(expected):
            return False
        return all(
            _exact_json_value(item, wanted)
            for item, wanted in zip(value, expected, strict=True)
        )
    if isinstance(expected, dict):
        if not isinstance(value, dict) or set(value) != set(expected):
            return False
        return all(_exact_json_value(value[key], wanted) for key, wanted in expected.items())
    return value == expected


def _validate_semantics(name: str, report: dict[str, Any]) -> None:
    if name == "full_connected":
        for field, expected in {
            "device_model": _M52_MODEL,
            "application_id": _QA_APPLICATION_ID,
            "tests": 234,
            "passed": 230,
            "failures": 0,
            "errors": 0,
            "skipped": 4,
        }.items():
            _expect(report, (field,), expected, "full_connected_evidence_mismatch")
    elif name == "playback_process_death":
        _expect(report, ("execution_target",), "physical_qa_side_by_side", "process_death_mismatch")
        _expect(report, ("package_identities_verified",), True, "process_death_mismatch")
        _expect(report, ("verified_process_boundary",), True, "process_death_mismatch")
    elif name == "sync_coordinator":
        _expect(report, ("tests",), 23, "sync_coordinator_mismatch")
        _expect(report, ("failures",), 0, "sync_coordinator_mismatch")
        _expect(report, ("skipped",), 0, "sync_coordinator_mismatch")
    elif name == "m5b_profile_pairing":
        _expect(report, ("durable_counts", "devices"), 2, "m5b_evidence_mismatch")
        _expect(report, ("durable_counts", "revoked_sessions"), 1, "m5b_evidence_mismatch")
        _expect(report, ("durable_counts", "security_audit_events"), 3, "m5b_evidence_mismatch")
    elif name == "p14_android_server":
        for field, expected in {
            "device_event_inbox": 1,
            "device_sync_cursor": 2,
            "sync_event": 1,
            "user_track_ref": 1,
        }.items():
            _expect(report, ("server_counts", field), expected, "p14_evidence_mismatch")
        recovery = report.get("process_recovery")
        if not isinstance(recovery, list) or len(recovery) != 3:
            _fail("p14_evidence_mismatch")
    elif name == "self_pairing_roles":
        _expect(report, ("roles",), ["OWNER", "ADMIN", "USER"], "role_pairing_mismatch")
        _expect(report, ("android_binding_commits_verified",), 3, "role_pairing_mismatch")
        for field, expected in {
            "exchanged_ceremonies": 3,
            "active_devices": 6,
            "self_pairing_audit_events": 12,
        }.items():
            _expect(report, ("durable_counts", field), expected, "role_pairing_mismatch")
        for field, expected in {
            "force_stop_issued": True,
            "process_exit_confirmed": True,
            "fresh_instrumentation_resume": True,
            "encrypted_pending_journal_replayed": True,
            "replayed_owner_binding_materialized": True,
            "committed_success_replies_dropped": 1,
            "owner_exchange_attempts": 2,
            "all_role_exchange_attempts": 4,
        }.items():
            _expect(
                report, ("lost_exchange_process_death", field), expected, "role_pairing_mismatch"
            )
        _expect(report, ("recovery_authority", "generation"), 1, "role_pairing_mismatch")
        _expect(report, ("recovery_authority", "operations"), 0, "role_pairing_mismatch")
        _expect(report, ("recovery_authority", "verifier_unchanged"), True, "role_pairing_mismatch")
    elif name == "account_recovery":
        _expect(report, ("authority_generation",), 2, "account_recovery_mismatch")
        _expect(report, ("recovery_generation",), 2, "account_recovery_mismatch")
        _expect(report, ("lost_reply", "committed_replies_dropped"), 1, "account_recovery_mismatch")
        _expect(report, ("lost_reply", "outcome_calls"), 1, "account_recovery_mismatch")
        for field, expected in {
            "active_devices": 1,
            "active_application_sessions": 1,
            "revoked_devices": 2,
            "revoked_application_sessions": 2,
            "revoked_browser_sessions": 1,
            "revoked_passkeys": 1,
            "removed_trusted_keys": 1,
            "recovery_operations": 1,
            "recovery_audit_events": 1,
        }.items():
            _expect(report, ("durable_counts", field), expected, "account_recovery_mismatch")
    elif name == "account_deletion":
        _expect(report, ("deletion_request", "state"), "CANCELLED", "account_deletion_mismatch")
        _expect(report, ("deletion_request", "revision"), 2, "account_deletion_mismatch")
        _expect(report, ("deletion_request", "cancel_window_days"), 30, "account_deletion_mismatch")
        _expect(
            report,
            ("independent_ledger", "cancellation_recorded"),
            True,
            "account_deletion_mismatch",
        )
        _expect(
            report, ("transport_evidence", "cancel_replies_dropped"), 1, "account_deletion_mismatch"
        )
        _expect(
            report, ("transport_evidence", "cancel_outcome_calls"), 1, "account_deletion_mismatch"
        )
        _expect(
            report,
            ("transport_evidence", "suspension", "active_devices"),
            0,
            "account_deletion_mismatch",
        )
        _expect(
            report,
            ("transport_evidence", "suspension", "active_sessions"),
            0,
            "account_deletion_mismatch",
        )
        _expect(report, ("active_owner_count",), 2, "account_deletion_mismatch")
    elif name == "training_consent":
        _expect(
            report,
            ("operations", "decisions"),
            ["GRANTED", "WITHDRAWN"],
            "training_consent_mismatch",
        )
        _expect(report, ("operations", "revisions"), [1, 2], "training_consent_mismatch")
        _expect(report, ("policy", "decision"), "WITHDRAWN", "training_consent_mismatch")
        _expect(report, ("policy", "revision"), 2, "training_consent_mismatch")
        _expect(
            report, ("lost_reply", "successful_replies_dropped"), 1, "training_consent_mismatch"
        )
        _expect(
            report,
            ("independent_ledger", "latest_decision"),
            "WITHDRAWN",
            "training_consent_mismatch",
        )
    elif name == "self_pairing_device_quota":
        _expect(report, ("refusal", "configured_device_limit"), 5, "device_quota_mismatch")
        _expect(report, ("refusal", "exact_exchange_attempts"), 2, "device_quota_mismatch")
        _expect(
            report,
            ("refusal", "safe_code"),
            "account_device_limit_reached",
            "device_quota_mismatch",
        )
        _expect(report, ("durable_counts", "active_devices"), 5, "device_quota_mismatch")
        _expect(report, ("durable_counts", "active_sessions"), 5, "device_quota_mismatch")
        _expect(report, ("process_death", "process_exit_confirmed"), True, "device_quota_mismatch")
        _expect(report, ("ui", "locales"), ["en", "ru"], "device_quota_mismatch")
        _expect(report, ("ui", "pending_retry_action"), True, "device_quota_mismatch")
    else:
        _fail("evidence_spec_unknown")


def _inventory(
    root: Path,
    *,
    excluded: frozenset[Path],
    raw_serial: bytes,
) -> tuple[dict[str, str], int, tuple[str, ...]]:
    files: dict[str, str] = {}
    total_bytes = 0
    empty_placeholders: list[str] = []
    for path in sorted(root.rglob("*")):
        if path in excluded:
            continue
        if path.is_symlink():
            _fail("evidence_symlink_forbidden")
        if not path.is_file():
            continue
        size = path.stat().st_size
        relative = _relative_path(root, path)
        if size == 0:
            if relative not in _KNOWN_EMPTY_PLACEHOLDERS:
                _fail("unexpected_empty_evidence")
            empty_placeholders.append(relative)
        elif size > MAX_EVIDENCE_BYTES:
            _fail("evidence_size_invalid")
        payload = path.read_bytes()
        if raw_serial in payload:
            _fail("raw_device_serial_present")
        files[relative] = hashlib.sha256(payload).hexdigest()
        total_bytes += size
    if not files:
        _fail("evidence_inventory_empty")
    return files, total_bytes, tuple(empty_placeholders)


def _validate_production_package(
    report: dict[str, Any],
    current: dict[str, object],
) -> None:
    package = _object(report["production_package"], "production_package_invalid")
    if package.get("installed") is not True or package.get("unchanged") is not True:
        _fail("production_package_changed")
    if package.get("application_id", _PRODUCTION_APPLICATION_ID) != _PRODUCTION_APPLICATION_ID:
        _fail("production_package_identity_mismatch")
    for field in ("version_code", "version_name", "last_update_time"):
        if package.get(field) != current.get(field):
            _fail("production_package_fingerprint_mismatch")


def audit(root: Path, output: Path, *, device: DeviceState) -> dict[str, object]:
    evidence_root = root.resolve(strict=True)
    if root.is_symlink() or not evidence_root.is_dir():
        _fail("evidence_root_invalid")
    resolved_output = output.resolve(strict=False)
    if resolved_output.parent != evidence_root:
        _fail("audit_output_outside_root")
    manifest_output = resolved_output.with_suffix(".sha256.json")
    excluded = frozenset({resolved_output, manifest_output})
    if device.model != _M52_MODEL:
        _fail("connected_device_not_m52")
    if not device.serial:
        _fail("connected_device_serial_missing")
    if device.adb_reverse_entries != 0 or device.qa_process_running:
        _fail("android_cleanup_incomplete")
    if device.disposable_docker_resources != 0:
        _fail("docker_cleanup_incomplete")
    expected_device_digest = hashlib.sha256(device.serial.encode("utf-8")).hexdigest()
    inventory_before, total_bytes, empty_placeholders = _inventory(
        evidence_root,
        excluded=excluded,
        raw_serial=device.serial.encode("utf-8"),
    )

    reports: list[dict[str, object]] = []
    referenced: set[str] = set()
    production_reports = 0
    for spec in EVIDENCE_SPECS:
        manifest = evidence_root / spec.manifest_path
        report_path = evidence_root / spec.report_path
        manifest_document = _read_json(manifest)
        entries = _manifest_entries(manifest_document)
        manifest_artifacts: set[str] = set()
        for raw_path, expected_hash in entries.items():
            relative, artifact = _safe_manifest_artifact(evidence_root, manifest, raw_path)
            if relative in referenced:
                _fail("manifest_artifact_duplicate")
            referenced.add(relative)
            manifest_artifacts.add(relative)
            if (
                inventory_before.get(relative) != expected_hash
                or _sha256(artifact) != expected_hash
            ):
                _fail("manifest_artifact_hash_mismatch")
        canonical_report = _relative_path(evidence_root, report_path)
        if canonical_report not in manifest_artifacts:
            _fail("canonical_report_not_manifested")
        report = _read_json(report_path)
        if (
            type(report.get("schema_version")) is not int
            or report["schema_version"] != 1
            or report.get("status") != spec.report_status
        ):
            _fail("report_header_invalid")
        if report.get("device_serial_sha256") != expected_device_digest:
            _fail("report_device_identity_mismatch")
        if (
            report.get("credentials_persisted") is not None
            and report["credentials_persisted"] is not False
        ):
            _fail("report_credentials_persisted")
        _validate_duration(report)
        _validate_semantics(spec.name, report)
        if "production_package" in report:
            _validate_production_package(report, device.production_package)
            production_reports += 1
        reports.append(
            {
                "name": spec.name,
                "manifest": spec.manifest_path,
                "report": spec.report_path,
                "report_sha256": inventory_before[canonical_report],
                "status": spec.report_status,
            }
        )

    expected_production_reports = sum(spec.name in _PRODUCTION_REPORTS for spec in EVIDENCE_SPECS)
    if production_reports != expected_production_reports:
        _fail("production_fingerprint_coverage_incomplete")
    inventory_after, total_after, empty_after = _inventory(
        evidence_root,
        excluded=excluded,
        raw_serial=device.serial.encode("utf-8"),
    )
    if (
        inventory_after != inventory_before
        or total_after != total_bytes
        or empty_after != empty_placeholders
    ):
        _fail("evidence_changed_during_audit")

    result: dict[str, object] = {
        "schema_version": 1,
        "status": _SUPPLEMENTAL_PASS,
        "audited_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "device": {
            "model": device.model,
            "serial_sha256": expected_device_digest,
        },
        "checks": {
            "canonical_manifests_verified": len(EVIDENCE_SPECS),
            "canonical_reports_verified": len(reports),
            "device_digest_consistent": True,
            "raw_serial_absent": True,
            "production_package_unchanged": True,
            "adb_reverse_entries": device.adb_reverse_entries,
            "qa_process_running": device.qa_process_running,
            "disposable_docker_resources": device.disposable_docker_resources,
        },
        "production_package": device.production_package,
        "reports": reports,
        "inventory": {
            "file_count": len(inventory_before),
            "total_bytes": total_bytes,
            "referenced_artifact_count": len(referenced),
            "non_evidence_empty_placeholders": list(empty_placeholders),
            "files_sha256": inventory_before,
        },
        "limitation": "Supplemental M52 evidence does not satisfy the required A55 target gate.",
    }
    resolved_output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    report_hash = _sha256(resolved_output)
    manifest_output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": _SUPPLEMENTAL_PASS,
                "files": {resolved_output.name: report_hash},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {**result, "report_sha256": report_hash}


def _run(arguments: list[str]) -> str:
    result = subprocess.run(arguments, check=True, capture_output=True, text=True)
    return result.stdout


def _production_fingerprint(adb: Path, serial: str) -> dict[str, object]:
    payload = _run(
        [str(adb), "-s", serial, "shell", "dumpsys", "package", _PRODUCTION_APPLICATION_ID]
    )
    if "Unable to find package" in payload:
        _fail("production_package_missing")

    def value(pattern: str) -> str:
        match = re.search(pattern, payload)
        if match is None:
            _fail("production_package_fingerprint_incomplete")
        return match.group(1).strip()

    return {
        "application_id": _PRODUCTION_APPLICATION_ID,
        "installed": True,
        "version_code": value(r"versionCode=(\d+)"),
        "version_name": value(r"versionName=([^\r\n]+)"),
        "last_update_time": value(r"lastUpdateTime=([^\r\n]+)"),
    }


def _disposable_resource_count(names: list[str]) -> int:
    return sum(any(marker in name for marker in _DISPOSABLE_MARKERS) for name in names)


def _device_state(adb: Path, docker: str) -> DeviceState:
    devices = []
    for line in _run([str(adb), "devices"]).splitlines()[1:]:
        match = re.fullmatch(r"([^\s]+)\s+device", line.strip())
        if match is not None:
            devices.append(match.group(1))
    if len(devices) != 1:
        _fail("connected_device_count_invalid")
    serial = devices[0]
    model = _run([str(adb), "-s", serial, "shell", "getprop", "ro.product.model"]).strip()
    reverse_entries = len(
        [
            line
            for line in _run([str(adb), "-s", serial, "reverse", "--list"]).splitlines()
            if line.strip()
        ]
    )
    qa_process = bool(
        subprocess.run(
            [str(adb), "-s", serial, "shell", "pidof", _QA_APPLICATION_ID],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )
    docker_names: list[str] = []
    for command in (
        [docker, "ps", "-a", "--format", "{{.Names}}"],
        [docker, "network", "ls", "--format", "{{.Name}}"],
        [docker, "volume", "ls", "--format", "{{.Name}}"],
    ):
        docker_names.extend(line.strip() for line in _run(command).splitlines() if line.strip())
    disposable = _disposable_resource_count(docker_names)
    return DeviceState(
        serial=serial,
        model=model,
        production_package=_production_fingerprint(adb, serial),
        adb_reverse_entries=reverse_entries,
        qa_process_running=qa_process,
        disposable_docker_resources=disposable,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    output = arguments.output or arguments.evidence_root / "M52_EVIDENCE_AUDIT.json"
    try:
        result = audit(
            arguments.evidence_root,
            output,
            device=_device_state(arguments.adb, arguments.docker),
        )
    except (EvidenceAuditError, OSError, subprocess.SubprocessError) as error:
        code = error.code if isinstance(error, EvidenceAuditError) else "environment_io_error"
        print(json.dumps({"status": "INVALID", "error": code}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "status": result["status"],
                "reports": len(EVIDENCE_SPECS),
                "report_sha256": result["report_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
