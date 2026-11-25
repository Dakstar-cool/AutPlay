"""Trim long boundary silence from private download staging, keeping encoded audio packets."""

import math
import os
import subprocess
import tempfile
from pathlib import Path


def trim_download_edges(path: Path, max_bytes: int) -> bool:
    base = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "1"]
    args = [
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-filter_threads",
        "1",
        "-af",
        "asetpts=N/SR/TB,silencedetect=n=-60dB:d=3,ametadata=mode=print:file=-",
        "-progress",
        "pipe:2",
        "-f",
        "null",
        "-",
    ]
    scan = subprocess.run(base + args, capture_output=True, text=True, timeout=120, check=True)
    start, head, tail = None, 0.0, None
    for line in scan.stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "lavfi.silence_start":
            start = float(value)
        if key == "lavfi.silence_end":
            # A decoder's first shortened packet can place the start one sample before zero.
            if start is not None and start <= 0 and float(value) > 3:
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
    if start is not None and duration - start > 3:
        tail = start
    keep = (duration if tail is None else tail) - head
    if (not head and tail is None) or keep <= 0:
        return False
    probe = ["ffprobe", "-v", "error", "-of", "default=nk=1:nw=1", "-show_entries"]
    container = (
        subprocess.check_output([*probe, "format=format_name", str(path)], text=True, timeout=20)
        .strip()
        .split(",")[0]
    )
    muxer = {"mov": "mp4", "matroska": "matroska"}.get(container, container)
    if muxer not in {"mp3", "mp4", "ogg", "wav", "flac", "aac", "matroska", "webm"}:
        raise ValueError("download_trim_format_unsupported")
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".trim")
    os.close(descriptor)
    output = Path(temporary)
    try:
        # Copy compressed packets and artwork. FLAC needs a new STREAMINFO sample count.
        args = [
            "-ss",
            str(head),
            "-i",
            str(path),
            "-t",
            str(keep),
            "-map",
            "0",
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
