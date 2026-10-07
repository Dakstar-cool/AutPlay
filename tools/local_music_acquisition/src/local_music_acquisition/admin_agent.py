"""Operator-run acquisition controller for the private Admin Web spool."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import UUID

from .orchestrator import PlaylistDownloadError
from .queue import queue_status, retry_unsuccessful, verify_downloads
from .queue_store import exclusive_lock, read_json, sync_directory, write_json

_COUNTS = ("pending", "running", "retry", "downloaded", "not_found", "failed", "needs_review")
_PROVIDERS = frozenset({"jamendo", "hitmo", "yt_dlp", "soundcloud", "bandcamp", "yandex"})
_PROVIDER_COUNTS = ("requests", "downloaded", "misses", "failures", "deferred")


class AcquisitionAgent:
    """Execute fixed commands with operator-owned paths and provider configuration."""

    def __init__(self, control: Path, queues: Path, output: Path, launcher: Path) -> None:
        for path in (control, queues, output):
            if not path.is_absolute() or any(
                part.is_symlink() or part.is_junction() for part in (path, *path.parents)
            ):
                raise ValueError("acquisition_agent_path_invalid")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not launcher.is_absolute() or launcher.is_symlink() or not launcher.is_file():
            raise ValueError("acquisition_agent_launcher_invalid")
        self.control = control
        self.queues = queues
        self.output = output
        self.launcher = launcher
        self.launcher_environment = os.environ.copy()
        enabled_sources: list[str] = []
        for provider in ("HITMO", "YOUTUBE", "SOUNDCLOUD", "BANDCAMP"):
            key = f"ACQUISITION_ENABLE_{provider}"
            value = self.launcher_environment.get(key, "0")
            if value not in {"0", "1"}:
                raise ValueError("acquisition_agent_source_policy_invalid")
            self.launcher_environment[key] = value
            if value == "1":
                enabled_sources.append("yt_dlp" if provider == "YOUTUBE" else provider.casefold())
        if self.launcher_environment.get("ACQUISITION_JAMENDO_ID"):
            enabled_sources.append("jamendo")
        if self.launcher_environment.get("ACQUISITION_YANDEX_TOKEN"):
            enabled_sources.append("yandex")
        if not enabled_sources:
            raise ValueError("acquisition_agent_source_policy_missing")
        self.enabled_sources = tuple(sorted(enabled_sources))
        self.active_id: UUID | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self._stopping = False

    def run(self, *, interval: float = 10.0) -> None:
        if not 0.2 <= interval <= 60:
            raise ValueError("acquisition_agent_interval_invalid")
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        with exclusive_lock(self.control / "agent.lock"):
            while not self._stopping:
                self.tick()
                time.sleep(interval)
            self._finish_active()

    def tick(self) -> None:
        if self.process is not None and self.process.poll() is not None:
            self._finish_active()
        # UUID filenames are random; preserve the order in which the web tier
        # published requests to the spool.
        requests = sorted(
            (self.control / "requests").glob("*.json"),
            key=lambda path: (path.stat().st_mtime_ns, path.name),
        )
        for request in requests:
            self._handle_request(request)
        if self.active_id is not None:
            self._publish(self.active_id)
        self._write_control_json(
            self.control / "agent.json",
            {
                "schema_version": 1,
                "last_seen_unix": time.time(),
                "active_queue": str(self.active_id) if self.active_id is not None else None,
                "sources": self.enabled_sources,
            },
        )

    def _handle_request(self, path: Path) -> None:
        queue_id: UUID | None = None
        try:
            request_id = UUID(path.stem)
            if str(request_id) != path.stem:
                raise ValueError("invalid request id")
            value = read_json(path, max_bytes=4_096)
            if (
                set(value)
                not in (
                    {"schema_version", "action", "queue_id"},
                    {
                        "schema_version",
                        "action",
                        "queue_id",
                        "workers",
                        "yt_dlp_concurrency",
                        "soundcloud_concurrency",
                    },
                )
                or value["schema_version"] != 1
            ):
                raise ValueError("invalid request")
            queue_id = UUID(value["queue_id"])
            if str(queue_id) != value["queue_id"]:
                raise ValueError("invalid queue id")
            action = value["action"]
            if action == "submit":
                if self.active_id is not None:
                    return
                self._start(queue_id, value)
            elif action == "configure":
                if self.active_id == queue_id:
                    return
                if set(value) != {
                    "schema_version",
                    "action",
                    "queue_id",
                    "workers",
                    "yt_dlp_concurrency",
                    "soundcloud_concurrency",
                }:
                    raise ValueError("invalid configuration request")
                self._configure(queue_id, value)
            elif action in {"run", "pause", "resume", "retry", "retry_not_found", "verify"}:
                if set(value) != {"schema_version", "action", "queue_id"}:
                    raise ValueError("invalid action request")
                if action == "run" and self.active_id is not None and self.active_id != queue_id:
                    return
                if self.active_id == queue_id and action in {"retry", "retry_not_found", "verify"}:
                    return
                self._action(queue_id, action)
            else:
                raise ValueError("invalid action")
            handled = self.control / "handled"
            handled.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.replace(path, handled / path.name)
            sync_directory(handled)
            sync_directory(path.parent)
        except (OSError, ValueError, TypeError, KeyError, PlaylistDownloadError):
            # A malformed command cannot block later commands. Error detail is never
            # copied from an exception that might contain a host path or provider URL.
            try:
                if queue_id is not None:
                    self._publish(queue_id, last_error="acquisition_request_invalid")
            except (ValueError, OSError, PlaylistDownloadError):
                pass
            rejected = self.control / "rejected"
            rejected.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.replace(path, rejected / path.name)
            sync_directory(rejected)

    def _start(self, queue_id: UUID, value: dict[str, object]) -> None:
        workers = self._bounded(value.get("workers"), 1, 4)
        youtube = self._bounded(value.get("yt_dlp_concurrency"), 1, 2)
        soundcloud = self._bounded(value.get("soundcloud_concurrency"), 1, 2)
        playlist = self.control / "playlists" / f"{queue_id}.txt"
        if playlist.is_symlink() or not playlist.is_file() or playlist.stat().st_size > 2 * 1024**2:
            raise ValueError("playlist invalid")
        queue = self.queues / str(queue_id)
        queue.mkdir(parents=True, exist_ok=True, mode=0o700)
        if queue.is_symlink():
            raise ValueError("queue invalid")
        self.process = subprocess.Popen(
            [
                str(self.launcher),
                str(playlist),
                str(queue),
                str(self.output),
                "--workers",
                str(workers),
                "--yt-dlp-concurrency",
                str(youtube),
                "--soundcloud-concurrency",
                str(soundcloud),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=self.launcher_environment,
        )
        self.active_id = queue_id
        self._publish(queue_id, state="running")

    def _action(self, queue_id: UUID, action: str) -> None:
        queue = self.queues / str(queue_id)
        if not (queue / "queue.json").is_file():
            raise ValueError("queue missing")
        if action == "run":
            if self.active_id is not None:
                return
            self._start(queue_id, self._configuration(queue_id))
        elif action == "pause":
            write_json(queue / "pause", {"paused": True})
        elif action == "resume":
            (queue / "pause").unlink(missing_ok=True)
            sync_directory(queue)
        elif action in {"retry", "retry_not_found"}:
            retry_unsuccessful(queue, include_not_found=action == "retry_not_found")
        elif action == "verify":
            verify_downloads(queue)
        self._publish(queue_id)

    def _configuration(self, queue_id: UUID) -> dict[str, object]:
        status = read_json(self.control / "status" / f"{queue_id}.json", max_bytes=16_384)
        return {
            "workers": status.get("workers"),
            "yt_dlp_concurrency": status.get("yt_dlp_concurrency"),
            "soundcloud_concurrency": status.get("soundcloud_concurrency"),
        }

    def _configure(self, queue_id: UUID, value: dict[str, object]) -> None:
        status_path = self.control / "status" / f"{queue_id}.json"
        status = read_json(status_path, max_bytes=16_384)
        status["workers"] = self._bounded(value["workers"], 1, 4)
        status["yt_dlp_concurrency"] = self._bounded(value["yt_dlp_concurrency"], 1, 2)
        status["soundcloud_concurrency"] = self._bounded(value["soundcloud_concurrency"], 1, 2)
        self._write_control_json(status_path, status)

    def _publish(
        self,
        queue_id: UUID,
        *,
        state: str | None = None,
        last_error: str | None = None,
        clear_error: bool = False,
    ) -> None:
        status_path = self.control / "status" / f"{queue_id}.json"
        status = read_json(status_path, max_bytes=16_384)
        queue = self.queues / str(queue_id)
        if (queue / "queue.json").is_file():
            summary = queue_status(queue)
            status.update({key: summary[key] for key in _COUNTS})
            status["requested"] = summary["requested"]
            status["paused"] = summary["paused"]
            if state is None:
                state = (
                    "paused"
                    if summary["paused"]
                    else "running"
                    if self.active_id == queue_id
                    else "finished"
                    if summary["state"] == "finished"
                    else "queued"
                )
            runtime_path = queue / "runtime.json"
            if runtime_path.is_file():
                runtime = read_json(runtime_path, max_bytes=65_536)
                raw_providers = runtime.get("providers")
                if isinstance(raw_providers, dict):
                    providers: dict[str, dict[str, int | bool]] = {}
                    for name, metrics in raw_providers.items():
                        if (
                            not isinstance(name, str)
                            or name not in _PROVIDERS
                            or not isinstance(metrics, dict)
                        ):
                            continue
                        counts: dict[str, int] = {}
                        for key in _PROVIDER_COUNTS:
                            number = metrics.get(key)
                            if type(number) is not int or not 0 <= number <= 10_000_000:
                                break
                            counts[key] = number
                        circuit = metrics.get("circuit_open")
                        if len(counts) == len(_PROVIDER_COUNTS) and isinstance(circuit, bool):
                            providers[name] = {**counts, "circuit_open": circuit}
                    status["providers"] = providers
        if state is not None:
            status["state"] = state
        if last_error is not None or clear_error:
            status["last_error"] = last_error
        self._write_control_json(status_path, status)

    def _finish_active(self) -> None:
        process, queue_id = self.process, self.active_id
        if process is None or queue_id is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=210)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        result = process.returncode
        self.process = None
        self.active_id = None
        successful_pass = result in {0, 1, 75}
        self._publish(
            queue_id,
            state=None if successful_pass else "failed",
            last_error=None if successful_pass else "acquisition_run_failed",
            clear_error=successful_pass,
        )

    def _stop(self, _signal: int, _frame: object) -> None:
        self._stopping = True

    @staticmethod
    def _write_control_json(path: Path, value: object) -> None:
        """Publish status readable by the API's shared private Unix group."""
        descriptor, name = tempfile.mkstemp(prefix=".acquisition-", dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                if os.name != "nt":
                    os.fchmod(handle.fileno(), 0o660)
                json.dump(value, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            sync_directory(path.parent)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _bounded(value: object, lower: int, upper: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or not lower <= value <= upper:
            raise ValueError("invalid concurrency")
        return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the operator-owned acquisition web agent.")
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--queue-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--launcher", type=Path, required=True)
    args = parser.parse_args()
    AcquisitionAgent(args.control_root, args.queue_root, args.output_dir, args.launcher).run()


if __name__ == "__main__":
    main()
