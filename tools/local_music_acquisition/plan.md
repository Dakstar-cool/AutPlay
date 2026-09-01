# Task

> Current scope: the completed Hitmo tool is now one provider inside the portable
> `local_music_acquisition` module. The module composes Jamendo -> Hitmo -> yt-dlp and remains
> local-only; the detailed checklist below records the original Hitmo delivery evidence.

Create a reusable Playwright CLI for `https://ru.hitmoz.org/` that accepts a
track title and artist (or a UTF-8 TSV queue), inspects only the first five
track results for an exact normalized title-and-artist match, downloads only
an exact match to a caller-selected directory, waits for the completed file,
and processes queued tracks sequentially.

The tool must not bypass DRM or site protection. A real download requires an
explicit assertion that the caller is authorized to download every requested
track. The default verification run is a no-download dry run using the example
visible in the supplied screenshot.

# Parameters

| name | type | source phrase from task | default | allowed / format |
|---|---|---|---|---|
| `title` | `str` | "название трека" | `Хочу перемен` | Non-empty title; used when `tracks_file` is absent |
| `artist` | `str` | "автора" | `Кино` | Non-empty artist; used when `tracks_file` is absent |
| `tracks_file` | `Path \| None` | "потом качаем следующий" | `None` | UTF-8 TSV, one `artist<TAB>title` pair per non-empty line |
| `download_dir` | `Path` | "заранее указать место для загрузки" | `downloads` | Local directory created when a real download is authorized |
| `result_limit` | `int` | "первые 5 позиций" | `5` | Integer from 1 through 5 |
| `timeout_seconds` | `float` | "ждём полной загрузки файла" | `120` | Positive timeout, at most 600 seconds per navigation/download |
| `download` | `bool` | "нажимаем на загрузку" | `False` | Downloads the exact authorized match and waits for validated completion |
| `rights_confirmed` | `bool` | repository authorized-import invariant | `False` | Must be true together with `download`; caller assertion only |
| `headless` | `bool` | browser automation mode | `True` | `--headed` shows a launched browser; ignored for an existing CDP browser |
| `browser` | `str` | repeated-error diagnostic browser choice | `firefox` | `firefox`, a clean installed `edge`, or an existing `cdp` session |
| `cdp_endpoint` | `str` | user-approved CDP attempt | `http://127.0.0.1:9222` | Loopback HTTP endpoint only; no credentials, query, or remote host |
| `evidence_screenshots` | `bool` | local verification evidence | `False` | Opt-in only; screenshots contain the visible query and are Git-ignored |

# Critical Points

- [x] CP1: Submit the title plus artist through the visible Hitmo search control in a controlled browser session. **Resolution:** the originally attempted fresh Firefox route remains unavailable (`site_http_403`); the user-approved dedicated Edge CDP path selects a clean provider tab or creates an owned fallback tab.
- [x] CP2: Inspect no more than the first `result_limit` canonical rows inside the unique `Треки`/`Tracks` section and require exact normalized equality for both title and the full displayed artist field; absent or noncanonical structures fail closed, while duplicate exact rows select the earliest ranked result. **Evidence:** local fixture coverage includes a composite-artist near-match, a missing control inside the five-row window, a sixth-row exact match, duplicate exact rows, and a noncanonical layout; live runs 27 and 29 scanned one canonical row and selected exact position 1 for `О боли` by `Рубеж веков`.
- [x] CP3: Record the matched row and its position in the privacy-redacted action log; capture a screenshot only with explicit diagnostic opt-in. **Evidence:** local opt-in run 3 screenshot and log; screenshots are disabled by default and Git-ignored.
- [x] CP4: Default dry-run performs no download and reports the exact match outcome. **Evidence:** live CDP run 27 contains `dry_run_no_download`, exact position 1, and no download.
- [x] CP5: A real live-site download requires explicit rights confirmation, clicks only the actionable exact row control, waits for the browser download, validates bounded non-empty audio, and publishes without overwriting. **Evidence:** live CDP run 29 completed `О боли` by `Рубеж веков` with `downloaded=1`; its 11,733,684-byte output matched the delivered file by SHA-256. Unit coverage includes rights gating, authorized completion, audio validation, exclusive publication, and sequential queue behavior.
- [x] CP6: TSV input is processed strictly one row at a time, with the next search starting only after the prior item completed or failed; the final summary preserves input order and requires a fresh result state. **Evidence:** `test_tsv_downloads_are_completed_sequentially` asserts completed order `one.mp3`, then `two.mp3`; live CDP run 19 submitted two identical rows independently and returned `matched=2`, `downloaded=0`.
- [x] CP7: Every navigation, fresh-result check, result scan, match decision, download decision, and final summary is bounded and recorded without logging private absolute paths; an overlaid search or download control fails before any pointer click. **Evidence:** privacy-redacted logs plus bounded CLI/unit tests, including stale-result and overlay regressions.
- [x] CP8: The module is side-effect-free on import, exposes exactly one public reusable function, and its CLI documents every parameter. **Evidence:** import smoke test prints only `download_hitmo_tracks`; `python final_script.py --help` succeeds.
- [x] CP9: Attach to a dedicated visible Edge profile over a loopback CDP endpoint, reuse only a clean loaded Hitmo tab or create an owned fallback tab, leave the browser/profile open, and support browser-managed downloads. **Evidence:** live dry-run 27 and authorized download run 29 selected exact position 1; the owned-page policy is covered by unit tests and the Edge process remained open.
- [x] CP10: Compose the existing TXT parser and Jamendo downloader with Hitmo as a second local-only contour. Process rows strictly as Jamendo -> Hitmo-on-genuine-miss -> next row; require playlist-wide Hitmo rights confirmation before provider I/O; never convert Jamendo provider, transport, permission, validation, or publication errors into a fallback. Keep the product A1B server boundary Jamendo-only under ADR-034. **Evidence:** playlist orchestration tests cover primary success, both accepted miss codes, denied fallback classes, row order, rights gating, numbered normalization, malformed-row isolation, exact Hitmo CDP arguments, and final not-found mapping.
- [x] CP11: Package the TXT parser and all providers as a repository-independent src-layout module, retain compatibility launchers, and add yt-dlp as the third contour. Only exact misses permit fallback; yt-dlp accepts no caller URL, credentials, cookies, plugins, or remote JS components, runs in a bounded subprocess, waits for ffmpeg, validates audio, and publishes exclusively. **Evidence:** isolated lock/sync, package tests, root compatibility tests, CLI help, and portable-copy smoke test.
