"""Real metadata child with a deterministic, paced, network-free provider transport."""

import json
import os
import sys
from pathlib import Path
from time import sleep
from urllib.parse import parse_qs

from autplay.adapters.filesystem import metadata_child
from autplay.adapters.public_track_metadata import PublicMetadataHttp


class FixtureHttp(PublicMetadataHttp):
    def get(self, url: str, *, artwork: bool = False, post: bytes | None = None) -> bytes | None:
        del artwork
        with self.gate():
            marker = os.environ.get("AUTPLAY_TEST_METADATA_BLOCK")
            if marker:
                Path(marker).write_text("request running", encoding="ascii")
                sleep(30)
            if "recording?" in url:
                return json.dumps({"recordings": []}).encode()
            if url == "https://api.acoustid.org/v2/lookup":
                assert post is not None
                fields = parse_qs(post.decode("ascii"))
                assert fields["client"] == ["fixture-key"] and fields["fingerprint"][0]
                assert fields["duration"] == ["12"]
                return json.dumps({"status": "ok", "results": []}).encode()
            raise AssertionError("unexpected fixture request")


vars(metadata_child)["PublicMetadataHttp"] = FixtureHttp
sys.exit(metadata_child.main())
