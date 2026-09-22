"""Finalize exact production release inputs without signing or deployment authority."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import uuid
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from scripts import release_audit_gate
except ModuleNotFoundError:  # Direct script execution from the repository root.
    import release_audit_gate  # type: ignore[no-redef]


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = REPOSITORY_ROOT / "dist" / "production"
SHA256_PATTERN = re.compile(r"[a-f0-9]{64}")
IMAGE_DIGEST_PATTERN = re.compile(r"sha256:[a-f0-9]{64}")
COMMIT_PATTERN = re.compile(r"[a-f0-9]{40}")
VERSION_NAME_PATTERN = re.compile(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,127}")
ARTIFACT_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
ANDROID_MANIFEST_FIELDS = {
    "schema_version",
    "status",
    "release_kind",
    "generated_at_utc",
    "release_tag",
    "source_commit",
    "application_id",
    "version_code",
    "version_name",
    "signer_certificate_sha256",
    "apk",
    "room_schema",
    "secrets",
}
ANDROID_APK_FIELDS = {
    "filename",
    "application_id",
    "version_code",
    "version_name",
    "bytes",
    "sha256",
    "certificate_sha256",
    "apksigner",
    "package_identity",
}
ROOM_SCHEMA_FIELDS = {"database", "version", "identity_hash", "repository_path", "sha256"}
CORE_ARTIFACT_NAMES = {
    "android_dependency_report",
    "android_production_manifest",
    "android_production_signed",
    "server_archive",
}


class ProductionReleaseError(RuntimeError):
    """A stable fail-closed production release finalization error."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ProductionReleaseError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load_json(path: Path, context: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8-sig"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProductionReleaseError(f"invalid {context}: {error}") from error
    if not isinstance(value, dict):
        raise ProductionReleaseError(f"{context} must contain a JSON object")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _exact_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ProductionReleaseError(
            f"{context} fields do not match schema: "
            f"missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
        )


def _regular_file(path: Path, context: str) -> Path:
    if path.is_symlink():
        raise ProductionReleaseError(f"{context} must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ProductionReleaseError(f"{context} is missing") from error
    if not resolved.is_file():
        raise ProductionReleaseError(f"{context} must be a regular file")
    return resolved


def _record(path: Path, root: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(root).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _git(arguments: list[str]) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _assert_source_identity(source_commit: str, release_tag: str) -> str:
    if COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ProductionReleaseError("source commit must be a lowercase full Git commit")
    if (
        not release_tag
        or release_tag.startswith("-")
        or any(char.isspace() for char in release_tag)
    ):
        raise ProductionReleaseError("release tag is invalid")
    try:
        _git(["check-ref-format", f"refs/tags/{release_tag}"])
    except subprocess.CalledProcessError as error:
        raise ProductionReleaseError("release tag is invalid") from error
    if _git(["rev-parse", "HEAD"]) != source_commit:
        raise ProductionReleaseError("source commit does not match checked-out HEAD")
    if _git(["status", "--porcelain=v1", "--untracked-files=all"]):
        raise ProductionReleaseError("production finalization requires a clean worktree")
    try:
        tag_commit = _git(["rev-parse", "--verify", f"refs/tags/{release_tag}^{{commit}}"])
    except subprocess.CalledProcessError as error:
        raise ProductionReleaseError("release tag does not resolve to a commit") from error
    if tag_commit != source_commit:
        raise ProductionReleaseError("release tag does not resolve to source commit")
    source_tree = _git(["rev-parse", f"{source_commit}^{{tree}}"])
    if COMMIT_PATTERN.fullmatch(source_tree) is None:
        raise ProductionReleaseError("source tree identity is invalid")
    return source_tree


def _validate_hash(value: Any, context: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise ProductionReleaseError(f"{context} must be a lowercase SHA-256")
    return value


def _validate_positive_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ProductionReleaseError(f"{context} must be a positive integer")
    return value


def _validate_android_manifest(
    manifest_path: Path,
    apk_path: Path,
    *,
    source_commit: str,
    release_tag: str,
    version_code: int,
    version_name: str,
    signer_certificate_sha256: str,
) -> dict[str, Any]:
    document = _load_json(manifest_path, "Android production manifest")
    _exact_keys(document, ANDROID_MANIFEST_FIELDS, "Android production manifest")
    if (
        document["schema_version"] != 1
        or document["status"] != "PASS"
        or document["release_kind"] != "ANDROID_PRODUCTION_APK"
        or document["secrets"] != "NOT_RECORDED"
    ):
        raise ProductionReleaseError("Android production manifest identity is invalid")
    expected_identity = {
        "release_tag": release_tag,
        "source_commit": source_commit,
        "application_id": "app.autplay",
        "version_code": version_code,
        "version_name": version_name,
        "signer_certificate_sha256": signer_certificate_sha256,
    }
    for key, expected in expected_identity.items():
        if document[key] != expected:
            raise ProductionReleaseError(f"Android production manifest {key} does not match")

    apk = document["apk"]
    if not isinstance(apk, dict):
        raise ProductionReleaseError("Android production manifest apk must be an object")
    _exact_keys(apk, ANDROID_APK_FIELDS, "Android production manifest apk")
    if (
        apk["filename"] != apk_path.name
        or apk["application_id"] != "app.autplay"
        or apk["version_code"] != version_code
        or apk["version_name"] != version_name
        or apk["certificate_sha256"] != signer_certificate_sha256
        or apk["apksigner"] != "PASS"
        or apk["package_identity"] != "PASS"
    ):
        raise ProductionReleaseError("Android APK identity does not match finalization inputs")
    if apk["bytes"] != apk_path.stat().st_size or apk["sha256"] != _sha256(apk_path):
        raise ProductionReleaseError("Android APK bytes do not match its signed-build manifest")

    room_schema = document["room_schema"]
    if not isinstance(room_schema, dict):
        raise ProductionReleaseError("Android Room schema identity must be an object")
    _exact_keys(room_schema, ROOM_SCHEMA_FIELDS, "Android Room schema identity")
    _validate_room_schema(room_schema)
    return document


def _validate_room_schema(record: dict[str, Any]) -> None:
    database = "app.autplay.data.local.AutPlayDatabase"
    version = _validate_positive_int(record["version"], "Room schema version")
    if record["database"] != database:
        raise ProductionReleaseError("Room schema database identity is invalid")
    relative_value = record["repository_path"]
    if not isinstance(relative_value, str) or "\\" in relative_value:
        raise ProductionReleaseError("Room schema repository path is invalid")
    relative = PurePosixPath(relative_value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ProductionReleaseError("Room schema repository path escapes the repository")
    expected_relative = PurePosixPath("apps", "android", "schemas", database, f"{version}.json")
    if relative != expected_relative:
        raise ProductionReleaseError("Room schema is not the expected versioned schema path")
    schema_path = _regular_file(REPOSITORY_ROOT.joinpath(*relative.parts), "Room schema")
    if _sha256(schema_path) != _validate_hash(record["sha256"], "Room schema sha256"):
        raise ProductionReleaseError("Room schema hash does not match the repository")
    schema = _load_json(schema_path, "Room schema")
    database_value = schema.get("database")
    if not isinstance(database_value, dict):
        raise ProductionReleaseError("Room schema database payload is invalid")
    if database_value.get("version") != version:
        raise ProductionReleaseError("Room schema version does not match its manifest")
    identity_hash = record["identity_hash"]
    if (
        not isinstance(identity_hash, str)
        or re.fullmatch(r"[a-f0-9]{32}", identity_hash) is None
        or database_value.get("identityHash") != identity_hash
    ):
        raise ProductionReleaseError("Room schema identity hash does not match its manifest")
    schema_root = schema_path.parent
    versions = sorted(
        int(path.stem)
        for path in schema_root.glob("*.json")
        if path.is_file() and re.fullmatch(r"[1-9][0-9]*", path.stem)
    )
    if not versions or versions[-1] != version:
        raise ProductionReleaseError("Room schema manifest does not name the latest schema")


def _literal_assignment(module: ast.Module, name: str, path: Path) -> Any:
    matches: list[ast.expr] = []
    for node in module.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                matches.append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            matches.append(node.value)
    if len(matches) != 1:
        raise ProductionReleaseError(f"migration {path.name} must define exactly one {name}")
    try:
        return ast.literal_eval(matches[0])
    except (ValueError, TypeError) as error:
        raise ProductionReleaseError(f"migration {path.name} has non-literal {name}") from error


def _alembic_identity() -> dict[str, Any]:
    migrations_root = REPOSITORY_ROOT / "server" / "migrations" / "versions"
    files = sorted(migrations_root.glob("*.py"))
    if not files:
        raise ProductionReleaseError("Alembic migration inventory is empty")
    revisions: dict[str, tuple[str | None, Path]] = {}
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ProductionReleaseError("Alembic migration inventory contains a non-regular file")
        try:
            module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as error:
            raise ProductionReleaseError(f"unable to parse migration {path.name}") from error
        revision = _literal_assignment(module, "revision", path)
        down_revision = _literal_assignment(module, "down_revision", path)
        if not isinstance(revision, str) or not revision:
            raise ProductionReleaseError(f"migration {path.name} has invalid revision")
        if down_revision is not None and not isinstance(down_revision, str):
            raise ProductionReleaseError("production migrations must have one linear predecessor")
        if revision in revisions:
            raise ProductionReleaseError(f"duplicate Alembic revision: {revision}")
        revisions[revision] = (down_revision, path)
    referenced = {down for down, _path in revisions.values() if down is not None}
    unknown = referenced - set(revisions)
    if unknown:
        raise ProductionReleaseError(
            f"Alembic migration has unknown predecessor: {sorted(unknown)}"
        )
    heads = set(revisions) - referenced
    if len(heads) != 1:
        raise ProductionReleaseError(f"Alembic migration graph must have one head: {sorted(heads)}")
    head = next(iter(heads))
    visited: set[str] = set()
    current: str | None = head
    while current is not None:
        if current in visited:
            raise ProductionReleaseError("Alembic migration graph contains a cycle")
        visited.add(current)
        current = revisions[current][0]
    if visited != set(revisions):
        raise ProductionReleaseError("Alembic migration graph is not one linear chain")
    head_path = revisions[head][1]
    return {
        "head": head,
        "revision_count": len(revisions),
        "head_revision_path": head_path.relative_to(REPOSITORY_ROOT).as_posix(),
        "head_revision_sha256": _sha256(head_path),
    }


def _safe_tar_member(archive: tarfile.TarFile, name: str, context: str) -> tarfile.TarInfo:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise ProductionReleaseError(f"{context} path is unsafe")
    try:
        member = archive.getmember(name)
    except KeyError as error:
        raise ProductionReleaseError(f"{context} is missing from the server archive") from error
    if not member.isfile() or member.issym() or member.islnk():
        raise ProductionReleaseError(f"{context} must be a regular archive member")
    return member


def _read_tar_json(
    archive: tarfile.TarFile,
    name: str,
    context: str,
    *,
    max_bytes: int,
) -> Any:
    member = _safe_tar_member(archive, name, context)
    if member.size < 1 or member.size > max_bytes:
        raise ProductionReleaseError(f"{context} has an invalid size")
    stream = archive.extractfile(member)
    if stream is None:
        raise ProductionReleaseError(f"{context} cannot be read")
    try:
        return json.loads(stream.read(max_bytes + 1).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ProductionReleaseError(f"{context} is invalid JSON") from error


def _server_image_identity(archive_path: Path) -> dict[str, Any]:
    try:
        with tarfile.open(archive_path, mode="r:*") as archive:
            manifest = _read_tar_json(
                archive, "manifest.json", "Docker archive manifest", max_bytes=1024 * 1024
            )
            if (
                not isinstance(manifest, list)
                or len(manifest) != 1
                or not isinstance(manifest[0], dict)
            ):
                raise ProductionReleaseError("server archive must contain exactly one Docker image")
            entry = manifest[0]
            config_name = entry.get("Config")
            repo_tags = entry.get("RepoTags")
            if not isinstance(config_name, str):
                raise ProductionReleaseError("Docker archive config identity is missing")
            config_path = PurePosixPath(config_name)
            legacy_config = (
                len(config_path.parts) == 1
                and re.fullmatch(r"[a-f0-9]{64}\.json", config_path.name) is not None
            )
            oci_blob_config = (
                len(config_path.parts) == 3
                and config_path.parts[:2] == ("blobs", "sha256")
                and re.fullmatch(r"[a-f0-9]{64}", config_path.name) is not None
            )
            if not legacy_config and not oci_blob_config:
                raise ProductionReleaseError("Docker archive config path is invalid")
            expected_config_digest = config_path.stem if legacy_config else config_path.name
            config_member = _safe_tar_member(archive, config_name, "Docker image configuration")
            if config_member.size < 1 or config_member.size > 16 * 1024 * 1024:
                raise ProductionReleaseError("Docker image configuration has an invalid size")
            config_stream = archive.extractfile(config_member)
            if config_stream is None:
                raise ProductionReleaseError("Docker image configuration cannot be read")
            config_bytes = config_stream.read(16 * 1024 * 1024 + 1)
            config_digest = hashlib.sha256(config_bytes).hexdigest()
            if config_digest != expected_config_digest:
                raise ProductionReleaseError("Docker image configuration digest is invalid")
            try:
                config = json.loads(config_bytes.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as error:
                raise ProductionReleaseError(
                    "Docker image configuration is invalid JSON"
                ) from error
    except (OSError, tarfile.TarError) as error:
        raise ProductionReleaseError(
            "server archive is not a readable Docker image archive"
        ) from error
    if not isinstance(config, dict):
        raise ProductionReleaseError("Docker image configuration must be an object")
    if config.get("os") != "linux" or config.get("architecture") != "amd64":
        raise ProductionReleaseError("server image must target linux/amd64")
    if repo_tags is None:
        normalized_tags: list[str] = []
    elif isinstance(repo_tags, list) and all(isinstance(value, str) for value in repo_tags):
        normalized_tags = sorted(set(repo_tags))
    else:
        raise ProductionReleaseError("Docker archive RepoTags must be an array of strings")
    digest = f"sha256:{config_digest}"
    if IMAGE_DIGEST_PATTERN.fullmatch(digest) is None:
        raise ProductionReleaseError("server image digest is invalid")
    return {
        "archive_format": "DOCKER_IMAGE_ARCHIVE",
        "image_digest": digest,
        "digest_kind": "DOCKER_CONFIG_DIGEST",
        "os": "linux",
        "architecture": "amd64",
        "repo_tags": normalized_tags,
        "runtime": "CPU_ONLY",
    }


def _copy_artifact(source: Path, destination: Path, context: str) -> Path:
    resolved = _regular_file(source, context)
    if destination.exists():
        raise ProductionReleaseError(f"duplicate production artifact filename: {destination.name}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(resolved, destination)
    if _sha256(resolved) != _sha256(destination):
        raise ProductionReleaseError(f"{context} copy hash mismatch")
    return destination


def _write_checksums(root: Path) -> Path:
    checksum_path = root / "SHA256SUMS"
    lines = [
        f"{_sha256(path)}  {path.relative_to(root).as_posix()}"
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix())
        if path.is_file() and path != checksum_path
    ]
    checksum_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return checksum_path


def _parse_extra_artifacts(values: list[str]) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for value in values:
        name, separator, path_value = value.partition("=")
        if separator != "=" or ARTIFACT_NAME_PATTERN.fullmatch(name) is None or not path_value:
            raise ProductionReleaseError("extra artifact must use lowercase_name=path")
        if name in CORE_ARTIFACT_NAMES or name in artifacts:
            raise ProductionReleaseError(f"duplicate or reserved extra artifact name: {name}")
        artifacts[name] = _regular_file(Path(path_value), f"extra artifact {name}")
    return artifacts


def finalize_release(
    *,
    output_directory: Path,
    android_manifest: Path,
    android_apk: Path,
    android_dependency_report: Path,
    server_archive: Path,
    source_commit: str,
    release_tag: str,
    version_code: int,
    version_name: str,
    signer_certificate_sha256: str,
    extra_artifacts: dict[str, Path],
    audit_valid_for_hours: int,
) -> Path:
    """Create one atomic, audited manifest for exact production release inputs."""
    if version_code < 1 or VERSION_NAME_PATTERN.fullmatch(version_name) is None:
        raise ProductionReleaseError("production version identity is invalid")
    if release_tag != f"v{version_name}":
        raise ProductionReleaseError("release tag must equal v plus versionName")
    signer_fingerprint = _validate_hash(
        signer_certificate_sha256, "production signer certificate sha256"
    )
    source_tree = _assert_source_identity(source_commit, release_tag)

    output = output_directory.resolve()
    production_root = PRODUCTION_ROOT.resolve()
    try:
        output.relative_to(production_root)
    except ValueError as error:
        raise ProductionReleaseError(
            f"production output must remain under {production_root}"
        ) from error
    if output == production_root or output.exists():
        raise ProductionReleaseError("production output must be a new child directory")

    apk_input = _regular_file(android_apk, "production APK")
    android_manifest_input = _regular_file(android_manifest, "Android production manifest")
    dependency_input = _regular_file(android_dependency_report, "Android release dependency report")
    server_input = _regular_file(server_archive, "server image archive")
    android_document = _validate_android_manifest(
        android_manifest_input,
        apk_input,
        source_commit=source_commit,
        release_tag=release_tag,
        version_code=version_code,
        version_name=version_name,
        signer_certificate_sha256=signer_fingerprint,
    )
    alembic = _alembic_identity()
    server_image = _server_image_identity(server_input)

    production_root.mkdir(parents=True, exist_ok=True)
    staging = production_root / f".{output.name}.staging-{uuid.uuid4().hex}"
    if staging.exists():
        raise ProductionReleaseError("production staging directory already exists")
    staging.mkdir()
    published = False
    try:
        apk_copy = _copy_artifact(
            apk_input,
            staging / f"autplay-{version_name}-production.apk",
            "production APK",
        )
        manifest_copy = _copy_artifact(
            android_manifest_input,
            staging / "android-production-manifest.json",
            "Android production manifest",
        )
        dependency_copy = _copy_artifact(
            dependency_input,
            staging / "ANDROID_RELEASE_DEPENDENCIES.txt",
            "Android release dependency report",
        )
        archive_copy = _copy_artifact(
            server_input,
            staging / f"autplay-server-{release_tag}.docker.tar.gz",
            "server image archive",
        )
        audit_artifacts: dict[str, Path] = {
            "android_dependency_report": dependency_copy,
            "android_production_manifest": manifest_copy,
            "android_production_signed": apk_copy,
            "server_archive": archive_copy,
        }
        extra_records: dict[str, dict[str, Any]] = {}
        for name, source in sorted(extra_artifacts.items()):
            destination = _copy_artifact(
                source,
                staging / "extra" / source.name,
                f"extra artifact {name}",
            )
            audit_artifacts[name] = destination
            extra_records[name] = _record(destination, staging)

        audit_root = staging / "release-audit"
        release_audit_gate.generate_package(
            audit_root,
            dependency_copy,
            source_commit,
            audit_artifacts,
            distribution_class="PRODUCTION_INPUTS_AUDIT_ONLY",
            valid_for_hours=audit_valid_for_hours,
        )
        audit_index = release_audit_gate.verify_package(
            audit_root,
            source_commit,
            audit_artifacts,
            max_age_hours=audit_valid_for_hours,
        )
        release_boundary = audit_index.get("release_boundary")
        if (
            not isinstance(release_boundary, dict)
            or release_boundary.get("distribution_class") != "PRODUCTION_INPUTS_AUDIT_ONLY"
        ):
            raise ProductionReleaseError(
                "release audit did not retain its production-input boundary"
            )

        generated_at = datetime.now(UTC).isoformat()
        manifest = {
            "schema_version": 1,
            "kind": "AUTPLAY_PRODUCTION_RELEASE_INPUTS",
            "status": "PASS",
            "distribution_class": "PRODUCTION_RELEASE_CANDIDATE",
            "generated_at": generated_at,
            "release_tag": release_tag,
            "source": {
                "commit": source_commit,
                "tree": source_tree,
                "worktree_clean": True,
            },
            "production_signed": True,
            "production_deployed": False,
            "android": {
                "application_id": "app.autplay",
                "version_code": version_code,
                "version_name": version_name,
                "signer_certificate_sha256": signer_fingerprint,
                "apk": _record(apk_copy, staging),
                "signed_build_manifest": _record(manifest_copy, staging),
                "room_schema": android_document["room_schema"],
            },
            "server": {
                **server_image,
                "archive": _record(archive_copy, staging),
            },
            "database": {"alembic": alembic},
            "release_audit": {
                "status": "PASS",
                "distribution_class": "PRODUCTION_INPUTS_AUDIT_ONLY",
                "generated_at": audit_index["generated_at"],
                "expires_at": audit_index["expires_at"],
                "index": _record(audit_root / "release-audit.json", staging),
            },
            "extra_artifacts": extra_records,
            "activation": {
                "status": "BLOCKED_PENDING_TARGET_ACCEPTANCE_AND_OPERATOR_APPROVAL",
                "production_deployed": False,
            },
            "secrets": "NOT_RECORDED",
        }
        manifest_path = staging / "production-release-manifest.json"
        _write_json(manifest_path, manifest)
        _write_checksums(staging)
        staging.rename(output)
        published = True
        return output / manifest_path.name
    except (
        OSError,
        subprocess.CalledProcessError,
        release_audit_gate.ReleaseAuditError,
    ) as error:
        raise ProductionReleaseError(f"production release finalization failed: {error}") from error
    finally:
        if not published and staging.exists():
            resolved_staging = staging.resolve()
            if resolved_staging.parent != production_root or not resolved_staging.name.startswith(
                "."
            ):
                raise ProductionReleaseError("refusing unsafe production staging cleanup")
            shutil.rmtree(resolved_staging)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--android-manifest", type=Path, required=True)
    parser.add_argument("--android-apk", type=Path, required=True)
    parser.add_argument("--android-dependency-report", type=Path, required=True)
    parser.add_argument("--server-archive", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--version-code", type=int, required=True)
    parser.add_argument("--version-name", required=True)
    parser.add_argument("--signer-certificate-sha256", required=True)
    parser.add_argument("--artifact", action="append", default=[])
    parser.add_argument("--audit-valid-for-hours", type=int, default=24)
    arguments = parser.parse_args()
    try:
        manifest_path = finalize_release(
            output_directory=arguments.output_directory,
            android_manifest=arguments.android_manifest,
            android_apk=arguments.android_apk,
            android_dependency_report=arguments.android_dependency_report,
            server_archive=arguments.server_archive,
            source_commit=arguments.source_commit,
            release_tag=arguments.release_tag,
            version_code=arguments.version_code,
            version_name=arguments.version_name,
            signer_certificate_sha256=arguments.signer_certificate_sha256,
            extra_artifacts=_parse_extra_artifacts(arguments.artifact),
            audit_valid_for_hours=arguments.audit_valid_for_hours,
        )
        print(f"production release inputs finalized: {manifest_path}")
    except (OSError, ProductionReleaseError, subprocess.CalledProcessError) as error:
        print(f"production release finalization FAIL: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
