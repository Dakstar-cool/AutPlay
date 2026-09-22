import hashlib
import importlib
import io
import json
import sys
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import pytest

SOURCE_COMMIT = "a" * 40
SOURCE_TREE = "b" * 40
SIGNER_SHA256 = "c" * 64
GENERATED_AT = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def _load_finalizer() -> ModuleType:
    repository_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repository_root))
    try:
        return importlib.import_module("scripts.finalize_production_release")
    finally:
        sys.path.remove(str(repository_root))


finalizer = _load_finalizer()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _add_tar_bytes(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    member = tarfile.TarInfo(name)
    member.size = len(payload)
    archive.addfile(member, io.BytesIO(payload))


def _docker_archive(
    path: Path,
    *,
    valid_digest: bool = True,
    oci_config_path: bool = False,
) -> str:
    config = json.dumps(
        {"architecture": "amd64", "config": {}, "os": "linux"},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    digest = hashlib.sha256(config).hexdigest()
    digest_name = digest if valid_digest else "f" * 64
    config_name = f"blobs/sha256/{digest_name}" if oci_config_path else f"{digest_name}.json"
    manifest = json.dumps(
        [{"Config": config_name, "Layers": [], "RepoTags": ["autplay-server:v1.0.0"]}],
        separators=(",", ":"),
    ).encode()
    with tarfile.open(path, mode="w:gz") as archive:
        _add_tar_bytes(archive, "manifest.json", manifest)
        _add_tar_bytes(archive, config_name, config)
    return f"sha256:{digest}"


def _repository(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "repo"
    room = root / "apps/android/schemas/app.autplay.data.local.AutPlayDatabase/17.json"
    _write_json(
        room,
        {
            "database": {
                "entities": [],
                "identityHash": "d" * 32,
                "version": 17,
            },
            "formatVersion": 1,
        },
    )
    migrations = root / "server/migrations/versions"
    migrations.mkdir(parents=True)
    (migrations / "0001_first.py").write_text(
        'revision = "0001_first"\ndown_revision = None\n', encoding="utf-8"
    )
    (migrations / "0002_second.py").write_text(
        'revision: str = "0002_second"\ndown_revision: str | None = "0001_first"\n',
        encoding="utf-8",
    )

    inputs = root / "inputs"
    inputs.mkdir()
    apk = inputs / "autplay-1.0.0-production.apk"
    apk.write_bytes(b"production-apk")
    android_manifest = inputs / "android-production-manifest.json"
    _write_json(
        android_manifest,
        {
            "apk": {
                "application_id": "app.autplay",
                "apksigner": "PASS",
                "bytes": apk.stat().st_size,
                "certificate_sha256": SIGNER_SHA256,
                "filename": apk.name,
                "package_identity": "PASS",
                "sha256": _sha256(apk),
                "version_code": 13,
                "version_name": "1.0.0",
            },
            "application_id": "app.autplay",
            "generated_at_utc": GENERATED_AT.isoformat(),
            "release_kind": "ANDROID_PRODUCTION_APK",
            "release_tag": "v1.0.0",
            "room_schema": {
                "database": "app.autplay.data.local.AutPlayDatabase",
                "identity_hash": "d" * 32,
                "repository_path": (
                    "apps/android/schemas/app.autplay.data.local.AutPlayDatabase/17.json"
                ),
                "sha256": _sha256(room),
                "version": 17,
            },
            "schema_version": 1,
            "secrets": "NOT_RECORDED",
            "signer_certificate_sha256": SIGNER_SHA256,
            "source_commit": SOURCE_COMMIT,
            "status": "PASS",
            "version_code": 13,
            "version_name": "1.0.0",
        },
    )
    dependency_report = inputs / "ANDROID_RELEASE_DEPENDENCIES.txt"
    dependency_report.write_text("releaseRuntimeClasspath\n", encoding="utf-8")
    server_archive = inputs / "server.docker.tar.gz"
    image_digest = _docker_archive(server_archive)
    installer = inputs / "server-installer.zip"
    installer.write_bytes(b"installer")
    return {
        "root": root,
        "room": room,
        "apk": apk,
        "android_manifest": android_manifest,
        "dependency_report": dependency_report,
        "server_archive": server_archive,
        "installer": installer,
        "image_digest": Path(image_digest),
    }


def _fake_git(arguments: list[str]) -> str:
    if arguments == ["check-ref-format", "refs/tags/v1.0.0"]:
        return ""
    if arguments == ["rev-parse", "HEAD"]:
        return SOURCE_COMMIT
    if arguments == ["status", "--porcelain=v1", "--untracked-files=all"]:
        return ""
    if arguments == ["rev-parse", "--verify", "refs/tags/v1.0.0^{commit}"]:
        return SOURCE_COMMIT
    if arguments == ["rev-parse", f"{SOURCE_COMMIT}^{{tree}}"]:
        return SOURCE_TREE
    raise AssertionError(f"unexpected git invocation: {arguments}")


def _audit_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    def generate(
        directory: Path,
        dependency_report: Path,
        source_commit: str,
        artifacts: dict[str, Path],
        *,
        distribution_class: str,
        valid_for_hours: int,
    ) -> Path:
        assert dependency_report.name == "ANDROID_RELEASE_DEPENDENCIES.txt"
        assert source_commit == SOURCE_COMMIT
        assert set(artifacts) == {
            "android_dependency_report",
            "android_production_manifest",
            "android_production_signed",
            "server_archive",
            "server_installer",
        }
        assert distribution_class == "PRODUCTION_INPUTS_AUDIT_ONLY"
        assert valid_for_hours == 24
        index = directory / "release-audit.json"
        _write_json(
            index,
            {
                "expires_at": (GENERATED_AT + timedelta(hours=24)).isoformat(),
                "generated_at": GENERATED_AT.isoformat(),
                "release_boundary": {
                    "claim": "AUDIT_EVIDENCE_ONLY_NO_PRODUCTION_RELEASE_CLAIM",
                    "distribution_class": "PRODUCTION_INPUTS_AUDIT_ONLY",
                    "production_deployed": False,
                    "production_signed": False,
                },
                "source_commit": SOURCE_COMMIT,
                "status": "PASS",
            },
        )
        return index

    def verify(
        directory: Path,
        source_commit: str,
        artifacts: dict[str, Path],
        *,
        max_age_hours: int,
    ) -> dict[str, Any]:
        assert source_commit == SOURCE_COMMIT
        assert max_age_hours == 24
        assert all(path.is_file() for path in artifacts.values())
        return cast(
            dict[str, Any],
            json.loads((directory / "release-audit.json").read_text(encoding="utf-8")),
        )

    monkeypatch.setattr(finalizer.release_audit_gate, "generate_package", generate)
    monkeypatch.setattr(finalizer.release_audit_gate, "verify_package", verify)


def _configure(monkeypatch: pytest.MonkeyPatch, paths: dict[str, Path]) -> None:
    root = paths["root"]
    monkeypatch.setattr(finalizer, "REPOSITORY_ROOT", root)
    monkeypatch.setattr(finalizer, "PRODUCTION_ROOT", root / "dist/production")
    monkeypatch.setattr(finalizer, "_git", _fake_git)


def _finalize(paths: dict[str, Path]) -> Path:
    return cast(
        Path,
        finalizer.finalize_release(
            output_directory=paths["root"] / "dist/production/v1.0.0",
            android_manifest=paths["android_manifest"],
            android_apk=paths["apk"],
            android_dependency_report=paths["dependency_report"],
            server_archive=paths["server_archive"],
            source_commit=SOURCE_COMMIT,
            release_tag="v1.0.0",
            version_code=13,
            version_name="1.0.0",
            signer_certificate_sha256=SIGNER_SHA256,
            extra_artifacts={"server_installer": paths["installer"]},
            audit_valid_for_hours=24,
        ),
    )


def test_finalizer_binds_android_server_alembic_and_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _repository(tmp_path)
    _configure(monkeypatch, paths)
    _audit_stubs(monkeypatch)

    manifest_path = _finalize(paths)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["kind"] == "AUTPLAY_PRODUCTION_RELEASE_INPUTS"
    assert manifest["distribution_class"] == "PRODUCTION_RELEASE_CANDIDATE"
    assert manifest["production_signed"] is True
    assert manifest["production_deployed"] is False
    assert manifest["source"] == {
        "commit": SOURCE_COMMIT,
        "tree": SOURCE_TREE,
        "worktree_clean": True,
    }
    assert manifest["android"]["signer_certificate_sha256"] == SIGNER_SHA256
    assert manifest["android"]["room_schema"]["version"] == 17
    assert manifest["server"]["image_digest"] == str(paths["image_digest"])
    assert manifest["server"]["digest_kind"] == "DOCKER_CONFIG_DIGEST"
    assert manifest["database"]["alembic"]["head"] == "0002_second"
    assert manifest["database"]["alembic"]["revision_count"] == 2
    assert manifest["release_audit"]["distribution_class"] == ("PRODUCTION_INPUTS_AUDIT_ONLY")
    assert manifest["activation"]["status"] == (
        "BLOCKED_PENDING_TARGET_ACCEPTANCE_AND_OPERATOR_APPROVAL"
    )
    checksum_text = (manifest_path.parent / "SHA256SUMS").read_text(encoding="utf-8")
    assert "production-release-manifest.json" in checksum_text
    assert "release-audit/release-audit.json" in checksum_text
    assert "extra/server-installer.zip" in checksum_text
    assert not list((paths["root"] / "dist/production").glob(".*.staging-*"))


def test_finalizer_rejects_apk_changed_after_signed_build_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _repository(tmp_path)
    _configure(monkeypatch, paths)
    paths["apk"].write_bytes(b"replaced-apk")

    with pytest.raises(finalizer.ProductionReleaseError, match="APK bytes"):
        _finalize(paths)

    assert not (paths["root"] / "dist/production/v1.0.0").exists()


def test_finalizer_rejects_invalid_docker_config_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _repository(tmp_path)
    _configure(monkeypatch, paths)
    _docker_archive(paths["server_archive"], valid_digest=False)

    with pytest.raises(finalizer.ProductionReleaseError, match="configuration digest"):
        _finalize(paths)

    assert not (paths["root"] / "dist/production/v1.0.0").exists()


def test_finalizer_accepts_modern_docker_oci_blob_config_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _repository(tmp_path)
    _configure(monkeypatch, paths)
    _audit_stubs(monkeypatch)
    image_digest = _docker_archive(paths["server_archive"], oci_config_path=True)

    manifest_path = _finalize(paths)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["server"]["image_digest"] == image_digest


def test_finalizer_cleans_staging_when_audit_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _repository(tmp_path)
    _configure(monkeypatch, paths)
    _audit_stubs(monkeypatch)

    def fail_verify(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise finalizer.release_audit_gate.ReleaseAuditError("changed audit input")

    monkeypatch.setattr(finalizer.release_audit_gate, "verify_package", fail_verify)
    with pytest.raises(finalizer.ProductionReleaseError, match="changed audit input"):
        _finalize(paths)

    production_root = paths["root"] / "dist/production"
    assert not (production_root / "v1.0.0").exists()
    assert not list(production_root.glob(".*.staging-*"))


def test_finalizer_rejects_dirty_source_before_creating_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _repository(tmp_path)
    _configure(monkeypatch, paths)

    def dirty_git(arguments: list[str]) -> str:
        if arguments == ["status", "--porcelain=v1", "--untracked-files=all"]:
            return " M apps/android/build.gradle.kts"
        return _fake_git(arguments)

    monkeypatch.setattr(finalizer, "_git", dirty_git)
    with pytest.raises(finalizer.ProductionReleaseError, match="clean worktree"):
        _finalize(paths)

    assert not (paths["root"] / "dist/production").exists()


def test_repository_alembic_inventory_has_one_expected_head() -> None:
    identity = finalizer._alembic_identity()

    assert identity["head"] == "0060_local_bridge_authority"
    assert identity["revision_count"] == 60
    assert identity["head_revision_path"].endswith("0060_local_bridge_authority.py")
