import base64
import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest
from autplay.adapters.media import edge_silence
from mutagen import File as open_audio_tags

RATE = 16000
TOOLS = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required"
)


def test_operator_downloads_use_the_same_trim() -> None:
    operator = (
        Path(__file__).resolve().parents[2]
        / "tools/local_music_acquisition/src/local_music_acquisition/edge_silence.py"
    )
    assert operator.read_text() == Path(edge_silence.__file__).read_text()


@TOOLS
@pytest.mark.parametrize(
    "quiet,fade,trim",
    [(2.4, 0.0, True), (2.0, 0.0, True), (1.85, 0.0, False), (2.4, 1.0, True), (0.6, 2.5, False)],
)
def test_boundaries_keep_fades_and_interior_pause(
    tmp_path: Path, quiet: float, fade: float, trim: bool
) -> None:
    # A quiet hum at -57 dBFS, followed by a tone emerging from that noise floor.
    def samples(seconds: float, start_db: float, end_db: float) -> list[bytes]:
        count = round(seconds * RATE)
        return [
            struct.pack(
                "<h",
                round(
                    32767
                    * 10 ** ((start_db + (end_db - start_db) * i / count) / 20)
                    * math.sin(2 * math.pi * 180 * i / RATE)
                ),
            )
            for i in range(count)
        ]

    flank = samples(quiet, -57, -57) + samples(fade, -57, -38)
    music = samples(0.4, -20, -20)
    center = music + samples(2.5, -100, -100) + music
    source = flank + center + list(reversed(flank))
    path = tmp_path / "example.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, RATE, 0, "NONE", "not compressed"))
        wav.writeframes(b"".join(source))
    before = path.read_bytes()
    assert edge_silence.trim_download_edges(path, path.stat().st_size + 65536) is trim
    if not trim:
        assert path.read_bytes() == before
        return
    with wave.open(str(path), "rb") as reader:
        audio = reader.readframes(reader.getnframes())
    assert b"".join(center) in audio
    retained_flank = (len(audio) / (2 * RATE) - len(center) / RATE) / 2
    assert fade - 0.08 <= retained_flank <= fade + 0.24


@TOOLS
def test_quiet_fade_and_sound_on_either_channel(tmp_path: Path) -> None:
    path = tmp_path / "stereo.wav"
    # Silent left channel, two-second rise from -88 to -55 dB in the right channel.
    blocks = []
    for i in range(4 * RATE):
        t = i / RATE
        db = -100 if t < 0.6 else min(-35, -88 + 16.5 * (t - 0.6))
        # 24-bit samples preserve the lowest part of the envelope.
        sample = round((2**23 - 1) * 10 ** (db / 20) * math.sin(2 * math.pi * 280 * t))
        blocks.append(bytes(3) + sample.to_bytes(3, "little", signed=True))
    with wave.open(str(path), "wb") as wav:
        wav.setparams((2, 3, RATE, 0, "NONE", "not compressed"))
        wav.writeframes(b"".join(blocks + blocks[::-1]))
    before = path.read_bytes()
    assert not edge_silence.trim_download_edges(path, len(before) + 65536)
    assert before == path.read_bytes()


@TOOLS
@pytest.mark.parametrize(("extension", "codec"), [("ogg", "libvorbis"), ("webm", "libopus")])
def test_packet_copy_trims_actual_audio_and_keeps_vorbis_art(
    tmp_path: Path, extension: str, codec: str
) -> None:
    path = tmp_path / ("music." + extension)
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-af",
            "adelay=2600:all=1,apad=pad_dur=2.3",
            "-c:a",
            codec,
            str(path),
        ],
        check=True,
    )
    if extension == "ogg":
        # A FLAC picture block as stored in an Ogg Vorbis comment.
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
            "x8AAwMCAO+jD9sAAAAASUVORK5CYII="
        )
        picture = (
            struct.pack(">II", 3, 9)
            + b"image/png"
            + struct.pack(">6I", 0, 1, 1, 8, 0, len(png))
            + png
        )
        encoded = base64.b64encode(picture).decode("ascii")
        original = open_audio_tags(path)
        assert original is not None and original.tags is not None
        original.tags["metadata_block_picture"] = [encoded]
        original.save()
    assert edge_silence.trim_download_edges(path, 1000000)
    pcm = subprocess.check_output(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-f",
            "s16le",
            "-ac",
            "1",
            "-ar",
            str(RATE),
            "-",
        ]
    )
    samples = struct.iter_unpack("<h", pcm)
    audible = [i for i, (value,) in enumerate(samples) if abs(value) > 300]
    assert 0.05 < audible[0] / RATE < 0.20
    assert (audible[-1] - audible[0]) / RATE == pytest.approx(1.0, abs=0.025)
    # Opus leaves a low-level decay after the tone. Preserve it with the edge padding.
    assert 1.15 < len(pcm) / (2 * RATE) < 1.5
    if extension == "ogg":
        result = open_audio_tags(path)
        assert result is not None and result.tags is not None
        assert result.tags["metadata_block_picture"] == [encoded]
