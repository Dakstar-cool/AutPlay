"""Bounded direct-helper diagnosis of synthetic PCM under neutral managed names.

Run with pinned Python on a Mac. Helper paths are argv capabilities, while all
synthetic media capabilities remain in private stdin frames. Output is sanitized.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, suppress
import hashlib
import json
import os
from pathlib import Path
import selectors
import struct
import subprocess
import sys
import tempfile
import time

MAX_FRAME = 65_536
HELLO = {"v": 1, "type": "hello", "protocol": "autplay.local-audio", "maxFrameBytes": MAX_FRAME}
DOMAINS = {"AVFOUNDATION", "OSSTATUS", "COCOA", "POSIX", "URL", "MACH", "OTHER"}
ERRORS = {"NATIVE_INVALID_PATH", "NATIVE_FILE_UNAVAILABLE", "NATIVE_UNSUPPORTED_MEDIA",
          "NATIVE_OPEN_FAILED", "NATIVE_OPEN_TIMEOUT", "NATIVE_PLAYBACK_FAILED",
          "NATIVE_SEEK_FAILED", "NATIVE_SEEK_TIMEOUT"}
STAGES = ERRORS | {"FILE_VALIDATION", "FILE_ATTRIBUTES", "ASSET_PLAYABLE", "ASSET_PROTECTED",
                   "ASSET_DURATION", "ASSET_TRACKS", "ITEM_READY"}


class ProbeFailure(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def pcm_wave(rate: int, channels: int) -> bytes:
    pcm = bytes(rate * channels * 2 * 8)
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * channels * 2, channels * 2, 16) +
            b"data" + struct.pack("<I", len(pcm)) + pcm)


def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result or isinstance(value, (dict, list)):
            raise ProbeFailure("PROBE_PROTOCOL_ERROR")
        result[key] = value
    return result


def diagnostic(frame: dict[str, object]) -> dict[str, object]:
    allowed = {"v", "type", "generation", "errorCode", "stage"}
    allowed |= {f"cause{i}{field}" for i in range(3) for field in ("Domain", "Code")}
    if set(frame) - allowed or frame.get("errorCode") not in ERRORS or frame.get("stage") not in STAGES:
        raise ProbeFailure("PROBE_PROTOCOL_ERROR")
    missing = False
    for index in range(3):
        domain, code = frame.get(f"cause{index}Domain"), frame.get(f"cause{index}Code")
        if domain is None and code is None:
            missing = True
            continue
        if missing or domain not in DOMAINS or type(code) is not int or not -(2**31) <= code < 2**31:
            raise ProbeFailure("PROBE_PROTOCOL_ERROR")
    return {key: value for key, value in frame.items() if key not in {"v", "type"}}


@contextmanager
def terminate_before_cleanup(child: subprocess.Popen[bytes]):
    try:
        yield
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)


class Pipe:
    def __init__(self, child: subprocess.Popen[bytes]) -> None:
        self.child = child
        self.selector = selectors.DefaultSelector()
        if child.stdout is None or child.stdin is None:
            raise ProbeFailure("PROBE_PIPE_FAILED")
        self.selector.register(child.stdout, selectors.EVENT_READ)
        self.command_id = 0

    def send(self, frame: dict[str, object]) -> None:
        payload = json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if not 0 < len(payload) <= MAX_FRAME:
            raise ProbeFailure("PROBE_PROTOCOL_ERROR")
        assert self.child.stdin is not None
        self.child.stdin.write(struct.pack(">I", len(payload)) + payload)
        self.child.stdin.flush()

    def command(self, op: str, generation: int, **extra: object) -> int:
        self.command_id += 1
        self.send({"v": 1, "type": "command", "id": self.command_id, "op": op,
                   "generation": generation, **extra})
        return self.command_id

    def read_exact(self, count: int, deadline: float) -> bytes:
        result = bytearray()
        assert self.child.stdout is not None
        while len(result) < count:
            left = deadline - time.monotonic()
            if left <= 0 or not self.selector.select(left):
                raise ProbeFailure("PROBE_READ_TIMEOUT")
            part = os.read(self.child.stdout.fileno(), count - len(result))
            if not part:
                raise ProbeFailure("PROBE_HELPER_EXITED")
            result.extend(part)
        return bytes(result)

    def receive(self, deadline: float) -> dict[str, object]:
        size = struct.unpack(">I", self.read_exact(4, deadline))[0]
        if not 0 < size <= MAX_FRAME:
            raise ProbeFailure("PROBE_PROTOCOL_ERROR")
        frame = json.loads(self.read_exact(size, deadline).decode("utf-8"), object_pairs_hook=object_pairs)
        if type(frame) is not dict or type(frame.get("v")) is not int or frame.get("v") != 1 or len(frame) > 11:
            raise ProbeFailure("PROBE_PROTOCOL_ERROR")
        return frame

    def ack(self, command_id: int) -> None:
        deadline = time.monotonic() + 5
        while True:
            frame = self.receive(deadline)
            if frame == {"v": 1, "type": "ack", "id": command_id}:
                return


def run(helper: Path) -> dict[str, object]:
    checks: list[dict[str, object]] = []
    report: dict[str, object] = {"protocol": 1, "checks": checks, "closed": False}
    child = subprocess.Popen([str(helper)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, bufsize=0)
    pipe: Pipe | None = None
    try:
        pipe = Pipe(child)
        if pipe.receive(time.monotonic() + 5) != HELLO:
            raise ProbeFailure("PROBE_PROTOCOL_ERROR")
        pipe.send(HELLO)
        if pipe.receive(time.monotonic() + 5) != {"v": 1, "type": "ready"}:
            raise ProbeFailure("PROBE_PROTOCOL_ERROR")
        with tempfile.TemporaryDirectory(prefix="autplay-audio-diagnostic-") as folder, terminate_before_cleanup(child):
            owned_root = Path(folder).resolve(strict=True)
            generation = 0
            for rate, channels, label in ((16_000, 1, "pcm16_mono"), (48_000, 2, "pcm48_stereo")):
                content = pcm_wave(rate, channels)
                for suffix in ("wav", "media"):
                    generation += 1
                    managed = owned_root / f"{label}.{suffix}"
                    managed.write_bytes(content)
                    check: dict[str, object] = {"fixture": label, "suffix": suffix,
                                               "sha256": hashlib.sha256(content).hexdigest()}
                    checks.append(check)
                    pipe.command("open", generation, managedPath=str(managed), positionMs=0, autoplay=True)
                    deadline = time.monotonic() + 18
                    frame_count = 0
                    while True:
                        frame = pipe.receive(deadline)
                        frame_count += 1
                        if frame_count > 128:
                            raise ProbeFailure("PROBE_FRAME_LIMIT")
                        if frame.get("generation") != generation:
                            continue
                        if type(frame.get("generation")) is not int:
                            raise ProbeFailure("PROBE_PROTOCOL_ERROR")
                        if frame.get("type") == "diagnostic":
                            check["diagnostic"] = diagnostic(frame)
                        elif frame.get("type") == "event":
                            status = frame.get("status")
                            if status == "ERROR":
                                if frame.get("errorCode") not in ERRORS:
                                    raise ProbeFailure("PROBE_PROTOCOL_ERROR")
                                check.update(result="ERROR", errorCode=frame["errorCode"])
                                break
                            if status == "READY" and frame.get("isPlaying") is True:
                                position, duration = frame.get("positionMs"), frame.get("durationMs")
                                if (type(position) is not int or type(duration) is not int or
                                        not 0 <= position <= 604_800_000 or not 0 < duration <= 604_800_000):
                                    raise ProbeFailure("PROBE_PROTOCOL_ERROR")
                                check.update(result="READY", positionMs=position, durationMs=duration)
                                break
                    pipe.ack(pipe.command("stop", generation))
            pipe.ack(pipe.command("close", generation))
            child.wait(timeout=3)
            report["closed"] = child.returncode == 0
    except ProbeFailure as failure:
        report["errorCode"] = failure.code
    except Exception:
        report["errorCode"] = "PROBE_EXECUTION_FAILED"
    finally:
        if child.poll() is None:
            child.kill()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            report["errorCode"] = "PROBE_TEARDOWN_FAILED"
        if pipe is not None:
            pipe.selector.close()
        if child.stdin is not None:
            with suppress(OSError):
                child.stdin.close()
        if child.stdout is not None:
            with suppress(OSError):
                child.stdout.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--helper", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        for rate, channels in ((16_000, 1), (48_000, 2)):
            content = pcm_wave(rate, channels)
            assert len(content) == 44 + rate * channels * 2 * 8
            assert content[:4] == b"RIFF" and content[8:12] == b"WAVE"
        assert diagnostic({"v": 1, "type": "diagnostic", "generation": 1,
                           "stage": "ASSET_DURATION", "errorCode": "NATIVE_OPEN_FAILED",
                           "cause0Domain": "AVFOUNDATION", "cause0Code": -11800,
                           "cause1Domain": "OSSTATUS", "cause1Code": -1})["cause1Code"] == -1
        base = {"v": 1, "type": "diagnostic", "generation": 1,
                "stage": "ASSET_DURATION", "errorCode": "NATIVE_OPEN_FAILED"}
        for extra in (
            {"cause1Domain": "OSSTATUS", "cause1Code": -1},
            {"cause0Domain": "OTHER", "cause0Code": 2**31},
            {"cause0Domain": "/private/untrusted", "cause0Code": -1},
            {"managedPath": "/private/untrusted"},
            {"stage": "/private/untrusted"},
        ):
            try:
                diagnostic({**base, **extra})
            except ProbeFailure as failure:
                assert failure.code == "PROBE_PROTOCOL_ERROR"
            else:
                raise AssertionError("Unsanitized or invalid diagnostic accepted")
        print(json.dumps({"selfCheck": "PASS", "nativePlayback": "NOT_RUN"}))
        return 0
    if sys.platform != "darwin" or args.helper is None:
        print(json.dumps({"errorCode": "PROBE_MAC_HELPER_REQUIRED"}))
        return 1
    try:
        helper = args.helper.resolve(strict=True)
        if not helper.is_file() or not os.access(helper, os.X_OK):
            raise ProbeFailure("PROBE_HELPER_UNAVAILABLE")
        report = run(helper)
    except Exception:
        report = {"errorCode": "PROBE_HELPER_UNAVAILABLE", "closed": False, "checks": []}
    print(json.dumps(report, sort_keys=True))
    return 0 if (report.get("closed") is True and len(report["checks"]) == 4 and
                 all(check.get("result") == "READY" for check in report["checks"]) and
                 "errorCode" not in report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
