"""Run two real process-death stages on an emulator or an isolated physical QA app."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_CLASS = "app.autplay.playback.PlaybackServiceProcessStageTest"
PRODUCTION_APPLICATION_ID = "app.autplay"
QA_APPLICATION_ID = "app.autplay.qa"


def _serial_sha256(serial: str) -> str:
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


def _require_safe_target(*, is_emulator: bool, qa_side_by_side: bool) -> None:
    if not is_emulator and not qa_side_by_side:
        raise RuntimeError("Physical devices require the isolated --qa-side-by-side package")


def _find_aapt(android_sdk: Path) -> Path:
    executable = "aapt.exe" if os.name == "nt" else "aapt"
    candidates = sorted((android_sdk / "build-tools").glob(f"*/{executable}"))
    if not candidates:
        raise RuntimeError("Android aapt is unavailable")
    return candidates[-1]


def _assert_apk_package(aapt: Path, apk: Path, expected: str) -> None:
    result = subprocess.run(
        [str(aapt), "dump", "badging", str(apk)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    first_line = result.stdout.splitlines()[0] if result.stdout else ""
    if f"package: name='{expected}' " not in first_line:
        raise RuntimeError(f"APK identity mismatch for required package {expected}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL", "emulator-5554"))
    parser.add_argument("--output", type=Path, default=ROOT / "build/l1-process-death")
    parser.add_argument("--qa-side-by-side", action="store_true")
    args = parser.parse_args()
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk:
        parser.error("ANDROID_HOME or ANDROID_SDK_ROOT is required")
    android_sdk = Path(sdk)
    adb = str(android_sdk / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb"))
    aapt = _find_aapt(android_sdk)

    def command(*parts: str, timeout: int = 30, check: bool = True) -> str:
        result = subprocess.run(
            [adb, "-s", args.serial, *parts],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout + result.stderr

    is_emulator = command("shell", "getprop", "ro.kernel.qemu").strip() == "1"
    try:
        _require_safe_target(is_emulator=is_emulator, qa_side_by_side=args.qa_side_by_side)
    except RuntimeError as error:
        parser.error(str(error))
    args.output.mkdir(parents=True, exist_ok=True)
    package = QA_APPLICATION_ID if args.qa_side_by_side else PRODUCTION_APPLICATION_ID
    runner = f"{package}.test/androidx.test.runner.AndroidJUnitRunner"
    stages = [
        "stage1_seedServiceAndWaitForPeriodicCheckpoint",
        "stage2_verifyServiceRestoresPersistedQueueAfterFreshConnection",
    ]
    receipt: dict[str, object] = {
        "schema_version": 1,
        "device_serial_sha256": _serial_sha256(args.serial),
        "execution_target": "emulator" if is_emulator else "physical_qa_side_by_side",
        "application_id": package,
        "stages": [],
        "status": "RUNNING",
    }
    try:
        artifacts = [
            ROOT / "apps/android/build/outputs/apk/debug/android-debug.apk",
            ROOT / "apps/android/build/outputs/apk/androidTest/debug/android-debug-androidTest.apk",
        ]
        expected_packages = [package, f"{package}.test"]
        for apk, expected_package in zip(artifacts, expected_packages, strict=True):
            _assert_apk_package(aapt, apk, expected_package)
        receipt["package_identities_verified"] = True
        receipt["apk_sha256"] = {
            apk.name: hashlib.sha256(apk.read_bytes()).hexdigest() for apk in artifacts
        }
        for apk in artifacts:
            command("install", "-r", "-t", str(apk), timeout=120)
        for index, method in enumerate(stages):
            output = command(
                "shell",
                "am",
                "instrument",
                "-w",
                "-r",
                "-e",
                "class",
                f"{TEST_CLASS}#{method}",
                "-e",
                "l1ProcessStage",
                "true",
                runner,
                timeout=180,
                check=False,
            )
            (args.output / f"stage{index + 1}.txt").write_text(output, encoding="utf-8")
            if not re.search(r"OK \(1 test\)", output) or "INSTRUMENTATION_FAILED" in output:
                raise RuntimeError(f"Stage {index + 1} failed; see saved instrumentation output")
            receipt["stages"] = stages[: index + 1]
            if index == 0:
                command("shell", "am", "start", "-W", "-n", f"{package}/app.autplay.MainActivity")
                if not command("shell", "pidof", package, check=False).strip():
                    raise RuntimeError("Process was not alive before force-stop")
                command("shell", "am", "force-stop", package)
                if command("shell", "pidof", package, check=False).strip():
                    raise RuntimeError("Process survived the force-stop boundary")
                receipt["verified_process_boundary"] = True
        receipt["status"] = "PASS"
    except Exception:
        receipt["status"] = "FAIL"
        raise
    finally:
        (args.output / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print("L1 process-death queue restoration: PASS")


if __name__ == "__main__":
    main()
