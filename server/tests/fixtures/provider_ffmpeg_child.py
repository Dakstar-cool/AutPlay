"""Synthetic loopback media selection inside the real provider tree and writer."""

import os
import sys
from pathlib import Path

from autplay.adapters.child_process import provider_child_launch
from autplay.adapters.filesystem import provider_child
from autplay.adapters.filesystem.provider_media import options, stream_format, write_source_metadata


def launch_media(candidate: str) -> list[str]:
    arguments, _ = provider_child_launch()
    return [
        arguments[0],
        "-I",
        str(Path(__file__).absolute()),
        "media",
        os.environ["AUTPLAY_TEST_MEDIA_URL"],
        os.environ["AUTPLAY_TEST_MEDIA_PROTOCOL"],
        os.environ["AUTPLAY_TEST_MEDIA_CODEC"],
        *([candidate] if os.environ.get("AUTPLAY_TEST_MEDIA_NATIVE") == "1" else []),
    ]


if __name__ == "__main__":
    if len(sys.argv) > 1:
        import importlib

        with importlib.import_module("yt_dlp").YoutubeDL(options()) as ydl:
            stream_format(
                ydl,
                {
                    "url": sys.argv[2],
                    "protocol": sys.argv[3],
                    "acodec": sys.argv[4],
                    "ext": "m4a" if sys.argv[4] == "aac" else "webm",
                    "http_headers": {},
                    "is_live": False,
                },
            )
            if len(sys.argv) > 5:
                write_source_metadata(
                    {
                        "id": sys.argv[5],
                        "track": "Song (Live)",
                        "artist": "Artist",
                        "album": "Native Album",
                        "release_year": 2020,
                    },
                    sys.argv[5],
                )
    else:
        provider_child.youtube_arguments = launch_media
        raise SystemExit(provider_child.main())
