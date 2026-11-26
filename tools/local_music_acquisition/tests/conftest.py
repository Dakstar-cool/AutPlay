import io
import shutil
import wave

import pytest
from test_xray import runtime as runtime


@pytest.fixture
def audio() -> bytes:
    if shutil.which("ffmpeg") is None:
        pytest.fail("ffmpeg is required for real queue audio validation")
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * 96000)
    return output.getvalue()
