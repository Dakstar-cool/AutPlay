"""Fail-closed Hitmo matching CLI for sequential authorized downloads."""

from __future__ import annotations

import argparse as _argparse
import asyncio as _asyncio
import contextlib as _contextlib
import errno as _errno
import hashlib as _hashlib
import json as _json
import os as _os
import re as _re
import shutil as _shutil
import unicodedata as _unicodedata
import uuid as _uuid
from pathlib import Path as _Path
from typing import Any as _Any
from urllib.parse import urlsplit as _urlsplit

from playwright.async_api import Error as _PlaywrightError
from playwright.async_api import TimeoutError as _PlaywrightTimeoutError
from playwright.async_api import async_playwright as _async_playwright

globals().pop("annotations", None)

_START_URL = "https://ru.hitmoz.org/"
_MAX_TRACKS = 500
_MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
_INVALID_FILENAME = _re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_FILENAMES = {
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


class _HitmoError(RuntimeError):
    """Stable, credential-free failure raised by the standalone tool."""


class _RunState:
    def __init__(self, run_dir: _Path, *, capture_screenshots: bool) -> None:
        self.run_dir = run_dir
        self.screenshots = run_dir / "screenshots"
        self.capture_screenshots = capture_screenshots
        if capture_screenshots:
            self.screenshots.mkdir(parents=True, exist_ok=True)
        self.log_path = run_dir / "final_script_log.txt"
        self.log_path.write_text("", encoding="utf-8")
        self.step = 0

    def params(self, values: dict[str, object]) -> None:
        rendered = " ".join(f"{name}={value}" for name, value in values.items())
        self._write(f"step 0 params: {rendered}")

    def action(self, message: str) -> int:
        self.step += 1
        self._write(f"step {self.step} action: {message}")
        return self.step

    def final(self, summary: dict[str, object]) -> None:
        self._write(f"FINAL_RESPONSE: {_json.dumps(summary, ensure_ascii=True, sort_keys=True)}")

    def _write(self, line: str) -> None:
        with self.log_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{line}\n")
        print(line)


def download_hitmo_tracks(
    title: str = "Хочу перемен",
    artist: str = "Кино",
    tracks_file: _Path | None = None,
    download_dir: _Path = _Path("downloads"),
    result_limit: int = 5,
    timeout_seconds: float = 120.0,
    download: bool = False,
    rights_confirmed: bool = False,
    headless: bool = True,
    browser: str = "firefox",
    cdp_endpoint: str = "http://127.0.0.1:9222",
    evidence_screenshots: bool = False,
) -> dict[str, object]:
    """Find exact Hitmo matches and gate sequential authorized downloads.

    Args:
        title: Track title used when ``tracks_file`` is absent; non-empty text.
            Default: ``"Хочу перемен"``.
        artist: Track artist used when ``tracks_file`` is absent; non-empty text.
            Default: ``"Кино"``.
        tracks_file: Optional UTF-8 TSV file containing ``artist<TAB>title`` rows.
            Default: ``None``.
        download_dir: Local destination directory for completed downloads.
            Default: ``Path("downloads")``.
        result_limit: Number of leading Hitmo track rows to inspect, from 1 to 5.
            Default: ``5``.
        timeout_seconds: Positive navigation/download timeout, at most 600 seconds.
            Default: ``120.0``.
        download: Download an exact authorized match and wait until the validated
            audio file is published. Default: ``False``.
        rights_confirmed: Caller assertion that every requested download is authorized.
            Default: ``False``; required when ``download`` is true.
        headless: Whether to run without a visible browser window.
            Default: ``True``.
        browser: Browser mode: ``"firefox"``, installed ``"edge"``, or ``"cdp"``.
            Default: ``"firefox"``.
        cdp_endpoint: Loopback HTTP endpoint for an already running Chromium browser;
            used only with ``browser="cdp"``. Default: ``"http://127.0.0.1:9222"``.
        evidence_screenshots: Store query-bearing screenshots for local diagnostics.
            Default: ``False``; generated artifacts remain Git-ignored.

    Returns:
        A dictionary containing the run-relative evidence directory, ordered per-track
        statuses, and aggregate counts. It never includes raw absolute paths.
    """

    requests = _load_requests(title, artist, tracks_file)
    _validate_arguments(
        requests=requests,
        result_limit=result_limit,
        timeout_seconds=timeout_seconds,
        download=download,
        rights_confirmed=rights_confirmed,
        browser=browser,
        cdp_endpoint=cdp_endpoint,
    )
    return _asyncio.run(
        _run(
            requests=requests,
            download_dir=_Path(download_dir),
            result_limit=result_limit,
            timeout_seconds=timeout_seconds,
            download=download,
            rights_confirmed=rights_confirmed,
            headless=headless,
            browser=browser,
            cdp_endpoint=cdp_endpoint,
            evidence_screenshots=evidence_screenshots,
        )
    )


def _load_requests(
    title: str,
    artist: str,
    tracks_file: _Path | None,
) -> list[tuple[str, str]]:
    if tracks_file is None:
        requests = [(artist.strip(), title.strip())]
    else:
        try:
            lines = _Path(tracks_file).read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError) as error:
            raise _HitmoError("tracks_file_unavailable") from error
        requests = []
        for line in lines:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            fields = [field.strip() for field in line.split("\t")]
            if len(fields) != 2 or not all(fields):
                raise _HitmoError("tracks_file_invalid")
            requests.append((fields[0], fields[1]))
    if not requests or len(requests) > _MAX_TRACKS:
        raise _HitmoError("track_count_invalid")
    if any(not artist_value or not title_value for artist_value, title_value in requests):
        raise _HitmoError("track_query_invalid")
    if any(len(value) > 300 for pair in requests for value in pair):
        raise _HitmoError("track_query_invalid")
    return requests


def _validate_arguments(
    *,
    requests: list[tuple[str, str]],
    result_limit: int,
    timeout_seconds: float,
    download: bool,
    rights_confirmed: bool,
    browser: str,
    cdp_endpoint: str,
) -> None:
    if not requests:
        raise _HitmoError("track_count_invalid")
    if not 1 <= result_limit <= 5:
        raise _HitmoError("result_limit_invalid")
    if not 0 < timeout_seconds <= 600:
        raise _HitmoError("timeout_invalid")
    if download and not rights_confirmed:
        raise _HitmoError("rights_confirmation_required")
    if browser not in {"firefox", "edge", "cdp"}:
        raise _HitmoError("browser_invalid")
    _validate_cdp_endpoint(cdp_endpoint)


def _validate_cdp_endpoint(value: str) -> None:
    try:
        parsed = _urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise _HitmoError("cdp_endpoint_invalid") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or port is None
        or not 1 <= port <= 65_535
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise _HitmoError("cdp_endpoint_invalid")


async def _run(
    *,
    requests: list[tuple[str, str]],
    download_dir: _Path,
    result_limit: int,
    timeout_seconds: float,
    download: bool,
    rights_confirmed: bool,
    headless: bool,
    browser: str,
    cdp_endpoint: str,
    evidence_screenshots: bool,
) -> dict[str, object]:
    run_dir = _prepare_run_dir()
    state = _RunState(run_dir, capture_screenshots=evidence_screenshots)
    state.params(
        {
            "title": _private_value(requests[0][1]),
            "artist": _private_value(requests[0][0]),
            "tracks_file": "set" if len(requests) > 1 else "none",
            "download_dir": _private_path(download_dir),
            "result_limit": result_limit,
            "timeout_seconds": timeout_seconds,
            "download": download,
            "rights_confirmed": rights_confirmed,
            "headless": headless,
            "browser": browser,
            "cdp_endpoint": _private_value(cdp_endpoint),
            "evidence_screenshots": evidence_screenshots,
        }
    )
    timeout_ms = timeout_seconds * 1_000
    if download:
        try:
            download_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            state.final({"error": "download_directory_unavailable"})
            raise _HitmoError("download_directory_unavailable") from error

    try:
        results = await _drive_browser(
            requests=requests,
            state=state,
            download_dir=download_dir,
            result_limit=result_limit,
            timeout_ms=timeout_ms,
            should_download=download,
            headless=headless,
            browser=browser,
            cdp_endpoint=cdp_endpoint,
        )
    except _PlaywrightError as error:
        state.final({"error": "browser_operation_failed"})
        raise _HitmoError("browser_operation_failed") from error

    summary: dict[str, object] = {
        "run": run_dir.name,
        "requested": len(results),
        "matched": sum(result["status"] in {"matched", "downloaded"} for result in results),
        "downloaded": sum(result["status"] == "downloaded" for result in results),
        "failed": sum(result["status"] not in {"matched", "downloaded"} for result in results),
        "results": results,
    }
    state.final(
        {
            "run": summary["run"],
            "requested": summary["requested"],
            "matched": summary["matched"],
            "downloaded": summary["downloaded"],
            "failed": summary["failed"],
        }
    )
    return summary


async def _drive_browser(
    *,
    requests: list[tuple[str, str]],
    state: _RunState,
    download_dir: _Path,
    result_limit: int,
    timeout_ms: float,
    should_download: bool,
    headless: bool,
    browser: str,
    cdp_endpoint: str,
) -> list[dict[str, object]]:
    async with _async_playwright() as playwright:
        if browser == "cdp":
            try:
                browser_instance = await playwright.chromium.connect_over_cdp(
                    cdp_endpoint,
                    is_local=True,
                    no_defaults=False,
                    timeout=timeout_ms,
                )
            except _PlaywrightError as error:
                state.final({"error": "cdp_connection_failed"})
                raise _HitmoError("cdp_connection_failed") from error
            if not browser_instance.contexts:
                state.final({"error": "cdp_context_missing"})
                raise _HitmoError("cdp_context_missing")
            context = browser_instance.contexts[0]
            page, reused_hitmo_page, owned_page = await _select_cdp_page(context, timeout_ms)
            await page.set_viewport_size({"width": 1280, "height": 1800})
            try:
                return await _process_queue(
                    page=page,
                    requests=requests,
                    state=state,
                    download_dir=download_dir,
                    result_limit=result_limit,
                    timeout_ms=timeout_ms,
                    should_download=should_download,
                    reuse_current_page=reused_hitmo_page,
                )
            finally:
                if owned_page:
                    await page.close()

        if browser == "firefox":
            browser_instance = await playwright.firefox.launch(headless=headless)
        else:
            browser_instance = await playwright.chromium.launch(channel="msedge", headless=headless)
        context = await browser_instance.new_context(
            accept_downloads=True,
            viewport={"width": 1280, "height": 1800},
        )
        try:
            page = await context.new_page()
            return await _process_queue(
                page=page,
                requests=requests,
                state=state,
                download_dir=download_dir,
                result_limit=result_limit,
                timeout_ms=timeout_ms,
                should_download=should_download,
                reuse_current_page=False,
            )
        finally:
            await context.close()
            await browser_instance.close()


async def _select_cdp_page(context: _Any, timeout_ms: float) -> tuple[_Any, bool, bool]:
    provider_host = _urlsplit(_START_URL).hostname
    for page in reversed(context.pages):
        if _urlsplit(page.url).hostname != provider_host:
            continue
        with _contextlib.suppress(_PlaywrightTimeoutError):
            await page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 10_000))
        try:
            clean = await page.evaluate(
                """() => {
                    const input = [...document.querySelectorAll('input')].find((element) =>
                        /^(поиск|search)$/i.test(
                            (element.getAttribute('placeholder') || '').trim()
                        )
                    ) || document.querySelector(
                        'input[type="search"], form input[type="text"]'
                    );
                    return Boolean(input) && input.value === '' &&
                        document.querySelectorAll('[data-autplay-stale-result]').length === 0;
                }"""
            )
        except _PlaywrightError:
            continue
        if clean:
            return page, True, False
    return await context.new_page(), False, True


async def _process_queue(
    *,
    page: _Any,
    requests: list[tuple[str, str]],
    state: _RunState,
    download_dir: _Path,
    result_limit: int,
    timeout_ms: float,
    should_download: bool,
    reuse_current_page: bool,
) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for queue_position, (request_artist, request_title) in enumerate(requests, 1):
        query_ref = _query_ref(request_artist, request_title)
        result = await _process_one(
            page=page,
            state=state,
            query_ref=query_ref,
            queue_position=queue_position,
            artist=request_artist,
            title=request_title,
            download_dir=download_dir,
            result_limit=result_limit,
            timeout_ms=timeout_ms,
            should_download=should_download,
            reuse_current_page=reuse_current_page,
        )
        results.append(result)
        reuse_current_page = True
    return results


async def _process_one(
    *,
    page: _Any,
    state: _RunState,
    query_ref: str,
    queue_position: int,
    artist: str,
    title: str,
    download_dir: _Path,
    result_limit: int,
    timeout_ms: float,
    should_download: bool,
    reuse_current_page: bool,
) -> dict[str, object]:
    if reuse_current_page:
        if _urlsplit(page.url).hostname != _urlsplit(_START_URL).hostname:
            state.final({"error": "cdp_provider_page_missing", "query_ref": query_ref})
            raise _HitmoError("cdp_provider_page_missing")
        state.action(f"queue={queue_position} query_ref={query_ref} reuse provider page")
    else:
        state.action(f"queue={queue_position} query_ref={query_ref} open provider start page")
        try:
            response = await page.goto(
                _START_URL,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
        except _PlaywrightError as error:
            state.final({"error": "site_navigation_failed", "query_ref": query_ref})
            raise _HitmoError("site_navigation_failed") from error
        if response is None:
            state.final({"error": "site_response_missing", "query_ref": query_ref})
            raise _HitmoError("site_response_missing")
        if response.status >= 400:
            await _screenshot(page, state, f"provider_http_{response.status}")
            state.final({"error": f"site_http_{response.status}", "query_ref": query_ref})
            raise _HitmoError(f"site_http_{response.status}")

    search = page.get_by_placeholder(_re.compile(r"^(поиск|search)$", _re.IGNORECASE))
    if await search.count() == 0:
        search = page.locator('input[type="search"], form input[type="text"]').first
    else:
        search = search.first
    if await search.count() == 0:
        await _screenshot(page, state, "search_control_missing")
        state.final({"error": "search_control_missing", "query_ref": query_ref})
        raise _HitmoError("search_control_missing")

    # Clear first so repeated identical queue entries still emit a fresh input
    # transition for the provider's controlled search field.
    await search.fill("")
    await search.fill(f"{title} {artist}")
    search_form = search.locator("xpath=ancestor::form[1]")
    submit = search_form.locator('button[type="submit"], input[type="submit"], button').first
    if await submit.count() == 0:
        await _screenshot(page, state, "search_submit_missing")
        state.final({"error": "search_submit_missing", "query_ref": query_ref})
        raise _HitmoError("search_submit_missing")
    stale_marker = _uuid.uuid4().hex
    await page.evaluate(
        """marker => {
            for (const row of document.querySelectorAll('.tracks__item')) {
                row.removeAttribute('data-autplay-stale-result');
                row.setAttribute('data-autplay-stale-result', marker);
            }
        }""",
        stale_marker,
    )
    click_point = await _actionable_click_point(submit)
    if click_point is None:
        state.final({"error": "search_submit_not_actionable", "query_ref": query_ref})
        raise _HitmoError("search_submit_not_actionable")

    try:
        # Use low-level pointer events so Hitmo receives its normal button-click
        # sequence without Playwright coupling the action to navigation waiting.
        await page.mouse.click(click_point["x"], click_point["y"])
        if not await _wait_for_fresh_results(page, search, stale_marker, timeout_ms):
            raise _PlaywrightTimeoutError("results_state_timeout")
    except _PlaywrightTimeoutError:
        with _contextlib.suppress(_PlaywrightError):
            await page.evaluate(
                """marker => {
                    for (const row of document.querySelectorAll(
                        `[data-autplay-stale-result="${marker}"]`
                    )) row.removeAttribute('data-autplay-stale-result');
                }""",
                stale_marker,
            )
        state.action(f"queue={queue_position} query_ref={query_ref} results_timeout")
        await _screenshot(page, state, "results_timeout")
        return {"query_ref": query_ref, "status": "results_timeout"}
    except _PlaywrightError as error:
        state.final({"error": "search_submit_failed", "query_ref": query_ref})
        raise _HitmoError("search_submit_failed") from error
    tracks_heading = page.get_by_role(
        "heading", name=_re.compile(r"^(треки|tracks)$", _re.IGNORECASE)
    ).first
    try:
        await tracks_heading.wait_for(state="visible", timeout=timeout_ms)
    except _PlaywrightTimeoutError:
        state.action(f"queue={queue_position} query_ref={query_ref} results_timeout")
        await _screenshot(page, state, "results_timeout")
        return {"query_ref": query_ref, "status": "results_timeout"}
    await page.wait_for_timeout(250)
    state.action(f"queue={queue_position} query_ref={query_ref} submitted visible search")
    await _screenshot(page, state, "search_results")

    candidates = await _extract_candidates(page, result_limit)
    state.action(
        f"queue={queue_position} query_ref={query_ref} scanned={len(candidates)} "
        f"limit={result_limit}"
    )
    if not candidates:
        return {"query_ref": query_ref, "status": "result_structure_unsupported"}

    normalized_title = _normalize(title)
    normalized_artist = _normalize(artist)
    matches = []
    for candidate in candidates:
        candidate_title = _normalize(str(candidate["title"]))
        candidate_artist = _normalize(str(candidate["artist"]))
        if normalized_title == candidate_title and normalized_artist == candidate_artist:
            matches.append(candidate)
    if not matches:
        return {"query_ref": query_ref, "status": "exact_match_not_found"}

    # The provider can return duplicate rows with the same exact title and
    # artist. Its ranked order is deterministic, so select the earliest exact
    # match within the user-requested result window.
    match = matches[0]
    position = int(match["position"])
    state.action(f"queue={queue_position} query_ref={query_ref} exact_match_position={position}")
    await _screenshot(page, state, "exact_match")
    if not should_download:
        state.action(f"queue={queue_position} query_ref={query_ref} dry_run_no_download")
        return {"query_ref": query_ref, "status": "matched", "position": position}
    if not bool(match["download_available"]):
        return {"query_ref": query_ref, "status": "download_control_missing"}

    control = page.locator(f'[data-autplay-hitmo-download="{position}"]').first
    download_click_point = await _actionable_click_point(control)
    if download_click_point is None:
        return {"query_ref": query_ref, "status": "download_control_not_actionable"}
    try:
        async with page.expect_download(timeout=timeout_ms) as download_info:
            await page.mouse.click(download_click_point["x"], download_click_point["y"])
        transfer = await download_info.value
    except _PlaywrightTimeoutError as error:
        state.final({"error": "download_event_timeout", "query_ref": query_ref})
        raise _HitmoError("download_event_timeout") from error
    failure = await transfer.failure()
    if failure is not None:
        state.final({"error": "download_failed", "query_ref": query_ref})
        raise _HitmoError("download_failed")
    try:
        saved_path = await _save_download(transfer, download_dir)
    except _HitmoError as error:
        state.final({"error": str(error), "query_ref": query_ref})
        raise
    try:
        file_ref = _content_ref(saved_path)
    except OSError as error:
        state.final({"error": "downloaded_content_hash_failed", "query_ref": query_ref})
        raise _HitmoError("downloaded_content_hash_failed") from error
    state.action(
        f"queue={queue_position} query_ref={query_ref} completed_download "
        f"file_ref={file_ref}"
    )
    return {
        "query_ref": query_ref,
        "status": "downloaded",
        "position": position,
        "file_ref": file_ref,
    }


async def _actionable_click_point(locator: _Any) -> dict[str, float] | None:
    value = await locator.evaluate(
        """element => {
            const style = window.getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            const disabled = Boolean(element.disabled) ||
                element.getAttribute('aria-disabled') === 'true';
            if (disabled || style.display === 'none' || style.visibility === 'hidden' ||
                rect.width <= 0 || rect.height <= 0) return null;
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;
            const top = document.elementFromPoint(x, y);
            if (!top || (top !== element && !element.contains(top))) return null;
            return { x, y };
        }"""
    )
    return value


async def _wait_for_fresh_results(
    page: _Any,
    search: _Any,
    stale_marker: str,
    timeout_ms: float,
) -> bool:
    deadline = _asyncio.get_running_loop().time() + timeout_ms / 1000
    while True:
        try:
            stale = page.locator(f'[data-autplay-stale-result="{stale_marker}"]')
            input_cleared = await search.count() and await search.input_value() == ""
            if input_cleared and await stale.count() == 0:
                return True
        except _PlaywrightError:
            # A full navigation destroys the old execution context briefly;
            # locators re-resolve against the replacement document next poll.
            pass
        remaining = deadline - _asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        await _asyncio.sleep(min(0.1, remaining))


async def _extract_candidates(page: _Any, result_limit: int) -> list[dict[str, object]]:
    script = """
    ({ limit }) => {
      const visible = (element) => {
        const style = window.getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' &&
               rect.width > 0 && rect.height > 0;
      };
      const signature = (element) => [
        element.tagName,
        element.getAttribute('href') || '',
        element.getAttribute('download') || '',
        element.getAttribute('aria-label') || '',
        element.getAttribute('title') || '',
        element.getAttribute('class') || '',
        element.innerText || ''
      ].join(' ').toLowerCase();
      const trackHeadings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')]
        .filter((element) =>
          visible(element) && /^(треки|tracks)$/i.test((element.innerText || '').trim())
        );
      if (trackHeadings.length !== 1) return [];
      const heading = trackHeadings[0];
      const headingLevel = Number(heading.tagName.slice(1));
      const laterHeadings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')]
        .filter((element) => {
          const level = Number(element.tagName.slice(1));
          return level <= headingLevel &&
                 Boolean(
                   heading.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING
                 );
        });
      const boundary = laterHeadings[0] || null;
      const insideTrackSection = (element) => {
        const followsHeading = Boolean(
          heading.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING
        );
        const precedesBoundary = !boundary || Boolean(
          element.compareDocumentPosition(boundary) & Node.DOCUMENT_POSITION_FOLLOWING
        );
        return followsHeading && precedesBoundary;
      };
      const providerRows = [...document.querySelectorAll('.tracks__item')]
        .filter((element) => insideTrackSection(element) && visible(element));
      const candidateRows = providerRows.slice(0, limit);
      if (candidateRows.length === 0) return [];
      const rows = [];
      for (let index = 0; index < candidateRows.length; index += 1) {
        const row = candidateRows[index];
        const title = (row.querySelector('.track__title')?.innerText || '').trim();
        const artist = (row.querySelector('.track__desc')?.innerText || '').trim();
        if (!title || !artist) continue;
        const control = [...row.querySelectorAll('a,button,[role="button"]')]
          .find((element) =>
            visible(element) && /(скач|загруз|download|\\bdl\\b)/i.test(signature(element))
          ) || null;
        const position = index + 1;
        if (control) control.setAttribute('data-autplay-hitmo-download', String(position));
        rows.push({ position, title, artist, download_available: Boolean(control) });
      }
      return rows;
    }
    """
    value = await page.evaluate(script, {"limit": result_limit})
    return list(value)


async def _save_download(transfer: _Any, download_dir: _Path) -> _Path:
    suggested = _safe_filename(transfer.suggested_filename)
    temporary = download_dir / f".{_uuid.uuid4().hex}.part"
    try:
        await transfer.save_as(temporary)
        size = temporary.stat().st_size
        if size <= 0 or size > _MAX_DOWNLOAD_BYTES or not _looks_like_audio(temporary):
            raise _HitmoError("downloaded_content_invalid")
        destination = _publish_exclusive(temporary, download_dir, suggested)
    except (OSError, _PlaywrightError) as error:
        raise _HitmoError("download_publish_failed") from error
    finally:
        with _contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
    return destination


def _safe_filename(value: str) -> str:
    name = _INVALID_FILENAME.sub("_", _Path(value).name).strip(" .")
    if not name:
        name = "download.mp3"
    if _Path(name).stem.casefold() in _RESERVED_FILENAMES:
        name = f"_{name}"
    return name[:240]


def _publish_exclusive(source: _Path, directory: _Path, name: str) -> _Path:
    fallback_errnos = {
        _errno.EACCES,
        _errno.EPERM,
        _errno.EXDEV,
        getattr(_errno, "ENOTSUP", -1),
        getattr(_errno, "EOPNOTSUPP", -1),
    }
    for index in range(1, 10_000):
        candidate = directory / _candidate_name(name, index)
        try:
            _os.link(source, candidate)
        except FileExistsError:
            continue
        except OSError as error:
            if error.errno not in fallback_errnos:
                raise
            try:
                _copy_exclusive(source, candidate)
            except FileExistsError:
                continue
        else:
            return candidate
        return candidate
    raise _HitmoError("download_name_exhausted")


def _candidate_name(name: str, index: int) -> str:
    if index == 1:
        return name
    return f"{_Path(name).stem} ({index - 1}){_Path(name).suffix}"


def _copy_exclusive(source: _Path, destination: _Path) -> None:
    created = False
    try:
        with destination.open("xb") as target:
            created = True
            with source.open("rb") as origin:
                _shutil.copyfileobj(origin, target, length=1024 * 1024)
            target.flush()
            _os.fsync(target.fileno())
    except BaseException:
        if created:
            with _contextlib.suppress(OSError):
                destination.unlink()
        raise


def _looks_like_audio(path: _Path) -> bool:
    try:
        with path.open("rb") as handle:
            header = handle.read(64)
    except OSError:
        return False
    if len(header) >= 10 and header[:3] == b"ID3" and header[3] in {2, 3, 4}:
        return True
    if header.startswith(b"fLaC"):
        return True
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE":
        return True
    if header.startswith(b"OggS") and any(
        codec in header for codec in (b"OpusHead", b"\x01vorbis", b"Speex   ")
    ):
        return True
    if len(header) >= 12 and header[4:12] in {b"ftypM4A ", b"ftypM4B "}:
        return True
    return _looks_like_mpeg_audio_frame(header)


def _looks_like_mpeg_audio_frame(header: bytes) -> bool:
    if len(header) < 4 or header[0] != 0xFF or header[1] & 0xE0 != 0xE0:
        return False
    version = (header[1] >> 3) & 0b11
    layer = (header[1] >> 1) & 0b11
    bitrate = (header[2] >> 4) & 0b1111
    sample_rate = (header[2] >> 2) & 0b11
    return version != 0b01 and layer != 0 and bitrate not in {0, 0b1111} and sample_rate != 0b11


async def _screenshot(page: _Any, state: _RunState, action: str) -> None:
    if not state.capture_screenshots:
        return
    step = state.step or 1
    path = state.screenshots / f"final_execution_{step}_{action}.png"
    await page.screenshot(path=str(path))


def _normalize(value: str) -> str:
    normalized = _unicodedata.normalize("NFKC", value).casefold().replace("ё", "\u0435")
    normalized = _re.sub(r"[^\w]+", " ", normalized, flags=_re.UNICODE)
    return " ".join(normalized.split())


def _query_ref(artist: str, title: str) -> str:
    payload = f"{_normalize(artist)}\0{_normalize(title)}".encode()
    return _hashlib.sha256(payload).hexdigest()[:12]


def _content_ref(path: _Path) -> str:
    """Return a bounded correlation fingerprint of the downloaded bytes."""

    digest = _hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()[:12]}"


def _private_value(value: str) -> str:
    digest = _hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"sha256:{digest}"


def _private_path(path: _Path) -> str:
    name = _Path(path).name or "directory"
    return f"private:{_private_value(name)}"


def _prepare_run_dir() -> _Path:
    source_dir = _Path(__file__).resolve().parent
    if source_dir.name.startswith("run_") and source_dir.parent.name == "final_runs":
        return source_dir
    module_root = _Path(__file__).resolve().parents[3]
    final_runs = module_root / "final_runs"
    final_runs.mkdir(parents=True, exist_ok=True)
    for index in range(1, 100_000):
        candidate = final_runs / f"run_{index}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        _shutil.copy2(__file__, candidate / "hitmo.py")
        return candidate
    raise _HitmoError("run_directory_exhausted")


def _build_parser() -> _argparse.ArgumentParser:
    parser = _argparse.ArgumentParser(description=download_hitmo_tracks.__doc__.splitlines()[0])
    parser.add_argument("--title", default="Хочу перемен", help="Track title for a single query.")
    parser.add_argument("--artist", default="Кино", help="Artist for a single query.")
    parser.add_argument(
        "--tracks-file",
        type=_Path,
        default=None,
        help="UTF-8 TSV queue with one artist<TAB>title row per line.",
    )
    parser.add_argument(
        "--download-dir",
        type=_Path,
        default=_Path("downloads"),
        help="Destination directory; default: ./downloads.",
    )
    parser.add_argument(
        "--result-limit",
        type=int,
        choices=range(1, 6),
        default=5,
        help="Inspect only this many leading track rows (1-5).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Per-navigation/download timeout in seconds (0-600].",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download an exact match and wait for validated completion.",
    )
    parser.add_argument(
        "--rights-confirmed",
        action="store_true",
        help="Assert authorization for every requested download.",
    )
    parser.add_argument(
        "--headed",
        dest="headless",
        action="store_false",
        default=True,
        help="Show the browser window for troubleshooting.",
    )
    parser.add_argument(
        "--browser",
        choices=("firefox", "edge", "cdp"),
        default="firefox",
        help="Browser mode; cdp attaches to an existing loopback Chromium endpoint.",
    )
    parser.add_argument(
        "--cdp-endpoint",
        default="http://127.0.0.1:9222",
        help="Loopback CDP endpoint used only with --browser cdp.",
    )
    parser.add_argument(
        "--evidence-screenshots",
        action="store_true",
        help="Store query-bearing screenshots locally for diagnostics.",
    )
    return parser


def _main() -> int:
    try:
        args = _build_parser().parse_args()
        result = download_hitmo_tracks(**vars(args))
    except _HitmoError as error:
        print(_json.dumps({"error": str(error)}, sort_keys=True))
        return 2
    public_summary = {
        "run": result["run"],
        "requested": result["requested"],
        "matched": result["matched"],
        "downloaded": result["downloaded"],
        "failed": result["failed"],
        "results": result["results"],
    }
    print(_json.dumps(public_summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
