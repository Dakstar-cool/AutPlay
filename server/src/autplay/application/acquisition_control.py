"""Owner-only handoff to the independently deployed file acquisition agent."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from autplay.domain.auth import AccountRole
from autplay.domain.web_admin import WebActor, WebAdminError

MAX_PLAYLIST_BYTES = 2 * 1024 * 1024
_IDENTITY = re.compile(r"^[^\x00-\x1f\x7f]{1,200}$")
_ACTIONS = frozenset({"run", "pause", "resume", "retry", "retry_not_found", "verify"})
_COUNTS = ("pending", "running", "retry", "downloaded", "not_found", "failed", "needs_review")
_PROVIDERS = frozenset({"jamendo", "hitmo", "yt_dlp", "soundcloud", "bandcamp", "yandex"})
_PROVIDER_COUNTS = ("requests", "downloaded", "misses", "failures", "deferred")


def _valid_identity(value: str) -> bool:
    cleaned = value.strip()
    return _IDENTITY.fullmatch(cleaned) is not None and all(char.isprintable() for char in cleaned)


@dataclass(frozen=True, slots=True)
class AcquisitionProviderView:
    name: str
    counts: dict[str, int]
    circuit_open: bool


@dataclass(frozen=True, slots=True)
class AcquisitionQueueView:
    queue_id: UUID
    state: str
    counts: dict[str, int]
    requested: int
    paused: bool
    workers: int
    yt_dlp_concurrency: int
    soundcloud_concurrency: int
    last_error: str | None
    providers: tuple[AcquisitionProviderView, ...]


class AcquisitionControlService:
    """Write bounded commands only; the API never opens Docker or provider credentials."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or root == Path(root.anchor):
            raise ValueError("acquisition control root must be a bounded absolute path")
        if any(part.is_symlink() or part.is_junction() for part in (root, *root.parents)):
            raise ValueError("acquisition control root cannot traverse links")
        self._root = root
        for name in ("requests", "playlists", "status"):
            directory = root / name
            if directory.is_symlink() or directory.is_junction():
                raise ValueError("acquisition control directory cannot be a link")
            created = not directory.exists()
            directory.mkdir(parents=True, exist_ok=True, mode=0o2770)
            if created and os.name != "nt":
                directory.chmod(0o2770)

    def queues(self, actor: WebActor) -> tuple[AcquisitionQueueView, ...]:
        self._owner(actor)
        result: list[AcquisitionQueueView] = []
        for path in sorted((self._root / "status").glob("*.json"), reverse=True)[:50]:
            try:
                queue_id = UUID(path.stem)
                value = self._read(path)
                counts = {key: self._number(value.get(key), 0, 10_000) for key in _COUNTS}
                state = value.get("state")
                if state not in {"queued", "running", "paused", "finished", "failed"}:
                    raise ValueError("invalid state")
                raw_error = value.get("last_error")
                last_error = (
                    raw_error
                    if isinstance(raw_error, str) and re.fullmatch(r"[a-z0-9_.-]{1,100}", raw_error)
                    else None
                )
                raw_providers = value.get("providers", {})
                if not isinstance(raw_providers, dict):
                    raise ValueError("provider metrics invalid")
                providers: list[AcquisitionProviderView] = []
                for name, raw_metrics in sorted(raw_providers.items()):
                    if name not in _PROVIDERS or not isinstance(raw_metrics, dict):
                        raise ValueError("provider metrics invalid")
                    circuit_open = raw_metrics.get("circuit_open")
                    if not isinstance(circuit_open, bool):
                        raise ValueError("provider metrics invalid")
                    providers.append(
                        AcquisitionProviderView(
                            name,
                            {
                                key: self._number(raw_metrics.get(key), 0, 10_000_000)
                                for key in _PROVIDER_COUNTS
                            },
                            circuit_open,
                        )
                    )
                result.append(
                    AcquisitionQueueView(
                        queue_id=queue_id,
                        state=state,
                        counts=counts,
                        requested=self._number(value.get("requested"), 0, 10_000),
                        paused=value.get("paused") is True,
                        workers=self._number(value.get("workers"), 1, 4),
                        yt_dlp_concurrency=self._number(value.get("yt_dlp_concurrency"), 1, 2),
                        soundcloud_concurrency=self._number(
                            value.get("soundcloud_concurrency"), 1, 2
                        ),
                        last_error=last_error,
                        providers=tuple(providers),
                    )
                )
            except (OSError, ValueError, TypeError) as error:
                raise WebAdminError("acquisition_control_unavailable") from error
        return tuple(result)

    def agent_online(self, actor: WebActor) -> bool:
        self._owner(actor)
        path = self._root / "agent.json"
        if not path.is_file():
            return False
        try:
            document = self._read(path)
            seen = document.get("last_seen_unix")
            return (
                document.get("schema_version") == 1
                and isinstance(seen, int | float)
                and not isinstance(seen, bool)
                and 0 <= time.time() - seen <= 30
            )
        except OSError, ValueError, TypeError:
            return False

    def agent_sources(self, actor: WebActor) -> tuple[str, ...]:
        self._owner(actor)
        if not self.agent_online(actor):
            return ()
        try:
            values = self._read(self._root / "agent.json").get("sources")
            if (
                not isinstance(values, list)
                or not 1 <= len(values) <= len(_PROVIDERS)
                or any(not isinstance(name, str) or name not in _PROVIDERS for name in values)
            ):
                return ()
            return tuple(values)
        except OSError, ValueError, TypeError:
            return ()

    def submit(
        self,
        actor: WebActor,
        operation_id: UUID,
        payload: bytes,
        *,
        workers: int,
        yt_dlp_concurrency: int,
        soundcloud_concurrency: int,
    ) -> None:
        self._owner(actor)
        if not 1 <= len(payload) <= MAX_PLAYLIST_BYTES or b"\x00" in payload:
            raise WebAdminError("acquisition_input_invalid")
        self._number(workers, 1, 4)
        self._number(yt_dlp_concurrency, 1, 2)
        self._number(soundcloud_concurrency, 1, 2)
        try:
            payload.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                payload.decode("cp1251")
            except UnicodeDecodeError as error:
                raise WebAdminError("acquisition_input_invalid") from error
        playlist = self._root / "playlists" / f"{operation_id}.txt"
        command = self._root / "requests" / f"{operation_id}.json"
        if playlist.exists() or command.exists():
            raise WebAdminError("operation_conflict")
        self._write_new(playlist, payload)
        self._write_new(
            self._root / "status" / f"{operation_id}.json",
            json.dumps(
                {
                    "state": "queued",
                    "requested": 0,
                    **dict.fromkeys(_COUNTS, 0),
                    "paused": False,
                    "workers": workers,
                    "yt_dlp_concurrency": yt_dlp_concurrency,
                    "soundcloud_concurrency": soundcloud_concurrency,
                    "last_error": None,
                },
                separators=(",", ":"),
            ).encode("ascii"),
        )
        self._write_new(
            command,
            json.dumps(
                {
                    "schema_version": 1,
                    "action": "submit",
                    "queue_id": str(operation_id),
                    "workers": workers,
                    "yt_dlp_concurrency": yt_dlp_concurrency,
                    "soundcloud_concurrency": soundcloud_concurrency,
                },
                separators=(",", ":"),
            ).encode("ascii"),
        )

    def submit_track(
        self,
        actor: WebActor,
        operation_id: UUID,
        *,
        artist: str,
        title: str,
        workers: int,
        yt_dlp_concurrency: int,
        soundcloud_concurrency: int,
    ) -> None:
        if not _valid_identity(artist) or not _valid_identity(title):
            raise WebAdminError("acquisition_input_invalid")
        self.submit(
            actor,
            operation_id,
            f"{artist.strip()}\t{title.strip()}\n".encode(),
            workers=workers,
            yt_dlp_concurrency=yt_dlp_concurrency,
            soundcloud_concurrency=soundcloud_concurrency,
        )

    def submit_artist_group(
        self,
        actor: WebActor,
        operation_id: UUID,
        *,
        artist: str,
        titles: str,
        workers: int,
        yt_dlp_concurrency: int,
        soundcloud_concurrency: int,
    ) -> None:
        if not _valid_identity(artist):
            raise WebAdminError("acquisition_input_invalid")
        rows = [line.strip() for line in titles.splitlines() if line.strip()]
        if not 1 <= len(rows) <= 500 or any(not _valid_identity(row) for row in rows):
            raise WebAdminError("acquisition_input_invalid")
        payload = "".join(f"{artist.strip()}\t{row}\n" for row in rows).encode("utf-8")
        self.submit(
            actor,
            operation_id,
            payload,
            workers=workers,
            yt_dlp_concurrency=yt_dlp_concurrency,
            soundcloud_concurrency=soundcloud_concurrency,
        )

    def action(self, actor: WebActor, operation_id: UUID, queue_id: UUID, action: str) -> None:
        self._owner(actor)
        if action not in _ACTIONS:
            raise WebAdminError("acquisition_action_invalid")
        if not (self._root / "status" / f"{queue_id}.json").is_file():
            raise WebAdminError("acquisition_queue_missing")
        self._write_new(
            self._root / "requests" / f"{operation_id}.json",
            json.dumps(
                {"schema_version": 1, "action": action, "queue_id": str(queue_id)},
                separators=(",", ":"),
            ).encode("ascii"),
        )

    def configure(
        self,
        actor: WebActor,
        operation_id: UUID,
        queue_id: UUID,
        *,
        workers: int,
        yt_dlp_concurrency: int,
        soundcloud_concurrency: int,
    ) -> None:
        self._owner(actor)
        if not (self._root / "status" / f"{queue_id}.json").is_file():
            raise WebAdminError("acquisition_queue_missing")
        self._number(workers, 1, 4)
        self._number(yt_dlp_concurrency, 1, 2)
        self._number(soundcloud_concurrency, 1, 2)
        self._write_new(
            self._root / "requests" / f"{operation_id}.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "action": "configure",
                    "queue_id": str(queue_id),
                    "workers": workers,
                    "yt_dlp_concurrency": yt_dlp_concurrency,
                    "soundcloud_concurrency": soundcloud_concurrency,
                },
                separators=(",", ":"),
            ).encode("ascii"),
        )

    @staticmethod
    def _owner(actor: WebActor) -> None:
        if actor.role is not AccountRole.OWNER:
            raise WebAdminError("forbidden")

    @staticmethod
    def _number(value: object, minimum: int, maximum: int) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
            raise WebAdminError("acquisition_input_invalid")
        return value

    @staticmethod
    def _read(path: Path) -> dict[str, object]:
        if path.is_symlink() or path.stat().st_size > 16_384:
            raise ValueError("status invalid")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("status invalid")
        return value

    @staticmethod
    def _write_new(path: Path, payload: bytes) -> None:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".acquisition-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                if os.name != "nt":
                    os.fchmod(handle.fileno(), 0o660)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, path)
        except OSError as error:
            raise WebAdminError("acquisition_control_unavailable") from error
        finally:
            temporary.unlink(missing_ok=True)
