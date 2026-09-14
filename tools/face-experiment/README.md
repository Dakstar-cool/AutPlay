# Private Face reference experiment

This isolated Linux x86_64 tool evaluates technical compatibility of frozen Musicnn + DEAM and
Discogs-EffNet + MTG-Jamendo mood/theme heads. It does not train, produce a calibrated Face
interpreter, publish timelines, backfill a library or alter the CPU/GPU application environments.

Dependencies are pinned in `uv.lock`, including the official CPython 3.14 Essentia TensorFlow wheel.
The Docker base is digest pinned. `libatomic1=14.2.0-19` supplies the only missing native shared
library in that base. Model/card/license hashes are fixed in `artifacts.lock.json`; downloaded
weights belong outside Git. The artifact manifest is verified before inference.

Models come from [Essentia](https://essentia.upf.edu/models/). The downloaded repository README and
LICENSE disagree between CC BY-NC-SA 4.0 and CC BY-NC-ND 4.0 (the LICENSE itself has inconsistent
wording). This experiment uses original frozen weights privately and noncommercially; it is not
permission to distribute weights, adaptations or a commercial bundle. Essentia TensorFlow declares
AGPL-3.0-only. Dataset downloads and training are not part of this tool.

From the repository root in PowerShell:

```powershell
./tools/face-experiment/fetch.ps1 -Destination .codex-state/face-reference -PersonalNoncommercial
docker build -t autplay-face-reference:local tools/face-experiment
New-Item -ItemType Directory -Force .codex-state/face-reference/output
$models = (Resolve-Path .codex-state/face-reference).Path
$output = (Resolve-Path .codex-state/face-reference/output).Path
docker run --rm --cpus 2 --memory 4g --memory-swap 4g --network none --read-only --pids-limit 128 --tmpfs /tmp:rw,nosuid,size=64m --mount "type=bind,source=$models,target=/models,readonly" --mount "type=bind,source=$output,target=/output" autplay-face-reference:local --baseline musicnn-deam
docker run --rm --cpus 2 --memory 4g --memory-swap 4g --network none --read-only --pids-limit 128 --tmpfs /tmp:rw,nosuid,size=64m --mount "type=bind,source=$models,target=/models,readonly" --mount "type=bind,source=$output,target=/output" autplay-face-reference:local --baseline effnet-jamendo
```

Run each baseline in a separate process. There are three deterministic 12-second 16 kHz float PCM
fixtures (silence, tone, pulsed chord); raw segment outputs, dimensions, elapsed time, real-time
factor, process peak RSS and runtime/model identities are retained. The first inference is cold;
these few samples are not a performance benchmark. Scores are not calibrated probabilities.
The result explicitly reports musical quality and target GPU measurements as unavailable.

The heads consume compatible per-segment embeddings (200 or 1280 values). Do not feed the existing
mean/L2-pooled retrieval representation into them. Use raw outputs to design a separate controlled
music evaluation, with held-out recordings, predetermined axes and abstention/calibration criteria.
Only a qualified interpreter can feed the later Face Timeline/Operations milestone.

`prepare_audio.py` also accepts a private acquisition inventory containing `row`, `file_name`,
`sha256`, `size_bytes`, and `playlist_index`. Run it with an offline FFmpeg 8.1.2 container, a
read-only music mount, and an empty output directory. It deduplicates source hashes, selects eight
tracks from each duration tercile (preferring different artists), verifies full source hashes,
decodes every selected file completely, and writes three 12-second mono 16 kHz float32 clips per
track. Source files, titles, and `sources.private.json` remain private and outside Git.

Run `/opt/face/evaluate_audio.py --baseline musicnn-deam` or `--baseline effnet-jamendo` with the
same limits above, using `/opt/face/.venv/bin/python` as the entrypoint and the prepared directory
mounted read-only at `/clips`. The source manifest, clip identities, offsets, byte sizes, PCM
hashes and finite values are checked before use. Each baseline runs separately with an actual
audio warmup and three repeated clips. Reports retain raw prediction rows and clip start offsets;
they do not assign unverified timestamps to extractor windows or join disjoint clips into a timeline.

Track-level mean/L2 embeddings are used only for cosine diagnostics. Neighbor scores, output
spread, repeatability and timings are technical evidence, not similarity or emotion accuracy.
DEAM outputs outside its nominal 1-9 scale remain visible in the report rather than being clipped.

`check_recommendations.py --clips ... --output ...` runs under the locked **server** environment,
with its source package available. It builds an in-memory sample snapshot with neutral preferences
and no invented listening history, then checks 54 context/seed/limit scenarios, five synthetic
policy exclusions, and rejection of duplicate Recording entries. It does not write a database,
train Sona, activate models, or qualify the production ONNX/CUDA worker.

Focused provenance validation from the repository root:

```powershell
uv run --project server --locked python -m pytest tools/face-experiment/test_audio_inputs.py -q
```

The real playlist run and its boundaries are recorded in
`docs/release/PLAYLIST_FACE_ML_2026-09-11.md`.
