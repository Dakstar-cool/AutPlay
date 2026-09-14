import json
from pathlib import Path
from typing import cast

EXPECTED_FINDING_IDS = {
    *(f"S{number}" for number in range(1, 6)),
    *(f"A{number}" for number in range(1, 9)),
    "G1",
    "G2",
    "T1",
    "T2",
    *(f"Q{number}" for number in range(1, 7)),
}


def _registry() -> dict[str, object]:
    path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "release"
        / "AUDIT_REMEDIATION_AFTER_R1B_V1.json"
    )
    return cast(dict[str, object], json.loads(path.read_text(encoding="utf-8")))


def test_registry_partitions_every_numbered_finding_exactly_once() -> None:
    registry = _registry()
    states = registry["states"]
    assert isinstance(states, dict)

    categorized = [finding for findings in states.values() for finding in findings]

    assert len(categorized) == len(set(categorized))
    assert set(categorized) == EXPECTED_FINDING_IDS
    assert len(categorized) == 23


def test_registry_preserves_unproven_claim_boundaries() -> None:
    registry = _registry()
    states = registry["states"]
    boundaries = registry["claim_boundaries"]
    dependent_r1b = registry["dependent_r1b"]
    assert isinstance(states, dict)
    assert isinstance(boundaries, dict)
    assert isinstance(dependent_r1b, dict)

    assert set(states["FIXED_WITH_LOCAL_EVIDENCE"]) >= {
        *(f"A{number}" for number in range(1, 9)),
    }
    assert states["CODE_CHANGED_HOSTED_CI_EVIDENCE_PENDING"] == ["Q3"]
    assert states["ACTIVATION_CONDITION_NOT_MET"] == ["G2"]
    assert boundaries == {
        "android_connected_gate": "LOCAL_API26_SUITE_AND_A1_A8_TARGETED_PASS",
        "linux_cuda_hardware_gate": "NOT_RUN_ON_THIS_SNAPSHOT",
        "release_published": False,
        "production_data_repaired_or_reset": False,
        "r1b_evaluation_pass": False,
    }
    assert dependent_r1b["status"] == "BLOCKED"
    assert dependent_r1b["r1c_activated"] is False

    evidence = registry["current_evidence"]
    remaining = registry["remaining_evidence"]
    assert isinstance(evidence, dict)
    assert isinstance(remaining, dict)
    root = Path(__file__).resolve().parents[2]
    connected = json.loads((root / evidence["android_connected"]).read_text(encoding="utf-8"))
    assert connected["status"] == "PASS"
    assert connected["suite"]["failures"] == "0"
    assert connected["suite"]["errors"] == "0"
    assert len(connected["testcases"]) == int(connected["suite"]["tests"])
    skipped = [row for row in connected["testcases"] if row["skipped"]]
    assert len(skipped) == int(connected["suite"]["skipped"])
    assert any("PlaybackServiceProcessStageTest" in row["class"] for row in skipped)
    implementation = json.loads((root / evidence["android_implementation"]).read_text())
    receipt = json.loads(
        (root / evidence["android_implementation"]).with_name("process-receipt.json").read_text()
    )
    assert implementation["checks"]["process_death"]["status"] == "PASS"
    assert receipt["verified_process_boundary"] is True
    assert len(receipt["stages"]) == 2
    assert all(f"A{number}" not in remaining for number in range(1, 9))
    passed_classes = {row["class"] for row in connected["testcases"] if not row["skipped"]}
    assert {
        "app.autplay.work.ServerWorkersAuthenticationTest",
        "app.autplay.work.SyncWorkerLifecycleTest",
        "app.autplay.playback.PlaybackServiceLifecycleTest",
    } <= passed_classes
    assert remaining["Q3"] and remaining["Q4"]
