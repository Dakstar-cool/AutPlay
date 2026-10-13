"""Build/run the isolated macOS probe, or verify only the disposable HTTP fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from fixture_server import Fixture, FixtureService, load_fixtures, verify_http

ROOT = Path(__file__).resolve().parent
REQUIRED = {".mp3", ".aac", ".m4a", ".flac", ".opus", ".wav"}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tool_output(argv: list[str]) -> str:
    try:
        result = subprocess.run(argv, capture_output=True, timeout=10, check=True)
        return result.stdout.decode("utf-8", errors="replace").strip()[:1000]
    except OSError, subprocess.SubprocessError:
        return "unavailable"


def run_native(binary: Path, config: dict[str, Any]) -> dict[str, Any]:
    # The token exists only in parent memory, an anonymous pipe and child memory.
    # Neither config, stderr nor argv is written to evidence. Native frameworks may
    # log URLs; captured stderr is discarded and must never be exported.
    try:
        result = subprocess.run(
            [str(binary)], input=json.dumps(config).encode(), capture_output=True, timeout=30
        )
    except subprocess.TimeoutExpired:
        return {"status": "failed", "code": "PROCESS_TIMEOUT"}
    except OSError:
        return {"status": "failed", "code": "PROCESS_START_FAILED"}
    if len(result.stdout) > 65536:
        return {"status": "failed", "code": "OUTPUT_LIMIT"}
    try:
        output = json.loads(result.stdout)
        if not isinstance(output, dict):
            raise ValueError("OUTPUT_SHAPE")
        # Only the known probe output schema can enter evidence.
        keys = {
            "status",
            "code",
            "stage",
            "elapsed_ms",
            "start_ms",
            "duration_ms",
            "seeks",
            "loader",
            "audible_output",
        }
        if set(output) - keys:
            raise ValueError("OUTPUT_SHAPE")
        if (result.returncode == 0) != (output.get("status") == "passed"):
            raise ValueError("EXIT_STATUS_MISMATCH")
        return output
    except ValueError, UnicodeError:
        return {"status": "failed", "code": "OUTPUT_INVALID"}


def case(binary: Path, fixture: Fixture, local: Path, route: str) -> dict[str, Any]:
    with FixtureService([fixture]) as service:
        config = {
            "kind": "local" if route == "local" else "http",
            "source": local.as_uri()
            if route == "local"
            else service.url("media" if route == "unauthorized" else route, fixture),
            "token": "synthetic-wrong-token" if route == "unauthorized" else service.token,
            "action": "cancel" if route == "slow" else "playback",
        }
        native = run_native(binary, config)
        events = service.primary.snapshot()
        sink = service.sink.snapshot()
        failed = native.get("status") == "failed" and native.get("code") == "ITEM_FAILED"
        loader = native.get("loader", {})
        if route == "unauthorized":
            passed = failed and any(event["status"] == 401 for event in events)
        elif route == "cross":
            passed = failed and loader.get("redirect_rejected", 0) > 0 and not sink
        elif route == "drop":
            passed = (
                failed
                and loader.get("failed", 0) > 0
                and any(event["route"] == "drop" and event["status"] == 206 for event in events)
            )
        elif route == "slow":
            passed = (
                native.get("status") == "passed"
                and loader.get("cancelled", 0) > 0
                and any(
                    event["route"] == "slow" and event["status"] == 206 and event["authenticated"]
                    for event in events
                )
                and not sink
            )
        elif route == "local":
            passed = native.get("status") == "passed" and not events
        else:
            passed = (
                native.get("status") == "passed"
                and loader.get("range_validated", 0) > 0
                and not sink
            )
            if route == "redirect":
                passed = passed and any(event["status"] == 302 for event in events)
        return {
            "case": route,
            "status": "passed" if passed else "failed",
            "native": native,
            "http_events": events,
            "sink_events": sink,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-check", action="store_true", help="HTTP fixture only, any OS")
    mode.add_argument("--run", action="store_true", help="Compile/run native AVFoundation on macOS")
    parser.add_argument(
        "--fixtures", type=Path, help="Flat directory of authorized/synthetic audio"
    )
    parser.add_argument(
        "--require-formats", action="store_true", help="Fail when the required matrix is incomplete"
    )
    parser.add_argument("--output", type=Path, help="Write sanitized JSON evidence to a new file")
    args = parser.parse_args()
    if args.output is not None and args.output.exists():
        parser.error("OUTPUT_EXISTS: choose a new evidence file")
    if args.self_check:
        result = verify_http()
        result.update(python=platform.python_version(), os=platform.system())
        code = 0
    elif platform.system() != "Darwin":
        result = {"status": "not_run", "code": "MACOS_REQUIRED", "native_audio_verified": False}
        code = 2
    else:
        result, code = native_matrix(args.fixtures, args.require_formats)
    serialized = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as output:
            output.write(serialized)
    print(serialized, end="")
    return code


def native_matrix(directory: Path | None, require_formats: bool) -> tuple[dict[str, Any], int]:
    fixtures = load_fixtures(directory)
    missing = sorted(REQUIRED - {fixture.extension for fixture in fixtures})
    runs = ROOT / ".runs"
    runs.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="probe-", dir=runs) as temporary:
        temporary_path = Path(temporary)
        binary = temporary_path / "audio-probe"
        source = ROOT / "AudioProbe.swift"
        # Host SDK/architecture are recorded, not guessed. No Gradle or downloads.
        compiler = [
            "xcrun",
            "swiftc",
            "-swift-version",
            "5",
            "-O",
            "-framework",
            "AVFoundation",
            "-framework",
            "UniformTypeIdentifiers",
            str(source),
            "-o",
            str(binary),
        ]
        try:
            build = subprocess.run(compiler, capture_output=True, timeout=60)
        except OSError, subprocess.SubprocessError:
            return {"status": "not_run", "code": "SWIFT_TOOLCHAIN_UNAVAILABLE"}, 2
        if build.returncode != 0:
            # Do not export diagnostics with the Mac user's raw source/SDK paths.
            # Reproduce the documented compile command locally for diagnostics.
            return {
                "status": "not_run",
                "code": "COMPILE_FAILED",
                "compiler_exit_code": build.returncode,
                "compiler_stderr_sha256": hashlib.sha256(build.stderr).hexdigest(),
            }, 2
        started = time.monotonic()
        matrix: list[dict[str, Any]] = []
        for fixture in fixtures:
            local = temporary_path / (fixture.identifier + fixture.extension)
            local.write_bytes(fixture.data)
            matrix.append(
                {
                    "fixture": fixture.identifier,
                    "extension": fixture.extension,
                    "sha256": fixture.sha256,
                    "bytes": len(fixture.data),
                    "cases": [
                        case(binary, fixture, local, route)
                        for route in (
                            "local",
                            "media",
                            "redirect",
                            "unauthorized",
                            "cross",
                            "drop",
                            "slow",
                        )
                    ],
                }
            )
        passed = all(entry["status"] == "passed" for row in matrix for entry in row["cases"])
        passed = passed and (not require_formats or not missing)
        result = {
            "status": "passed" if passed else "failed",
            "kind": "avfoundation_spike_only",
            "os": tool_output(["sw_vers", "-productVersion"]),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "swift": tool_output(["xcrun", "swiftc", "--version"]),
            "sdk": tool_output(["xcrun", "--show-sdk-version"]),
            "source_sha256": digest(source),
            "binary_sha256": digest(binary),
            "missing_required_extensions": missing,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "matrix": matrix,
            "audible_output": "not_verified",
            "jvm_bridge": "not_verified",
            "package": "not_verified",
            "keychain": "not_verified",
            "production_engine_accepted": False,
        }
        return result, 0 if passed else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OSError, ValueError, AssertionError:
        print(json.dumps({"status": "failed", "code": "FIXTURE_OR_EVIDENCE_ERROR"}))
        sys.exit(1)
