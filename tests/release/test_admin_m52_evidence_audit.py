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
        return importlib.import_module("scripts.admin_m52_evidence_audit")
    finally:
        sys.path.remove(str(repository_root))


tool = _load_tool()
SERIAL = "private-device"


def _device() -> Any:
    return tool.DeviceState(
        serial=SERIAL,
        model="SM-M526B",
        production_package={
            "application_id": "app.autplay",
            "installed": True,
            "version_code": "11",
            "version_name": "0.3.7-metadata",
            "last_update_time": "2026-09-16 15:12:06",
        },
        adb_reverse_entries=0,
        qa_process_running=False,
        disposable_docker_resources=0,
    )


def _write_sync_fixture(root: Path, *, serial: str = SERIAL) -> tuple[Path, Path]:
    directory = root / "sync-coordinator-current"
    directory.mkdir(parents=True)
    report = directory / "receipt.json"
    report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS_SUPPLEMENTAL_M52_NOT_GATE",
                "tests": 23,
                "failures": 0,
                "skipped": 0,
                "device_serial_sha256": hashlib.sha256(serial.encode()).hexdigest(),
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    instrumentation = directory / "instrumentation.txt"
    instrumentation.write_text("OK (23 tests)\n", encoding="utf-8")
    manifest = directory / "SHA256.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "PASS_SUPPLEMENTAL_M52_NOT_GATE",
                "artifacts": {
                    "receipt.json": hashlib.sha256(report.read_bytes()).hexdigest(),
                    "instrumentation.txt": hashlib.sha256(instrumentation.read_bytes()).hexdigest(),
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return report, manifest


def _single_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tool,
        "EVIDENCE_SPECS",
        (
            tool.EvidenceSpec(
                "sync_coordinator",
                "sync-coordinator-current/SHA256.json",
                "sync-coordinator-current/receipt.json",
                "PASS_SUPPLEMENTAL_M52_NOT_GATE",
            ),
        ),
    )


def _rewrite_sync_report(
    report: Path,
    manifest: Path,
    document: dict[str, Any],
) -> None:
    report.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    manifest_document = cast(
        dict[str, Any],
        json.loads(manifest.read_text(encoding="utf-8")),
    )
    manifest_document["artifacts"]["receipt.json"] = hashlib.sha256(report.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(manifest_document, sort_keys=True), encoding="utf-8")


def test_audit_binds_closed_inventory_and_writes_redacted_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _single_spec(monkeypatch)
    _write_sync_fixture(tmp_path)
    manual = tmp_path / "manual-screen.png"
    manual.write_bytes(b"safe screenshot bytes")
    output = tmp_path / "M52_EVIDENCE_AUDIT.json"

    result = tool.audit(tmp_path, output, device=_device())

    assert result["status"] == "PASS_SUPPLEMENTAL_M52_NOT_GATE"
    assert result["checks"]["raw_serial_absent"] is True
    assert result["inventory"]["file_count"] == 4
    assert (
        result["inventory"]["files_sha256"]["manual-screen.png"]
        == hashlib.sha256(manual.read_bytes()).hexdigest()
    )
    assert SERIAL not in output.read_text(encoding="utf-8")
    manifest = cast(
        dict[str, Any],
        json.loads(output.with_suffix(".sha256.json").read_text(encoding="utf-8")),
    )
    assert manifest["files"][output.name] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_audit_rejects_changed_manifest_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _single_spec(monkeypatch)
    report, _ = _write_sync_fixture(tmp_path)
    report.write_text("{}", encoding="utf-8")

    with pytest.raises(tool.EvidenceAuditError, match="manifest_artifact_hash_mismatch"):
        tool.audit(tmp_path, tmp_path / "M52_EVIDENCE_AUDIT.json", device=_device())


def test_audit_rejects_boolean_manifest_schema_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _single_spec(monkeypatch)
    _, manifest = _write_sync_fixture(tmp_path)
    document = cast(dict[str, Any], json.loads(manifest.read_text(encoding="utf-8")))
    document["schema_version"] = True
    manifest.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")

    with pytest.raises(tool.EvidenceAuditError, match="manifest_header_invalid"):
        tool.audit(tmp_path, tmp_path / "M52_EVIDENCE_AUDIT.json", device=_device())


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("schema_version", True, "report_header_invalid"),
        ("tests", True, "sync_coordinator_mismatch"),
    ),
)
def test_audit_rejects_boolean_aliases_in_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    error: str,
) -> None:
    _single_spec(monkeypatch)
    report, manifest = _write_sync_fixture(tmp_path)
    document = cast(dict[str, Any], json.loads(report.read_text(encoding="utf-8")))
    document[field] = value
    _rewrite_sync_report(report, manifest, document)

    with pytest.raises(tool.EvidenceAuditError, match=error):
        tool.audit(tmp_path, tmp_path / "M52_EVIDENCE_AUDIT.json", device=_device())


def test_audit_rejects_raw_serial_anywhere_in_private_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _single_spec(monkeypatch)
    _write_sync_fixture(tmp_path)
    (tmp_path / "unlisted.txt").write_text(f"adb target {SERIAL}", encoding="utf-8")

    with pytest.raises(tool.EvidenceAuditError, match="raw_device_serial_present"):
        tool.audit(tmp_path, tmp_path / "M52_EVIDENCE_AUDIT.json", device=_device())


def test_audit_rejects_unknown_empty_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _single_spec(monkeypatch)
    _write_sync_fixture(tmp_path)
    (tmp_path / "unexpected.png").write_bytes(b"")

    with pytest.raises(tool.EvidenceAuditError, match="unexpected_empty_evidence"):
        tool.audit(tmp_path, tmp_path / "M52_EVIDENCE_AUDIT.json", device=_device())


def test_audit_rejects_wrong_device_digest_and_incomplete_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _single_spec(monkeypatch)
    _write_sync_fixture(tmp_path, serial="different-device")

    with pytest.raises(tool.EvidenceAuditError, match="report_device_identity_mismatch"):
        tool.audit(tmp_path, tmp_path / "M52_EVIDENCE_AUDIT.json", device=_device())

    dirty = replace(_device(), adb_reverse_entries=1)
    with pytest.raises(tool.EvidenceAuditError, match="android_cleanup_incomplete"):
        tool.audit(tmp_path, tmp_path / "M52_EVIDENCE_AUDIT.json", device=dirty)


def test_disposable_resource_matchers_cover_every_physical_harness_project() -> None:
    names = [
        "autplay-p14-m5b-e2e-1234-abcd-postgres-1",
        "autplay-p14-android-e2e-1234-abcd_default",
        "autplay-p14-self-pairing-roles-e2e-1234-abcd-postgres-data",
        "autplay-p14-account-recovery-android-e2e-1234-abcd-postgres-1",
        "autplay-p14-account-deletion-android-e2e-1234-abcd_default",
        "autplay-p14-training-consent-android-e2e-1234-abcd-postgres-data",
        "autplay-p14-self-pairing-device-quota-e2e-1234-abcd-postgres-1",
        "unrelated-android-e2e",
    ]

    assert tool._disposable_resource_count(names) == 7
