"""Search Jamendo and download one artist-authorized track for personal use.

The workflow is deliberately sequential: search the official API, reject
tracks whose authors disabled downloads, rank the eligible results, download
through the official file endpoint, verify the MP3, and publish it together
with an attribution/license sidecar. This standalone CLI does not call the
AutPlay server. The server has a separate disabled-by-default adapter with the
same permission boundary; neither path claims Vault or READY state.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

JAMENDO_API_VERSION = "3.0"
JAMENDO_TRACKS_URL = "https://api.jamendo.com/v3.0/tracks/"
JAMENDO_TRACK_FILE_URL = "https://api.jamendo.com/v3.0/tracks/file/"
MAX_QUERY_LENGTH = 200
MAX_RESULTS = 50
MAX_SEARCH_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 30
USER_AGENT = "AutPlay-Authorized-Jamendo-Tool/1.0"
_AUDIO_CONTENT_TYPES = frozenset(
    {"application/octet-stream", "audio/mp3", "audio/mpeg", "binary/octet-stream"}
)
_INVALID_FILENAME_CHARACTERS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "aux",
    "com1",
    "com2",
    "com3",
    "com4",
    "com5",
    "com6",
    "com7",
    "com8",
    "com9",
    "con",
    "lpt1",
    "lpt2",
    "lpt3",
    "lpt4",
    "lpt5",
    "lpt6",
    "lpt7",
    "lpt8",
    "lpt9",
    "nul",
    "prn",
}


class JamendoToolError(RuntimeError):
    """A stable, credential-free failure raised by the standalone tool."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class TrackCandidate:
    """One downloadable Jamendo search result with license evidence."""

    track_id: str
    title: str
    artist: str
    album: str | None
    duration_seconds: int
    license_url: str
    share_url: str

    @property
    def duration(self) -> str:
        """Return a bounded human-readable duration."""

        minutes, seconds = divmod(self.duration_seconds, 60)
        return f"{minutes:02d}:{seconds:02d}"


@dataclass(frozen=True, slots=True)
class RankedTrack:
    """A candidate paired with its deterministic query match score."""

    candidate: TrackCandidate
    score: float


@dataclass(frozen=True, slots=True)
class TransferReceipt:
    """Bounded response metadata returned by the HTTPS transport."""

    content_type: str
    byte_count: int


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """Published audio and attribution filenames."""

    audio_path: Path
    attribution_path: Path
    byte_count: int
    track: TrackCandidate


class SearchTransport(Protocol):
    """Minimal injected boundary for the Jamendo search request."""

    def fetch_search_response(self, query: str, *, limit: int) -> bytes: ...


class DownloadTransport(Protocol):
    """Minimal injected boundary for an authorized Jamendo file request."""

    def download_audio(
        self,
        track_id: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> TransferReceipt: ...


class CurlTransport:
    """Sequential HTTPS-only Jamendo transport with bounded transient retries."""

    def __init__(
        self,
        client_id_file: Path,
        *,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not 5 <= timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be between 5 and 60")
        curl_name = "curl.exe" if os.name == "nt" else "curl"
        curl_path = shutil.which(curl_name) or shutil.which("curl")
        if curl_path is None:
            raise JamendoToolError("curl_not_found")
        self._client_id = load_client_id(client_id_file)
        self._curl = curl_path
        self._timeout_seconds = timeout_seconds

    def fetch_search_response(self, query: str, *, limit: int) -> bytes:
        """Request one relevance-ordered page from the official tracks API."""

        with tempfile.TemporaryDirectory(prefix="autplay-jamendo-search-") as directory:
            response_path = Path(directory) / "response.json"
            self._request_to_file(
                JAMENDO_TRACKS_URL,
                response_path,
                parameters=(
                    ("format", "json"),
                    ("limit", str(limit)),
                    ("order", "relevance"),
                    ("search", query),
                    ("include", "licenses"),
                    ("audiodlformat", "mp32"),
                    ("type", "single albumtrack"),
                ),
                accept="application/json",
                max_bytes=MAX_SEARCH_BYTES,
                failure_code="search_request_failed",
            )
            try:
                return response_path.read_bytes()
            except OSError as error:
                raise JamendoToolError("search_response_invalid") from error

    def download_audio(
        self,
        track_id: str,
        destination: Path,
        *,
        max_bytes: int,
    ) -> TransferReceipt:
        """Download one track through Jamendo's official file redirect endpoint."""

        if re.fullmatch(r"\d{1,20}", track_id) is None:
            raise JamendoToolError("track_id_invalid")
        return self._request_to_file(
            JAMENDO_TRACK_FILE_URL,
            destination,
            parameters=(
                ("id", track_id),
                ("action", "download"),
                ("audioformat", "mp32"),
            ),
            accept="audio/mpeg,application/octet-stream;q=0.9,*/*;q=0.1",
            max_bytes=max_bytes,
            failure_code="download_failed",
        )

    def _request_to_file(
        self,
        url: str,
        destination: Path,
        *,
        parameters: Sequence[tuple[str, str]],
        accept: str,
        max_bytes: int,
        failure_code: str,
    ) -> TransferReceipt:
        arguments = [
            self._curl,
            "--silent",
            "--show-error",
            "--fail",
            "--location",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--max-redirs",
            "5",
            "--connect-timeout",
            "10",
            "--max-time",
            str(self._timeout_seconds),
            "--retry",
            "2",
            "--retry-delay",
            "1",
            "--retry-max-time",
            str(self._timeout_seconds),
            "--max-filesize",
            str(max_bytes),
            "--user-agent",
            USER_AGENT,
            "--header",
            f"Accept: {accept}",
            "--get",
            "--data-urlencode",
            "client_id@-",
        ]
        for name, value in parameters:
            arguments.extend(("--data-urlencode", f"{name}={value}"))
        arguments.extend(
            (
                "--output",
                str(destination),
                "--write-out",
                "%{content_type}\n%{http_code}",
                url,
            )
        )
        try:
            completed = subprocess.run(
                arguments,
                input=self._client_id,
                check=True,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds + 5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise JamendoToolError(failure_code) from error

        metadata = completed.stdout.splitlines()
        if len(metadata) != 2 or not metadata[1].isdigit():
            raise JamendoToolError(failure_code)
        status = int(metadata[1])
        if not 200 <= status < 300:
            raise JamendoToolError(failure_code)
        try:
            byte_count = destination.stat().st_size
        except OSError as error:
            raise JamendoToolError(failure_code) from error
        if byte_count > max_bytes:
            raise JamendoToolError("response_too_large")
        content_type = metadata[0].partition(";")[0].strip().casefold()
        return TransferReceipt(content_type, byte_count)


def load_client_id(path: Path) -> str:
    """Read and validate a Jamendo client ID without exposing it in errors."""

    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise JamendoToolError("client_id_file_unavailable") from error
    if re.fullmatch(r"[A-Za-z0-9_-]{4,100}", value) is None:
        raise JamendoToolError("client_id_invalid")
    return value


def parse_search_response(payload: bytes, *, limit: int) -> tuple[TrackCandidate, ...]:
    """Parse a bounded Jamendo response and retain only downloadable tracks."""

    if len(payload) > MAX_SEARCH_BYTES:
        raise JamendoToolError("search_response_too_large")
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise JamendoToolError("search_response_invalid") from error
    if not isinstance(document, dict):
        raise JamendoToolError("search_response_invalid")
    headers = document.get("headers")
    if not isinstance(headers, dict):
        raise JamendoToolError("search_response_invalid")
    if headers.get("status") != "success" or headers.get("code") != 0:
        raise JamendoToolError("provider_response_failed")
    results = document.get("results")
    if not isinstance(results, list) or len(results) > limit:
        raise JamendoToolError("search_response_invalid")

    candidates: list[TrackCandidate] = []
    seen: set[str] = set()
    for raw_result in results:
        if not isinstance(raw_result, dict):
            raise JamendoToolError("search_response_invalid")
        allowed = raw_result.get("audiodownload_allowed")
        if not isinstance(allowed, bool):
            raise JamendoToolError("download_permission_missing")
        if not allowed:
            continue
        candidate = _parse_candidate(raw_result)
        if candidate.track_id in seen:
            continue
        seen.add(candidate.track_id)
        candidates.append(candidate)
    return tuple(candidates)


def search_tracks(
    query: str,
    *,
    transport: SearchTransport,
    limit: int = 20,
) -> tuple[TrackCandidate, ...]:
    """Run one official API search and return artist-authorized candidates."""

    normalized_query = " ".join(query.split())
    if not normalized_query or len(normalized_query) > MAX_QUERY_LENGTH:
        raise JamendoToolError("query_invalid")
    if not 1 <= limit <= MAX_RESULTS:
        raise ValueError(f"limit must be between 1 and {MAX_RESULTS}")
    return parse_search_response(
        transport.fetch_search_response(normalized_query, limit=limit),
        limit=limit,
    )


def rank_tracks(query: str, candidates: Sequence[TrackCandidate]) -> tuple[RankedTrack, ...]:
    """Rank candidates without converting provider metadata into canonical identity."""

    query_text = _normalize_match_text(query)
    if not query_text:
        raise JamendoToolError("query_invalid")
    ranked = [
        RankedTrack(candidate, _match_score(query_text, candidate)) for candidate in candidates
    ]
    return tuple(sorted(ranked, key=lambda item: item.score, reverse=True))


def select_track(
    query: str,
    candidates: Sequence[TrackCandidate],
    *,
    index: int | None = None,
    minimum_score: float = 0.45,
) -> RankedTrack:
    """Select a ranked result explicitly or use the strongest bounded match."""

    if not candidates:
        raise JamendoToolError("no_downloadable_tracks_found")
    ranked = rank_tracks(query, candidates)
    if index is not None:
        if not 1 <= index <= len(ranked):
            raise JamendoToolError("track_index_invalid")
        return ranked[index - 1]
    if not 0.0 <= minimum_score <= 1.0:
        raise ValueError("minimum_score must be between zero and one")
    if ranked[0].score < minimum_score:
        raise JamendoToolError("match_too_weak")
    return ranked[0]


def download_track(
    ranked_track: RankedTrack,
    output_directory: Path,
    *,
    transport: DownloadTransport,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> DownloadResult:
    """Download, verify, attribute, and publish one permitted Jamendo track."""

    if not 1024 <= max_bytes <= 1024 * 1024 * 1024:
        raise ValueError("max_bytes must be between 1 KiB and 1 GiB")
    try:
        output_directory.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise JamendoToolError("output_directory_unavailable") from error
    if not output_directory.is_dir():
        raise JamendoToolError("output_directory_unavailable")

    file_stem = sanitize_filename(
        f"{ranked_track.candidate.artist} - {ranked_track.candidate.title}"
    )
    audio_descriptor, audio_name = tempfile.mkstemp(
        prefix=".autplay-jamendo-",
        suffix=".mp3.part",
        dir=output_directory,
    )
    os.close(audio_descriptor)
    audio_temporary_path = Path(audio_name)
    attribution_descriptor, attribution_name = tempfile.mkstemp(
        prefix=".autplay-jamendo-",
        suffix=".json.part",
        dir=output_directory,
    )
    os.close(attribution_descriptor)
    attribution_temporary_path = Path(attribution_name)
    try:
        receipt = transport.download_audio(
            ranked_track.candidate.track_id,
            audio_temporary_path,
            max_bytes=max_bytes,
        )
        _validate_download(audio_temporary_path, receipt)
        _write_attribution(attribution_temporary_path, ranked_track.candidate)
        audio_path, attribution_path = _publish_pair(
            audio_temporary_path,
            attribution_temporary_path,
            output_directory,
            file_stem,
        )
        return DownloadResult(
            audio_path,
            attribution_path,
            receipt.byte_count,
            ranked_track.candidate,
        )
    finally:
        audio_temporary_path.unlink(missing_ok=True)
        attribution_temporary_path.unlink(missing_ok=True)


def sanitize_filename(value: str) -> str:
    """Return a bounded cross-platform filename stem."""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = _INVALID_FILENAME_CHARACTERS.sub("_", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    if not normalized:
        normalized = "track"
    if normalized.casefold() in _RESERVED_WINDOWS_NAMES:
        normalized = f"_{normalized}"
    return normalized[:180].rstrip(" .") or "track"


def _parse_candidate(value: Mapping[str, object]) -> TrackCandidate:
    track_id = _required_digits(value, "id")
    title = _required_text(value, "name", 500)
    artist = _required_text(value, "artist_name", 500)
    album = _optional_text(value, "album_name", 500)
    duration_seconds = _positive_integer(value, "duration", 24 * 60 * 60)
    license_url = _required_text(value, "license_ccurl", 1_000)
    share_url = _required_text(value, "shareurl", 1_000)
    if not _is_creative_commons_license(license_url):
        raise JamendoToolError("license_url_invalid")
    if not _is_jamendo_share_url(share_url):
        raise JamendoToolError("share_url_invalid")
    return TrackCandidate(
        track_id,
        title,
        artist,
        album,
        duration_seconds,
        license_url,
        share_url,
    )


def _required_digits(value: Mapping[str, object], key: str) -> str:
    raw = value.get(key)
    if isinstance(raw, int) and not isinstance(raw, bool):
        raw = str(raw)
    if not isinstance(raw, str) or re.fullmatch(r"\d{1,20}", raw) is None:
        raise JamendoToolError("search_response_invalid")
    return raw


def _required_text(value: Mapping[str, object], key: str, limit: int) -> str:
    raw = value.get(key)
    if not isinstance(raw, str):
        raise JamendoToolError("search_response_invalid")
    cleaned = " ".join(raw.split())
    if not cleaned or len(cleaned) > limit:
        raise JamendoToolError("search_response_invalid")
    return cleaned


def _optional_text(value: Mapping[str, object], key: str, limit: int) -> str | None:
    raw = value.get(key)
    if raw in {None, ""}:
        return None
    if not isinstance(raw, str):
        raise JamendoToolError("search_response_invalid")
    cleaned = " ".join(raw.split())
    if not cleaned or len(cleaned) > limit:
        raise JamendoToolError("search_response_invalid")
    return cleaned


def _positive_integer(value: Mapping[str, object], key: str, upper: int) -> int:
    raw = value.get(key)
    if isinstance(raw, str) and raw.isdigit():
        raw = int(raw)
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= upper:
        raise JamendoToolError("search_response_invalid")
    return raw


def _is_creative_commons_license(url: str) -> bool:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold()
    return (
        parsed.scheme in {"http", "https"}
        and hostname in {"creativecommons.org", "www.creativecommons.org"}
        and parsed.path.startswith("/licenses/")
        and parsed.username is None
    )


def _is_jamendo_share_url(url: str) -> bool:
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold()
    return (
        parsed.scheme == "https"
        and (hostname in {"jamendo.com", "jamen.do"} or hostname.endswith(".jamendo.com"))
        and parsed.username is None
    )


def _match_score(query_text: str, candidate: TrackCandidate) -> float:
    title = _normalize_match_text(candidate.title)
    artist = _normalize_match_text(candidate.artist)
    variants = (title, f"{artist} {title}".strip(), f"{title} {artist}".strip())
    sequence_score = max(SequenceMatcher(None, query_text, value).ratio() for value in variants)
    query_tokens = set(query_text.split())
    candidate_tokens = set(f"{artist} {title}".split())
    common = len(query_tokens.intersection(candidate_tokens))
    token_score = (2 * common / (len(query_tokens) + len(candidate_tokens))) if common else 0.0
    containment_bonus = 0.1 if any(query_text == value for value in variants) else 0.0
    return min(1.0, (sequence_score * 0.6) + (token_score * 0.4) + containment_bonus)


def _normalize_match_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[\w]+", without_marks, flags=re.UNICODE))


def _looks_like_mp3(path: Path) -> bool:
    try:
        with path.open("rb") as input_file:
            header = input_file.read(512)
    except OSError as error:
        raise JamendoToolError("downloaded_content_invalid") from error
    if header.startswith(b"ID3"):
        return True
    return len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0


def _validate_download(path: Path, receipt: TransferReceipt) -> None:
    if receipt.byte_count < 1024:
        raise JamendoToolError("downloaded_content_invalid")
    try:
        with path.open("rb") as input_file:
            prefix = input_file.read(512).lstrip().lower()
    except OSError as error:
        raise JamendoToolError("downloaded_content_invalid") from error
    if prefix.startswith((b"<!doctype html", b"<html")):
        raise JamendoToolError("downloaded_content_invalid")
    if receipt.content_type not in _AUDIO_CONTENT_TYPES or not _looks_like_mp3(path):
        raise JamendoToolError("downloaded_content_invalid")


def _write_attribution(path: Path, track: TrackCandidate) -> None:
    document = {
        "album": track.album,
        "artist": track.artist,
        "download_permission": "audiodownload_allowed=true",
        "duration_seconds": track.duration_seconds,
        "license_url": track.license_url,
        "provider": "Jamendo",
        "provider_api_version": JAMENDO_API_VERSION,
        "provider_track_id": track.track_id,
        "share_url": track.share_url,
        "title": track.title,
    }
    try:
        path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise JamendoToolError("attribution_write_failed") from error


def _publish_pair(
    audio_source: Path,
    attribution_source: Path,
    directory: Path,
    stem: str,
) -> tuple[Path, Path]:
    for number in range(1, 10_000):
        qualifier = "" if number == 1 else f" ({number})"
        audio_destination = directory / f"{stem}{qualifier}.mp3"
        attribution_destination = directory / f"{stem}{qualifier}.jamendo.json"
        if audio_destination.exists() or attribution_destination.exists():
            continue
        attribution_published = False
        audio_published = False
        try:
            _publish_one(attribution_source, attribution_destination)
            attribution_published = True
            _publish_one(audio_source, audio_destination)
            audio_published = True
        except FileExistsError:
            if audio_published:
                audio_destination.unlink(missing_ok=True)
            if attribution_published:
                attribution_destination.unlink(missing_ok=True)
            continue
        except OSError as error:
            if audio_published:
                audio_destination.unlink(missing_ok=True)
            if attribution_published:
                attribution_destination.unlink(missing_ok=True)
            raise JamendoToolError("download_publish_failed") from error
        audio_source.unlink(missing_ok=True)
        attribution_source.unlink(missing_ok=True)
        return audio_destination, attribution_destination
    raise JamendoToolError("download_name_exhausted")


def _publish_one(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination)
    except OSError as error:
        if isinstance(error, FileExistsError):
            raise
        if error.errno not in {errno.EPERM, errno.EXDEV, errno.ENOTSUP}:
            raise
        try:
            with source.open("rb") as input_file, destination.open("xb") as output_file:
                shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
        except OSError:
            destination.unlink(missing_ok=True)
            raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Search Jamendo API v3.0 and download one track whose artist enabled downloads. "
            "Use only for authorized non-commercial personal use and follow the saved license."
        )
    )
    parser.add_argument("query", help="Song query, preferably 'artist - title'.")
    parser.add_argument(
        "--client-id-file",
        required=True,
        type=Path,
        help="UTF-8 file containing your Jamendo API client_id.",
    )
    parser.add_argument("--output-dir", type=Path, help="Destination directory for the MP3.")
    parser.add_argument("--list", action="store_true", help="List permitted matches only.")
    parser.add_argument("--index", type=int, help="Use a 1-based index from the ranked list.")
    parser.add_argument("--limit", type=int, default=20, choices=range(1, MAX_RESULTS + 1))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--minimum-score", type=float, default=0.45)
    parser.add_argument(
        "--max-mib",
        type=int,
        default=DEFAULT_MAX_DOWNLOAD_BYTES // (1024 * 1024),
        help="Maximum accepted download size in MiB.",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the standalone CLI and return a stable process status."""

    parser = _build_parser()
    options = parser.parse_args(arguments)
    try:
        transport = CurlTransport(options.client_id_file, timeout_seconds=options.timeout)
        candidates = search_tracks(options.query, transport=transport, limit=options.limit)
        ranked = rank_tracks(options.query, candidates)
        if options.list:
            if not ranked:
                raise JamendoToolError("no_downloadable_tracks_found")
            for position, item in enumerate(ranked, start=1):
                print(
                    f"{position:02d}. {item.candidate.artist} - {item.candidate.title} "
                    f"[{item.candidate.duration}] score={item.score:.3f} "
                    f"license={item.candidate.license_url}"
                )
            return 0
        if options.output_dir is None:
            raise JamendoToolError("output_directory_required")
        selected = select_track(
            options.query,
            candidates,
            index=options.index,
            minimum_score=options.minimum_score,
        )
        result = download_track(
            selected,
            options.output_dir,
            transport=transport,
            max_bytes=options.max_mib * 1024 * 1024,
        )
        print(
            f"Downloaded: {result.audio_path.name} "
            f"({result.byte_count / (1024 * 1024):.2f} MiB); "
            f"attribution: {result.attribution_path.name}"
        )
        return 0
    except JamendoToolError as error:
        print(f"Error: {error.code}", file=sys.stderr)
        return 2
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
