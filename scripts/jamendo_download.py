"""Compatibility alias for the portable local music acquisition module."""

from __future__ import annotations

import sys
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[1] / "tools" / "local_music_acquisition" / "src"
sys.path.insert(0, str(_SOURCE))
try:
    from local_music_acquisition.providers import jamendo as _implementation
finally:
    sys.path.remove(str(_SOURCE))

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

sys.modules[__name__] = _implementation
