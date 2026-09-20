"""Audit the complete physical Samsung Galaxy A55 Android evidence set."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import admin_m52_evidence_audit as shared

MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_BYTES = 256 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_PASS = "PASS"
_AUDIT_PASS = "PASS_A55_PHYSICAL_ANDROID_EVIDENCE_NOT_TARGET_GATE"
_A55_MODEL = "SM-A556E"
_PRODUCTION_APPLICATION_ID = "app.autplay"
_QA_APPLICATION_ID = "app.autplay.qa"
_EXPECTED_VERSION_CODE = "11"
_EXPECTED_VERSION_NAME = "0.3.7-metadata"
_INSTALL_REPORT = "A55_REVIEWED_INSTALL.json"
_INSTALL_MANIFEST = "A55_REVIEWED_INSTALL.sha256.json"
_INSTALL_APK = "AUTPLAY_A55_REVIEWED_DEBUG_V11.apk"

EvidenceAuditError = shared.EvidenceAuditError


@dataclass(frozen=True, slots=True)
class EvidenceSpec:
    name: str
    manifest_path: str
    report_path: str
    artifact_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeviceState:
    serial: str
    model: str
    build_fingerprint: str
    security_patch: str
    production_package: dict[str, object]
    installed_apk_sha256: str
    installed_signer_certificate_sha256: str
    adb_reverse_entries: int
    qa_process_running: bool
    disposable_docker_resources: int


EVIDENCE_SPECS = (
    EvidenceSpec(
        "self_pairing_roles",
        "SELF_PAIRING_ROLES_ANDROID_E2E.sha256.json",
        "SELF_PAIRING_ROLES_ANDROID_E2E.json",
        ("SELF_PAIRING_ROLES_ANDROID_E2E.json",),
    ),
    EvidenceSpec(
        "account_recovery",
        "ACCOUNT_RECOVERY_ANDROID_E2E.sha256.json",
        "ACCOUNT_RECOVERY_ANDROID_E2E.json",
        ("ACCOUNT_RECOVERY_ANDROID_E2E.json",),
    ),
    EvidenceSpec(
        "account_deletion",
        "ACCOUNT_DELETION_ANDROID_E2E.sha256.json",
        "ACCOUNT_DELETION_ANDROID_E2E.json",
        ("ACCOUNT_DELETION_ANDROID_E2E.json",),
    ),
    EvidenceSpec(
        "training_consent",
        "TRAINING_CONSENT_ANDROID_E2E.sha256.json",
        "TRAINING_CONSENT_ANDROID_E2E.json",
        ("TRAINING_CONSENT_ANDROID_E2E.json",),
    ),
    EvidenceSpec(
        "self_pairing_device_quota",
        "SELF_PAIRING_DEVICE_QUOTA_ANDROID_E2E.sha256.json",
        "SELF_PAIRING_DEVICE_QUOTA_ANDROID_E2E.json",
        (
            "SELF_PAIRING_DEVICE_QUOTA_ANDROID_E2E.json",
            "self-pairing-device-quota-ui/self-pairing-quota-en-dark.png",
            "self-pairing-device-quota-ui/self-pairing-quota-ru-dark.png",
        ),
    ),
)


def _fail(code: str) -> NoReturn:
    raise EvidenceAuditError(code)


def _read_json(path: Path) -> dict[str, Any]:
    return shared._read_json(path)


def _sha256(path: Path) -> str:
    return shared._sha256(path)


def _object(value: object, code: str) -> dict[str, Any]:
    return shared._object(value, code)


def _manifest_entries(document: dict[str, Any]) -> dict[str, str]:
    if set(document) == {"schema_version", "files"}:
        pass
    elif set(document) == {"schema_version", "status", "files"}:
        if document["status"] != _PASS:
            _fail("manifest_header_invalid")
    else:
        _fail("manifest_shape_invalid")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        _fail("manifest_header_invalid")
    raw = _object(document["files"], "manifest_files_invalid")
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


def _inventory(
    root: Path,
    *,
    excluded: frozenset[Path],
    raw_serial: bytes,
) -> tuple[dict[str, str], int]:
    files: dict[str, str] = {}
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if path in excluded:
            continue
        if path.is_symlink():
            _fail("evidence_symlink_forbidden")
        if not path.is_file():
            continue
        size = path.stat().st_size
        if not 1 <= size <= MAX_EVIDENCE_BYTES:
            _fail("evidence_size_invalid")
        relative = shared._relative_path(root, path)
        payload = path.read_bytes()
        if raw_serial in payload:
            _fail("raw_device_serial_present")
        files[relative] = hashlib.sha256(payload).hexdigest()
        total_bytes += size
    if not files:
        _fail("evidence_inventory_empty")
    return files, total_bytes


def _validate_sha256(value: object, code: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail(code)
    return value


def _validate_current_device(device: DeviceState) -> tuple[str, str]:
    if device.model != _A55_MODEL:
        _fail("connected_device_not_a55")
    if not device.serial:
        _fail("connected_device_serial_missing")
    if not device.build_fingerprint or not device.security_patch:
        _fail("connected_device_build_incomplete")
    if device.adb_reverse_entries != 0 or device.qa_process_running:
        _fail("android_cleanup_incomplete")
    if device.disposable_docker_resources != 0:
        _fail("docker_cleanup_incomplete")
    package = device.production_package
    expected_package: dict[str, object] = {
        "application_id": _PRODUCTION_APPLICATION_ID,
        "installed": True,
        "version_code": _EXPECTED_VERSION_CODE,
        "version_name": _EXPECTED_VERSION_NAME,
    }
    for key, expected in expected_package.items():
        if type(package.get(key)) is not type(expected) or package.get(key) != expected:
            _fail("production_package_fingerprint_mismatch")
    if not isinstance(package.get("last_update_time"), str) or not package["last_update_time"]:
        _fail("production_package_fingerprint_incomplete")
    installed_apk = _validate_sha256(device.installed_apk_sha256, "installed_apk_digest_invalid")
    installed_signer = _validate_sha256(
        device.installed_signer_certificate_sha256,
        "installed_signer_digest_invalid",
    )
    return installed_apk, installed_signer


def _validate_install_report(
    report: dict[str, Any],
    *,
    device: DeviceState,
    expected_device_digest: str,
    expected_build_digest: str,
    inventory: dict[str, str],
) -> None:
    expected_fields = {
        "schema_version",
        "status",
        "started_at",
        "finished_at",
        "duration_seconds",
        "device_model",
        "device_serial_sha256",
        "device_build_fingerprint_sha256",
        "security_patch",
        "clean_install",
        "preexisting_production_package",
        "preexisting_qa_package",
        "application_id",
        "version_code",
        "version_name",
        "last_update_time",
        "apk_file",
        "apk_sha256",
        "signer_certificate_sha256",
        "credentials_persisted",
    }
    if set(report) != expected_fields:
        _fail("install_report_shape_invalid")
    exact_values: dict[str, object] = {
        "schema_version": 1,
        "status": _PASS,
        "device_model": _A55_MODEL,
        "device_serial_sha256": expected_device_digest,
        "device_build_fingerprint_sha256": expected_build_digest,
        "security_patch": device.security_patch,
        "clean_install": True,
        "preexisting_production_package": False,
        "preexisting_qa_package": False,
        "application_id": _PRODUCTION_APPLICATION_ID,
        "version_code": int(_EXPECTED_VERSION_CODE),
        "version_name": _EXPECTED_VERSION_NAME,
        "last_update_time": device.production_package["last_update_time"],
        "apk_file": _INSTALL_APK,
        "apk_sha256": device.installed_apk_sha256,
        "signer_certificate_sha256": device.installed_signer_certificate_sha256,
        "credentials_persisted": False,
    }
    for key, expected in exact_values.items():
        if type(report.get(key)) is not type(expected) or report.get(key) != expected:
            _fail("install_report_mismatch")
    shared._validate_duration(report)
    if inventory.get(_INSTALL_APK) != report["apk_sha256"]:
        _fail("install_apk_hash_mismatch")


def _validate_report(
    spec: EvidenceSpec,
    report: dict[str, Any],
    *,
    device: DeviceState,
    expected_device_digest: str,
    inventory: dict[str, str],
) -> dict[str, str]:
    if (
        type(report.get("schema_version")) is not int
        or report["schema_version"] != 1
        or report.get("status") != _PASS
    ):
        _fail("report_header_invalid")
    if report.get("device_serial_sha256") != expected_device_digest:
        _fail("report_device_identity_mismatch")
    if report.get("credentials_persisted") is not False:
        _fail("report_credentials_persisted")
    shared._validate_duration(report)
    shared._validate_semantics(spec.name, report)
    shared._validate_production_package(report, device.production_package)
    apk_hashes = _object(report.get("apk_sha256"), "report_apk_hashes_invalid")
    if set(apk_hashes) != {"qa_apk", "qa_test_apk"}:
        _fail("report_apk_hashes_invalid")
    validated = {
        key: _validate_sha256(value, "report_apk_hashes_invalid")
        for key, value in apk_hashes.items()
    }
    if spec.name == "self_pairing_device_quota":
        ui = _object(report.get("ui"), "device_quota_mismatch")
        screenshots = _object(ui.get("screenshots_sha256"), "device_quota_mismatch")
        expected_screenshots = set(spec.artifact_paths) - {spec.report_path}
        if set(screenshots) != expected_screenshots:
            _fail("device_quota_screenshot_mismatch")
        for path, digest in screenshots.items():
            if inventory.get(path) != digest:
                _fail("device_quota_screenshot_mismatch")
    return validated


def _verify_manifest(
    root: Path,
    manifest_relative: str,
    expected_artifacts: tuple[str, ...],
    inventory: dict[str, str],
) -> set[str]:
    manifest = root / manifest_relative
    entries = _manifest_entries(_read_json(manifest))
    artifacts: set[str] = set()
    for raw_path, expected_hash in entries.items():
        relative, artifact = shared._safe_manifest_artifact(root, manifest, raw_path)
        artifacts.add(relative)
        if inventory.get(relative) != expected_hash or _sha256(artifact) != expected_hash:
            _fail("manifest_artifact_hash_mismatch")
    if artifacts != set(expected_artifacts):
        _fail("manifest_artifact_set_mismatch")
    return artifacts


def audit(root: Path, output: Path, *, device: DeviceState) -> dict[str, object]:
    evidence_root = root.resolve(strict=True)
    if root.is_symlink() or not evidence_root.is_dir():
        _fail("evidence_root_invalid")
    resolved_output = output.resolve(strict=False)
    if resolved_output.parent != evidence_root:
        _fail("audit_output_outside_root")
    manifest_output = resolved_output.with_suffix(".sha256.json")
    excluded = frozenset({resolved_output, manifest_output})
    installed_apk, installed_signer = _validate_current_device(device)
    expected_device_digest = hashlib.sha256(device.serial.encode("utf-8")).hexdigest()
    expected_build_digest = hashlib.sha256(device.build_fingerprint.encode("utf-8")).hexdigest()
    inventory_before, total_bytes = _inventory(
        evidence_root,
        excluded=excluded,
        raw_serial=device.serial.encode("utf-8"),
    )

    install_artifacts = _verify_manifest(
        evidence_root,
        _INSTALL_MANIFEST,
        (_INSTALL_REPORT, _INSTALL_APK),
        inventory_before,
    )
    install_report = _read_json(evidence_root / _INSTALL_REPORT)
    _validate_install_report(
        install_report,
        device=device,
        expected_device_digest=expected_device_digest,
        expected_build_digest=expected_build_digest,
        inventory=inventory_before,
    )

    reports: list[dict[str, object]] = []
    referenced = set(install_artifacts)
    qa_apk_hashes: dict[str, str] | None = None
    for spec in EVIDENCE_SPECS:
        artifacts = _verify_manifest(
            evidence_root,
            spec.manifest_path,
            spec.artifact_paths,
            inventory_before,
        )
        if referenced.intersection(artifacts):
            _fail("manifest_artifact_duplicate")
        referenced.update(artifacts)
        report = _read_json(evidence_root / spec.report_path)
        current_apk_hashes = _validate_report(
            spec,
            report,
            device=device,
            expected_device_digest=expected_device_digest,
            inventory=inventory_before,
        )
        if qa_apk_hashes is None:
            qa_apk_hashes = current_apk_hashes
        elif current_apk_hashes != qa_apk_hashes:
            _fail("report_apk_hashes_inconsistent")
        reports.append(
            {
                "name": spec.name,
                "manifest": spec.manifest_path,
                "report": spec.report_path,
                "report_sha256": inventory_before[spec.report_path],
                "status": _PASS,
            }
        )

    manifest_paths = {_INSTALL_MANIFEST, *(spec.manifest_path for spec in EVIDENCE_SPECS)}
    allowed_files = manifest_paths | referenced
    if set(inventory_before) != allowed_files:
        _fail("unmanifested_or_unexpected_evidence")
    inventory_after, total_after = _inventory(
        evidence_root,
        excluded=excluded,
        raw_serial=device.serial.encode("utf-8"),
    )
    if inventory_after != inventory_before or total_after != total_bytes:
        _fail("evidence_changed_during_audit")

    result: dict[str, object] = {
        "schema_version": 1,
        "status": _AUDIT_PASS,
        "audited_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "device": {
            "model": device.model,
            "serial_sha256": expected_device_digest,
            "build_fingerprint_sha256": expected_build_digest,
            "security_patch": device.security_patch,
        },
        "checks": {
            "clean_install_verified": True,
            "canonical_manifests_verified": 1 + len(EVIDENCE_SPECS),
            "canonical_reports_verified": len(reports),
            "device_digest_consistent": True,
            "raw_serial_absent": True,
            "installed_apk_matches_reviewed_apk": installed_apk == inventory_before[_INSTALL_APK],
            "installed_signer_matches_reviewed_signer": installed_signer
            == install_report["signer_certificate_sha256"],
            "production_package_unchanged": True,
            "adb_reverse_entries": device.adb_reverse_entries,
            "qa_process_running": device.qa_process_running,
            "disposable_docker_resources": device.disposable_docker_resources,
        },
        "production_package": device.production_package,
        "reviewed_install": {
            "manifest": _INSTALL_MANIFEST,
            "report": _INSTALL_REPORT,
            "apk": _INSTALL_APK,
            "report_sha256": inventory_before[_INSTALL_REPORT],
            "apk_sha256": inventory_before[_INSTALL_APK],
            "signer_certificate_sha256": install_report["signer_certificate_sha256"],
        },
        "qa_apk_sha256": qa_apk_hashes,
        "reports": reports,
        "inventory": {
            "file_count": len(inventory_before),
            "total_bytes": total_bytes,
            "referenced_artifact_count": len(referenced),
            "files_sha256": inventory_before,
        },
        "limitation": (
            "This proves the reviewed production-ID install and joined physical Android "
            "runtime scenarios on the A55. It does not by itself prove the exact target "
            "server identity, Windows Hello plus A55 platform-passkey ceremonies, Android "
            "system-picker interaction, target-host resource measurements, or isolated "
            "backup restore required by the unified acceptance gates."
        ),
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
                "status": _AUDIT_PASS,
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
    return shared._run(arguments)


def _installed_apk_state(adb: Path, apksigner: Path, serial: str) -> tuple[str, str]:
    package_paths = [
        line.removeprefix("package:").strip()
        for line in _run(
            [str(adb), "-s", serial, "shell", "pm", "path", _PRODUCTION_APPLICATION_ID]
        ).splitlines()
        if line.startswith("package:") and line.strip().endswith("/base.apk")
    ]
    if len(package_paths) != 1:
        _fail("installed_apk_path_invalid")
    with tempfile.TemporaryDirectory(prefix="autplay-a55-audit-") as temporary:
        local_apk = Path(temporary) / "base.apk"
        subprocess.run(
            [str(adb), "-s", serial, "pull", package_paths[0], str(local_apk)],
            check=True,
            capture_output=True,
            text=True,
        )
        signature = _run([str(apksigner), "verify", "--print-certs", str(local_apk)])
        match = re.search(r"Signer #1 certificate SHA-256 digest:\s*([0-9a-fA-F]{64})", signature)
        if match is None:
            _fail("installed_signer_digest_missing")
        return _sha256(local_apk), match.group(1).lower()


def _device_state(adb: Path, docker: str, apksigner: Path) -> DeviceState:
    devices = []
    for line in _run([str(adb), "devices"]).splitlines()[1:]:
        match = re.fullmatch(r"([^\s]+)\s+device", line.strip())
        if match is not None:
            devices.append(match.group(1))
    if len(devices) != 1:
        _fail("connected_device_count_invalid")
    serial = devices[0]
    model = _run([str(adb), "-s", serial, "shell", "getprop", "ro.product.model"]).strip()
    build_fingerprint = _run(
        [str(adb), "-s", serial, "shell", "getprop", "ro.build.fingerprint"]
    ).strip()
    security_patch = _run(
        [str(adb), "-s", serial, "shell", "getprop", "ro.build.version.security_patch"]
    ).strip()
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
    installed_apk_sha256, installed_signer = _installed_apk_state(adb, apksigner, serial)
    return DeviceState(
        serial=serial,
        model=model,
        build_fingerprint=build_fingerprint,
        security_patch=security_patch,
        production_package=shared._production_fingerprint(adb, serial),
        installed_apk_sha256=installed_apk_sha256,
        installed_signer_certificate_sha256=installed_signer,
        adb_reverse_entries=reverse_entries,
        qa_process_running=qa_process,
        disposable_docker_resources=shared._disposable_resource_count(docker_names),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--adb", type=Path, required=True)
    parser.add_argument("--apksigner", type=Path, required=True)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    output = arguments.output or arguments.evidence_root / "A55_EVIDENCE_AUDIT.json"
    try:
        result = audit(
            arguments.evidence_root,
            output,
            device=_device_state(arguments.adb, arguments.docker, arguments.apksigner),
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
