from __future__ import annotations

import asyncio
import contextlib
import hashlib
import http.server
import importlib
import sys
import threading
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import ClassVar

import pytest


@pytest.fixture
def tool() -> ModuleType:
    source = str(Path(__file__).parents[1] / "src")
    sys.path.insert(0, source)
    try:
        return importlib.import_module("local_music_acquisition.providers.hitmo")
    finally:
        sys.path.remove(source)


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    canonical_rows = True
    exact_position = 2
    duplicate_exact = False
    missing_control_position = 0
    overlay_submit = False
    overlay_download = False
    prevent_search_navigation = False

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path == "/download/test.mp3":
            payload = b"ID3\x04\x00\x00\x00\x00\x00\x00" + (b"\x00" * 4096)
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Disposition", 'attachment; filename="test.mp3"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
        rows = ""
        if query or self.prevent_search_navigation:
            candidates = [
                ("Хочу перемен", "Кино, Виктор Цой"),
                ("Другая песня", "Другой артист"),
                ("Третий трек", "Третий артист"),
                ("Четвертый трек", "Четвертый артист"),
                ("Пятый трек", "Пятый артист"),
                ("Шестой трек", "Шестой артист"),
            ]
            candidates[self.exact_position - 1] = ("Хочу перемен", "Кино")
            if self.duplicate_exact:
                candidates[4] = ("Хочу перемен", "Кино")
            row_class = "track tracks__item" if self.canonical_rows else "track"
            rows = "".join(
                f"<article class='{row_class}'>"
                f"<span class='track__title'>{title}</span>"
                f"<span class='track__desc'>{artist}</span><span>04:55</span>"
                + (
                    ""
                    if index == self.missing_control_position
                    else "<a class='download' download href='/download/test.mp3'>Скачать</a>"
                )
                + (
                    "<span style='position:absolute;inset:0;z-index:10'>Overlay</span>"
                    if self.overlay_download and index == self.exact_position
                    else ""
                )
                + "</article>"
                for index, (title, artist) in enumerate(candidates, 1)
            )
            if self.overlay_download:
                rows = rows.replace(
                    "class='track tracks__item'",
                    "class='track tracks__item' style='position:relative'",
                )
        search_handler = (
            " onclick='event.preventDefault()'" if self.prevent_search_navigation else ""
        )
        form_style = " style='position:relative'" if self.overlay_submit else ""
        overlay = (
            "<a href='/download/test.mp3' download "
            "style='position:absolute;inset:0;z-index:10'>Overlay</a>"
            if self.overlay_submit
            else ""
        )
        body = (
            "<!doctype html><html><body>"
            f"<form method='get'{form_style}>"
            "<input type='search' name='q' placeholder='Поиск'>"
            f"<button type='submit'{search_handler}>Search</button>{overlay}</form>"
            "<aside><a class='download' href='/download/advert.mp3'>Скачать</a></aside>"
            f"<main><h2>Треки</h2>{rows}</main></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextlib.contextmanager
def _fixture_site(
    exact_position: int,
    *,
    duplicate_exact: bool = False,
    canonical_rows: bool = True,
    missing_control_position: int = 0,
    overlay_submit: bool = False,
    overlay_download: bool = False,
    prevent_search_navigation: bool = False,
) -> Iterator[str]:
    handler = type(
        "FixtureHandler",
        (_FixtureHandler,),
        {
            "duplicate_exact": duplicate_exact,
            "canonical_rows": canonical_rows,
            "exact_position": exact_position,
            "missing_control_position": missing_control_position,
            "overlay_submit": overlay_submit,
            "overlay_download": overlay_download,
            "prevent_search_navigation": prevent_search_navigation,
        },
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class _QueueFixtureHandler(http.server.BaseHTTPRequestHandler):
    download_order: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/download/"):
            name = Path(parsed.path).name
            self.download_order.append(name)
            payload = b"ID3\x04\x00\x00\x00\x00\x00\x00" + (name.encode() * 512)
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        query = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
        requested = {
            "Track One Artist One": ("Track One", "Artist One", "one.mp3"),
            "Track Two Artist Two": ("Track Two", "Artist Two", "two.mp3"),
        }.get(query)
        rows = ""
        if requested is not None:
            title, artist, name = requested
            rows = (
                "<article class='track tracks__item'>"
                "<span class='track__title'>Decoy</span>"
                "<span class='track__desc'>Someone</span>"
                "<a class='download' download href='/download/decoy.mp3'>Скачать</a></article>"
                "<article class='track tracks__item'>"
                f"<span class='track__title'>{title}</span>"
                f"<span class='track__desc'>{artist}</span>"
                f"<a class='download' download href='/download/{name}'>Скачать</a></article>"
            )
        body = (
            "<!doctype html><html><body>"
            "<form method='get'><input type='search' name='q' placeholder='Поиск'>"
            "<button type='submit'>Search</button></form>"
            "<aside><a class='download' href='/download/advert.mp3'>Скачать</a></aside>"
            f"<main><h2>Треки</h2>{rows}</main></body></html>"
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@contextlib.contextmanager
def _queue_fixture_site() -> Iterator[tuple[str, type[_QueueFixtureHandler]]]:
    handler = type("QueueFixtureHandler", (_QueueFixtureHandler,), {"download_order": []})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", handler
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _set_run(tool: ModuleType, monkeypatch: pytest.MonkeyPatch, run_dir: Path) -> None:
    run_dir.mkdir()
    monkeypatch.setattr(tool, "_prepare_run_dir", lambda: run_dir)


def test_module_exposes_one_public_reusable_function(tool: ModuleType) -> None:
    public = [name for name in dir(tool) if not name.startswith("_")]
    assert public == ["download_hitmo_tracks"]


def test_real_download_requires_rights_confirmation(tool: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="rights_confirmation_required"):
        tool.download_hitmo_tracks(download=True)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.0.0.1:9222",
        "http://example.com:9222",
        "http://user:secret@127.0.0.1:9222",
        "http://127.0.0.1:9222/json/version",
        "http://127.0.0.1:9222/?token=secret",
    ],
)
def test_cdp_endpoint_is_restricted_to_plain_loopback(tool: ModuleType, endpoint: str) -> None:
    with pytest.raises(RuntimeError, match="cdp_endpoint_invalid"):
        tool._validate_cdp_endpoint(endpoint)


def test_cdp_uses_owned_tab_without_closing_browser(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakePage:
        url = "about:blank"

        def __init__(self) -> None:
            self.closed = False
            self.viewport: dict[str, int] | None = None

        async def set_viewport_size(self, value: dict[str, int]) -> None:
            self.viewport = value

        async def close(self) -> None:
            self.closed = True

    class FakeContext:
        def __init__(self, page: FakePage) -> None:
            self.page = page
            self.pages: list[FakePage] = []

        async def new_page(self) -> FakePage:
            return self.page

    class FakeBrowser:
        def __init__(self, context: FakeContext) -> None:
            self.contexts = [context]

    class FakeChromium:
        def __init__(self, browser: FakeBrowser) -> None:
            self.browser = browser
            self.arguments: dict[str, object] | None = None

        async def connect_over_cdp(self, endpoint: str, **kwargs: object) -> FakeBrowser:
            self.arguments = {"endpoint": endpoint, **kwargs}
            return self.browser

    class FakePlaywright:
        def __init__(self, chromium: FakeChromium) -> None:
            self.chromium = chromium

    class FakeManager:
        def __init__(self, playwright: FakePlaywright) -> None:
            self.playwright = playwright

        async def __aenter__(self) -> FakePlaywright:
            return self.playwright

        async def __aexit__(self, *_args: object) -> None:
            return None

    page = FakePage()
    chromium = FakeChromium(FakeBrowser(FakeContext(page)))
    manager = FakeManager(FakePlaywright(chromium))
    monkeypatch.setattr(tool, "_async_playwright", lambda: manager)

    async def fake_process_queue(**kwargs: object) -> list[dict[str, object]]:
        assert kwargs["page"] is page
        assert kwargs["reuse_current_page"] is False
        return [{"status": "matched"}]

    monkeypatch.setattr(tool, "_process_queue", fake_process_queue)
    run_dir = tmp_path / "run_1"
    run_dir.mkdir()
    state = tool._RunState(run_dir, capture_screenshots=False)
    result = asyncio.run(
        tool._drive_browser(
            requests=[("Кино", "Хочу перемен")],
            state=state,
            download_dir=tmp_path / "downloads",
            result_limit=5,
            timeout_ms=15_000,
            should_download=False,
            headless=True,
            browser="cdp",
            cdp_endpoint="http://127.0.0.1:9222",
        )
    )
    assert result == [{"status": "matched"}]
    assert page.closed is True
    assert page.viewport == {"width": 1280, "height": 1800}
    assert chromium.arguments == {
        "endpoint": "http://127.0.0.1:9222",
        "is_local": True,
        "no_defaults": False,
        "timeout": 15_000,
    }


def test_cdp_selector_reuses_only_clean_provider_page(tool: ModuleType) -> None:
    class FakePage:
        url = "https://ru.hitmoz.org/"

        async def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def evaluate(self, _script: str) -> bool:
            return True

    class FakeContext:
        def __init__(self, page: FakePage) -> None:
            self.pages = [page]

        async def new_page(self) -> None:
            raise AssertionError("clean provider page should be reused")

    page = FakePage()
    selected, reused, owned = asyncio.run(tool._select_cdp_page(FakeContext(page), 15_000))
    assert selected is page
    assert reused is True
    assert owned is False


def test_dry_run_finds_only_exact_match_in_first_five(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run_1"
    _set_run(tool, monkeypatch, run_dir)
    with _fixture_site(exact_position=2) as start_url:
        monkeypatch.setattr(tool, "_START_URL", start_url)
        result = tool.download_hitmo_tracks(timeout_seconds=15)
    assert result["matched"] == 1
    assert result["downloaded"] == 0
    assert result["results"] == [
        {
            "position": 2,
            "query_ref": tool._query_ref("Кино", "Хочу перемен"),
            "status": "matched",
        }
    ]
    assert not (tmp_path / "downloads").exists()
    log = (run_dir / "final_script_log.txt").read_text(encoding="utf-8")
    assert "step 0 params:" in log
    assert "dry_run_no_download" in log
    assert "Хочу перемен" not in log
    assert not (run_dir / "screenshots").exists()


def test_exact_match_after_first_five_is_rejected(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run_1"
    _set_run(tool, monkeypatch, run_dir)
    with _fixture_site(exact_position=6, missing_control_position=1) as start_url:
        monkeypatch.setattr(tool, "_START_URL", start_url)
        result = tool.download_hitmo_tracks(timeout_seconds=15)
    assert result["matched"] == 0
    assert result["results"][0]["status"] == "exact_match_not_found"


def test_existing_results_are_not_reused_when_search_does_not_navigate(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run_1"
    _set_run(tool, monkeypatch, run_dir)
    with _fixture_site(exact_position=2, prevent_search_navigation=True) as start_url:
        monkeypatch.setattr(tool, "_START_URL", start_url)
        # Leave enough headroom for a cold local browser navigation; the assertion
        # targets stale-result rejection, not a two-second startup performance gate.
        result = tool.download_hitmo_tracks(timeout_seconds=5)
    assert result["matched"] == 0
    assert result["results"][0]["status"] == "results_timeout"


def test_noncanonical_result_rows_fail_closed(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run_1"
    _set_run(tool, monkeypatch, run_dir)
    with _fixture_site(exact_position=2, canonical_rows=False) as start_url:
        monkeypatch.setattr(tool, "_START_URL", start_url)
        result = tool.download_hitmo_tracks(timeout_seconds=15)
    assert result["matched"] == 0
    assert result["results"][0]["status"] == "result_structure_unsupported"


def test_overlaid_search_submit_fails_before_pointer_click(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run_1"
    _set_run(tool, monkeypatch, run_dir)
    with _fixture_site(exact_position=2, overlay_submit=True) as start_url:
        monkeypatch.setattr(tool, "_START_URL", start_url)
        with pytest.raises(RuntimeError, match="search_submit_not_actionable"):
            tool.download_hitmo_tracks(timeout_seconds=15)
    assert not (tmp_path / "downloads").exists()


def test_overlaid_download_control_fails_before_pointer_click(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run_1"
    _set_run(tool, monkeypatch, run_dir)
    destination = tmp_path / "music"
    with _fixture_site(exact_position=2, overlay_download=True) as start_url:
        monkeypatch.setattr(tool, "_START_URL", start_url)
        result = tool.download_hitmo_tracks(
            download=True,
            rights_confirmed=True,
            download_dir=destination,
            timeout_seconds=15,
        )
    assert result["downloaded"] == 0
    assert result["results"][0]["status"] == "download_control_not_actionable"
    assert not destination.exists() or not any(destination.iterdir())


def test_earliest_exact_match_wins_when_provider_returns_duplicates(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run_1"
    _set_run(tool, monkeypatch, run_dir)
    with _fixture_site(exact_position=2, duplicate_exact=True) as start_url:
        monkeypatch.setattr(tool, "_START_URL", start_url)
        result = tool.download_hitmo_tracks(timeout_seconds=15)
    assert result["matched"] == 1
    assert result["results"][0]["position"] == 2


def test_authorized_download_waits_and_publishes_audio(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run_1"
    _set_run(tool, monkeypatch, run_dir)
    destination = tmp_path / "music"
    with _fixture_site(exact_position=2) as start_url:
        monkeypatch.setattr(tool, "_START_URL", start_url)
        result = tool.download_hitmo_tracks(
            download=True,
            rights_confirmed=True,
            download_dir=destination,
            timeout_seconds=15,
        )
    assert result["downloaded"] == 1
    downloaded = destination / "test.mp3"
    assert downloaded.read_bytes().startswith(b"ID3")
    assert result["results"][0]["file_ref"] == (
        "sha256:" + hashlib.sha256(downloaded.read_bytes()).hexdigest()[:12]
    )
    assert not list(destination.glob("*.part"))


def test_tsv_queue_preserves_input_order(tool: ModuleType, tmp_path: Path) -> None:
    queue = tmp_path / "tracks.tsv"
    queue.write_text("Artist One\tTrack One\nArtist Two\tTrack Two\n", encoding="utf-8")
    assert tool._load_requests("ignored", "ignored", queue) == [
        ("Artist One", "Track One"),
        ("Artist Two", "Track Two"),
    ]


def test_invalid_tsv_encoding_has_stable_error(tool: ModuleType, tmp_path: Path) -> None:
    queue = tmp_path / "tracks.tsv"
    queue.write_bytes(b"\xff\xfe\x00")
    with pytest.raises(RuntimeError, match="tracks_file_unavailable"):
        tool._load_requests("ignored", "ignored", queue)


def test_exclusive_publish_never_overwrites_existing_file(tool: ModuleType, tmp_path: Path) -> None:
    source = tmp_path / "source.part"
    source.write_bytes(b"ID3new")
    existing = tmp_path / "test.mp3"
    existing.write_bytes(b"user-data")
    published = tool._publish_exclusive(source, tmp_path, "test.mp3")
    assert existing.read_bytes() == b"user-data"
    assert published.name == "test (1).mp3"
    assert published.read_bytes() == b"ID3new"


def test_audio_validation_reads_only_the_header(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    audio = tmp_path / "large.mp3"
    audio.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + (b"x" * 4096))

    def reject_read_bytes(_path: Path) -> bytes:
        raise AssertionError("read_bytes must not load the complete download")

    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)
    assert tool._looks_like_audio(audio)


@pytest.mark.parametrize(
    "header",
    [
        b"RIFF\x00\x00\x00\x00AVI ",
        b"RIFF\x00\x00\x00\x00WEBP",
        b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00",
        b"OggS" + (b"\x00" * 60),
    ],
)
def test_non_audio_container_headers_are_rejected(
    tool: ModuleType,
    tmp_path: Path,
    header: bytes,
) -> None:
    candidate = tmp_path / "candidate.bin"
    candidate.write_bytes(header)
    assert not tool._looks_like_audio(candidate)


def test_tsv_downloads_are_completed_sequentially(
    tool: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run_1"
    _set_run(tool, monkeypatch, run_dir)
    queue = tmp_path / "tracks.tsv"
    queue.write_text("Artist One\tTrack One\nArtist Two\tTrack Two\n", encoding="utf-8")
    destination = tmp_path / "music"
    with _queue_fixture_site() as (start_url, handler):
        monkeypatch.setattr(tool, "_START_URL", start_url)
        result = tool.download_hitmo_tracks(
            tracks_file=queue,
            download=True,
            rights_confirmed=True,
            download_dir=destination,
            timeout_seconds=15,
        )
    assert result["downloaded"] == 2
    assert handler.download_order == ["one.mp3", "two.mp3"]
    assert (destination / "one.mp3").is_file()
    assert (destination / "two.mp3").is_file()
