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

    assert set(states["CODE_CHANGED_CONNECTED_EVIDENCE_PENDING"]) == {
        *(f"A{number}" for number in range(1, 9)),
        "Q3",
    }
    assert states["ACTIVATION_CONDITION_NOT_MET"] == ["G2"]
    assert boundaries == {
        "android_connected_gate": "NOT_RUN_ON_THIS_SNAPSHOT",
        "linux_cuda_hardware_gate": "NOT_RUN_ON_THIS_SNAPSHOT",
        "release_published": False,
        "production_data_repaired_or_reset": False,
        "r1b_evaluation_pass": False,
    }
    assert dependent_r1b["status"] == "BLOCKED"
    assert dependent_r1b["r1c_activated"] is False
