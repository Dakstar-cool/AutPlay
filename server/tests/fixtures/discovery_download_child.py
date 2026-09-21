"""Test-only transport injection into the real bounded A1 process."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autplay.adapters.filesystem.discovery_child import main
from discovery_download_support import provider

raise SystemExit(main(provider_factory=lambda: provider(os.environ["AUTPLAY_TEST_DISCOVERY_URL"])))
