# Playlist acquisition and real-audio Face/ML checks

The user's existing playlist contains 4,591 unique entries. Initial server reconciliation found
91 MP3 files with 90 distinct full SHA-256 hashes, corresponding to 90 previous successful
playlist receipts. The remaining 4,501 entries were queued in 181 batches of at most 25 tracks.
The acquisition status and observation time are retained in
[VALIDATION.json](evidence/playlist-face-ml-2026-09-11/VALIDATION.json).

Each completed batch has a private receipt and checkpoint. Completed batches are skipped on
resume; an interrupted batch without a complete receipt requires reconciliation before retry,
so uncertain partial effects cannot silently create duplicate downloads. The queue uses one
process lock, bounded container resources, per-batch deadlines and pauses between batches.
The persistent user service is `autplay-acquire-playlist`; its active/enabled state is recorded
in the evidence. User lingering keeps the service independent of the SSH session. The original
playlist, protected credentials and completed audio files were preserved.

Jamendo and Hitmo remain available for exact-match acquisition. Yandex is unavailable without
its protected OAuth token. Two yt-dlp failures triggered additional diagnosis: a YouTube IPv4
HEAD request timed out after eight seconds, and IPv6 failed immediately. The image has yt-dlp
2026.8.19 and EJS 0.8.0, but Node 20.19.2 is below the officially supported minimum Node 22.
[Official EJS requirements](https://github.com/yt-dlp/yt-dlp/wiki/EJS).

Five recovery options were considered:

| Option | Evidence and decision |
| --- | --- |
| Force IPv4 | IPv4 itself timed out; this cannot resolve the observed failure alone. |
| Tune socket timeouts and retry pacing | Existing socket and process limits already bound requests; batch pacing is retained. |
| Install a compatible Node/EJS runtime | Required for a later YouTube qualification; it cannot restore failed network connectivity. |
| Update the pinned extractor | Consider after connectivity is restored and an extractor-specific failure is demonstrated. |
| Defer the unhealthy contour and continue other sources | Applied at a completed-batch boundary; sequential workers reduce contention. |

The network and retry options are documented in the
[yt-dlp README](https://github.com/yt-dlp/yt-dlp/blob/master/README.md).
Misses remain explicit in receipts. Queueing does not establish that all 4,501 tracks have been
downloaded. No proxy, cookie import, CAPTCHA/DRM bypass, or weakening of the container sandbox
was introduced.

The test sample uses **24 tracks from 24 artist groups**, selected deterministically from the
90 initially available sources: eight per duration tercile, distinct artists preferred, SHA-256
as a tie-breaker. Selected durations range from 129.600 to 365.957 seconds. All 90 files passed
the initial duration probe. Each selected source was completely decoded with FFmpeg 8.1.2, with
its full hash checked before and after decoding. Three 12-second fragments at the beginning,
middle and end yield **72 clips / 864 seconds** of mono 16 kHz float32 audio.

The private source manifest binds exact source hashes, durations and filenames. Its digest,
unique source identities, clip offsets, sample counts and PCM digests are checked before model
inference. Raw music, titles, filenames and per-clip predictions remain private on the server;
the repository contains aggregate evidence and hashes. The sample describes the available
downloads and is not representative coverage of the entire playlist.

| Frozen CPU baseline | Clips | Inference time | Peak process RSS | Repeat checks |
| --- | ---: | ---: | ---: | --- |
| Musicnn → DEAM | 72/72 | 52.414 s | 362.61 MiB | 3/3 identical tensors |
| Discogs-EffNet → Jamendo mood/theme | 72/72 | 138.047 s | 762.81 MiB | 3/3 identical tensors |

Both runs produced finite, correctly shaped embeddings and predictions. All 24 pooled track
vectors were distinct in each baseline. Heads consumed raw window embeddings; mean/L2 pooling
was used only for cosine-neighbor diagnostics. DEAM produced **11 values outside its nominal
1-9 range**, with an observed maximum of 9.5658. These values are retained without clipping.
Jamendo outputs stayed within 0-1. Neither range validity nor deterministic output establishes
emotion accuracy or calibrated confidence.

Timings cover measured inference after a separate warmup, in isolated containers limited to
two CPUs, with acquisition running concurrently. These are technical observations, not capacity
benchmarks. The server has an RTX 3060, but these runs used CPU TensorFlow; production ONNX/CUDA
inference, GPU performance and Sona training were not qualified.

The real `RecommendationPipelineRunner` also passed **54 context/seed/limit scenarios** on an
in-memory snapshot of the selected recordings and five synthetic policy exclusion checks.
Playlist membership remained neutral: it was not converted to Like or listening history.
Testing exposed duplicate Recording inputs doubling ranking contributions. The domain snapshot
now rejects duplicate Recording IDs before generation and scoring, including conflicting ACLs.
Valid unique snapshots retain their existing ranking behavior and versions; production SQL
already selects distinct Recording IDs. The fix was exercised in the isolated server runtime.

**90 focused tests passed**, including Face contracts, source/clip tamper cases, recommendation
and adaptive/Sona seams, API behavior and duplicate-snapshot regressions. Ruff, formatting and
affected diff checks passed. Bounded read-only review approved the experiment and the domain fix.
Source hashes and the exact inference image identity are included in the validation artifact.

No application model activation, production database writes, training, public deployment,
commit or push occurred. A calibrated Face interpreter still needs recording-level labels,
held-out evaluation and an approved mapping of model outputs to Face axes. Disjoint clips are
not published as a continuous timeline, and extractor row indices are not assigned invented
window timestamps.
