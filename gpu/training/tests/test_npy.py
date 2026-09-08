"""NumPy payloads are bounded by their embedded header before allocation."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from autplay_sona_training.npy import validate_canonical_npy


def test_canonical_npy_validator_rejects_forged_embedded_shape_at_original_size(
    tmp_path: Path,
) -> None:
    path = tmp_path / "forged.npy"
    with path.open("wb") as stream:
        np.save(stream, np.asarray((1.0,), dtype=np.float32), allow_pickle=False)
    payload = bytearray(path.read_bytes())
    header_length = int.from_bytes(payload[8:10], "little")
    header_start = 10
    header = payload[header_start : header_start + header_length].decode("latin1")
    core = header.rstrip(" \n").replace("(1,)", "(10000000,)")
    forged_header = core + " " * (header_length - len(core) - 1) + "\n"
    payload[header_start : header_start + header_length] = forged_header.encode("latin1")
    path.write_bytes(payload)

    with pytest.raises(ValueError, match="header escapes canonical shape"):
        validate_canonical_npy(
            path,
            expected_shape=(1,),
            expected_dtype=np.dtype(np.float32),
        )
