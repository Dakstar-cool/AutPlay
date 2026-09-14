"""Run the two real process-death stages on a disposable Android emulator."""

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", default=os.environ.get("ANDROID_SERIAL", "emulator-5554"))
    parser.add_argument("--output", type=Path, default=ROOT / "build/l1-process-death")
    parser.add_argument("--qa-side-by-side", action="store_true")
    args = parser.parse_args()
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    if not sdk:
        parser.error("ANDROID_HOME or ANDROID_SDK_ROOT is required")
    adb = str(Path(sdk) / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb"))

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

    if command("shell", "getprop", "ro.kernel.qemu").strip() != "1":
        parser.error("This fixture resets its test database and requires a disposable emulator")
    args.output.mkdir(parents=True, exist_ok=True)
    package = "app.autplay.qa" if args.qa_side_by_side else "app.autplay"
    runner = f"{package}.test/androidx.test.runner.AndroidJUnitRunner"
    stages = [
        "stage1_seedServiceAndWaitForPeriodicCheckpoint",
        "stage2_verifyServiceRestoresPersistedQueueAfterFreshConnection",
    ]
    receipt: dict[str, object] = {"serial": args.serial, "stages": [], "status": "RUNNING"}
    try:
        artifacts = [
            ROOT / "apps/android/build/outputs/apk/debug/android-debug.apk",
            ROOT / "apps/android/build/outputs/apk/androidTest/debug/android-debug-androidTest.apk",
        ]
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
