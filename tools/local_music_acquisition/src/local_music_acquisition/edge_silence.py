"""Trim long boundary silence from private download staging, keeping encoded audio packets."""

import json
import math
import os
import subprocess
import tempfile
from pathlib import Path
from statistics import median

from mutagen import File as open_audio_tags

MIN_QUIET_SECONDS = 2.0
QUIET_DB = -54
EDGE_PAD = 0.12


def _cut_length(quiet: float, peaks: list[tuple[float, float]]) -> float:
    """Measure from the outside in, treating a sustained rise above the edge floor as music."""
    if quiet < MIN_QUIET_SECONDS:
        return 0.0
    floor = [db for t, db in peaks if t < 0.25]
    if not floor:
        return 0.0
    baseline = median(floor)
    rise_db = min(5.0, max(1.5, (QUIET_DB - baseline) / 2))
    rising_level = max(-90.0, baseline + rise_db)
    rise = None
    for t, db in peaks:
        if t >= quiet:
            break
        if db < rising_level:
            rise = None
        elif rise is None:
            rise = t
        elif t - rise >= 0.10:
            quiet = rise
            break
    return quiet - EDGE_PAD if quiet >= MIN_QUIET_SECONDS else 0.0


def trim_download_edges(path: Path, max_bytes: int) -> bool:
    base = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "1"]
    probe = ["ffprobe", "-v", "error", "-of", "default=nk=1:nw=1", "-show_entries"]
    sample_rate = int(
        subprocess.check_output(
            [*probe, "stream=sample_rate", "-select_streams", "a:0", str(path)],
            text=True,
            timeout=20,
        )
    )
    frame_samples = max(1, round(sample_rate * 0.02))
    args = [
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-filter_threads",
        "1",
        "-af",
        f"asetpts=N/SR/TB,asetnsamples=n={frame_samples}:p=0,"
        f"silencedetect=n={QUIET_DB}dB:d={MIN_QUIET_SECONDS},"
        "astats=metadata=1:reset=1:measure_perchannel=none:measure_overall=Peak_level,"
        "ametadata=mode=print:file=-",
        "-progress",
        "pipe:2",
        "-f",
        "null",
        "-",
    ]
    scan = subprocess.run(base + args, capture_output=True, text=True, timeout=120, check=True)
    start, head = None, 0.0
    peaks: list[tuple[float, float]] = []
    frame_time = 0.0
    for line in scan.stdout.splitlines():
        if line.startswith("frame:"):
            frame_time = float(line.split("pts_time:", 1)[1])
            continue
        key, _, value = line.partition("=")
        if key == "lavfi.astats.Overall.Peak_level":
            peaks.append((frame_time, float(value)))
        if key == "lavfi.silence_start":
            start = float(value)
        if key == "lavfi.silence_end":
            # A decoder's first shortened packet can place the start one sample before zero.
            if start is not None and start <= 0:
                head = float(value)
            start = None
    # The EOF notification goes to the filter log, never to an audio frame's metadata.
    times = [
        int(line[12:])
        for line in scan.stderr.splitlines()
        if line.startswith("out_time_us=") and line[12:].isdigit()
    ]
    if not times:
        return False
    duration = times[-1] / 1_000_000
    if start is not None and start <= 0:
        return False  # An entirely quiet file has no boundary with audible content.
    head = _cut_length(head, peaks)
    tail_cut = 0.0
    if start is not None and duration - start >= MIN_QUIET_SECONDS:
        end = duration
        reverse_peaks = []
        for t, db in reversed(peaks):
            if t < start:
                break
            reverse_peaks.append((duration - end, db))
            end = t
        tail_cut = _cut_length(duration - start, reverse_peaks)
    keep = duration - head - tail_cut
    if (not head and not tail_cut) or keep <= 0:
        return False
    info = json.loads(
        subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-of",
                "json",
                "-show_entries",
                "format=format_name:stream=index:stream_disposition=attached_pic",
                str(path),
            ],
            timeout=20,
        )
    )
    container = info["format"]["format_name"].split(",")[0]
    stream_map = ["-map", "0"]
    pictures: dict[str, list[str]] = {}
    if container == "ogg":
        attached = [s["index"] for s in info["streams"] if s["disposition"]["attached_pic"]]
        if attached:
            # FFmpeg exposes Vorbis picture comments as video packets its Ogg muxer rejects.
            original = open_audio_tags(path)
            if original is None or original.tags is None:
                return False
            pictures = {
                k: original.tags[k]
                for k in ("metadata_block_picture", "coverart", "coverartmime")
                if k in original.tags
            }
            if not (pictures.get("metadata_block_picture") or pictures.get("coverart")):
                return False
            for index in attached:
                stream_map.extend(["-map", f"-0:{index}"])
    muxer = {"mov": "mp4", "matroska": "matroska"}.get(container, container)
    if muxer not in {"mp3", "mp4", "ogg", "wav", "flac", "aac", "matroska", "webm"}:
        raise ValueError("download_trim_format_unsupported")
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".trim")
    os.close(descriptor)
    output = Path(temporary)
    try:
        # Copy compressed packets and artwork. FLAC needs a new STREAMINFO sample count.
        # Input seeking can copy the preceding Ogg page or Matroska cluster in full.
        # Discard packets on the output timeline for these containers.
        seek = ["-ss", str(head)]
        source = ["-i", str(path)]
        args = [
            *(source + seek if muxer in {"ogg", "matroska", "webm"} else seek + source),
            "-t",
            str(keep),
            *stream_map,
            "-c",
            "copy",
            *(["-c:a", "flac", "-threads:a", "1"] if muxer == "flac" else []),
            "-map_metadata",
            "0",
            "-f",
            "adts" if muxer == "aac" else muxer,
            "-fs",
            str(max_bytes + 1),
            "-y",
            str(output),
        ]
        subprocess.run(base + args, capture_output=True, timeout=120, check=True)
        if pictures:
            result = open_audio_tags(output)
            if result is None or result.tags is None:
                return False
            for key, values in pictures.items():
                result.tags[key] = values
            result.save()
        measured = float(
            subprocess.check_output([*probe, "format=duration", str(output)], text=True, timeout=20)
        )
        if (
            not math.isfinite(measured)
            or abs(measured - keep) > 0.15
            or not 0 < output.stat().st_size <= max_bytes
        ):
            raise ValueError("download_trim_output_invalid")
        os.replace(output, path)
        return True
    finally:
        output.unlink(missing_ok=True)
