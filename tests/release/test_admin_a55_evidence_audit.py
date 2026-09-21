from __future__ import annotations

import hashlib
import importlib
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest


def _load_tool() -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository_root))
    try:
        return importlib.import_module("scripts.admin_a55_evidence_audit")
    finally:
        sys.path.remove(str(repository_root))


tool = _load_tool()
SERIAL = "private-a55-device"
BUILD = "samsung/a55/test-build"
APK = b"reviewed production apk"
APK_SHA256 = hashlib.sha256(APK).hexdigest()
SIGNER_SHA256 = hashlib.sha256(b"signer").hexdigest()


def _device() -> Any:
    return tool.DeviceState(
        serial=SERIAL,
        model="SM-A556E",
        build_fingerprint=BUILD,
        security_patch="2026-08-05",
        production_package={
            "application_id": "app.autplay",
            "installed": True,
            "version_code": "11",
            "version_name": "0.3.7-metadata",
            "last_update_time": "2026-09-19 18:25:43",
        },
        installed_apk_sha256=APK_SHA256,
        installed_signer_certificate_sha256=SIGNER_SHA256,
        adb_reverse_entries=0,
        qa_process_running=False,
        disposable_docker_resources=0,
    )


def _write_manifest(root: Path, name: str, files: tuple[str, ...], *, status: bool = False) -> None:
    document: dict[str, object] = {
        "schema_version": 1,
        "files": {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in files},
    }
    if status:
        document["status"] = "PASS"
    (root / name).write_text(json.dumps(document, sort_keys=True), encoding="utf-8")


def _write_fixture(root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    apk = root / "AUTPLAY_A55_REVIEWED_DEBUG_V11.apk"
    apk.write_bytes(APK)
    install_report = root / "A55_REVIEWED_INSTALL.json"
    install_report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS",
                "started_at": "2026-09-19T15:25:39Z",
                "finished_at": "2026-09-19T15:25:43Z",
                "duration_seconds": 4.0,
                "device_model": "SM-A556E",
                "device_serial_sha256": hashlib.sha256(SERIAL.encode()).hexdigest(),
                "device_build_fingerprint_sha256": hashlib.sha256(BUILD.encode()).hexdigest(),
                "security_patch": "2026-08-05",
                "clean_install": True,
                "preexisting_production_package": False,
                "preexisting_qa_package": False,
                "application_id": "app.autplay",
                "version_code": 11,
                "version_name": "0.3.7-metadata",
                "last_update_time": "2026-09-19 18:25:43",
                "apk_file": apk.name,
                "apk_sha256": APK_SHA256,
                "signer_certificate_sha256": SIGNER_SHA256,
                "credentials_persisted": False,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    _write_manifest(
        root,
        "A55_REVIEWED_INSTALL.sha256.json",
        (install_report.name, apk.name),
        status=True,
    )

    report = root / "TRAINING_CONSENT_ANDROID_E2E.json"
    report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS",
                "started_at": "2026-09-19T15:29:03Z",
                "finished_at": "2026-09-19T15:29:30Z",
                "duration_seconds": 27.0,
                "device_serial_sha256": hashlib.sha256(SERIAL.encode()).hexdigest(),
                "credentials_persisted": False,
                "apk_sha256": {
                    "qa_apk": hashlib.sha256(b"qa").hexdigest(),
                    "qa_test_apk": hashlib.sha256(b"qa-test").hexdigest(),
                },
                "production_package": {
                    "application_id": "app.autplay",
                    "installed": True,
                    "unchanged": True,
                    "version_code": "11",
                    "version_name": "0.3.7-metadata",
                    "last_update_time": "2026-09-19 18:25:43",
                },
                "operations": {
                    "decisions": ["GRANTED", "WITHDRAWN"],
                    "revisions": [1, 2],
                },
                "policy": {"decision": "WITHDRAWN", "revision": 2},
                "lost_reply": {"successful_replies_dropped": 1},
                "independent_ledger": {"latest_decision": "WITHDRAWN"},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    manifest = root / "TRAINING_CONSENT_ANDROID_E2E.sha256.json"
    _write_manifest(root, manifest.name, (report.name,))
    monkeypatch.setattr(
        tool,
        "EVIDENCE_SPECS",
        (
            tool.EvidenceSpec(
                "training_consent",
                manifest.name,
                report.name,
                (report.name,),
            ),
        ),
    )
    return install_report, apk, report


def _rewrite_json(path: Path, document: dict[str, Any]) -> None:
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")


def _rehash_manifest(root: Path, manifest_name: str, artifact_name: str) -> None:
    manifest = root / manifest_name
    document = cast(dict[str, Any], json.loads(manifest.read_text(encoding="utf-8")))
    document["files"][artifact_name] = hashlib.sha256(
        (root / artifact_name).read_bytes()
    ).hexdigest()
    _rewrite_json(manifest, document)


def test_audit_binds_reviewed_install_reports_and_closed_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_fixture(tmp_path, monkeypatch)
    output = tmp_path / "A55_EVIDENCE_AUDIT.json"

    result = tool.audit(tmp_path, output, device=_device())

    assert result["status"] == "PASS_A55_PHYSICAL_ANDROID_EVIDENCE_NOT_TARGET_GATE"
    assert result["checks"]["clean_install_verified"] is True
    assert result["checks"]["installed_apk_matches_reviewed_apk"] is True
    assert result["inventory"]["file_count"] == 5
    assert SERIAL not in output.read_text(encoding="utf-8")
    manifest = cast(
        dict[str, Any],
        json.loads(output.with_suffix(".sha256.json").read_text(encoding="utf-8")),
    )
    assert manifest["files"][output.name] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_audit_rejects_changed_reviewed_apk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, apk, _ = _write_fixture(tmp_path, monkeypatch)
    apk.write_bytes(b"changed")

    with pytest.raises(tool.EvidenceAuditError, match="manifest_artifact_hash_mismatch"):
        tool.audit(tmp_path, tmp_path / "A55_EVIDENCE_AUDIT.json", device=_device())


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("schema_version", True),
        ("version_code", True),
        ("clean_install", 1),
    ),
)
def test_audit_rejects_boolean_and_integer_aliases_in_install_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    install, _, _ = _write_fixture(tmp_path, monkeypatch)
    document = cast(dict[str, Any], json.loads(install.read_text(encoding="utf-8")))
    document[field] = value
    _rewrite_json(install, document)
    _rehash_manifest(tmp_path, "A55_REVIEWED_INSTALL.sha256.json", install.name)

    with pytest.raises(tool.EvidenceAuditError, match="install_report_mismatch"):
        tool.audit(tmp_path, tmp_path / "A55_EVIDENCE_AUDIT.json", device=_device())


def test_audit_rejects_wrong_model_build_and_signer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_fixture(tmp_path, monkeypatch)

    with pytest.raises(tool.EvidenceAuditError, match="connected_device_not_a55"):
        tool.audit(
            tmp_path,
            tmp_path / "A55_EVIDENCE_AUDIT.json",
            device=replace(_device(), model="SM-M556B"),
        )
    with pytest.raises(tool.EvidenceAuditError, match="install_report_mismatch"):
        tool.audit(
            tmp_path,
            tmp_path / "A55_EVIDENCE_AUDIT.json",
            device=replace(_device(), build_fingerprint="different-build"),
        )
    with pytest.raises(tool.EvidenceAuditError, match="install_report_mismatch"):
        tool.audit(
            tmp_path,
            tmp_path / "A55_EVIDENCE_AUDIT.json",
            device=replace(
                _device(),
                installed_signer_certificate_sha256=hashlib.sha256(b"other").hexdigest(),
            ),
        )


def test_audit_rejects_raw_serial_unmanifested_file_and_incomplete_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_fixture(tmp_path, monkeypatch)
    extra = tmp_path / "extra.txt"
    extra.write_text(SERIAL, encoding="utf-8")
    with pytest.raises(tool.EvidenceAuditError, match="raw_device_serial_present"):
        tool.audit(tmp_path, tmp_path / "A55_EVIDENCE_AUDIT.json", device=_device())

    extra.write_text("safe but unmanifested", encoding="utf-8")
    with pytest.raises(tool.EvidenceAuditError, match="unmanifested_or_unexpected_evidence"):
        tool.audit(tmp_path, tmp_path / "A55_EVIDENCE_AUDIT.json", device=_device())

    extra.unlink()
    with pytest.raises(tool.EvidenceAuditError, match="android_cleanup_incomplete"):
        tool.audit(
            tmp_path,
            tmp_path / "A55_EVIDENCE_AUDIT.json",
            device=replace(_device(), adb_reverse_entries=1),
        )
