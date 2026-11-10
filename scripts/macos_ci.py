"""Bounded packaged startup and fail-closed evidence for the unsigned macOS CI gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path("build/macos-audio-spike")
APP = Path("apps/desktop/build/compose/binaries/main/app/AutPlay.app")
REPORTS = {
    **{f"shared/{name}": Path(f"shared/{name}/build/test-results/jvmTest")
       for name in ("domain", "application", "data", "ui")},
    "desktop": Path("apps/desktop/build/test-results/test"),
}
NATIVE_CLASS = "app.autplay.desktop.acceptance.MacNativePlaybackAcceptanceTest"
NATIVE_CASE = "actualNativeTimelineQueueAndPausedRestartUseManagedWav"


def write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def startup(root: Path) -> int:
    executable = root / APP / "Contents/MacOS/AutPlay"
    report: dict[str, object] = {"kind": "packaged_desktop_startup", "status": "failed"}
    try:
        with tempfile.TemporaryDirectory(prefix="autplay-startup-", dir=os.environ["RUNNER_TEMP"]) as storage:
            result = subprocess.run(
                [str(executable), "--smoke-test", "--test-data-root=" + storage],
                capture_output=True, text=True, timeout=60, check=False,
            )
        ready = "AUTPLAY_DESKTOP_SMOKE_READY java=17.0.20 bundled=true" in result.stdout.splitlines()
        report.update(exit_code=result.returncode, window_and_frames=ready, bundled_runtime=ready,
                      status="passed" if result.returncode == 0 and ready else "failed")
        # Keep hashes, not framework output that can include paths or account payloads.
        report.update(stdout_sha256=hashlib.sha256(result.stdout.encode()).hexdigest(),
                      stderr_sha256=hashlib.sha256(result.stderr.encode()).hexdigest())
    except subprocess.TimeoutExpired:
        report["code"] = "STARTUP_TIMEOUT"
    except OSError:
        report["code"] = "STARTUP_FAILED"
    write_json(root / EVIDENCE / "desktop-startup.json", report)
    print(json.dumps(report))
    return 0 if report["status"] == "passed" else 1


def test_reports(root: Path) -> tuple[dict[str, object], list[str]]:
    counts: dict[str, object] = {}
    problems: list[str] = []
    native_passed = False
    for name, directory in REPORTS.items():
        cases = []
        for path in sorted((root / directory).glob("TEST-*.xml")):
            try:
                suite = ET.parse(path).getroot()
                if suite.tag != "testsuite":
                    raise ValueError("EXPECTED_TESTSUITE")
                cases.extend(suite.findall("testcase"))
            except (ET.ParseError, ValueError):
                problems.append(f"{name}: malformed JUnit report")
        failures = sum(case.find("failure") is not None or case.find("error") is not None for case in cases)
        skipped = sum(case.find("skipped") is not None for case in cases)
        counts[name] = {"tests": len(cases), "failures": failures, "skipped": skipped}
        if not cases or failures or skipped:
            problems.append(f"{name}: missing, failed or skipped tests")
        for case in cases:
            if case.get("classname") == NATIVE_CLASS and case.get("name") == NATIVE_CASE:
                native_passed = not any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
    if not native_passed:
        problems.append("Packaged AVFoundation acceptance did not pass")
    return counts, problems


def evidence(root: Path) -> int:
    counts, problems = test_reports(root)
    outcomes = {name: os.environ.get(f"CI_{name}_OUTCOME", "missing")
                for name in ("PACKAGE", "STARTUP", "NATIVE_TESTS", "PROBE")}
    problems.extend(f"{name}: {outcome}" for name, outcome in outcomes.items() if outcome != "success")
    for filename in ("desktop-startup.json", "native-wav.json"):
        try:
            report = json.loads((root / EVIDENCE / filename).read_text(encoding="utf-8"))
            if report.get("status") != "passed":
                problems.append(f"{filename}: did not pass")
        except (OSError, ValueError):
            problems.append(f"{filename}: missing or invalid")
    packages = []
    for path in sorted((root / "apps/desktop/build/compose/binaries/main/dmg").glob("*.dmg")):
        with path.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        packages.append({"name": path.name, "bytes": path.stat().st_size, "sha256": checksum})
    if not packages or any(package["bytes"] == 0 for package in packages):
        problems.append("Nonempty DMG package required")
    report = {
        "status": "passed" if not problems else "failed", "source_commit": os.environ.get("GITHUB_SHA"),
        "run_id": os.environ.get("GITHUB_RUN_ID"), "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "architecture": platform.machine(), "macos": platform.mac_ver()[0],
        "test_results": counts, "step_outcomes": outcomes, "packages": packages, "problems": problems,
        "scope": "unsigned development package and automated macOS checks",
        "not_qualified": ["audible output", "full codec matrix", "signed account flows", "Gatekeeper", "manual feature matrix"],
    }
    write_json(root / EVIDENCE / "ci-summary.json", report)
    summary = ["## macOS automated gate", "", f"Status: **{report['status']}**", "",
               f"Source: `{report['source_commit']}`", "", "| Module | Tests | Failed | Skipped |",
               "| --- | ---: | ---: | ---: |"]
    for name, row in counts.items():
        summary.append(f"| {name} | {row['tests']} | {row['failures']} | {row['skipped']} |")
    summary.extend(["", "DMG files are built and hashed, but not uploaded. Reports expire after 3 days.",
                    "", "This is unsigned development evidence; manual and signed-device qualification remains separate."])
    summary.extend(["", *[f"- {problem}" for problem in problems]])
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as output:
            output.write("\n".join(summary) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if not problems else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("startup", "evidence"))
    args = parser.parse_args()
    raise SystemExit(startup(ROOT) if args.command == "startup" else evidence(ROOT))
