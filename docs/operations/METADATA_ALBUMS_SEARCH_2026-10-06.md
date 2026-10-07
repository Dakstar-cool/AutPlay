# Native metadata, album groups and typed music search — 2026-10-06

Status: **ACCEPTED for the reviewed implementation and QA scope.** No actionable P1 finding remains open. Independent source, artifact, runtime, visual and cleanup verification is complete.

Independent implementation and evidence review of the combined native acquisition, owner metadata, album grouping, local/Vault search, and Internet metadata discovery/context changes. Validation applies to the exact source manifests and artifacts below; the worktree includes uncommitted changes on HEAD a17a4569ab462f2c650c757d1da433cdd58c0b03. No production deployment, persistent production data mutation, commit, push or physical-phone installation is established by this report.

## Behavior and identity boundaries

A first successfully published track with confirmed native provider album identity (or an independently confirmed MusicBrainz release) exposes an owner AlbumGroup. Later tracks with the same exact edition key join that derived album; equal album titles alone never merge editions. Descriptive tags and available decoded/cached artwork are exposed through owner metadata while immutable Vault audio remains unchanged.

Enrichment version 2 requeues eligible existing published Vault references when their stored enrichment version is older or absent. The bounded sweep (1–100 references per batch) skips live metadata jobs and reuses existing audio; it does not redownload or republish the recording. Existing selected/manual/locked values and explicit clears remain authoritative. This describes the implemented worker path; no production backfill was executed in this task.

Lookup normalization removes only explicit presentation wrappers and a matching credited-artist prefix. It preserves live/remix/edit and other recording-version qualifiers; raw titles are not rewritten. Failure to independently confirm an edition leaves candidates for review or preserves valid native grouping.

The metadata strategy is embedded audio first, then bounded native evidence and independently checked MusicBrainz candidates. Covers come from embedded art or the exact confirmed release's Cover Art Archive endpoint and are normalized before private caching. Native artwork hints do not authorize arbitrary CDN fetching. Optional AcoustID fallback uses an already configured key when ordinary lookup yields no candidates; fingerprints identify recordings and do not automatically establish an album edition.

Artist/album kinds in library and Vault search operate on current-owner effective metadata. Internet artist/album/recording browsing creates metadata context; the subsequent provider search presents actual downloadable candidates. A catalog card itself cannot start acquisition.

| Contract | Reviewed behavior |
| --- | --- |
| Native acquisition | SourceMetadata schema 1 is bounded to 16 KiB and four artwork hints. Source-native album/artist identifiers, explicit ISRC/MusicBrainz identifiers and provenance travel through acquisition, durable publication and owner metadata. Canonical audio/recording identity remains distinct; yt_dlp labels do not establish canonical identity. Artwork URLs are inert hints; this work does not fetch arbitrary native artwork CDN URLs. |
| Owner metadata | SOURCE_NATIVE evidence is weaker than embedded, selected/manual and locked evidence. Server attachment requires actual published live-owner Vault audio and preserves idempotence and stale-worker fences. Audio publication remains successful when optional metadata must retry. Metadata retries are bounded and do not republish audio. |
| Album grouping | AlbumGroupV1 is a derived owner/profile view over all live owned references, using confirmed effective exact release or provider-native album identity. Title is required; album artist may be unknown. Distinct editions are not merged by label, candidates do not establish membership, and explicit manual clears/locks remain authoritative. No new canonical entity or destructive Room migration is introduced. |
| Typed local/Vault search | Search uses the current profile's effective metadata, including explicit nulls, without indexing candidate labels or unintended aliases. Room-backed projection remains reactive to edits, deletion/restoration and reopen. Vault IDs and effective descriptions retain their owner boundary. |
| Internet discovery | Authenticated private/no-store artist, release, recording and release-occurrence cards are metadata-only, with acquisition disabled. The All view retains downloadable provider tracks. Pagination consumes raw provider offsets before UI deduplication, preserves repeated occurrences and reports truncation; limits are bounded. |
| Discovery-to-acquisition context | A server-hydrated, immutable owner-scoped 24-hour receipt carries exact selected identities. It is lookup context, never source-native evidence or a manual metadata selection. Operation replay preserves the original context, including absence; rebinding is rejected. The worker requires real owner/audio linkage, independent embedded/native corroboration and measured audio duration before public catalog data can confirm metadata. |
| Android async/retry safety | Transport retries preserve operation identity; a terminal failed acquisition gets a new operation while preserving selected context. Expired context requires explicit rehydration of the same selected IDs. Owner/query/kind/request/context changes fence late results. Shared Search/Library restoration captures the actual logical first content key and exact scroll offset. |

Internet requests are bounded: query 1–200 characters, limit 1–50, offset 0–1,000, response budget 1 MiB and full-release budget 5,000 occurrences; missing counts are rejected. Exact selected release/occurrence identities must remain coherent. Context rebinding returns 409, including attempts to add context to an operation originally created without it. Neither public duration nor uploader labels can independently confirm a recording. Metadata failure leaves successful audio publication intact.

Evidence report names and checksums below refer to locally retained QA records;
raw machine paths and private artifacts are not published in this repository.

## Final server gate

`scripts/check.ps1 -ServerOnly` ran unmodified and returned actual exit 0. Actual exit 0; root checks 311 passed in 45.63 s; server tests 2,610 passed, 76 skipped, one existing Starlette cookie deprecation warning in 2,562.59 s. Ruff/format covered 724 files; strict mypy covered 655. Pinned lock/dependency audit and Compose configuration checks passed.

1,003 source files were identical before/after the gate and in the independent current-source audit. Python 3.14.7, pytest 9.1.1, PostgreSQL 18.4, pgvector 0.8.6. The final migration head is 0065. An independent closure rehash at **2026-10-06T20:56:29.5691365Z** again found **1,003/1,003 server files and 529/529 Android files unchanged**, with HEAD `a17a4569ab462f2c650c757d1da433cdd58c0b03`.

The 76 skips comprise 20 Windows symlink cases, 18 Linux cgroup cases and 38 Linux media/IPC cases. Separate Linux proof covers the scopes stated below; it must not be presented as blanket coverage of every skipped case.

The safe final server receipt and actual full gate log preserve the outcome. Raw native execution returned exit 0 at 2026-10-06T19:27:54.321Z; the recovered native output is an additional record of that execution.

| Server evidence | SHA-256 |
| --- | --- |
| Safe final receipt | `8824db7f905b44facf9b1d39a52a542089e046536c8bdbc6d15169f9aca14d26` |
| check-server-only.log | `95206d67220963ceb5e3324a0d0098207f70cf5aec4a688459168a4cfce7fe08` |
| recovered-native-output.log | `07c54b3728771958ddb440a53b642c71efb4089ba693b3bf9c37cfcd83a80c92` |
| source-before.json | `a1881f514b50fc3bb6638357aef93d846ff4e8b9433ef22831d4988672007751` |
| source-after.json | `ba64b5db9b8e332b07d0f1a98e02cacfdbeb9f562d1aea6aa951126905995343` |
| result.json | `632ef7c8ba3454559b6cc97dd0c5599dc9d493228d692df09edfa601c0ef5987` |

Disposable project autplay-p06-24552 was removed with its volumes. Independent label-scoped Docker inventory at 2026-10-06T19:33:12.835Z found zero containers, volumes and networks.

## Final Android HOST and source binding

The frozen HOST report binds the exact source manifest, source archive, scripts and APKs. Independent review matched all 529 current source files and all 529 archived contents. Parsing 110 unit-test XML files established **516 passed, zero failures/errors/skips**. Lint XML contains zero fatal/error/warning issues and one existing `AutoboxingStateCreation` Hint. Build output separately contains a nonfatal SDK XML version 3-versus-4 warning.

HOST debug gates completed in 8m04s; trustedLan/release gates completed in 7m05s, both exit 0. R8 and release lint passed. Environment: JDK 17.0.20+8-LTS, 2 GiB Gradle heap, one worker, no parallel execution, strict dependency verification. Release is unsigned QA output.

```powershell
.\gradlew.bat --no-daemon --console=plain --max-workers=1 --no-parallel --dependency-verification=strict :apps:android:lintDebug :apps:android:testDebugUnitTest :apps:android:assembleDebug :apps:android:assembleDebugAndroidTest
.\gradlew.bat --no-daemon --console=plain --max-workers=1 --no-parallel --dependency-verification=strict :apps:android:assembleTrustedLan :apps:android:assembleRelease
```

| Frozen artifact | SHA-256 |
| --- | --- |
| HOST report | `bbe709c292a6ca837af39a565aca8a97399ea51d6e962d31e6ce52008c2d38fd` |
| Source manifest, 529 files | `a019b0d172a2eeaa56958595b108884fc28c6f11fab059f8fa98f5f32664d77f` |
| Source ZIP | `c68c6481945a2eeac9c396a381b315078e39891e36070ce1734891305c38259b` |
| Debug APK, 30,682,024 bytes | `20020658b4f022d57c1d67716f68b033cb8033d163e21a183c448fa5a5dae1d7` |
| Instrumentation APK, 2,745,116 bytes | `070c029b91a917ae56f6fc6c88993a855e9bfe6cc7e47b98c3232885ea8014ba` |
| trustedLan APK, 30,677,566 bytes | `c99b1451c76635b2da7761c47d632f881daea301b644c7cc25f1a571a9e08338` |
| Unsigned release APK, 10,730,336 bytes | `d7607dea5927ec508bb5d7f8a4a0635eb844744d54d5ddad64205122d4d5e28f` |

The compiled `AlbumGroupRoomTest.class` SHA-256 is `bf32b951001a4f4f801f5408d9db788ab2914d019721ede79f7eeb9a2c1c32e0`. Independent class-file inspection confirmed all nine JUnit/lifecycle methods have JVM descriptor `()V`.

## Actual final Android runtime

Raw instrumentation streams independently establish **96 unique started-and-passed method IDs**, with no duplicates, failures, errors, skips or other result statuses. All five run reports bind the final HOST/source identities above and the exact installed debug/instrumentation APK bytes both before and after execution. These counts are actual runtime results, separate from the 516 HOST JVM tests.

| Evidence directory | Actual passed | Wrapper elapsed, s | Report SHA-256 |
| --- | --- | --- | --- |
| runtime-metadata | 19 | 107.98 | `24d061b311ce9f2267955454af9dca851864d2e7298c1e9b59f083f1c08db72d` |
| runtime-ui | 61 | 86.44 | `029e7a975c696c9a49f1210fa2bb68001c0d69dabd744f84d01b4f50b2920ad6` |
| runtime-phone-motion | 6 | 14.79 | `c8a8539bb7a6a000f65de34855df84b1f5a07d685b1747216289af595d0e3fb0` |
| runtime-tablet | 1 | 10.35 | `05c8a4e14d7e747d87a380dd79f738886c98293661db9acd63dbc170ccfa6cfe` |
| runtime-legacy-activities | 9 | 47.61 | `dec5f2df8dee5c41e81487a22a8a7f2a14f6c1d2c22134daf315466bc3be4016` |

Each linked directory also contains `instrumentation.txt`; independent review parsed the raw per-method status stream rather than accepting only the report total. Phone runs used API 26 at 1080×1920/420 dpi, tablet geometry used 900×1280/160 dpi, and animation scales were restored to 1. The tablet raw summary is the singular `OK (1 test)`.

Metadata runtime consists of seven album-group Room cases, four typed-search Room cases and eight Internet Compose cases. Four exact Search/Library restoration checks (key `track-20`, offsets 0 and 37 px after empty-to-loaded rows) are included in the 61 UI cases and therefore in 96; the earlier successful targeted four-case run is not added.

The final Android runtime/visual handoff was independently read back with runtime, visible review and overall acceptance all `PASS`; SHA-256 `8f2983ebf28fa21831159fa4ac452b06eef430e5270975502bf5ab92cd79538b`. It binds the independent receipt below. The earlier pending handoff is preserved separately and is not the final result.

## Acceptance coverage

| Area | Accepted behavior | Evidence type |
| --- | --- | --- |
| Owner/native metadata | Bounded schema, provenance and precedence; live owner/published audio attachment; idempotent retries and stale-result veto; negatives for wrong variant, unmeasured/mismatched duration, public-only artist, live/version/album/date/position mismatch. | Real PostgreSQL with controlled media/provider inputs; separate actual Linux media proof. |
| Album membership | First and second tracks, repeated insertion and database reopen produce one intended album without canonical or Journal writes; equal labels with different editions/profiles remain separate; unknown artist survives; concurrent same-ref writes and unknown schema roundtrip remain stable. | Seven real Room cases, including full membership beyond 5,000 visible rows. |
| Reactive grouping | Manual clear, deletion, restore, same-timestamp edition change, old-revision veto, metadata-row removal and raw-search fallback. | Real Room persistence, reactive flow and reopen checks. |
| Typed library/Vault search | Every selected field and All search effective current-profile values; explicit null does not fall back; candidate labels stay unsearchable; rank order, alias exclusion, deletion, Vault IDs and reopen remain correct. | Four real Room cases. |
| Internet selection/retry | Every kind, raw pagination/dedup/truncation, repeated occurrences, exact rehydration after expiry, lost HTTP response versus terminal retry, artist/recording without invented release membership, stale owner/query/kind/request/context pages, mismatched receipt. | Eight Compose cases with injected private ports/work, plus HTTP/transport/JVM and server contract suites. |
| List restoration | Search and Library start empty, reload 30 rows, display track 20 and emit its exact actual key, context and offset 0 or 37 pixels. | Four targeted instrumented checks; included again as a subset of the final 96, never added to that total. |

## Visual acceptance and actual MainActivity

Independent review inspected the selected **24 PNGs**: 18 fixture frames, two supplementary short-player fixture frames and four actual MainActivity profile frames. All selected PNG/XML hashes match the capture receipts and final source/APK binding. The independent visible-review receipt records exact paths, hashes, classifications and exclusions; SHA-256 `a7bc4e39de3a878a75fd80bcfd377fca3875344722b9eb941233c20f62f21d56`.

The 13-frame base matrix covers Russian/English, light/dark, phone/short/tablet views of player, library/search, profile/statistics, onboarding ID and friends. Additional frames prove active shuffle with repeat ALL/ONE, a fresh short-player viewport, a different footer viewport, and fully visible incoming-request actions after keyboard dismissal.

Actual MainActivity was cold-started against disposable local state after runtime tests, without inserting visual-fixture data for those four captures. The profile shows listening time, manual Refresh, Top 5 genres/tracks/artists, local ID and Friends. Refresh was actually clicked. This device had zero listening time and empty rankings; populated lists are separately supported by fixture/repository/Compose tests. The visible profile has no settings preference slab; the global settings action remains separate.

On the short player, the fresh untouched viewport focuses near the seek bar. It is not evidence of an untouched top position. Two ordinary swipes reveal the header and cover top while fixed transport controls and Android navigation remain visible. Current initial/footer PNGs differ; an earlier identical pair is excluded as evidence of movement. API 26 XML reports `selected=false` for some controls; visible treatment, changed repeat labels, the ONE badge and Compose checks establish state without misreporting that XML flag.

| Current visual receipt | SHA-256 |
| --- | --- |
| Base matrix | `258e8b41dc620e2ef497e2ad3c13901c0aad8d4d36a1b658780b7a8270cb0e91` |
| Extras and actual profile | `7ebbf07de18428bdc8b3105fefb9da2314e4701686ee1da9bb5da91e8a046f0e` |
| Full incoming-request containers | `942493426988adbb4b21eb9ac33f328393231914558c1bf3ec24c2deb35ceaca` |
| Stable short-player top | `00c0ab4f9faa0c4447af9ef8ce41ea52fcff794c90070ec94fcc02c3a43ebfaf` |

The accepted friend replacement is `narrow-visual-proofs/friends-requests-keyboard-dismissed-full-containers-ru-dark.png`, SHA-256 `e7905055cc0902300054847d27946d85ff237d5110e6451788f08eaca51829b8`. Both enabled clickable containers are 126 px high, at `[84,654][353,780]` and `[374,654][681,780]`, with 1,014 px clearance above the navigation boundary. The earlier clipped friend image is preserved but excluded.

The accepted short top is `short-top-stable/player-short-en-top-stable-second-frame.png`, SHA-256 `36ed286b8b07e3539995062f2804f1087b5f2f50f60e38776a2f425b30b8bc47`. The transiently black earlier capture is excluded. Repairs affected only external QA capture helpers; frozen production/test sources and APKs did not change.

## Cleanup

The final runtime cleanup receipt records release at **2026-10-06T20:54:14.356871Z**: owned emulator processes **0**, instrumentation processes **0**, Gradle/compiler processes **0**. The owned AVD was shut down after restoring 1080×1920, density 420 and all animation scales to 1. Receipt SHA-256: `61a49e8d3176c16d3017fe2c6e7134ec1e00f7565a289866629c1839f056647b`.

The QA-end source audit records 529 files with zero mismatches; SHA-256 `9ad52c0fcfa46bf0d85648b8ed9f4e12c9685638b454cb7572873c32152d28d0`. The independent later closure rehash also covers the 1,003 server files. HOST-handover cleanup is not substituted for this final runtime cleanup.

## Separate targeted and platform evidence

| Scope | Observed result and limits |
| --- | --- |
| Native metadata/media | 127 passed, zero skipped on pinned Linux image e391cdeca21ff4e9ed79bccc13d54a5639731b721a0be9740aea26fd39871d8a; real ffmpeg/ffprobe 8.1.2, child/media behavior. No external catalog availability claim. |
| Acquisition publication and metadata | 50 passed, one Windows platform skip against real PostgreSQL. Search-to-trusted-handoff-to-publication and subsequent metadata execution used controlled provider/media/read_audio fixtures; this is distinct from real-media proof. |
| Local acquisition and bridge | 332 passed/eight skipped; separate wire proof three passed and PostgreSQL handoff/bridge proof 28 passed. Linux native media/IPC proof: 68 passed/17 Windows-only skips, including 16 actual AAC/Opus/HLS/DASH scenarios. These runs overlap other suites and are not additive. |
| Catalog process containment | 47 passed/two Windows-only skips in Linux using real child processes, cgroup lifecycle, PostgreSQL and outage/kill/reap scenarios with controlled HTTP. The delegated proof cgroup root was removed and owned PostgreSQL resources cleaned. This does not contact live MusicBrainz. |
| Immutable catalog contexts | 48 passed, including 11 real PostgreSQL, six ORM, nine HTTP and 22 pure tests; additional authority proof 11 passed. Context rows are lookup-only and owner-scoped; the final server gate includes the corresponding suites. |
| Historical migration guards | 59 passed/zero skipped against disposable PostgreSQL after test repair. Exact 0065 refusal and unchanged current-head state remain required. Separate genuine historical 0039–0042 schemas prove their own guards, legal FK lineage, actual lock blocking/claim replay and supported downgrade roundtrips. No production guard was bypassed or weakened. |

The Linux containment proof used a copied 0064 snapshot, not the later combined 0065 source snapshot. Its production gate hashes still match current files. Revision 0064 SHA-256: `4a0fa0c62f8d31e30ead5b23178fda8c6526b317b25847d2b919bb6539821400`; revision 0065: `cc637f704446c4742af5d463f8596634230bfd2e634e74c400d6a3bc69612e77`. Exact final 0065 acceptance comes from the final ServerOnly run. Targeted and repeated runs overlap and are never summed into an inflated total.

## Resolved findings and historical evidence

An earlier complete server gate failed eight tests: stale historical migration assertions and a browser stylesheet selector that matched two stylesheets. The narrow test repairs retained exact production guards and responsive/browser assertions; the unchanged final ServerOnly gate passed.

The first combined Android run had seven Room cases not run because one inferred runBlocking result produced a non-void JUnit method, plus an obsolete search-context assertion. All nine Room JUnit/lifecycle methods were made explicitly Unit; a missing search:kind key in the restoration map was fixed.

The following Android snapshot passed all 19 metadata scenarios but failed exact Search anchor capture: expected track-20, callback returned track-19. Visible padding could include the preceding row. Research and actual geometry ruled out insufficient tail content; the shared capture now uses the logical index/offset and actual layout key. Search and Library retain exact track-20 with offset 0 and add offset 37 cases. Historical failures are not reported as passes on current artifacts.

The final anchor implementation uses `firstVisibleItemIndex`/`firstVisibleItemScrollOffset` and an actual matching layout key, excluding padding-only preceding rows. This follows the distinction documented for [LazyListState](https://developer.android.com/reference/kotlin/androidx/compose/foundation/lazy/LazyListState). Assertions retain the exact expected key/context and offset, rather than weakening expectations. The final two-file patch SHA-256 is `5965cd9566bb6ead3fbe076d8d5f72239354bd013e045262f7d2489ad914aef2`.

## Operational limits

- All reported counts describe their own runs or subsets; repeated and overlapping tests are not summed into a larger unique total.

- Room instrumentation proves real SQLite/Room persistence and reopen behavior. Compose Internet UI tests inject private ports/work and do not establish real-server or real-media execution.

- Catalog Linux containment requires an absolute delegated cgroup-v2 AUTPLAY_CATALOG_CGROUP_ROOT when Internet discovery is enabled. Missing/invalid containment fails closed with unavailable/readiness failure. Production provisioning was not performed.

- Controlled HTTP/provider data proves contracts and failure handling, not live MusicBrainz availability, catalog completeness or provider download success in production.

- Release output is unsigned QA output; no production signing material or deployment was used.

- Enrichment version 2/backfill, optional AcoustID and Internet context behavior are implemented and tested; no production backfill, API-key provisioning or live catalog acceptance run was performed.

- Reviewer writes are limited to this operations document and the explicitly authorized independent visible-review receipt outside the repository. No implementation, schema, test or unrelated documentation file was edited by this reviewer.
