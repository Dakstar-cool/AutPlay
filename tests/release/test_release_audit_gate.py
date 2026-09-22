import hashlib
import importlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SOURCE_COMMIT = "a" * 40
SOURCE_TREE = "b" * 40
NOW = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
BOUNDARY = {
    "claim": "AUDIT_EVIDENCE_ONLY_NO_PRODUCTION_RELEASE_CLAIM",
    "distribution_class": "DEVELOPMENT_RELEASE",
    "production_deployed": False,
    "production_signed": False,
}


def _load_release_audit_gate() -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository_root))
    try:
        return importlib.import_module("scripts.release_audit_gate")
    finally:
        sys.path.remove(str(repository_root))


release_audit_gate = _load_release_audit_gate()


@pytest.fixture(autouse=True)
def _resolved_source_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_git(arguments: list[str]) -> str:
        if arguments == ["rev-parse", f"{SOURCE_COMMIT}^{{tree}}"]:
            return SOURCE_TREE
        raise AssertionError(f"unexpected git invocation: {arguments}")

    monkeypatch.setattr(release_audit_gate, "_git", fake_git)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(root: Path, name: str, path: Path) -> dict[str, Any]:
    return {
        "name": name,
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _build_package(
    tmp_path: Path,
    *,
    generated_at: datetime = NOW,
) -> tuple[Path, Path]:
    artifact = tmp_path / "autplay.apk"
    artifact.write_bytes(b"exact-release-artifact")
    root = tmp_path / "release-audit"
    root.mkdir()

    evidence_paths: dict[str, dict[str, Path]] = {
        "sbom": {},
        "vulnerability": {},
        "license": {},
        "secret_scan": {},
        "provenance": {},
    }
    for name in ("root", "server", "gpu", "training", "acquisition"):
        sbom = root / "sbom" / f"{name}.json"
        _write_json(
            sbom,
            {"bomFormat": "CycloneDX", "specVersion": "1.5", "components": []},
        )
        evidence_paths["sbom"][name] = sbom
        vulnerability = root / "vulnerability" / f"{name}.json"
        _write_json(
            vulnerability,
            {"summary": {"vulnerabilities": 0, "adverse_statuses": 0}},
        )
        evidence_paths["vulnerability"][name] = vulnerability

    license_path = root / "license" / "inventory.json"
    _write_json(
        license_path,
        {
            "status": "PASS_WITH_DECLARED_PUBLICATION_REVIEW",
            "unresolved_license_count": 0,
            "environments": {
                name: []
                for name in (
                    "root",
                    "server",
                    "gpu",
                    "training",
                    "acquisition",
                    "android_release_runtime",
                )
            },
        },
    )
    evidence_paths["license"]["dependency_licenses"] = license_path
    secret_path = root / "secret" / "scan.json"
    _write_json(secret_path, {"status": "PASS", "findings": []})
    evidence_paths["secret_scan"]["repository_secret_scan"] = secret_path

    generated_text = generated_at.isoformat()
    provenance_path = root / "provenance.json"
    _write_json(
        provenance_path,
        {
            "schema_version": 1,
            "kind": "AUTPLAY_RELEASE_PROVENANCE",
            "generated_at": generated_text,
            "source": {
                "commit": SOURCE_COMMIT,
                "tree": SOURCE_TREE,
                "tracked_worktree_clean": True,
            },
            "inputs": [
                {
                    "filename": artifact.name,
                    "name": "android_unsigned",
                    "sha256": _sha256(artifact),
                    "size_bytes": artifact.stat().st_size,
                }
            ],
            "generator": {
                "p14_release_audit_sha256": "c" * 64,
                "release_audit_gate_sha256": "d" * 64,
            },
            "release_boundary": BOUNDARY,
        },
    )
    evidence_paths["provenance"]["release_provenance"] = provenance_path
    evidence = {
        category: {
            "status": "PASS",
            "files": [_record(root, name, path) for name, path in sorted(paths.items())],
        }
        for category, paths in evidence_paths.items()
    }
    _write_json(
        root / "release-audit.json",
        {
            "schema_version": 1,
            "kind": "AUTPLAY_RELEASE_AUDIT",
            "generated_at": generated_text,
            "expires_at": (generated_at + timedelta(hours=24)).isoformat(),
            "status": "PASS",
            "source_commit": SOURCE_COMMIT,
            "evidence": evidence,
            "release_boundary": BOUNDARY,
        },
    )
    return root, artifact


def test_valid_package_is_bound_to_source_artifact_and_nonproduction_boundary(
    tmp_path: Path,
) -> None:
    root, artifact = _build_package(tmp_path)

    result = release_audit_gate.verify_package(
        root,
        SOURCE_COMMIT,
        {"android_unsigned": artifact},
        max_age_hours=24,
        now=NOW,
    )

    assert result["status"] == "PASS"
    assert result["release_boundary"]["production_signed"] is False
    assert result["release_boundary"]["production_deployed"] is False


def test_stale_release_evidence_fails_closed(tmp_path: Path) -> None:
    root, artifact = _build_package(tmp_path, generated_at=NOW - timedelta(hours=25))

    with pytest.raises(release_audit_gate.ReleaseAuditError, match="stale or expired"):
        release_audit_gate.verify_package(
            root,
            SOURCE_COMMIT,
            {"android_unsigned": artifact},
            max_age_hours=24,
            now=NOW,
        )


def test_source_commit_mismatch_fails_closed(tmp_path: Path) -> None:
    root, artifact = _build_package(tmp_path)

    with pytest.raises(release_audit_gate.ReleaseAuditError, match="source commit does not match"):
        release_audit_gate.verify_package(
            root,
            "e" * 40,
            {"android_unsigned": artifact},
            max_age_hours=24,
            now=NOW,
        )


def test_artifact_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    root, artifact = _build_package(tmp_path)
    artifact.write_bytes(b"artifact-replaced-after-audit")

    with pytest.raises(release_audit_gate.ReleaseAuditError, match="exact release artifacts"):
        release_audit_gate.verify_package(
            root,
            SOURCE_COMMIT,
            {"android_unsigned": artifact},
            max_age_hours=24,
            now=NOW,
        )


def test_missing_evidence_file_fails_closed(tmp_path: Path) -> None:
    root, artifact = _build_package(tmp_path)
    (root / "secret" / "scan.json").unlink()

    with pytest.raises(release_audit_gate.ReleaseAuditError, match="file is missing"):
        release_audit_gate.verify_package(
            root,
            SOURCE_COMMIT,
            {"android_unsigned": artifact},
            max_age_hours=24,
            now=NOW,
        )


def test_evidence_hash_mismatch_fails_closed(tmp_path: Path) -> None:
    root, artifact = _build_package(tmp_path)
    (root / "sbom" / "server.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(release_audit_gate.ReleaseAuditError, match="evidence index"):
        release_audit_gate.verify_package(
            root,
            SOURCE_COMMIT,
            {"android_unsigned": artifact},
            max_age_hours=24,
            now=NOW,
        )


def test_unindexed_evidence_file_fails_closed(tmp_path: Path) -> None:
    root, artifact = _build_package(tmp_path)
    (root / "unexpected.txt").write_text("not indexed\n", encoding="utf-8")

    with pytest.raises(release_audit_gate.ReleaseAuditError, match="file inventory"):
        release_audit_gate.verify_package(
            root,
            SOURCE_COMMIT,
            {"android_unsigned": artifact},
            max_age_hours=24,
            now=NOW,
        )


def test_provenance_tree_mismatch_fails_closed(tmp_path: Path) -> None:
    root, artifact = _build_package(tmp_path)
    provenance_path = root / "provenance.json"
    provenance: dict[str, Any] = json.loads(provenance_path.read_text(encoding="utf-8"))
    provenance["source"]["tree"] = "e" * 40
    _write_json(provenance_path, provenance)
    index_path = root / "release-audit.json"
    index: dict[str, Any] = json.loads(index_path.read_text(encoding="utf-8"))
    record = index["evidence"]["provenance"]["files"][0]
    record["sha256"] = _sha256(provenance_path)
    record["size_bytes"] = provenance_path.stat().st_size
    _write_json(index_path, index)

    with pytest.raises(release_audit_gate.ReleaseAuditError, match="source tree"):
        release_audit_gate.verify_package(
            root,
            SOURCE_COMMIT,
            {"android_unsigned": artifact},
            max_age_hours=24,
            now=NOW,
        )


def test_nonclean_vulnerability_evidence_fails_even_with_a_matching_index_hash(
    tmp_path: Path,
) -> None:
    root, artifact = _build_package(tmp_path)
    audit_path = root / "vulnerability" / "root.json"
    _write_json(audit_path, {"summary": {"vulnerabilities": 1, "adverse_statuses": 0}})
    index_path = root / "release-audit.json"
    index: dict[str, Any] = json.loads(index_path.read_text(encoding="utf-8"))
    records = index["evidence"]["vulnerability"]["files"]
    record = next(item for item in records if item["name"] == "root")
    record["sha256"] = _sha256(audit_path)
    record["size_bytes"] = audit_path.stat().st_size
    _write_json(index_path, index)

    with pytest.raises(release_audit_gate.ReleaseAuditError, match="is not clean"):
        release_audit_gate.verify_package(
            root,
            SOURCE_COMMIT,
            {"android_unsigned": artifact},
            max_age_hours=24,
            now=NOW,
        )


def test_release_paths_invoke_the_gate_without_production_claims() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    packager = (repository_root / "scripts" / "package-release.ps1").read_text(encoding="utf-8")
    workflow = (repository_root / ".github" / "workflows" / "release-candidate.yml").read_text(
        encoding="utf-8"
    )

    for release_path in (packager, workflow):
        assert "scripts.release_audit_gate" in release_path
        assert '"generate"' in release_path or " generate " in release_path
        assert '"verify"' in release_path or " verify " in release_path
        assert "--max-age-hours" in release_path
    assert 'distribution_class = "DEVELOPMENT_RELEASE"' in packager
    assert "production_signed = $false" in packager
    assert "production_deployed = $false" in packager
    assert "UNSIGNED_CANDIDATE_NOT_FOR_PUBLICATION" in workflow
    assert "production_deployment=NOT_CONFIGURED" in workflow
    assert "PRODUCTION_RELEASE" not in workflow


def test_repository_secret_scan_is_clean_with_explicit_disposable_ci_allowlist() -> None:
    result = release_audit_gate.p14_release_audit._secret_scan()

    assert result["status"] == "PASS"
    assert result["findings"] == []
    assert (
        ".github/workflows/ci-training.yml"
        in (result["allowlisted_disposable_credential"]["paths"])
    )
