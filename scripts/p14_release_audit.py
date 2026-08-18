"""Generate local-only P14 SBOM, vulnerability, secret and artifact evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ElementTree
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    REPOSITORY_ROOT / "server" / "src",
    REPOSITORY_ROOT / "gpu" / "src",
    REPOSITORY_ROOT / "apps" / "android" / "src" / "main",
    REPOSITORY_ROOT / "deploy",
    REPOSITORY_ROOT / "scripts",
)
TEXT_SUFFIXES = {
    ".ini",
    ".json",
    ".kt",
    ".kts",
    ".md",
    ".properties",
    ".ps1",
    ".py",
    ".sh",
    ".toml",
    ".xml",
    ".yaml",
    ".yml",
}
SECRET_PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "github_token": re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b"),
    "slack_token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "url_secret": re.compile(
        r"https?://[^\s\"']+[?&](?:access_token|api_key|password|secret|token)=[^\s&#\"']+",
        re.IGNORECASE,
    ),
}
DISPOSABLE_PASSWORD_PATTERN = re.compile(r"\bautplay_dev_only\b")
DISPOSABLE_PASSWORD_ALLOWLIST = {
    "deploy/compose/compose.yaml",
    "deploy/compose/compose.runtime.yaml",
    "deploy/compose/README.md",
    "scripts/check.ps1",
    "scripts/check.sh",
    "scripts/p14_drill.py",
    "scripts/p14_release_audit.py",
}
INTERNAL_PACKAGES = {
    "autplay-codex-harness",
    "autplay-gpu-worker",
    "autplay-server",
}
PYTHON_LICENSE_SCRIPT = """
import importlib.metadata as metadata
import json

rows = []
for distribution in sorted(
    metadata.distributions(),
    key=lambda item: (item.metadata.get("Name") or "").lower(),
):
    document = distribution.metadata
    rows.append(
        {
            "name": document.get("Name"),
            "version": distribution.version,
            "license_expression": document.get("License-Expression"),
            "license": document.get("License"),
            "classifiers": [
                value
                for value in (document.get_all("Classifier") or [])
                if value.startswith("License ::")
            ],
        }
    )
print(json.dumps(rows, separators=(",", ":")))
"""


def _run(arguments: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        arguments,
        cwd=REPOSITORY_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _android_smoke_apk(output_directory: Path, apksigner: Path) -> Path:
    """Return the build-bound, v2/v3-verified RC artifact for physical-device smoke."""
    apk = REPOSITORY_ROOT / "docs" / "release" / "artifacts" / "autplay-rc1-dev-signed.apk"
    if not apk.is_file():
        raise RuntimeError("dev-signed RC APK is missing; run scripts/build-p14-rc.ps1 first")
    build_evidence_path = output_directory / "P14_RELEASE_BUILD.json"
    if not build_evidence_path.is_file():
        raise RuntimeError("P14 release-build evidence is missing")
    build_evidence = json.loads(build_evidence_path.read_text(encoding="utf-8-sig"))
    if not isinstance(build_evidence, dict):
        raise RuntimeError("P14 release-build evidence must be a JSON object")
    android = build_evidence.get("android")
    device = build_evidence.get("device")
    if (
        build_evidence.get("status") != "PASS"
        or not isinstance(android, dict)
        or not isinstance(device, dict)
        or device.get("physical_samsung_a55") is not True
    ):
        raise RuntimeError("P14 release-build evidence does not contain a physical A55 PASS")
    expected_hash = android.get("dev_signed_rc_sha256")
    actual_hash = _sha256(apk)
    if not isinstance(expected_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
        raise RuntimeError("P14 release-build evidence has an invalid dev-signed APK hash")
    if actual_hash != expected_hash:
        raise RuntimeError("dev-signed RC APK does not match P14 release-build evidence")
    if not apksigner.is_file():
        raise RuntimeError("pinned Android apksigner is missing")
    signature_output = _run([str(apksigner), "verify", "--verbose", str(apk)])
    required_signature_lines = (
        "Verified using v2 scheme (APK Signature Scheme v2): true",
        "Verified using v3 scheme (APK Signature Scheme v3): true",
    )
    if any(line not in signature_output for line in required_signature_lines):
        raise RuntimeError("dev-signed RC APK is not verified with both v2 and v3 signatures")
    return apk


def _release_status(android_device: dict[str, Any]) -> str:
    if android_device.get("physical_samsung_a55") is True:
        return "PASS"
    return "PASS_WITH_PHYSICAL_DEVICE_GATE_REPORTED_SEPARATELY"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _export_sbom(project: str | None, output: Path) -> dict[str, Any]:
    arguments = ["uv", "export"]
    if project is not None:
        arguments.extend(["--project", project])
    arguments.extend(["--frozen", "--format", "cyclonedx1.5", "--output-file", str(output)])
    _run(arguments)
    document = json.loads(output.read_text(encoding="utf-8"))
    return {
        "path": output.relative_to(REPOSITORY_ROOT).as_posix(),
        "sha256": _sha256(output),
        "components": len(document.get("components", [])),
        "format": "CycloneDX 1.5",
    }


def _audit(project: str | None, output: Path) -> dict[str, Any]:
    arguments = [
        "uv",
        "audit",
        "--preview-features",
        "audit-command",
        "--preview-features",
        "json-output",
    ]
    if project is not None:
        arguments.extend(["--project", project])
    arguments.extend(["--frozen", "--output-format", "json"])
    document = json.loads(_run(arguments))
    _write_json(output, document)
    summary = document.get("summary", {})
    vulnerabilities = int(summary.get("vulnerabilities", -1))
    adverse_statuses = int(summary.get("adverse_statuses", -1))
    if vulnerabilities != 0 or adverse_statuses != 0:
        raise RuntimeError(
            f"dependency audit is not clean: vulnerabilities={vulnerabilities}, "
            f"adverse_statuses={adverse_statuses}"
        )
    return {
        "path": output.relative_to(REPOSITORY_ROOT).as_posix(),
        "sha256": _sha256(output),
        "audited_packages": int(summary.get("audited_packages", 0)),
        "vulnerabilities": vulnerabilities,
        "adverse_statuses": adverse_statuses,
        "service": "OSV via uv audit",
        "status": "PASS",
    }


def _secret_scan() -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    scanned_files = 0
    scanned_bytes = 0
    for root in SOURCE_ROOTS:
        if not root.exists():
            continue
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            relative = path.relative_to(REPOSITORY_ROOT).as_posix()
            if any(part in {"build", "__pycache__", ".gradle"} for part in path.parts):
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            scanned_files += 1
            scanned_bytes += len(content.encode("utf-8"))
            for name, pattern in SECRET_PATTERNS.items():
                for match in pattern.finditer(content):
                    findings.append(
                        {
                            "rule": name,
                            "path": relative,
                            "line": content.count("\n", 0, match.start()) + 1,
                        }
                    )
            if relative not in DISPOSABLE_PASSWORD_ALLOWLIST:
                for match in DISPOSABLE_PASSWORD_PATTERN.finditer(content):
                    findings.append(
                        {
                            "rule": "disposable_password_outside_allowlist",
                            "path": relative,
                            "line": content.count("\n", 0, match.start()) + 1,
                        }
                    )
    if findings:
        summary = ", ".join(
            f"{finding['rule']}:{finding['path']}:{finding['line']}" for finding in findings
        )
        raise RuntimeError(
            f"production-source secret scan found {len(findings)} issue(s): {summary}"
        )
    return {
        "status": "PASS",
        "scanned_files": scanned_files,
        "scanned_bytes": scanned_bytes,
        "findings": findings,
        "allowlisted_disposable_credential": {
            "value_disclosed": False,
            "paths": sorted(DISPOSABLE_PASSWORD_ALLOWLIST),
            "scope": "loopback/disposable development Compose only",
        },
        "rules": sorted([*SECRET_PATTERNS, "disposable_password_outside_allowlist"]),
    }


def _android_dependency_inventory(output: Path, java_home: str) -> dict[str, Any]:
    environment = os.environ.copy()
    environment["JAVA_HOME"] = java_home
    report = _run(
        [
            str(REPOSITORY_ROOT / "gradlew.bat"),
            f"-Dorg.gradle.java.home={java_home}",
            "--no-daemon",
            "--console=plain",
            ":apps:android:dependencies",
            "--configuration",
            "releaseRuntimeClasspath",
        ],
        env=environment,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    return {
        "path": output.relative_to(REPOSITORY_ROOT).as_posix(),
        "sha256": _sha256(output),
        "configuration": "releaseRuntimeClasspath",
    }


def _python_licenses(project: str | None) -> list[dict[str, Any]]:
    arguments = ["uv", "run"]
    if project is not None:
        arguments.extend(["--project", project])
    arguments.extend(["--frozen", "python", "-c", PYTHON_LICENSE_SCRIPT])
    rows = json.loads(_run(arguments))
    if not isinstance(rows, list):
        raise RuntimeError("Python license inventory returned an invalid document")
    inventory: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Python license inventory returned an invalid row")
        name = str(row.get("name") or "").strip()
        version = str(row.get("version") or "").strip()
        evidence = _python_license_evidence(row, name=name)
        inventory.append(
            {
                "name": name,
                "version": version,
                "license_evidence": evidence,
                "review": _license_review(evidence),
            }
        )
    return inventory


def _python_license_evidence(row: dict[str, Any], *, name: str) -> list[str]:
    if name.lower() in INTERNAL_PACKAGES:
        return ["PROJECT-INTERNAL-NOT-PUBLISHED"]
    values: list[str] = []
    for key in ("license_expression", "license"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            normalized = " ".join(value.split())
            values.append(
                normalized if len(normalized) <= 300 else f"TEXT_SHA256:{_text_sha256(normalized)}"
            )
    classifiers = row.get("classifiers")
    if isinstance(classifiers, list):
        values.extend(str(value) for value in classifiers if str(value).strip())
    if not values:
        raise RuntimeError(f"Python package license metadata is missing: {name}")
    return sorted(set(values))


def _android_licenses(dependency_report: Path) -> list[dict[str, Any]]:
    coordinates: set[tuple[str, str, str]] = set()
    pattern = re.compile(
        r"([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+):([A-Za-z0-9_.-]+)"
        r"(?:\s+->\s+([A-Za-z0-9_.-]+))?"
    )
    for line in dependency_report.read_text(encoding="utf-8").splitlines():
        if "--- " not in line:
            continue
        for match in pattern.finditer(line):
            group, artifact, declared, resolved = match.groups()
            coordinates.add((group, artifact, resolved or declared))
    cache = Path(os.environ.get("GRADLE_USER_HOME", Path.home() / ".gradle"))
    cache /= "caches/modules-2/files-2.1"
    inventory: list[dict[str, Any]] = []
    for group, artifact, version in sorted(coordinates):
        pom_candidates = sorted((cache / group / artifact / version).glob("*/*.pom"))
        if not pom_candidates:
            raise RuntimeError(
                f"Android dependency POM is unavailable: {group}:{artifact}:{version}"
            )
        evidence, inherited_from = _pom_license_evidence(pom_candidates[0], cache=cache)
        if not evidence:
            raise RuntimeError(
                f"Android dependency license metadata is missing: {group}:{artifact}:{version}"
            )
        inventory.append(
            {
                "coordinate": f"{group}:{artifact}:{version}",
                "license_evidence": evidence,
                "metadata_source": inherited_from,
                "review": _license_review(evidence),
            }
        )
    return inventory


def _pom_license_evidence(
    pom: Path,
    *,
    cache: Path,
    depth: int = 0,
) -> tuple[list[str], str]:
    if depth > 4:
        return [], "unresolved-parent"
    root = ElementTree.parse(pom).getroot()
    evidence: list[str] = []
    for license_node in root.findall("./{*}licenses/{*}license"):
        name = license_node.findtext("{*}name")
        url = license_node.findtext("{*}url")
        value = " | ".join(part.strip() for part in (name, url) if part and part.strip())
        if value:
            evidence.append(value)
    if evidence:
        return sorted(set(evidence)), "pom"
    parent = root.find("./{*}parent")
    if parent is None:
        return [], "pom"
    group = parent.findtext("{*}groupId")
    artifact = parent.findtext("{*}artifactId")
    version = parent.findtext("{*}version")
    if not group or not artifact or not version:
        return [], "pom"
    parents = sorted((cache / group / artifact / version).glob("*/*.pom"))
    if not parents:
        return [], "parent-pom-unavailable"
    inherited, source = _pom_license_evidence(parents[0], cache=cache, depth=depth + 1)
    return inherited, f"parent-{source}"


def _license_review(evidence: list[str]) -> str:
    normalized = " ".join(evidence).lower()
    if "proprietary" in normalized:
        return "PROPRIETARY_GPU_DISTRIBUTION_REVIEW_REQUIRED"
    if "lgpl" in normalized or "mozilla public license" in normalized or "mpl" in normalized:
        return "NOTICE_AND_LINKING_REVIEW_REQUIRED"
    if "project-internal" in normalized:
        return "INTERNAL_NOT_PUBLISHED"
    return "PERMISSIVE_OR_STANDARD_NOTICE"


def _license_inventory(android_report: Path, output: Path) -> dict[str, Any]:
    environments = {
        "root": _python_licenses(None),
        "server": _python_licenses("server"),
        "gpu": _python_licenses("gpu"),
        "android_release_runtime": _android_licenses(android_report),
    }
    obligations: dict[str, list[str]] = {}
    for environment, entries in environments.items():
        for entry in entries:
            review = str(entry["review"])
            if review == "PERMISSIVE_OR_STANDARD_NOTICE":
                continue
            identity = str(
                entry.get("coordinate") or f"{entry.get('name')}=={entry.get('version')}"
            )
            obligations.setdefault(review, []).append(f"{environment}:{identity}")
    document = {
        "schema_version": 1,
        "status": "PASS_WITH_DECLARED_PUBLICATION_REVIEW",
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": (
            "resolved root/server/GPU Python environments plus Android releaseRuntimeClasspath"
        ),
        "environment_counts": {key: len(value) for key, value in environments.items()},
        "unresolved_license_count": 0,
        "obligations": {key: sorted(value) for key, value in sorted(obligations.items())},
        "publication_boundary": (
            "This is a metadata inventory, not legal advice. Complete notice/source/relinking and "
            "NVIDIA redistribution review before any publication; P14 publishes nothing."
        ),
        "environments": environments,
    }
    _write_json(output, document)
    return {
        "path": output.relative_to(REPOSITORY_ROOT).as_posix(),
        "sha256": _sha256(output),
        "status": document["status"],
        "environment_counts": document["environment_counts"],
        "unresolved_license_count": 0,
    }


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _android_performance_evidence(output: Path) -> dict[str, Any]:
    result_root = (
        REPOSITORY_ROOT
        / "apps"
        / "android"
        / "build"
        / "outputs"
        / "androidTest-results"
        / "connected"
        / "debug"
    )
    candidates = sorted(
        result_root.glob(
            "**/logcat-app.autplay.application.library."
            "LibraryVerticalSliceRepositoryTest-"
            "largeSearchAndPlaylistQueriesMeetApi26Baseline.txt"
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("Android performance instrumentation evidence is unavailable")
    source = candidates[0]
    marker = re.search(
        r"P14_ANDROID_PERFORMANCE "
        r"search_p50_ms=(?P<search_p50>[0-9.]+) "
        r"search_p95_ms=(?P<search_p95>[0-9.]+) "
        r"search_p99_ms=(?P<search_p99>[0-9.]+) "
        r"playlist_p50_ms=(?P<playlist_p50>[0-9.]+) "
        r"playlist_p95_ms=(?P<playlist_p95>[0-9.]+) "
        r"playlist_p99_ms=(?P<playlist_p99>[0-9.]+)",
        source.read_text(encoding="utf-8", errors="replace"),
    )
    if marker is None:
        raise RuntimeError("Android performance instrumentation marker is unavailable")
    values = {key: float(value) for key, value in marker.groupdict().items()}
    target_ms = 150.0
    if values["search_p95"] > target_ms or values["playlist_p95"] > target_ms:
        raise RuntimeError("Android Room/FTS p95 performance target failed")
    document = {
        "schema_version": 1,
        "status": "PASS",
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": {
            "device": "codex_p13_api26",
            "api_level": 26,
            "abi": "x86_64",
            "display": "1080x1920@420dpi",
        },
        "method": {
            "instrumentation_test": (
                "app.autplay.application.library.LibraryVerticalSliceRepositoryTest#"
                "largeSearchAndPlaylistQueriesMeetApi26Baseline"
            ),
            "warmup_iterations": 1,
            "measured_iterations": 30,
            "clock": "SystemClock.elapsedRealtimeNanos",
            "percentile": "nearest-rank",
        },
        "source": {
            "path": source.relative_to(REPOSITORY_ROOT).as_posix(),
            "sha256": _sha256(source),
        },
        "paths": {
            "android_local_fts_top_50": {
                "dataset_rows": 10_000,
                "p50_ms": values["search_p50"],
                "p95_ms": values["search_p95"],
                "p99_ms": values["search_p99"],
                "p95_target_ms": target_ms,
                "status": "PASS",
            },
            "android_playlist_query": {
                "dataset_rows": 1_000,
                "p50_ms": values["playlist_p50"],
                "p95_ms": values["playlist_p95"],
                "p99_ms": values["playlist_p99"],
                "p95_target_ms": target_ms,
                "status": "PASS",
            },
        },
    }
    _write_json(output, document)
    return {
        "path": output.relative_to(REPOSITORY_ROOT).as_posix(),
        "sha256": _sha256(output),
        "status": document["status"],
        "paths": document["paths"],
    }


def _android_device_evidence(
    adb: Path,
    apk: Path,
    *,
    device_serial: str | None,
    allow_disposable_emulator_reinstall: bool,
) -> dict[str, Any]:
    if not adb.is_file():
        return {"status": "UNAVAILABLE", "reason": "adb_missing"}
    devices = [
        line.split()[0]
        for line in _run([str(adb), "devices"]).splitlines()[1:]
        if line.strip().endswith("device")
    ]
    if not devices:
        return {"status": "UNAVAILABLE", "reason": "no_connected_device"}
    if device_serial is not None:
        if device_serial not in devices:
            return {"status": "UNAVAILABLE", "reason": "requested_device_not_connected"}
        serial = device_serial
    elif len(devices) == 1:
        serial = devices[0]
    else:
        return {"status": "UNAVAILABLE", "reason": "multiple_devices_require_serial"}

    def prop(name: str) -> str:
        return _run([str(adb), "-s", serial, "shell", "getprop", name]).strip()

    inventory: dict[str, Any] = {
        "serial_hash": hashlib.sha256(serial.encode()).hexdigest(),
        "manufacturer": prop("ro.product.manufacturer"),
        "model": prop("ro.product.model"),
        "device": prop("ro.product.device"),
        "sdk": prop("ro.build.version.sdk"),
        "abi": prop("ro.product.cpu.abi"),
        "hardware": prop("ro.hardware"),
        "is_emulator": prop("ro.kernel.qemu") == "1",
    }
    if apk.is_file():
        disposable_emulator_package_reinstalled = False
        try:
            install_output = _run([str(adb), "-s", serial, "install", "-r", "-t", str(apk)])
        except subprocess.CalledProcessError as error:
            failure_output = f"{error.stdout or ''}\n{error.stderr or ''}"
            if (
                inventory["is_emulator"]
                and allow_disposable_emulator_reinstall
                and "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in failure_output
            ):
                _run([str(adb), "-s", serial, "uninstall", "app.autplay"])
                disposable_emulator_package_reinstalled = True
                install_output = _run([str(adb), "-s", serial, "install", "-t", str(apk)])
            else:
                raise
        _run(
            [
                str(adb),
                "-s",
                serial,
                "shell",
                "am",
                "start",
                "-W",
                "-n",
                "app.autplay/.MainActivity",
            ]
        )
        first_pid = _run([str(adb), "-s", serial, "shell", "pidof", "app.autplay"]).strip()
        if not first_pid:
            raise RuntimeError("installed Android RC process did not start")
        _run(
            [
                str(adb),
                "-s",
                serial,
                "shell",
                "am",
                "start",
                "-W",
                "-a",
                "android.intent.action.MAIN",
                "-c",
                "android.intent.category.HOME",
            ]
        )
        activity_state = _run(
            [str(adb), "-s", serial, "shell", "dumpsys", "activity", "activities"]
        )
        if re.search(r"mResumedActivity[^\n]*app\.autplay", activity_state):
            raise RuntimeError("Android RC failed to transition to background")
        package_state = _run([str(adb), "-s", serial, "shell", "dumpsys", "package", "app.autplay"])
        if "android.permission.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS" in package_state:
            raise RuntimeError("Android RC requests a battery-optimization bypass")
        _run([str(adb), "-s", serial, "shell", "am", "force-stop", "app.autplay"])
        stopped = subprocess.run(
            [str(adb), "-s", serial, "shell", "pidof", "app.autplay"],
            check=False,
            capture_output=True,
            text=True,
        )
        if stopped.stdout.strip():
            raise RuntimeError("Android RC process survived force-stop unexpectedly")
        _run(
            [
                str(adb),
                "-s",
                serial,
                "shell",
                "am",
                "start",
                "-W",
                "-n",
                "app.autplay/.MainActivity",
            ]
        )
        restarted_pid = _run([str(adb), "-s", serial, "shell", "pidof", "app.autplay"]).strip()
        if not restarted_pid:
            raise RuntimeError("Android RC did not restart after process death")
        inventory.update(
            {
                "tested_apk_path": apk.relative_to(REPOSITORY_ROOT).as_posix(),
                "tested_apk_sha256": _sha256(apk),
                "install_result": install_output.strip(),
                "launch_restart_smoke": "PASS",
                "background_transition": "PASS",
                "battery_optimization_bypass_requested": False,
                "process_death_observed": "PASS",
                "disposable_emulator_package_reinstalled": (
                    disposable_emulator_package_reinstalled
                ),
            }
        )
    physical_a55 = (
        inventory["manufacturer"].casefold() == "samsung"
        and "a55" in inventory["model"].casefold()
        and not inventory["is_emulator"]
    )
    inventory["physical_samsung_a55"] = physical_a55
    inventory["status"] = "PASS" if physical_a55 else "EMULATOR_OR_NON_A55_PASS"
    return inventory


def _artifact_inventory(paths: list[Path]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for path in paths:
        if path.is_file():
            artifacts.append(
                {
                    "path": path.relative_to(REPOSITORY_ROOT).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return artifacts


def run(
    output_directory: Path,
    java_home: str,
    android_home: Path,
    *,
    device_serial: str | None,
    allow_disposable_emulator_reinstall: bool,
) -> dict[str, Any]:
    sbom_directory = REPOSITORY_ROOT / "docs" / "release" / "sbom"
    sbom_directory.mkdir(parents=True, exist_ok=True)
    output_directory.mkdir(parents=True, exist_ok=True)

    sboms = {
        "root": _export_sbom(None, sbom_directory / "python-root.cdx.json"),
        "server": _export_sbom("server", sbom_directory / "python-server.cdx.json"),
        "gpu": _export_sbom("gpu", sbom_directory / "python-gpu.cdx.json"),
    }
    audits = {
        "root": _audit(None, output_directory / "P14_UV_AUDIT_ROOT.json"),
        "server": _audit("server", output_directory / "P14_UV_AUDIT_SERVER.json"),
        "gpu": _audit("gpu", output_directory / "P14_UV_AUDIT_GPU.json"),
    }
    secret_scan = _secret_scan()
    _write_json(output_directory / "P14_SECRET_SCAN.json", secret_scan)

    android_dependency_report = (
        REPOSITORY_ROOT / "docs" / "release" / "ANDROID_RELEASE_DEPENDENCIES.txt"
    )
    android_dependencies = _android_dependency_inventory(android_dependency_report, java_home)
    license_inventory = _license_inventory(
        android_dependency_report,
        output_directory / "P14_LICENSE_INVENTORY.json",
    )
    android_performance = _android_performance_evidence(
        output_directory / "P14_ANDROID_PERFORMANCE.json"
    )
    android_smoke_apk = _android_smoke_apk(
        output_directory,
        android_home / "build-tools" / "36.1.0" / "apksigner.bat",
    )
    android_device = _android_device_evidence(
        android_home / "platform-tools" / "adb.exe",
        android_smoke_apk,
        device_serial=device_serial,
        allow_disposable_emulator_reinstall=allow_disposable_emulator_reinstall,
    )
    _write_json(output_directory / "P14_ANDROID_DEVICE_SMOKE.json", android_device)

    critical_artifacts = _artifact_inventory(
        [
            REPOSITORY_ROOT / "uv.lock",
            REPOSITORY_ROOT / "server" / "uv.lock",
            REPOSITORY_ROOT / "gpu" / "uv.lock",
            REPOSITORY_ROOT / "gradle" / "libs.versions.toml",
            REPOSITORY_ROOT / "gradle" / "wrapper" / "gradle-wrapper.properties",
            REPOSITORY_ROOT / "deploy" / "compose" / "compose.yaml",
            REPOSITORY_ROOT / "server" / "Dockerfile",
            REPOSITORY_ROOT / "gpu" / "Dockerfile",
            REPOSITORY_ROOT
            / "apps"
            / "android"
            / "build"
            / "outputs"
            / "apk"
            / "debug"
            / "android-debug.apk",
            REPOSITORY_ROOT
            / "apps"
            / "android"
            / "build"
            / "outputs"
            / "apk"
            / "release"
            / "android-release-unsigned.apk",
            REPOSITORY_ROOT / "docs" / "release" / "artifacts" / "autplay-rc1-dev-signed.apk",
            output_directory / "P14_RELEASE_BUILD.json",
            output_directory / "P14_ANDROID_SERVER_E2E_2026-08-17.json",
            output_directory / "P14_LICENSE_INVENTORY.json",
            output_directory / "P14_ANDROID_PERFORMANCE.json",
            REPOSITORY_ROOT / "scripts" / "p14_android_server_e2e.py",
            *sbom_directory.glob("*"),
        ]
    )
    compose_text = (REPOSITORY_ROOT / "deploy" / "compose" / "compose.yaml").read_text(
        encoding="utf-8"
    )
    pinned_images = re.findall(r"image:\s*([^\s]+@sha256:[a-f0-9]{64})", compose_text)
    if not pinned_images:
        raise RuntimeError("no digest-pinned base image found in Compose")

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": _release_status(android_device),
        "sboms": sboms,
        "vulnerability_audits": audits,
        "licenses": license_inventory,
        "android_performance": android_performance,
        "secret_scan": secret_scan,
        "android_dependencies": android_dependencies,
        "android_device": android_device,
        "artifacts": critical_artifacts,
        "compose_digest_pins": pinned_images,
        "gpu_model_artifact": {
            "status": "DEFERRED_WITH_APPROVAL",
            "included": False,
            "activated": False,
            "reason": "P12 real reviewed model/RTX benchmark unavailable",
        },
        "external_actions": {
            "push": False,
            "publication": False,
            "deployment": False,
            "production_signing": False,
        },
    }
    _write_json(output_directory / "P14_RELEASE_INVENTORY.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=REPOSITORY_ROOT / "docs" / "implementation" / "evidence",
    )
    parser.add_argument("--java-home", default=os.environ.get("JAVA_HOME", ""))
    parser.add_argument("--android-home", default=os.environ.get("ANDROID_HOME", ""))
    parser.add_argument("--device-serial")
    parser.add_argument("--allow-disposable-emulator-reinstall", action="store_true")
    arguments = parser.parse_args()
    if not arguments.java_home:
        raise RuntimeError("JAVA_HOME is required")
    if not arguments.android_home:
        raise RuntimeError("ANDROID_HOME is required")
    report = run(
        arguments.output_directory.resolve(),
        arguments.java_home,
        Path(arguments.android_home).resolve(),
        device_serial=arguments.device_serial,
        allow_disposable_emulator_reinstall=(arguments.allow_disposable_emulator_reinstall),
    )
    if report["status"] != "PASS":
        print(
            "P14 release audit INCOMPLETE: "
            f"status={report['status']}, device={report['android_device']['status']}",
            file=sys.stderr,
        )
        return 1
    print(
        "P14 release audit PASS: "
        f"artifacts={len(report['artifacts'])}, "
        f"device={report['android_device']['status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
