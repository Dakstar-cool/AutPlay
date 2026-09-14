# Acquisition deployment, 2026-09-14

The operator authorized deployment, publication of all outstanding source changes,
and acquisition of the full server playlist. They explicitly confirmed rights to
the tracks and requested every configured source.

## Release and isolation

The active acquisition release is `20260914-xray-v1`. Its runtime image is:

```text
sha256:dc2b8925a0b7d9729ce7a9922b70be2908c8621c5aef76d07c3f41abdd7a3867
```

Jamendo and Hitmo use direct connections. YouTube, SoundCloud and Bandcamp use
the shared on-demand Xray manager and `socks5h://127.0.0.1:10808`, with a 15-second
startup timeout, 60-second idle timeout and 5-second termination timeout. Yandex
remains disabled because no operator token is configured.

The binary and existing client config are read-only mounts. The client config's
permissions were tightened to `0600`; its contents were never printed or committed.
The previous manual client was stopped using a verified identity and Linux pidfd.
The separate systemd Xray server was preserved. No host proxy, route, TUN, iptables,
host network mode or published proxy port was introduced.

The existing lingering user service `autplay-acquire-playlist.service` now runs
the acquisition container. Its previous unit and release remain available for
rollback. Docker readiness is checked before launch; temporary queue exits resume
after 60 seconds. Graceful stop allows bounded active downloads to save receipts,
then the CLI closes its owned Xray process. An already-removed container during
normal completion is accepted by the stop command.

## Full playlist reconciliation

The original playlist has 4,591 unique source rows. Existing files were read-only
during SHA-256 reconciliation and complete ffmpeg decoding:

| Result | Count |
| --- | ---: |
| Already present and verified | 1,389 |
| Rows sent to the new queue | 3,202 |
| Duplicate rows after reviewed normalization | 2 |
| Unique queue jobs | 3,200 |
| Existing files rejected by decoding and scheduled again | 5 |
| Unresolved legacy hash references | 0 |

The new run is stored below the operator acquisition `runs/20260914-xray-full`
directory. New files and full-hash receipts are under
`/srv/autplay/music-downloads-xray`; the previous music directory is preserved.
This is file acquisition, without automatic insertion into the server Vault/database.
The queue is started; this report does not claim that all tracks were downloaded.

## Verification

- Linux checks image: 235 tests passed without skips; mypy passed for 28 source files.
- Final runtime image: YouTube, SoundCloud and Bandcamp returned HTTP 200 through
  the real managed client in 0.67, 7.14 and 0.90 seconds respectively. Concurrent
  lease sharing, idle cancellation, idle stop, restart and explicit shutdown passed.
- Root CI-style Ruff lint and formatting passed for all 55 acquisition Python files;
  explicit first-party classification makes import checks independent of working directory.
- 171 contract/release tests and 55 affected server tests passed.
- Android JVM/lint command completed successfully; Gradle accepted existing matching
  task outputs, with 277 JVM tests and zero failures/errors in the XML results.
- Bounded read-only code/publication/deployment reviews completed. No credentials,
  music files, model weights or private operator receipts were staged for publication.

Private reconciliation, full playlist rows, configuration, logs and operational
receipts remain outside Git. Imported historical evidence retains its original
formatting, including whitespace in the upstream license and captured Gradle output.
