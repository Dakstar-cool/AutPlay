from __future__ import annotations

import hashlib
import importlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from uuid import UUID

import pytest


def _load_tool() -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository_root))
    try:
        return importlib.import_module("scripts.admin_target_acceptance")
    finally:
        sys.path.remove(str(repository_root))


tool = _load_tool()
NOW = "2026-09-19T10:00:00Z"
HEAD = "a" * 40
SHA = "b" * 64


def _read_manifest(root: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((root / "manifest.json").read_text(encoding="utf-8")))


def _write_manifest(root: Path, manifest: dict[str, Any]) -> None:
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _artifact(root: Path, relative: str, payload: bytes = b"reviewed evidence") -> dict[str, Any]:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return {
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "captured_at": NOW,
        "redaction_reviewed": True,
    }


def _resource_document() -> dict[str, Any]:
    metrics = {
        "cpu_peak_percent": 35,
        "disk_busy_peak_percent": 20,
        "network_busy_peak_percent": 20,
        "disk_read_mib_per_second": 5,
        "disk_write_mib_per_second": 5,
        "network_receive_mib_per_second": 5,
        "network_send_mib_per_second": 5,
        "database_p95_ms": 10,
        "queue_wait_p95_ms": 10,
        "queue_depth_max": 1,
        "playback_mib_per_second": 1,
        "transfer_mib_per_second": 5,
        "successful_operations": 20,
        "failed_operations": 0,
        "timed_out_operations": 0,
    }
    return {
        "version": 1,
        "measurement_id": "11111111-1111-4111-8111-111111111111",
        "server_instance_id": "22222222-2222-4222-8222-222222222222",
        "identity_epoch": 7,
        "environment_sha256": "e" * 64,
        "workload_sha256": "f" * 64,
        "measured_at": NOW,
        "duration_seconds": 120,
        "sample_count": 20,
        "simultaneous_playbacks": 4,
        "simultaneous_transfers": 4,
        "workload_paths": [
            "A1_ACQUISITION",
            "DOWNLOAD",
            "INTERNET_ACQUISITION",
            "PLAYBACK_CURRENT_NEXT",
            "RANGE_SEEK",
            "UPLOAD",
        ],
        "metrics": metrics,
        "acceptance_maxima": {
            name: 0 if name in {"failed_operations", "timed_out_operations"} else 100
            for name in metrics
        },
        "acceptance_minima": {
            "duration_seconds": 60,
            "sample_count": 10,
            "playback_mib_per_second": 0.1,
            "transfer_mib_per_second": 0.1,
            "successful_operations": 10,
        },
    }


def _internal_document(resource: dict[str, Any], *, version: int = 3) -> dict[str, Any]:
    paths = [
        "DEVICE_UPLOAD_CLEANUP",
        "FINALIZED_STAGING_CLEANUP",
        "INGEST_ANALYSIS_PUBLICATION",
        "METADATA_ENRICHMENT",
        "ORPHAN_MISSING_CHECK",
        "ORPHAN_RETIREMENT",
        "PROVIDER_SCRATCH_RETIREMENT",
        "PROVIDER_STAGING_CLEANUP",
        "TRAINING_DATASET_CHECKPOINT",
        "TRAINING_ROOT_CLEANUP",
        "VAULT_INVENTORY",
    ]
    if version == 2:
        paths = [path for path in paths if not path.startswith("TRAINING_")]
    return {
        "version": version,
        "resource_measurement": resource,
        "simultaneous_internal_io": 4,
        "workload_paths": paths,
        "worst_permitted_mix": True,
        "internal_mib_per_second": 2,
        "minimum_internal_mib_per_second": 1,
        "successful_internal_operations": 10,
        "minimum_successful_internal_operations": 5,
    }


def _restored_state_document(targets: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "PASS",
        "migration_head": targets["migration_head"],
        "server_identity_sha256": targets["server_identity_sha256"],
        "environment_sha256": targets["environment_sha256"],
        "backup_sha256": targets["backup_sha256"],
        "isolated_environment": True,
        "production_direct_targeted": False,
        "normal_supervisors_stopped": True,
        "deletion_ledger_guard_passed": True,
        "consent_ledger_guard_passed": True,
        "offline_drain_passed": True,
        "vault_reconcile_drained": True,
        "upload_cleanup_drained": True,
        "cpu_ingest_drained": True,
        "metadata_cleanup_drained": True,
        "readiness_started_after_guards": True,
        "finally_deleted_accounts_absent": True,
        "withdrawn_training_inputs_unauthorized": True,
        "current_publication_tuple_served": True,
        "open_execution_capacity_released_without_host_empty": False,
    }


def _live_pid_abort_document(targets: dict[str, Any]) -> dict[str, Any]:
    state = "c" * 64
    return {
        "schema_version": 1,
        "status": "PASS",
        "migration_head": targets["migration_head"],
        "server_identity_sha256": targets["server_identity_sha256"],
        "environment_sha256": targets["environment_sha256"],
        "backup_sha256": targets["backup_sha256"],
        "platform": "windows",
        "error_code": "offline_process_still_running",
        "live_process_identity_sha256": "d" * 64,
        "database_state_before_sha256": state,
        "database_state_after_sha256": state,
        "process_was_live": True,
        "transaction_aborted": True,
        "production_direct_targeted": False,
    }


def _complete_bundle(root: Path, *, internal_version: int = 3) -> dict[str, Any]:
    tool.scaffold(root, branch="codex/readme-current-state", head=HEAD, dirty_worktree=True)
    manifest = _read_manifest(root)
    manifest["status"] = "PASS"
    manifest["targets"].update(
        {field: SHA for field in manifest["targets"] if field.endswith("_sha256")}
    )
    manifest["targets"].update(
        {
            "migration_head": "0060_local_bridge_authority",
            "android_application_id": "app.autplay",
            "android_version_code": 11,
            "android_version_name": "0.3.7-metadata",
            "started_at": NOW,
            "ended_at": NOW,
        }
    )
    resource = _resource_document()
    identity = json.dumps(
        {
            "identity_epoch": resource["identity_epoch"],
            "server_instance_id": resource["server_instance_id"],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    manifest["targets"]["server_identity_sha256"] = hashlib.sha256(identity).hexdigest()
    manifest["targets"]["environment_sha256"] = resource["environment_sha256"]

    for gate_name, gate in manifest["gates"].items():
        gate["status"] = "PASS"
        for control_name, control in gate["controls"].items():
            control["status"] = "PASS"
            if control_name == "operator_signoff":
                continue
            payload = f"{gate_name}:{control_name}".encode()
            if (gate_name, control_name) == ("C", "resource_report_v1"):
                payload = json.dumps(resource, sort_keys=True).encode()
            elif (gate_name, control_name) == ("C", "internal_report_v3"):
                payload = json.dumps(
                    _internal_document(resource, version=internal_version), sort_keys=True
                ).encode()
            elif (gate_name, control_name) == ("D", "restored_state_assertions"):
                payload = json.dumps(
                    _restored_state_document(manifest["targets"]), sort_keys=True
                ).encode()
            elif (gate_name, control_name) == ("D", "live_pid_abort"):
                payload = json.dumps(
                    _live_pid_abort_document(manifest["targets"]), sort_keys=True
                ).encode()
            control["evidence"] = [
                _artifact(root, f"gate-{gate_name.lower()}/{control_name}.json", payload)
            ]
    for gate_name, gate in manifest["gates"].items():
        controls = gate["controls"]
        signoff = tool.gate_signoff_document(
            gate_name,
            manifest["targets"],
            controls,
            reviewer="fixture-reviewer",
            reviewed_at=NOW,
        )
        controls["operator_signoff"]["evidence"] = [
            _artifact(
                root,
                f"gate-{gate_name.lower()}/operator_signoff.json",
                json.dumps(signoff, sort_keys=True).encode(),
            )
        ]
    _write_manifest(root, manifest)
    return manifest


def test_scaffold_is_valid_but_cannot_claim_acceptance(tmp_path: Path) -> None:
    tool.scaffold(tmp_path, branch="codex/readme-current-state", head=HEAD, dirty_worktree=True)

    result = tool.validate_bundle(tmp_path)

    assert result["status"] == "PENDING"
    assert result["artifact_count"] == 0
    assert result["gates"] == {"A": "PENDING", "B": "PENDING", "C": "PENDING", "D": "PENDING"}


def test_inspect_scaffold_reports_every_missing_target_and_control(tmp_path: Path) -> None:
    tool.scaffold(tmp_path, branch="codex/readme-current-state", head=HEAD, dirty_worktree=True)

    result = tool.inspect_bundle(tmp_path)

    assert result["status"] == "PENDING"
    assert result["ready"] is False
    assert result["acceptance_window_ready"] is False
    for gate_name, required_controls in tool.REQUIRED_CONTROLS.items():
        gate = result["gates"][gate_name]
        assert gate["status"] == "PENDING"
        assert gate["missing_targets"] == sorted(tool._GATE_TARGETS[gate_name])
        assert gate["incomplete_controls"] == list(required_controls)
        assert gate["artifact_count"] == 0


def test_complete_closed_bundle_uses_production_report_parsers(tmp_path: Path) -> None:
    _complete_bundle(tmp_path)

    result = tool.validate_bundle(tmp_path)

    assert result["status"] == "PASS"
    assert result["artifact_count"] == sum(
        len(controls) for controls in tool.REQUIRED_CONTROLS.values()
    )
    assert result["gates"] == {"A": "PASS", "B": "PASS", "C": "PASS", "D": "PASS"}

    inspection = tool.inspect_bundle(tmp_path)
    assert inspection["ready"] is True
    assert inspection["acceptance_window_ready"] is True
    for gate in inspection["gates"].values():
        assert gate["missing_targets"] == []
        assert gate["incomplete_controls"] == []
        assert gate["artifact_count"] > 0


def test_report_v2_cannot_satisfy_retained_training_gate(tmp_path: Path) -> None:
    _complete_bundle(tmp_path, internal_version=2)

    with pytest.raises(tool.AcceptanceError, match="gate_c_report_mismatch"):
        tool.validate_bundle(tmp_path)


def test_partial_bundle_cannot_mark_gate_c_pass_with_invalid_reports(tmp_path: Path) -> None:
    manifest = _complete_bundle(tmp_path, internal_version=2)
    manifest["status"] = "PENDING"
    for gate_name in ("A", "B", "D"):
        gate = manifest["gates"][gate_name]
        gate["status"] = "PENDING"
        gate["controls"]["operator_signoff"]["status"] = "PENDING"
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match="gate_c_report_mismatch"):
        tool.validate_bundle(tmp_path)


def _replace_json_evidence(
    root: Path,
    manifest: dict[str, Any],
    gate: str,
    control: str,
    document: dict[str, Any],
) -> None:
    evidence = manifest["gates"][gate]["controls"][control]["evidence"][0]
    path = root / evidence["path"]
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()


def test_partial_bundle_validates_gate_d_structured_reports(tmp_path: Path) -> None:
    manifest = _complete_bundle(tmp_path)
    manifest["status"] = "PENDING"
    for gate_name in ("A", "B", "C"):
        gate = manifest["gates"][gate_name]
        gate["status"] = "PENDING"
        gate["controls"]["operator_signoff"]["status"] = "PENDING"
    report = _restored_state_document(manifest["targets"])
    report["production_direct_targeted"] = True
    _replace_json_evidence(tmp_path, manifest, "D", "restored_state_assertions", report)
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match="gate_d_restored_state_invalid"):
        tool.validate_bundle(tmp_path)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("backup_sha256", "f" * 64, "gate_d_restored_state_target_mismatch"),
        ("current_publication_tuple_served", False, "gate_d_restored_state_invalid"),
        (
            "open_execution_capacity_released_without_host_empty",
            True,
            "gate_d_restored_state_invalid",
        ),
    ),
)
def test_gate_d_rejects_false_restored_state_assertions(
    tmp_path: Path,
    field: str,
    value: object,
    error: str,
) -> None:
    manifest = _complete_bundle(tmp_path)
    report = _restored_state_document(manifest["targets"])
    report[field] = value
    _replace_json_evidence(tmp_path, manifest, "D", "restored_state_assertions", report)
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match=error):
        tool.validate_bundle(tmp_path)


@pytest.mark.parametrize(
    ("field", "value", "error"),
    (
        ("environment_sha256", "f" * 64, "gate_d_live_pid_abort_target_mismatch"),
        ("transaction_aborted", False, "gate_d_live_pid_abort_invalid"),
        ("database_state_after_sha256", "e" * 64, "gate_d_live_pid_abort_invalid"),
        ("error_code", "offline_process_evidence_unavailable", "gate_d_live_pid_abort_invalid"),
    ),
)
def test_gate_d_rejects_unproven_live_pid_abort(
    tmp_path: Path,
    field: str,
    value: object,
    error: str,
) -> None:
    manifest = _complete_bundle(tmp_path)
    report = _live_pid_abort_document(manifest["targets"])
    report[field] = value
    _replace_json_evidence(tmp_path, manifest, "D", "live_pid_abort", report)
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match=error):
        tool.validate_bundle(tmp_path)


def test_pass_gate_rejects_unreviewed_migration_head(tmp_path: Path) -> None:
    manifest = _complete_bundle(tmp_path)
    manifest["targets"]["migration_head"] = "0058_training_privacy_fence"
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match="target_migration_head_unreviewed"):
        tool.validate_bundle(tmp_path)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("android_application_id", "app.autplay.qa"),
        ("android_version_code", 10),
        ("android_version_name", "0.3.6"),
    ),
)
def test_gate_b_rejects_unreviewed_android_build(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    manifest = _complete_bundle(tmp_path)
    manifest["targets"][field] = value
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match="gate_b_android_build_unreviewed"):
        tool.validate_bundle(tmp_path)


def test_declared_pass_rejects_missing_control_evidence(tmp_path: Path) -> None:
    manifest = _complete_bundle(tmp_path)
    evidence = manifest["gates"]["A"]["controls"]["windows_hello_ceremony"]["evidence"].pop()
    (tmp_path / evidence["path"]).unlink()
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match="gate_control_pass_without_evidence"):
        tool.validate_bundle(tmp_path)


def test_changed_evidence_and_unlisted_files_are_rejected(tmp_path: Path) -> None:
    manifest = _complete_bundle(tmp_path)
    evidence = manifest["gates"]["D"]["controls"]["live_pid_abort"]["evidence"][0]
    path = tmp_path / evidence["path"]
    original = path.read_bytes()
    path.write_bytes(b"changed after review")

    with pytest.raises(tool.AcceptanceError, match="evidence_hash_mismatch"):
        tool.validate_bundle(tmp_path)

    path.write_bytes(original)
    (tmp_path / "unreviewed-secret.txt").write_text("must not be packaged", encoding="utf-8")
    with pytest.raises(tool.AcceptanceError, match="bundle_inventory_mismatch"):
        tool.validate_bundle(tmp_path)


def test_manifest_rejects_duplicate_keys_before_acceptance(tmp_path: Path) -> None:
    tool.scaffold(tmp_path, branch="main", head=HEAD, dirty_worktree=False)
    path = tmp_path / "manifest.json"
    payload = path.read_text(encoding="utf-8")
    path.write_text(
        payload.replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'),
        encoding="utf-8",
    )

    with pytest.raises(tool.AcceptanceError, match="manifest_duplicate_key"):
        tool.validate_bundle(tmp_path)


def test_manifest_schema_version_rejects_boolean_alias(tmp_path: Path) -> None:
    tool.scaffold(tmp_path, branch="main", head=HEAD, dirty_worktree=False)
    manifest = _read_manifest(tmp_path)
    manifest["schema_version"] = True
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match="manifest_version_invalid"):
        tool.validate_bundle(tmp_path)


@pytest.mark.parametrize(
    ("control", "document_factory", "error"),
    (
        ("restored_state_assertions", _restored_state_document, "gate_d_restored_state_invalid"),
        ("live_pid_abort", _live_pid_abort_document, "gate_d_live_pid_abort_invalid"),
    ),
)
def test_gate_d_schema_versions_reject_boolean_alias(
    tmp_path: Path,
    control: str,
    document_factory: Any,
    error: str,
) -> None:
    manifest = _complete_bundle(tmp_path)
    report = document_factory(manifest["targets"])
    report["schema_version"] = True
    _replace_json_evidence(tmp_path, manifest, "D", control, report)
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match=error):
        tool.validate_bundle(tmp_path)


def test_gate_signoff_schema_version_rejects_boolean_alias(tmp_path: Path) -> None:
    manifest = _complete_bundle(tmp_path)
    evidence = manifest["gates"]["A"]["controls"]["operator_signoff"]["evidence"][0]
    path = tmp_path / evidence["path"]
    signoff = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    signoff["schema_version"] = True
    path.write_text(json.dumps(signoff, sort_keys=True), encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match="gate_signoff_invalid"):
        tool.validate_bundle(tmp_path)


def test_evidence_must_be_redaction_reviewed_inside_acceptance_window(tmp_path: Path) -> None:
    manifest = _complete_bundle(tmp_path)
    evidence = manifest["gates"]["B"]["controls"]["txt_manual_recovery"]["evidence"][0]
    evidence["redaction_reviewed"] = False
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match="evidence_redaction_review_required"):
        tool.validate_bundle(tmp_path)

    evidence["redaction_reviewed"] = True
    evidence["captured_at"] = datetime(2026, 9, 20, tzinfo=UTC).isoformat().replace("+00:00", "Z")
    _write_manifest(tmp_path, manifest)
    with pytest.raises(tool.AcceptanceError, match="evidence_outside_acceptance_window"):
        tool.validate_bundle(tmp_path)


def test_server_identity_digest_binds_uuid_and_epoch(tmp_path: Path) -> None:
    manifest = _complete_bundle(tmp_path)
    manifest["targets"]["server_identity_sha256"] = hashlib.sha256(
        str(UUID("22222222-2222-4222-8222-222222222222")).encode()
    ).hexdigest()
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match="gate_c_server_identity_mismatch"):
        tool.validate_bundle(tmp_path)


def test_operator_signoff_binds_exact_gate_evidence(tmp_path: Path) -> None:
    manifest = _complete_bundle(tmp_path)
    evidence = manifest["gates"]["B"]["controls"]["pairing_owner"]["evidence"][0]
    path = tmp_path / evidence["path"]
    path.write_bytes(b"replacement reviewed evidence")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match="gate_signoff_evidence_mismatch"):
        tool.validate_bundle(tmp_path)


def test_operator_signoff_binds_exact_gate_targets(tmp_path: Path) -> None:
    manifest = _complete_bundle(tmp_path)
    manifest["targets"]["tls_chain_sha256"] = "c" * 64
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match="gate_signoff_target_mismatch"):
        tool.validate_bundle(tmp_path)


def test_operator_signoff_must_follow_all_gate_evidence(tmp_path: Path) -> None:
    manifest = _complete_bundle(tmp_path)
    manifest["targets"]["started_at"] = "2026-09-19T09:59:00Z"
    evidence = manifest["gates"]["A"]["controls"]["operator_signoff"]["evidence"][0]
    path = tmp_path / evidence["path"]
    signoff = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    signoff["reviewed_at"] = "2026-09-19T09:59:30Z"
    path.write_text(json.dumps(signoff, sort_keys=True), encoding="utf-8")
    evidence["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    _write_manifest(tmp_path, manifest)

    with pytest.raises(tool.AcceptanceError, match="gate_signoff_precedes_evidence"):
        tool.validate_bundle(tmp_path)
