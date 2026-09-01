"""Compatibility launcher for the Hitmo-only CDP contour."""

from __future__ import annotations

import sys
from pathlib import Path

_SOURCE = Path(__file__).resolve().parent / "src"
sys.path.insert(0, str(_SOURCE))
try:
    from local_music_acquisition.providers import hitmo as _implementation
finally:
    sys.path.remove(str(_SOURCE))

if __name__ == "__main__":
    raise SystemExit(_implementation._main())

sys.modules[__name__] = _implementation
