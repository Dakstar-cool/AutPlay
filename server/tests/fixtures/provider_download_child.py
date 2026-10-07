"""Only test launchers select this synthetic loopback HTTP provider."""

import os
from pathlib import Path
from urllib.request import urlopen

from autplay.adapters.filesystem.provider_child import main
from autplay.adapters.filesystem.provider_media import SOURCE_METADATA_FILE


def download(candidate: str, workspace: Path, maximum: int) -> Path:
    del candidate
    source = workspace / "audio.test"
    with (
        urlopen(os.environ["AUTPLAY_TEST_PROVIDER_URL"], timeout=2) as response,
        source.open("xb") as output,
    ):
        size = 0
        while block := response.read(min(32768, maximum + 1 - size)):
            size += len(block)
            output.write(block)
            if size > maximum:
                break
    metadata = os.environ.get("AUTPLAY_TEST_PROVIDER_METADATA")
    if metadata is not None:
        (workspace / SOURCE_METADATA_FILE).write_text(metadata, encoding="utf-8")
    return source


if __name__ == "__main__":
    raise SystemExit(main(download=download))
