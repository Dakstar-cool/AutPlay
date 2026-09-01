"""Create Webwright evidence against the local, non-copyrighted browser fixture."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import final_script


def main() -> None:
    tests_path = Path(__file__).parent / "tests" / "test_final_script.py"
    spec = importlib.util.spec_from_file_location("hitmo_fixture", tests_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("fixture_import_failed")
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    with fixture._fixture_site(exact_position=2) as start_url:
        final_script._START_URL = start_url
        result = final_script.download_hitmo_tracks(
            timeout_seconds=15,
            evidence_screenshots=True,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
