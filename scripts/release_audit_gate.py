"""Generate and independently verify release-bound supply-chain evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from scripts import p14_release_audit
except ModuleNotFoundError:  # Direct script execution from the repository root.
    import p14_release_audit  # type: ignore[no-redef]


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SHA256_PATTERN = re.compile(r"[a-f0-9]{64}")
COMMIT_PATTERN = re.compile(r"[a-f0-9]{40}")
ARTIFACT_NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{0,63}")
EVIDENCE_NAMES = {
    "sbom": {"root", "server", "gpu", "training", "acquisition"},
    "vulnerability": {"root", "server", "gpu", "training", "acquisition"},
    "license": {"dependency_licenses"},
    "secret_scan": {"repository_secret_scan"},
    "provenance": {"release_provenance"},
}
RELEASE_BOUNDARIES = {
    "DEVELOPMENT_RELEASE",
    "PRODUCTION_INPUTS_AUDIT_ONLY",
    "UNSIGNED_CANDIDATE_NOT_FOR_PUBLICATION",
}
MAX_FUTURE_SKEW = timedelta(minutes=5)


class ReleaseAuditError(RuntimeError):
    """A stable fail-closed release-audit validation error."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ReleaseAuditError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8-sig"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseAuditError(f"invalid JSON evidence file {path.name}: {error}") from error
    if not isinstance(value, dict):
        raise ReleaseAuditError(f"JSON evidence file must contain an object: {path.name}")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ReleaseAuditError(
            f"{context} fields do not match schema: missing={missing}, unexpected={unexpected}"
        )


def _timestamp(value: Any, context: str) -> datetime:
    if not isinstance(value, str):
        raise ReleaseAuditError(f"{context} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReleaseAuditError(f"{context} is not a valid ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReleaseAuditError(f"{context} must include a UTC offset")
    return parsed.astimezone(UTC)


def _commit(value: Any, context: str) -> str:
    if not isinstance(value, str) or COMMIT_PATTERN.fullmatch(value) is None:
        raise ReleaseAuditError(f"{context} must be a lowercase 40-character Git commit")
    return value


def _positive_int(value: Any, context: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ReleaseAuditError(f"{context} must be a non-negative integer")
    return value


def _record(path: Path, root: Path, name: str) -> dict[str, Any]:
    return {
        "name": name,
        "path": path.relative_to(root).as_posix(),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _resolve_record(root: Path, record: dict[str, Any], context: str) -> Path:
    _exact_keys(record, {"name", "path", "sha256", "size_bytes"}, context)
    relative_value = record["path"]
    if not isinstance(relative_value, str) or not relative_value or "\\" in relative_value:
        raise ReleaseAuditError(f"{context}.path must be a non-empty POSIX relative path")
    relative = PurePosixPath(relative_value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseAuditError(f"{context}.path escapes the evidence package")
    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ReleaseAuditError(f"{context} must not traverse a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ReleaseAuditError(f"{context} file is missing: {relative_value}") from error
    resolved_root = root.resolve(strict=True)
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ReleaseAuditError(f"{context}.path resolves outside the evidence package")
    if candidate.is_symlink() or not resolved.is_file():
        raise ReleaseAuditError(f"{context} must reference a regular non-symlink file")
    expected_size = _positive_int(record["size_bytes"], f"{context}.size_bytes")
    if resolved.stat().st_size != expected_size:
        raise ReleaseAuditError(f"{context} size does not match the evidence index")
    expected_hash = record["sha256"]
    if not isinstance(expected_hash, str) or SHA256_PATTERN.fullmatch(expected_hash) is None:
        raise ReleaseAuditError(f"{context}.sha256 is invalid")
    if _sha256(resolved) != expected_hash:
        raise ReleaseAuditError(f"{context} hash does not match the evidence index")
    return resolved


def _category_files(
    root: Path,
    evidence: dict[str, Any],
    category: str,
) -> dict[str, Path]:
    value = evidence.get(category)
    if not isinstance(value, dict):
        raise ReleaseAuditError(f"evidence.{category} must be an object")
    _exact_keys(value, {"status", "files"}, f"evidence.{category}")
    if value["status"] != "PASS":
        raise ReleaseAuditError(f"evidence.{category}.status must be PASS")
    records = value["files"]
    if not isinstance(records, list):
        raise ReleaseAuditError(f"evidence.{category}.files must be an array")
    paths: dict[str, Path] = {}
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            raise ReleaseAuditError(f"evidence.{category}.files[{index}] must be an object")
        name = item.get("name")
        if not isinstance(name, str) or ARTIFACT_NAME_PATTERN.fullmatch(name) is None:
            raise ReleaseAuditError(f"evidence.{category}.files[{index}].name is invalid")
        if name in paths:
            raise ReleaseAuditError(f"duplicate evidence name in {category}: {name}")
        paths[name] = _resolve_record(root, item, f"evidence.{category}.{name}")
    expected_names = EVIDENCE_NAMES[category]
    if set(paths) != expected_names:
        raise ReleaseAuditError(
            f"evidence.{category} names do not match: "
            f"missing={sorted(expected_names - set(paths))}, "
            f"unexpected={sorted(set(paths) - expected_names)}"
        )
    return paths


def _validate_sboms(paths: dict[str, Path]) -> None:
    for name, path in paths.items():
        document = _load_json(path)
        if document.get("bomFormat") != "CycloneDX" or document.get("specVersion") != "1.5":
            raise ReleaseAuditError(f"SBOM {name} is not CycloneDX 1.5")
        if not isinstance(document.get("components"), list):
            raise ReleaseAuditError(f"SBOM {name} has no component inventory")


def _validate_vulnerability_audits(paths: dict[str, Path]) -> None:
    for name, path in paths.items():
        document = _load_json(path)
        summary = document.get("summary")
        if not isinstance(summary, dict):
            raise ReleaseAuditError(f"vulnerability audit {name} has no summary")
        vulnerabilities = _positive_int(
            summary.get("vulnerabilities"), f"vulnerability audit {name}.vulnerabilities"
        )
        adverse_statuses = _positive_int(
            summary.get("adverse_statuses"), f"vulnerability audit {name}.adverse_statuses"
        )
        if vulnerabilities != 0 or adverse_statuses != 0:
            raise ReleaseAuditError(
                f"vulnerability audit {name} is not clean: "
                f"vulnerabilities={vulnerabilities}, adverse_statuses={adverse_statuses}"
            )


def _validate_license_evidence(path: Path) -> None:
    document = _load_json(path)
    if document.get("status") not in {"PASS", "PASS_WITH_DECLARED_PUBLICATION_REVIEW"}:
        raise ReleaseAuditError("license evidence does not have an accepted status")
    if _positive_int(document.get("unresolved_license_count"), "unresolved_license_count") != 0:
        raise ReleaseAuditError("license evidence contains unresolved dependencies")
    environments = document.get("environments")
    expected = {"root", "server", "gpu", "training", "acquisition", "android_release_runtime"}
    if not isinstance(environments, dict) or set(environments) != expected:
        raise ReleaseAuditError("license evidence does not cover every release dependency set")


def _validate_secret_scan(path: Path) -> None:
    document = _load_json(path)
    if document.get("status") != "PASS" or document.get("findings") != []:
        raise ReleaseAuditError("secret-scan evidence is not clean")


def _artifact_records(artifacts: dict[str, Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for name, path in sorted(artifacts.items()):
        if path.is_symlink():
            raise ReleaseAuditError(f"release artifact must not be a symlink: {name}")
        resolved = path.resolve(strict=True)
        if not resolved.is_file():
            raise ReleaseAuditError(f"release artifact must be a regular non-symlink file: {name}")
        records.append(
            {
                "filename": resolved.name,
                "name": name,
                "sha256": _sha256(resolved),
                "size_bytes": resolved.stat().st_size,
            }
        )
    return records


def _validate_provenance(
    path: Path,
    *,
    generated_at: str,
    source_commit: str,
    source_tree: str,
    artifacts: dict[str, Path],
    release_boundary: dict[str, Any],
) -> None:
    document = _load_json(path)
    _exact_keys(
        document,
        {
            "schema_version",
            "kind",
            "generated_at",
            "source",
            "inputs",
            "generator",
            "release_boundary",
        },
        "provenance",
    )
    if document["schema_version"] != 1 or document["kind"] != "AUTPLAY_RELEASE_PROVENANCE":
        raise ReleaseAuditError("provenance schema identity is invalid")
    if document["generated_at"] != generated_at:
        raise ReleaseAuditError("provenance timestamp does not match the evidence index")
    source = document["source"]
    if not isinstance(source, dict):
        raise ReleaseAuditError("provenance.source must be an object")
    _exact_keys(source, {"commit", "tree", "tracked_worktree_clean"}, "provenance.source")
    if _commit(source["commit"], "provenance.source.commit") != source_commit:
        raise ReleaseAuditError("provenance source commit does not match the requested commit")
    if source["tree"] != source_tree:
        raise ReleaseAuditError("provenance source tree does not match the requested commit")
    if source["tracked_worktree_clean"] is not True:
        raise ReleaseAuditError("provenance does not attest a clean tracked worktree")
    if document["inputs"] != _artifact_records(artifacts):
        raise ReleaseAuditError("provenance inputs do not match the exact release artifacts")
    if document["release_boundary"] != release_boundary:
        raise ReleaseAuditError("provenance release boundary does not match the evidence index")
    generator = document["generator"]
    if not isinstance(generator, dict) or set(generator) != {
        "p14_release_audit_sha256",
        "release_audit_gate_sha256",
    }:
        raise ReleaseAuditError("provenance generator identity is invalid")
    if any(
        not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None
        for value in generator.values()
    ):
        raise ReleaseAuditError("provenance generator hash is invalid")


def _validate_package_inventory(root: Path, expected_paths: set[Path]) -> None:
    actual_paths: set[Path] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ReleaseAuditError("release-audit package must not contain symlinks")
        if path.is_file():
            actual_paths.add(path.resolve(strict=True))
    if actual_paths != expected_paths:
        missing = sorted(
            path.relative_to(root).as_posix() for path in expected_paths - actual_paths
        )
        unexpected = sorted(
            path.relative_to(root).as_posix() for path in actual_paths - expected_paths
        )
        raise ReleaseAuditError(
            "release-audit package file inventory does not match the index: "
            f"missing={missing}, unexpected={unexpected}"
        )


def _validate_release_boundary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReleaseAuditError("release_boundary must be an object")
    _exact_keys(
        value,
        {"distribution_class", "production_signed", "production_deployed", "claim"},
        "release_boundary",
    )
    if value["distribution_class"] not in RELEASE_BOUNDARIES:
        raise ReleaseAuditError("release evidence has an unsupported distribution boundary")
    if value["production_signed"] is not False or value["production_deployed"] is not False:
        raise ReleaseAuditError(
            "this audit package cannot make production signing/deployment claims"
        )
    if value["claim"] != "AUDIT_EVIDENCE_ONLY_NO_PRODUCTION_RELEASE_CLAIM":
        raise ReleaseAuditError("release evidence claim boundary is invalid")
    return value


def verify_package(
    evidence_directory: Path,
    source_commit: str,
    artifacts: dict[str, Path],
    *,
    max_age_hours: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Verify a release-audit package against exact source and artifact inputs."""
    expected_commit = _commit(source_commit, "requested source commit")
    if max_age_hours <= 0:
        raise ReleaseAuditError("max_age_hours must be greater than zero")
    if not artifacts:
        raise ReleaseAuditError("at least one exact release artifact is required")
    root = evidence_directory.resolve(strict=True)
    if not root.is_dir():
        raise ReleaseAuditError("release-audit evidence path must be a directory")
    index = _load_json(root / "release-audit.json")
    _exact_keys(
        index,
        {
            "schema_version",
            "kind",
            "generated_at",
            "expires_at",
            "status",
            "source_commit",
            "evidence",
            "release_boundary",
        },
        "release-audit index",
    )
    if index["schema_version"] != 1 or index["kind"] != "AUTPLAY_RELEASE_AUDIT":
        raise ReleaseAuditError("release-audit schema identity is invalid")
    if index["status"] != "PASS":
        raise ReleaseAuditError("release-audit status must be PASS")
    if _commit(index["source_commit"], "release-audit source_commit") != expected_commit:
        raise ReleaseAuditError("release-audit source commit does not match the requested commit")
    try:
        expected_tree = _git(["rev-parse", f"{expected_commit}^{{tree}}"])
    except subprocess.CalledProcessError as error:
        raise ReleaseAuditError("requested source commit is unavailable") from error
    if COMMIT_PATTERN.fullmatch(expected_tree) is None:
        raise ReleaseAuditError("requested source tree identity is invalid")

    generated_at = _timestamp(index["generated_at"], "generated_at")
    expires_at = _timestamp(index["expires_at"], "expires_at")
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if generated_at > current + MAX_FUTURE_SKEW:
        raise ReleaseAuditError("release-audit evidence timestamp is unacceptably in the future")
    if expires_at <= generated_at:
        raise ReleaseAuditError("release-audit expiry must be after its generation time")
    if current > expires_at or current - generated_at > timedelta(hours=max_age_hours):
        raise ReleaseAuditError("release-audit evidence is stale or expired")

    release_boundary = _validate_release_boundary(index["release_boundary"])
    evidence = index["evidence"]
    if not isinstance(evidence, dict):
        raise ReleaseAuditError("evidence must be an object")
    _exact_keys(evidence, set(EVIDENCE_NAMES), "evidence")
    category_paths = {
        category: _category_files(root, evidence, category) for category in EVIDENCE_NAMES
    }
    _validate_sboms(category_paths["sbom"])
    _validate_vulnerability_audits(category_paths["vulnerability"])
    _validate_license_evidence(category_paths["license"]["dependency_licenses"])
    _validate_secret_scan(category_paths["secret_scan"]["repository_secret_scan"])
    _validate_provenance(
        category_paths["provenance"]["release_provenance"],
        generated_at=index["generated_at"],
        source_commit=expected_commit,
        source_tree=expected_tree,
        artifacts=artifacts,
        release_boundary=release_boundary,
    )
    expected_paths = {root / "release-audit.json"}
    expected_paths.update(
        path for category in category_paths.values() for path in category.values()
    )
    _validate_package_inventory(root, {path.resolve(strict=True) for path in expected_paths})
    return index


def _git(arguments: list[str]) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def generate_package(
    evidence_directory: Path,
    android_dependency_report: Path,
    source_commit: str,
    artifacts: dict[str, Path],
    *,
    distribution_class: str,
    valid_for_hours: int,
) -> Path:
    """Generate a fresh audit package without signing or deployment credentials."""
    expected_commit = _commit(source_commit, "requested source commit")
    if distribution_class not in RELEASE_BOUNDARIES:
        raise ReleaseAuditError("unsupported release distribution boundary")
    if valid_for_hours <= 0 or valid_for_hours > 168:
        raise ReleaseAuditError("valid_for_hours must be between 1 and 168")
    if not artifacts:
        raise ReleaseAuditError("at least one exact release artifact is required")
    if _git(["rev-parse", "HEAD"]) != expected_commit:
        raise ReleaseAuditError("requested source commit is not the checked-out HEAD")
    tracked_status = _git(["status", "--porcelain=v1", "--untracked-files=no"])
    if tracked_status:
        raise ReleaseAuditError("release evidence generation requires a clean tracked worktree")
    source_tree = _git(["rev-parse", f"{expected_commit}^{{tree}}"])
    if COMMIT_PATTERN.fullmatch(source_tree) is None:
        raise ReleaseAuditError("unable to resolve the source commit tree")
    dependency_report = android_dependency_report.resolve(strict=True)
    if not dependency_report.is_file():
        raise ReleaseAuditError("Android dependency report is missing")
    output = evidence_directory.resolve()
    try:
        output.relative_to(REPOSITORY_ROOT)
    except ValueError as error:
        raise ReleaseAuditError(
            "release evidence output must remain inside the repository"
        ) from error
    if output.exists():
        raise ReleaseAuditError(f"release evidence output already exists: {output}")
    output.mkdir(parents=True)

    collected = p14_release_audit.generate_release_supply_chain_evidence(
        output,
        dependency_report,
    )
    generated_at = datetime.now(UTC)
    generated_text = generated_at.isoformat()
    release_boundary = {
        "claim": "AUDIT_EVIDENCE_ONLY_NO_PRODUCTION_RELEASE_CLAIM",
        "distribution_class": distribution_class,
        "production_deployed": False,
        "production_signed": False,
    }
    provenance_path = output / "provenance.json"
    provenance = {
        "schema_version": 1,
        "kind": "AUTPLAY_RELEASE_PROVENANCE",
        "generated_at": generated_text,
        "source": {
            "commit": expected_commit,
            "tree": source_tree,
            "tracked_worktree_clean": True,
        },
        "inputs": _artifact_records(artifacts),
        "generator": {
            "p14_release_audit_sha256": _sha256(
                REPOSITORY_ROOT / "scripts" / "p14_release_audit.py"
            ),
            "release_audit_gate_sha256": _sha256(Path(__file__).resolve()),
        },
        "release_boundary": release_boundary,
    }
    _write_json(provenance_path, provenance)
    collected["provenance"] = {"release_provenance": provenance_path}
    evidence = {
        category: {
            "status": "PASS",
            "files": [
                _record(path, output, name) for name, path in sorted(collected[category].items())
            ],
        }
        for category in EVIDENCE_NAMES
    }
    index = {
        "schema_version": 1,
        "kind": "AUTPLAY_RELEASE_AUDIT",
        "generated_at": generated_text,
        "expires_at": (generated_at + timedelta(hours=valid_for_hours)).isoformat(),
        "status": "PASS",
        "source_commit": expected_commit,
        "evidence": evidence,
        "release_boundary": release_boundary,
    }
    index_path = output / "release-audit.json"
    _write_json(index_path, index)
    return index_path


def _parse_artifacts(values: list[str]) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    for value in values:
        name, separator, path_value = value.partition("=")
        if separator != "=" or ARTIFACT_NAME_PATTERN.fullmatch(name) is None or not path_value:
            raise ReleaseAuditError("artifact must use name=path with a lowercase stable name")
        if name in artifacts:
            raise ReleaseAuditError(f"duplicate release artifact name: {name}")
        supplied_path = Path(path_value)
        if supplied_path.is_symlink():
            raise ReleaseAuditError(f"release artifact must not be a symlink: {name}")
        path = supplied_path.resolve(strict=True)
        if not path.is_file():
            raise ReleaseAuditError(f"release artifact is missing: {name}")
        artifacts[name] = path
    return artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate")
    generate.add_argument("--output-directory", type=Path, required=True)
    generate.add_argument("--android-dependency-report", type=Path, required=True)
    generate.add_argument("--source-commit", required=True)
    generate.add_argument("--artifact", action="append", required=True)
    generate.add_argument("--distribution-class", choices=sorted(RELEASE_BOUNDARIES), required=True)
    generate.add_argument("--valid-for-hours", type=int, default=24)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--evidence-directory", type=Path, required=True)
    verify.add_argument("--source-commit", required=True)
    verify.add_argument("--artifact", action="append", required=True)
    verify.add_argument("--max-age-hours", type=int, default=24)
    arguments = parser.parse_args()
    try:
        artifacts = _parse_artifacts(arguments.artifact)
        if arguments.command == "generate":
            index_path = generate_package(
                arguments.output_directory,
                arguments.android_dependency_report,
                arguments.source_commit,
                artifacts,
                distribution_class=arguments.distribution_class,
                valid_for_hours=arguments.valid_for_hours,
            )
            print(f"release audit generated: {index_path}")
        else:
            index = verify_package(
                arguments.evidence_directory,
                arguments.source_commit,
                artifacts,
                max_age_hours=arguments.max_age_hours,
            )
            print(
                "release audit PASS: "
                f"source_commit={index['source_commit']}, artifacts={len(artifacts)}"
            )
    except (OSError, ReleaseAuditError, subprocess.CalledProcessError) as error:
        print(f"release audit FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
