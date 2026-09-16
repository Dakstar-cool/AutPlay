# Completed work publication, 2026-09-16

This source snapshot combines the completed Android, Vault, metadata and acquisition work
from September 16. It was assembled in an independent Git worktree from the current master,
the verified M52 source, the isolated deployed server snapshot through migration 0032, and
the verified acquisition expansion source. The active Admin/account implementation continues
in its original working tree; its passkey, self-pairing and resource-admission modules and
migrations 0033 onward are excluded from this publication.

## Included

- Android interface changes and the [audit fixes](AUDIT_FIXES_2026-09-16.md), including Room 16
  search repair, language switching, playback navigation, command errors and sync retry.
- [Phone/server sync and the acquisition bridge](SYNC_VAULT_2026-09-16.md), preserving
  existing track identities and journal/history data.
- [Phone imports, Vault transfers and explicit Internet selection](MUSIC_LIBRARY_2026-09-16.md).
- [Track metadata and artwork](TRACK_METADATA_2026-09-16.md), with PostgreSQL migration 0032,
  Room 17, provider provenance, manual overrides and offline display.
- Acquisition [performance improvements](../../tools/local_music_acquisition/PERFORMANCE_2026-09-16.md)
  and [bounded related-recording expansion](ACQUISITION_EXPANSION_2026-09-16.md).
- A refreshed README showing delivered capabilities and a separate upcoming Admin/account section.

## Publication fixes

The combined checks found a metadata N+1 query in catalog publication. Metadata now joins the
owner-scoped track query; the existing query-budget and event-replay regression tests pass
without raising their query limit. The migration inventory now names all five added tables,
the additional index, two functions and four triggers. Full down/up reconstruction remains
checked. Test fixtures now have explicit types, and the browser qualification uses a bounded
selection of high loopback ports to avoid Chromium's blocked service ports.

Android lint also found that the embedded metadata reader implicitly required the
[`AutoCloseable` interface added in API 29](https://developer.android.com/sdk/api_diff/29/changes/android.media.MediaMetadataRetriever).
The reader now releases its resources in `finally` using the API available on supported
older devices. Equivalent AndroidX URI/bitmap helpers replaced lint-flagged calls, and
two unused strings were removed from both locales. No lint baseline or suppression was added.

Formatting and annotations were corrected without disabling checks. Existing source worktrees
and production services were not modified during publication. Device acceptance results in
the linked reports describe their original verified builds; no new APK release or deployment
is part of this Git publication.

## Final source validation

Checks were repeated against the combined publication worktree after the fixes above.

| Check | Result |
| --- | --- |
| Root contract and release-policy tests | 171 passed |
| Full server suite on Linux with PostgreSQL and Chromium | 979 passed, no skips; one existing Starlette cookie deprecation warning |
| Server static checks | Ruff and formatting passed; strict mypy passed for 311 source files |
| Full acquisition suite on Linux | 308 passed |
| Acquisition static checks | Ruff and formatting passed; mypy passed for 34 source files |
| Android JVM tests | 297 passed across 75 suites, no failures or skips |
| Android debug lint | 0 errors, 0 warnings, 1 existing informational hint |
| Android debug, trustedLan and release builds | All passed in the same final host gate; `BUILD SUCCESSFUL` |
| README | Local links and four images checked; wide/mobile previews inspected in light and dark themes |

The Android host gate uses strict dependency verification and one Gradle worker. It covers
`lintDebug`, `testDebugUnitTest`, `assembleDebug`, `assembleTrustedLan` and `assembleRelease`.
The source publication does not claim a fresh connected-device run for its four small
Android lint/API compatibility corrections.
