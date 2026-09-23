# ML Revision 15 implementation status

**Approved authority:** `PLAN_REVISION_15.md`, SHA-256
`33d4d69ac6f1b18968291de7a24d35ad6638a7a90faafc15b95650fb17b36ba2`.
The copied plan retains its pre-sign-off status text as immutable review history. The user
subsequently authorized implementation in this task. Publication, deployment, merge, and release
checkout changes remain outside that authorization.

**Exact base:** annotated tag `v1.0.0`, commit
`9ac8778b6a2a24141bcf37af01fa22ed8600eacb`.
**Isolated branch/worktree:** `codex/ml-r15-20260923` in
`D:\AutPlayProd\AutPlay-ml-r15-20260923`.

| Milestone | State | Evidence and next gate |
| --- | --- | --- |
| M0 | Complete | Worktree created directly from the tag; release checkout was clean when inspected. Plan copy hash matches the approved source. |
| M1 | In progress | Corrected Face v1/v2 and teacher V2 current-state wording; added ML contract map, frozen Face artifact-policy vectors, Face v2 map/semantic-key/timeline-result Python/Kotlin golden contracts, `SCORE_E8_V1` binary64/decimal vectors, `CANONICAL_JSON_LP_V1` Python vectors, `SonaExecutionProfileV1` golden identity and pure work/decision transition guards. Added disabled artifact-review, self-only Sona serving-consent and owner-cohort command schemas plus control-plane, breaker and two-key/operation-bound step-up contracts; disabled signed Face projection-upsert and no-timeline tombstone schemas; a Face capability/download/lease contract; and unwired Python/Android signature/policy/lease verifiers sharing a checked-in signed vector. Remaining activation/other admin schemas, complete SQL lifecycle fences and broader contract suites precede M1 completion. |
| M2 | In progress | Added bounded Face required-artifact/policy-list codec and tuple lease/disposition derivation with golden vectors. Additive `0061` creates non-activating artifact/license and step-up storage. An unwired internal artifact review writer now checks reviewer role, current generation and migration issues, and an activation-side reader locks current license decisions while deriving the Face tuple policy. A strict bounded review command parser freezes an operation-bound hash before WebAuthn. Additive `0062` creates generation-fenced GPU admission history/current state and an unwired process-facing authority adapter. Additive `0063` reserves owner-bound Sona capture/cursor/dispatch/work/attempt/evidence storage. Its SQL work graph now matches the pure graph, and every exit from `CLAIMED` requires an exact immutable attempt in the same transaction. P11 now captures snapshot and commits request/items in one transaction. An independent R1B grant reader can lock and verify the current receipt in that same transaction; a conditional native-capture gate extends retention only for an eligible, proven public recommendations request. An independent R1C serving-consent ledger with optional settings and offline init/head-check command is implemented but unwired. Manual review API/idempotent audit/WebAuthn, two-key attestation verifier, target-GPU NVML qualification, cutoff-bound native event source, capture scheduling and consent projection remain pending. |
| M3 | In progress | Added shared six-axis point metrics, ordinal raw-rater reliability, complete-corpus collation, a development selector, strict rater-submission schema, deterministic final-window IDs, all five frozen bootstrap-family primitives, a pure final technical evaluator and canonical technical report archive. Downloaded quarantined CLAP/YAMNet weights and primary-source audio candidates outside Git; CLAP passed one local technical smoke inference. No approved fixture/rater authority, complete model bake-off, real final qualification or signed approval exists. |
| M4 | In progress | Additive dormant `0064` defines interpreter, qualification, activation-chain and owner-policy authority; typed ORM parity and migrations pass. Unwired policy/source readers and a pure sponsor-lineage guard are present. The 0065 work/timeline/fan-out schema and all publication, sync and Android integration remain open. No activation or owner-data capture occurred. |
| M5-M10 | Not started | No Face publication, Sona serving, or activation has occurred. |

The Face artifact codec is a pure non-activating foundation. Its `APPROVED` input must come from
the transactionally current license authority; callers cannot make an artifact selectable by
constructing an in-memory entry. The branch's Alembic head is `0064`; it has run only on
disposable PostgreSQL. P11 still serves the same CPU algorithm; snapshot and request/items now
commit together. No control endpoint, credential enrollment, model selection, GPU process, or
new owner-data capture is enabled by these migrations. The GPU authority adapter is deliberately
not wired to a CUDA process until target-device NVML, crash, restore, and batch-boundary proof is available.

The internal `SqlAlchemyArtifactLicenseAuthority` is likewise unwired. Its caller-owned
transaction appends a reviewed successor only for a live `OWNER`/`ADMIN`, at the expected
generation and without an unresolved artifact migration issue. A Face tuple reader locks each
artifact and current decision through the caller's transaction, preventing a concurrent
conflict-issue insert from crossing the check, and derives the minimum lease and strictest
disposition from exactly those rows. The `0061` issue-insert trigger now takes a key-share lock
when the artifact row exists; the issue table intentionally has no artifact FK because some
legacy issues reference no importable artifact. Five focused tests passed against disposable
PostgreSQL, including rollback, stale generation, reviewer role, a concurrent revocation and a
concurrent issue insert blocked by tuple-read locks, and rejection after revocation or issue.
The focused `0061` migration suite plus clean downgrade/re-upgrade and live Alembic metadata
check passed (14 tests total). WebAuthn proof, operation idempotency/audit, artifact
import and activation are still absent, so this adapter is not an authorized manual review flow.

Ruff `SIM117` recurred in the new test. Per the project rule, the official Ruff documentation
was consulted: combine adjacent context managers, apply Ruff's safe fix, suppress the line with
`noqa`, or add a per-file ignore. The local fix combines context managers; no lint rule was
weakened. Source: https://docs.astral.sh/ruff/rules/multiple-with-statements/ .
The Windows path error from passing `*` as a positional `rg` path also recurred. The official
ripgrep guide/discussions describe searching an existing directory with `-g`, listing matches
with `rg --files -g`, or naming exact files. We chose directory search plus `-g`, keeping glob
patterns out of Windows path arguments. Source:
https://github.com/BurntSushi/ripgrep/blob/master/GUIDE.md .
Two `E501` diagnostics in the Face verifier led to the official Ruff options: split the
expression, run the formatter where it can wrap, raise the configured line length, or suppress
the line. We split the expressions and kept the configured limit. Repeated mypy `arg-type`
diagnostics at test-only nested JSON serialization led to the documented options of an explicit
recursive JSON alias, a TypedDict, narrowing/copying, or a boundary cast. The fixture helper
uses a cast at the canonical-serialization boundary; production verifier inputs remain checked.
Sources: https://docs.astral.sh/ruff/rules/line-too-long/ and
https://mypy.readthedocs.io/en/stable/common_issues.html .

Two focused Android `AssertionError` failures were caused by hand-calculated
time expectations, not verifier behavior. The official Android `SystemClock`
and JUnit assertion references were checked. The options were to correct the
expected monotonic times, change the fixture's elapsed-time inputs, clamp the
clock to the old maximum, or use wall time alone. The latter two would weaken
rollback resistance. We kept elapsed time as the authority and corrected the
two test expectations using the signed issue time and exact elapsed delta.
Sources: https://developer.android.com/reference/android/os/SystemClock and
https://junit.org/javadoc/latest/org/junit/Assert.html .

The migrated-function inventory failed twice after the `0061` issue-lock
helper was added (76 actual, 75 expected). The official PostgreSQL catalog
and pytest assertion documentation were checked. Possible fixes were updating
exact expected names/counts, comparing only a named set, excluding the helper
from the inventory, or removing the helper. We retained the concurrency lock
and updated both function and trigger names/counts; the exact inventory test
now passes. Sources: https://www.postgresql.org/docs/current/catalog-pg-proc.html
and https://docs.pytest.org/en/stable/how-to/assert.html .

## Verification

Run focused checks from `server/` with the committed lock:

```text
uv run --frozen pytest tests/test_face_artifact_policy.py -q
uv run --frozen ruff check src/autplay/application/face_artifact_policy.py tests/test_face_artifact_policy.py
uv run --frozen mypy src/autplay/application/face_artifact_policy.py tests/test_face_artifact_policy.py
```

The Face policy module has 17 passing contract tests. This is not a Semantic Face quality,
Android device, or Sona native-data PASS.
The separate Face v2 source-presentation map has 15 Python tests, identity 12, and timeline result
8. All three have passing Android JVM cross-language golden test classes. The map codecs agree on
598-byte AAC and 602-byte VBR MP3 golden maps, exact SHA-256, half-open seek boundaries,
encoder delay/padding and neutral mismatch behavior;
the identity codecs agree on 1,032 canonical bytes and the domain-separated semantic key; result
codecs agree on 2,382 canonical bytes and the domain-separated result hash. The Face v1 codec is
unchanged; no v2 projection, server publication or Android playback integration is enabled.
The non-activating `SCORE_E8_V1` codec has eight passing tests over checked-in binary64 hex
vectors, int64 endpoints, negative zero, non-finite/out-of-range rejection and the legacy
raw-score generator budget boundary. P11 production scoring and serialization remain unchanged.
The signed Face v2 projection fixture now passes five Python verifier tests and six focused Android
JVM signature/lineage/tamper/clock tests. The Android pure lease clock covers rollback, elapsed-time,
process-restore, reboot, identity-change and expiry tests. It is not wired to Room, sync, fetch or
playback; accepting an online envelope still requires a trusted lower-bound time source, and
selection still requires current authority and exact Media3 source proof.
The root contract suite also validates the same signed vector against its JSON Schema and checks
that missing role counts, unsafe generations, extra upsert fields and timeline-bearing tombstones
are rejected (three focused tests). JSON Schema does not authenticate a signature; the pure
verifiers supply that separate check.
The pre-SQL `CANONICAL_JSON_LP_V1` nested-field codec has 15 passing tests for exact typed
bytes, schema-declared ASCII keys, NFC, int64, duplicate-key and resource bounds. The matching
PostgreSQL recursive encoder and serving-v2 item/set/decision hashes are not yet implemented.
The pure work/discovery/download/backfill/serving-decision transition guard has 13 passing tests,
including terminal-state closure and retry classification. SQL state mutations are not yet wired
to this graph.
The pure `SonaExecutionProfileV1` codec has 19 passing golden/mutation/rejection tests. Its
domain-separated digest covers exact image, code, runtimes, GPU, provider, tensor, determinism,
thread and adapter identities. It is not wired to a GPU process or approval chain. The separate
R1C serving-consent purpose now matches the approved plan exactly, with a literal regression
assertion in the consent tests.

Disposable PostgreSQL 18.4/pgvector 0.8.6 checks passed for the 0061 upgrade and clean
downgrade/re-upgrade, Alembic metadata drift, exact catalog inventory, legacy content and license
handling, oversized manifest reporting, current-decision row locking, device-key generation,
absence of PUBLIC grants on new ML tables, and refusal to drop reviewed or newly imported evidence.
The wider migration regression suite passed 26 tests; the inventory/acceptance/resource schema
subset passed 8; seven dedicated 0061 tests passed after the final downgrade guard change.
Face policy plus readiness tests passed 21. These are focused structural checks, not full server,
Android, model quality, privacy, or Sona native-data gates.

For `0062`, 30 migration/inventory/acceptance tests passed in a wider run; its one stale linear-
revision expectation was corrected and passed separately. Eight targeted GPU authority tests
passed, including a real PostgreSQL row-lock race, release proof, budget fence, process adapter,
and process stop on authority loss. GPU hardware and NVML behavior remain untested.

An optional NVML ctypes adapter now reads exact device UUID, free/total memory, and exact
compute PID usage. A separate application gate checks the database lease first, then a
fresh measurement before/after model load and before a measured heartbeat. Release proof
requires the PID to disappear from the compute-process list. NVML query failure or
Windows WDDM unavailable process memory fails closed. Eight synthetic memory-gate tests,
Ruff and mypy pass. No local NVIDIA driver or target RTX 3060 was present to test the
native ABI, crash recovery or Face/Sona coexistence; no GPU process is enabled.

For the P11 transaction refactor, four focused real-PostgreSQL cases passed, including CPU
failpoint rollback after snapshot capture, optional native-capture failpoints after request flush,
exact replay, owner isolation and offline pack. For
`0063`, the typed metadata inventory passes six structural tests; a real-PostgreSQL case proved
baseline binding, immutable rows, cursor/work fences, terminal evidence and owner-data cascade.
The SQL work graph was narrowed to the approved pure graph: `PENDING` cannot
jump to terminal ineligible, and `RETRY_WAIT` cannot jump to exhausted without
a new claim. A deferred constraint now requires the matching immutable attempt
for every claimed-work exit; success still additionally requires terminal
evidence. A real-PostgreSQL regression covers both forbidden jumps and missing
attempt/evidence, followed by a valid atomic success. Clean migration
downgrade/re-upgrade and exact named catalog inventory passed after this change.
The `0063` inventory, clean upgrade/downgrade, live Alembic drift and focused lifecycle/owner
cascade/expiry tests pass. A bounded database-clock retention adapter is present but not scheduled.
Training-consent withdrawal deletes native capture roots transactionally and preserves P11 truth;
three focused consent regressions passed after that change.
The independent serving ledger and pre-existing training ledger passed 22 tests together, including
tamper detection, idempotency, owner isolation, distinct-file/key provisioning and concurrent
appends. Runtime settings passed 63 tests with the new optional fields.
The canonical `SonaCaptureBundleV1` codec now validates exact P11/baseline/temporal/evidence
ancestry, 180-day retention, complete mandatory-filtered membership, over-cap ineligibility and
byte/hash bounds. Four pure contract tests pass, including a checked-in canonical-byte/hash vector.
The unwired SQL writer joins a caller-owned P11
transaction and requires its exact temporal row. The P11 unit of work now requires the writer's
prepared bundle and verifies its canonical document, response ancestry and inserted rows before
commit. Real PostgreSQL failpoints proved rollback after all P11, temporal, bundle and cursor
inserts and rejection of a mismatched prepared digest, followed by a successful atomic commit. The temporal
repository supports a caller-owned transaction for that path. `0063` now checks hashes against
bundle, attempt and evidence bytes in PostgreSQL. Training-consent withdrawal and account purge
both have seeded-capture privacy proofs. The capture writer still needs same-transaction event
source, production composition, restore reconciliation and cleanup scheduling before
production wiring. Historical downgrade tests continue to pass against the 0054 schema.
The existing training-consent startup restore guard now erases capture roots for accounts without
a proven current independent grant. A closed-database clone test passed: restoring a stale grant
and capture after withdrawal deletes bundle/cursor while retaining the original P11 request.
Sona-specific quarantine receipts and post-backup deletion projection remain pending.
No native capture or shadow coordinator has been enabled.

The operation-bound artifact review command now has a strict 32,768-byte parser and a fixed
RFC 8785/domain-separated SHA-256 vector. It rejects duplicate JSON keys, mismatched path hash,
invalid state/generation/lag, oversized nested policy and ambiguous UUID or integer forms before
challenge issuance. Four parser tests, three JSON Schema contract tests, Ruff and mypy passed.
The reviewed DTO moved to the domain layer without changing the SQL adapter; five real-PostgreSQL
review authority tests passed after that move. No review endpoint or WebAuthn mutation is enabled.

The R1B capture consent reader now returns a receipt hash only when the current PostgreSQL grant
matches the independent ledger under an account/policy lock. Missing independent evidence skips
optional native capture while preserving CPU P11. The optional P11 gate runs before the baseline
snapshot in the same repeatable-read transaction, extends retention to 180 days only for a proven
eligible public recommendations request, and still rolls back all P11 rows if native capture fails.
The gate and writer must be installed together. The writer receives the exact grant; a prepared
bundle with a different receipt hash or revision aborts the P11 transaction. A PostgreSQL failpoint
proved that mismatch rollback. Three focused real-PostgreSQL consent/gate tests and the existing
two native writer/failpoint tests
passed. Production remains unwired because the cutoff-bound native event source is still absent.

The non-activating M3 development selector passed five tests for full versus limited outcome,
four-axis anchor, six-place score, ECE/throughput/hash tie order, subfloor rounding and invalid
annotations. Ruff and mypy passed. This is a selector over supplied development point estimates;
the annotation-reliability evaluator, qualified dataset manifests, approved model licenses,
target-GPU benchmark and final-set approval are not present. The new point metric evaluator
implements tie-averaged Spearman, coverage, directional balanced accuracy and fixed-bin ECE,
including the exact abstention convention. Four focused tests, Ruff and mypy passed. It requires
an independently proven alpha and complete preregistered segment set; it does not infer either.

The user confirmed that no preapproved qualification corpus or model weights were available and
authorized finding and downloading them. A local external manifest at
`D:\AutPlayProd\ML_artifacts_r15_20260923\external-assets.manifest.json` now records exact
hashes/bytes and source pages for a pinned LAION-CLAP music `safetensors` revision, official
TensorFlow YAMNet HDF5 weights, and a 26-track CC0-labeled OpenGameArt archive. The archive is
quarantined: one artist and third-party DAW/sample assertions require review, so it is not a
qualified smoke, development, or final set. Neither model is approved for activation. CLAP's
safetensors file is from a pinned unmerged official-repository PR and still needs checkpoint and
training-provenance review; YAMNet needs exact weight-license review. The manifest recorder checks
the CLAP upstream LFS digest, safe-format header, YAMNet HDF5 signature, audio archive CRC,
bounded members, and every stored SHA-256 without executing downloaded weights or decoding audio.
Essentia's previously locked baseline fetch was attempted but its upstream host failed both
PowerShell and curl connections. Three baseline binaries were then obtained from pinned third-party
mirrors and matched byte-for-byte against the previously locked upstream SHA-256 values:
Musicnn, EffNet and the Jamendo head. Their exact mirror URLs and missing files are recorded in
`D:\AutPlayProd\ML_artifacts_r15_20260923\essentia_reference\mirror-provenance.json`.
The DEAM head and metadata/license documents remain unavailable locally; no complete Essentia pair
is importable. Its published CC BY-NC-SA versus CC BY-NC-ND conflict remains a separate approval
blocker, regardless of matching file hashes.

The connected `E:` volume provides roughly 152 GB of free capacity for research assets. The official
FMA metadata ZIP was obtained there with its published SHA-1, and the metadata was read only as CSV;
its included pickle file was never loaded or extracted. An offline deterministic genre round-robin
selected 70 **development candidates** with 70 distinct artists and exact CC0/CC-BY 4.0 metadata
labels. The 7.68 GB official audio ZIP repeatedly stalled or reset during a full transfer, so a
bounded HTTP-range reader fetched only those 70 ZIP members from the official URL. Every selected
member passed the ZIP CRC and has a recorded SHA-256 in
`E:\AutPlay_ML_Research_r15_20260923\fma\selected_audio\range-acquisition.manifest.json`.
The whole archive's published SHA-1 is explicitly **unverified** by this selective fetch. An
abandoned partial whole-ZIP download on `E:` remains unverified and is not evidence or a corpus.
The 70 clips are about 30 seconds each and cannot supply twelve nonoverlapping ten-second final
windows; they are development candidates only. Live per-track license pages and operator review
are still required before any candidate becomes an approved fixture. Official CC BY 4.0 and CC0
legalcode snapshots, with hashes, are stored separately on `E:` for that review.
All 70 shortlisted clips passed a complete audio-only FFmpeg decode, with tool hashes and stream
metadata recorded in `selected_audio/decoded-audio.manifest.json`. A separate ID3 license-tag
audit found 26 embedded CC links matching the historic FMA CSV, 37 conflicting, and seven with
no embedded CC link. **Even matching tags are not a current license grant**; FMA says the current
per-track page carries the license and the artist remains the rights holder. The live pages were
not retrievable in this environment, so the development collection has no approved fixture yet.

A disjoint full-length source **pool** was selected from the same FMA metadata: 40 artists across
15 genre labels, excluding all development artists and tracks. The official file host allowed a
curl GET of all 40 original-length MP3s on `E:` after Python's HTTP client received 403 for those
same URLs. Exact source URLs, SHA-256, transfer client hash, and the failed first attempt are
retained. All 40 have a single decodable MP3 audio stream and are at least 120 seconds. Four also
contain ordinary ID3 attached cover art; the decoder now identifies that disposition explicitly
and maps only the audio stream. **None is a sealed final qualification set.** The ID3 license-tag
audit found only three matching the historic CSV, 29 conflicts, and eight with no embedded CC
link. All 40 remain quarantined pending current per-track written authority; conflicted tracks
are not candidates for approval on the CSV label alone.

The repeated 403 was examined against curl's HTTP documentation, Cloudflare's 403 diagnostic,
and FMA's official direct/ZIP download routes. Considered remedies were a conventional curl GET,
a bounded HTTP range, selected-member access from the published full ZIP, site-owner access
support, or a different licensed source. A direct curl GET returned the complete official MP3
without credentials or bypass flags, so the bounded curl client was used. Source:
https://curl.se/docs/faq.html ; https://developers.cloudflare.com/support/troubleshooting/http-status-codes/4xx-client-error/error-403/ ;
https://github.com/mdeff/fma . The repeated extra-stream decoder result was checked against
FFmpeg's attached-picture disposition and audio mapping documentation; options were excluding
these files, mapping only audio, stripping cover art, or decoding the picture. We chose bounded
attached-art recognition and explicit audio-only decode. Sources:
https://ffmpeg.org/ffprobe.html and https://ffmpeg.org/ffmpeg-formats.html .

The user will organize three independent qualification raters. A rubric and strict canonical
submission schema now bind each pseudonymous rater's consent receipt, study/fixture/recording
hashes, twelve segment IDs, six raw integer scores per segment, confidence, track summary, and
independent transition marks. The pure codec forms medians only with three distinct pseudonyms
and current, separately supplied receipt facts. A deterministic source-sample schedule spans each
full-length final track with twelve content-bound ten-second windows and rejects tracks shorter
than 120 seconds. Ordinal Krippendorff alpha uses the original pooled-frequency distance and exact
rational disagreement before converting to binary64; a constant axis is undefined and cannot
pass. The independent rater ledger, withdrawal/deletion workflow, full-set reliability report,
transition reference aggregation, bootstrap final bounds, and human ratings do not yet exist.
The alpha implementation follows the author's method:
https://www.asc.upenn.edu/sites/default/files/2021-03/Computing%20Krippendorff's%20Alpha-Reliability.pdf .
The final-statistics foundation now freezes five PCG64 family names, domain-separated seed
derivation, exactly 10,000 track-block draws, exact type-1 quantile indices, and maximum-cardinality
one-to-one transition matching within 3,000 ms. Development and final axis bounds use the same
point-metric implementation. The per-axis three Bonferroni families retain all bootstrap draws;
the transition F1 and blinded-preference singleton families retain theirs. A golden seed/draw
vector and perfect/insufficient-reference tests passed. This is not yet a complete signed six-axis
final report or a quality PASS: raw rater authority, full-set assembly, model outputs and runtime
evidence remain absent.

The official Wikimedia Commons Action API was also inventoried as an independent possible audio
source. Its current per-file metadata yielded 28 CC0/CC BY 4.0 audio leads of at least 135 seconds,
from 23 displayed authors, among 1,746 directly categorized music files. This is a discovery set,
not a license decision: some file descriptions explicitly carry `License review needed (audio)`
and some are demonstrations or historic/government works requiring individual scrutiny. The exact
API media SHA-1, file description URL, author, category and license link are recorded in
`E:\AutPlay_ML_Research_r15_20260923\commons_music_discovery.json`. The media host then returned
HTTP 429 after a few transfers. A first acquisition manifest (four hash-verified files) and a
second retry/status-parser failure manifest (seven hash-verified files) were retained. No 429/error
body was accepted: each retained file matched the exact MediaWiki byte count and SHA-1. The
downloader now uses serial pacing, curl's bounded 429 retry/`Retry-After`, and exact source hashes;
the remaining files are still absent. A site-wide Commons page label is not treated as artist
authorization without per-file source review.

The repeated Commons 429 was researched using Wikimedia's 2026 API-rate guidance and the official
curl retry specification. Considered options were reducing request rate, honoring `Retry-After`,
grouping/cache/checkpointing API reads, identifying the client by a meaningful project URL, using
another authorized source, or seeking approved higher-rate access. We implemented serial reads,
three-second metadata pacing with resumable checkpoints, a project-contact user agent, ten-second
media pacing, and bounded curl retry. The source also cautions that 429 should not be hammered:
https://www.mediawiki.org/wiki/Wikimedia_APIs/Rate_limits ;
https://www.mediawiki.org/wiki/API:Etiquette ; https://curl.se/docs/manpage.html .

An independent OpenGameArt primary-source pool is now on the removable `E:` drive. The explicit
selection and acquisition manifests are
`E:\AutPlay_ML_Research_r15_20260923\oga_primary_selection.json` and
`E:\AutPlay_ML_Research_r15_20260923\oga_primary_candidates\oga-primary-acquisition.manifest.json`.
The 21 selected original-site audio files have 21 distinct displayed authors, each has a snapshot
of its exact OpenGameArt page showing a single CC0 label and its exact file URL, each is at least
120 seconds, and all 21 fully decoded with FFmpeg. The acquisition recorded source-page and audio
SHA-256, size, sample rate, duration, and URL; an independent reread rehashed all 42 files and
found no mismatch. Total acquired audio is 114,056,089 bytes. Site submission guidelines and FAQ
were snapshotted and hashed separately. The OGA FAQ describes CC0 reuse and the site guidelines
require rights-compatible submissions, but this is not a guarantee about every audio component.
The source pages' text was inspected for third-party components and use conditions. For example,
one soundtrack was composed for a separate game and another mentions an added sound effect; both
need component-rights review. The per-file pending decisions and notes are in
`E:\AutPlay_ML_Research_r15_20260923\oga_primary_candidates\oga-component-rights-triage.json`.
**All 21 are quarantined source leads, not approved smoke,
development, or final fixtures. No final set is selected or unsealed.** A second, disjoint
OpenGameArt source acquisition under `E:\AutPlay_ML_Research_r15_20260923\oga_additional_candidates`
verified 27 more full-length files from 27 other displayed authors. One selected WAV exceeded
the 60 MB per-file bound and was not admitted. The 48 accepted audio files have 48 distinct
displayed authors and distinct audio hashes; every acquired source page and audio file was
independently rehashed. The second rights-triage manifest explicitly excludes two files: one
uploader says another person composed the music, and another says the composer is unknown.
Other pages mentioning commissioned context, added effects, artist/uploader name differences,
or preview compilations remain pending individual review. No source file is yet an approved
fixture. Source authority:
https://opengameart.org/content/art-submission-guidelines ;
https://opengameart.org/node/5571 ;
https://creativecommons.org/publicdomain/zero/1.0/legalcode .

The pinned CLAP safetensors checkpoint loaded locally through Transformers 4.57.1 and CPU-only
PyTorch 2.8.0 without network fetch or custom model code. One ten-second OpenGameArt source lead
decoded to exactly 480,000 mono samples at 48 kHz and produced finite similarity values for four
fixed text probes. The input/model hashes, versions, prompt strings, scores and timing are in
`E:\AutPlay_ML_Research_r15_20260923\clap_cpu_smoke.json`. This establishes only technical
execution on this 16 GB laptop, not semantic quality, licensing approval or target-server GPU
throughput. The checkpoint's provenance review remains open. CLAP API reference:
https://huggingface.co/docs/transformers/model_doc/clap .

The Commons media downloader now halts its run and records partial verified progress when the
media host continues returning 429 or a response no longer matches its pinned API hash, instead
of trying the rest of the inventory during a rate limit. It has not been restarted against Commons
since this correction.

The repeated Essentia download failure was researched using the official curl manual and upstream
model catalog. Options were bounded transient retries, IPv4/IPv6 selection, an approved proxy,
an exact-hash alternate mirror, or continuing with reachable independent candidates. We downloaded
the independent candidates from official locations, then used exact-hash mirrors for three
non-activating Essentia research binaries. Neither a TLS bypass nor an unverified mirror was used.
Sources:
https://curl.se/docs/manpage.html , https://essentia.upf.edu/models.html , and
https://essentia.upf.edu/licensing_information.html .
The continuation's broad non-PostgreSQL server run passed 1,149 tests with 56 environment-specific
skips (Linux cgroup/media/symlink proofs); the latest root contract run passed 192 tests. The M3
statistics/rater modules passed 26 focused tests; the new source-acquisition tools pass Ruff and
mypy. None of these results constitutes a Face semantic qualification result.

Ruff `I001` import ordering occurred twice during this continuation. The official rule offers
manual isort order, `ruff check --select I --fix`, local suppression, or configuration ignore.
We used the shown manual order. Mypy reported two `int | None` comparisons in one selector path;
its official narrowing guidance offers an explicit local branch, an assertion, a cast, or a type
guard. We used a local branch and collected only proven integer margins. Neither lint nor type
checking was weakened. Sources: https://docs.astral.sh/ruff/rules/unsorted-imports/ and
https://mypy.readthedocs.io/en/latest/type_narrowing.html .

Two status-document patches failed because their context no longer matched the edited paragraph.
The official Git patch documentation describes context matching. Available remedies include
re-reading current lines, reducing the hunk to exact context, regenerating a diff, ignoring only
whitespace differences, or context-free application. We used fresh exact lines and smaller hunks,
keeping patch safety checks intact. Source: https://git-scm.com/docs/git-apply .

Concurrent edits appeared in the original release checkout during this task. The user confirmed
that those parallel edits are expected and directed work to continue in this adjacent branch.
This worktree remains on the approved base; the original checkout is untouched by this task.
The disposable PostgreSQL Compose project is used only for migration checks and is removed after
each verification session.

## 2026-09-23 handoff checkpoint

The newly downloaded OpenGameArt buffer is in
`E:\AutPlay_ML_Research_r15_20260923\oga_buffer_candidates` with its selection and acquisition
manifests alongside the earlier two OGA pools. It contains 21 hash/FFmpeg-verified complete
audio files from the 23 selected additional leads; two fetches failed. All remain quarantined
until individual component rights review. Across the three OGA pools, 69 files are verified,
but none is an approved smoke, development, or final fixture. The newly acquired buffer still
needs an independent byte rehash and per-page component-rights triage.

The `0064_face_artifact_activation` migration is now the dormant Alembic head. It adds eight
tables, six guard functions and ten triggers; the singleton activation starts at epoch zero.
The migration and its typed SQLAlchemy mappings are in
`server/migrations/versions/0064_face_artifact_activation.py` and
`server/src/autplay/adapters/postgresql/models/face_activation.py`. A clean upgrade, full
downgrade to base/re-upgrade, snapshot reconstruction, and two focused Face authority tests
passed against a disposable PostgreSQL 18.4 container. Seven of eight tests in the recent
focused migration/metadata run passed; the sole failure was an outdated ORM fingerprint,
which has since been updated. Ruff, formatter, and mypy pass for the new migration/ORM/test.

**Checkpoint blocker, now resolved:** `alembic check` detected about 50 inline SQL CHECK constraints in `0064`
which are absent from the ORM metadata. Mirror those named checks in the Face model, update its
fingerprint, and rerun `test_resource_admission_migration_matches_runtime_metadata` and
`test_live_alembic_metadata_check_has_no_upgrade_operations`. The current migration was applied
to disposable Compose project `autplay-ml-r15-0064-20260923`; stop it with the exact project
name and `compose.yaml` plus `compose.test.yaml` after tests. The adjacent-revision test was
interrupted after the two ORM drift failures to avoid a long irrelevant run. A deliberate
test-name typo caused one zero-test invocation; it was corrected on the next invocation.

The 0064 ORM parity fix was verified on the resumed implementation: both named Alembic drift
tests, the metadata fingerprint and the two Face authority tests passed (5 focused tests).
The wider migration/metadata/close-gate/resource-schema/Face authority run passed 35 tests in
98.57 seconds. The exact disposable Compose project was then stopped with `down -v`, removing
its PostgreSQL container and test volume. No qualification or activation was performed.

An internal, unwired `SqlAlchemyFacePolicyAuthority` now writes one owner policy event and
current projection in the caller's transaction. It locks actor/owner rows, enforces self-only
desire versus OWNER/ADMIN safety inhibition, derives the activation epoch, checks the expected
generation and operation-bound canonical request hash, and returns the original receipt on exact
replay. Its focused real-PostgreSQL test passed through enable, inhibit, replay/conflict,
stale-generation denial, USER safety denial, self opt-out, and inhibition release. Ruff and mypy
passed. It has no endpoint, step-up, independent audit receipt, sponsor trigger, or Face
publication wiring; the new disposable policy-test Compose project was removed with its volume.
The disabled self-service Face policy JSON Schema now freezes only operation ID, expected
generation, `enabled`, and `scope=NEW_UPLOADS`; it rejects a body-supplied target user,
backfill scope, booleans encoded as integers, and unsafe generations. Its root contract test
passes. The schema remains unwired until authenticated self-only API and audit authority exist.

The 0064 policy history insert now checks a live actor/owner, the actor's actual account role,
the exact self/safety scope, and the current activation epoch under row locks. The initial
projection refuses self-created safety inhibition and admin-created desired enablement. A direct
SQL role-spoof and initial safety-grant regression passed. The exact PostgreSQL catalog inventory
now includes all 0064 tables, indexes, functions and triggers. After this DDL hardening, the
migration/metadata/close-gate/resource-schema/inventory/Face authority run passed 39 tests in
99.60 seconds; Ruff and mypy passed. The exact `autplay-ml-r15-face-authority-20260923`
disposable Compose project and test volume were removed afterward.

A pure final-corpus rater collator now combines the twelve parsed segments for every sealed
track into six raw ordinal matrices, checks distinct recordings and stable pseudonym-to-current
consent-receipt bindings across tracks, and computes per-axis Krippendorff alpha without reducing
ratings to medians first. It returns the segment medians separately and cannot approve a model.
Two synthetic corpus tests passed for complete six-axis reliability and rejection of a missing
track, changed receipt, or absent current grant; Ruff and mypy passed. The operator fixture and
rater authorities remain external and unproven.

The separate read-only OpenGameArt rights audit at
`E:\AutPlay_ML_Research_r15_20260923\oga_rights_audit_20260923` reports 69
hash-verified, fully decoded acquired files from three pools: 56 preliminary
`SOURCE_CANDIDATE`, 10 `RIGHTS_HOLD`, two `EXCLUDED_RIGHTS_CONFLICT`, and one
`SOURCE_CANDIDATE_CONTROL_ONLY`. Its `fixture_approved` count is zero. The audit
requires per-file component rights and exact operator qualification license authority
before any fixture is admitted. This implementation read its final manifest and
unresolved-case report but did not edit the parallel research directory.

An unwired `SqlAlchemyFaceSourceReader` now locks the exact canonical variant,
valid nondeleted AudioVariant and committed Vault object through one caller-owned
transaction. It rejects a mismatched source SHA-256, a stale canonical choice,
a quarantined variant/object, and a recording with a redirect. Its focused real
PostgreSQL test passed for an exact source, wrong hash and later quarantine;
Ruff and mypy passed. This is a metadata snapshot only: source byte verification,
redirect-generation serialization, owner sponsor authorization and publication
fences remain M4 work. The disposable Face-source Compose project and volume were
removed after the test.

The source reader was tightened to match the existing authorized Vault path's
recording-deletion and verified `LOCAL_FILESYSTEM` replica predicates. A focused
real-PostgreSQL regression passed for loss of the available replica as well as
variant quarantine; Ruff and mypy passed. The second disposable source-test
Compose project and volume were removed. On-disk bytes still require the
bounded Vault reader's independent verification at worker execution.

The 0064 activation singleton and immutable interpreter/profile/qualification/approval/activation
evidence now reject direct SQL DELETE, closing a way to erase the active chain. The exact trigger
inventory was updated. Six focused authority/inventory/Alembic tests and two clean/full-adjacent
downgrade/re-upgrade tests passed after this change. Ruff passed, and the exact disposable
`autplay-ml-r15-face-immutable-20260923` Compose project and test volume were removed.

The pure Face sponsorship contract now freezes exact owner/reference/source/lineage,
artifact-policy, policy/activation/redirect generations and one-use sponsor UUIDs.
It links replacement rows and rejects revival of a historical A sponsor after an
A -> B -> A source sequence, even when the first and final source hashes match.
It also rejects cross-owner, stale-license, disabled-reference and invalid authority
inputs. Two focused synthetic tests, Ruff and mypy passed. No SQL sponsor table,
coalesced work, trigger or publication path is wired yet.

The final Face technical evaluator now binds every supplied model observation
to the exact twelve raw-rater reference segments on each sealed track, binds
transition references and blinded voters to the same rater corpus, and runs the
six-axis/transition/preference frozen bootstrap primitives. The corpus collator
now retains per-track transition references and pseudonyms for these checks.
Four focused corpus/evaluator tests passed. A one-off synthetic full invocation
completed with `technical_pass=True`, six axes, and exactly 10,000 draws for
each axis family, transition and preference. That result is only a software
smoke: it used synthetic perfect labels, has no authorized fixture, no three
independent human ratings, no sealed candidate, and no signed report or approval.
Ruff and mypy passed.

A separate technical archive now executes that evaluator and stores the complete
canonical RFC 8785 report: sealed segment/candidate inputs, all 10,000 draws per
metric, fixed bins/seeds, exclusions, runtime versions and source-script hashes.
Its SHA-256 is for external evidence storage only, never a signed approval or
activation. The full synthetic archive test passed in 49.17 seconds with ten source-script hashes;
four focused ancestry/exclusion/source-change tests, Ruff and mypy also passed. Script hashes
are now checked before and after evaluation, rejecting a mid-run code change. An initial canonicalization failure
exposed RFC 8785's JSON integer range for 64-bit PCG64 seeds, so the archive
records those seeds losslessly as decimal strings. No legal authority was gained.

The rater transition converter now accepts the complete Face v2 source-map
sample-rate range through 384 kHz; its prior 192 kHz cap contradicted the
frozen v2 schema. The collator uses the same bound. Five focused rater tests,
Ruff and mypy passed after the correction.

The working tree remains uncommitted and unmerged in
`D:\AutPlayProd\AutPlay-ml-r15-20260923`. Do not infer that the overall Revision 15 plan is
complete or that any source/model has been qualified. The remaining implementation continues
in this isolated task.
