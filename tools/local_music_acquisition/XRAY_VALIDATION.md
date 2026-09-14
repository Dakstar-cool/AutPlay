# Xray acquisition validation, 2026-09-14

Scope: the portable acquisition module, its provider workers and server launcher.
The server database, Android client and production services were not changed.
Existing unrelated working-tree changes were preserved. The initial implementation
validation did not commit or deploy; the later operator-authorized activation is
recorded in [the deployment report](DEPLOYMENT_2026-09-14.md).

## Network and real lifecycle evidence

Before implementation, requests from the server through the existing manual
`socks5h://127.0.0.1:10808` listener returned HTTP 200 from YouTube, SoundCloud
and Bandcamp in 0.77-1.05 seconds. The unauthenticated SoundCloud API root returned
HTTP 401 in 0.67 seconds; transport worked, without a claim of authenticated access.

The new manager was then exercised inside a disposable Docker bridge container
using the existing binary and existing SOCKS client config, both mounted read-only.
The container had an unprivileged UID, read-only root, no capabilities, no published
ports and no host networking. Three concurrent requests returned HTTP 200:

| Site | Seconds |
| --- | ---: |
| YouTube | 0.70 |
| SoundCloud | 0.86 |
| Bandcamp | 0.89 |

The same child served all concurrent leases. A new lease during idle reused it.
After idle it exited and the port closed; another lease started a new child;
explicit shutdown reaped that child and closed the port. This live test used a
0.5-second idle timeout; the unit test verifies the production default of 60 seconds.
An earlier live run hit a TLS handshake timeout; the subsequent bounded check above
succeeded. Port readiness is not a guarantee that the upstream service will respond.
No music library or queue was modified by these network probes.

The manual SOCKS client and a separate systemd Xray instance were identified as
distinct processes. Both were left running. Activation must select the client
config and retire only the prior manual client/startup mechanism.

## Automated checks

- Baseline on Windows: 180 passed, 1 POSIX-only test skipped.
- Final Windows suite: 231 passed, 4 POSIX-only tests skipped.
- Final Linux suite: 235 passed, no skips, including real SIGINT/SIGTERM cleanup
  and Docker launcher argument validation. The existing checks image supplied locked
  dependencies; the updated source and tests were mounted read-only.
- `uv run --frozen python -m ruff check src` and checks of new test files passed.
- `uv run --frozen python -m mypy --config-file pyproject.toml src` passed.
- `git diff --check -- tools/local_music_acquisition` passed.
- `bash -n docker/run-queue.sh` passed on the server.
- Independent read-only review identified a possible direct network fallback from
  native HLS to FFmpeg. Both private workers now block external network downloaders
  for proxied requests; the YouTube format selector admits only native protocols.
  Five regression cases prove the block, normal direct behavior, restoration on
  error, and preservation of local FFmpeg postprocessing. Follow-up review found
  no remaining actionable issues.

## Test environment corrections

After two synchronization failures, four options were considered using the
[Python threading reference](https://docs.python.org/3/library/threading.html),
[executor documentation](https://docs.python.org/3/library/concurrent.futures.html),
[mock patching guidance](https://docs.python.org/3/library/unittest.mock.html#where-to-patch)
and [pytest ordering guidance](https://docs.pytest.org/en/stable/explanation/flaky.html):
remove fixture order dependence, coordinate with Events, move barriers outside held
provider lanes, or narrow subprocess mocks. The fix makes the first provider call
fall back independently of track order and intercepts only Xray process creation,
leaving real ffmpeg validation active.

Linux validation initially used a different HOME and a non-executable tmpfs, which
hid the image's installed Firefox and prevented executable test fixtures. Options
were to select the installed browser path, retain the image's original HOME,
reinstall a matching browser, or use an executable test directory. Validation now
retains the checks image's HOME and uses executable temporary storage for fixture
scripts. Production container restrictions were not relaxed. References:
[Playwright browser locations](https://playwright.dev/python/docs/browsers),
[Docker tmpfs options](https://docs.docker.com/engine/storage/tmpfs/),
[pytest temporary directories](https://docs.pytest.org/en/stable/how-to/tmp_path.html).
