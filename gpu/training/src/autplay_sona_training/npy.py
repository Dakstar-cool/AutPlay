"""Pre-allocation validation for canonical pickle-free NumPy payloads."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import numpy.lib.format as npy_format

SONA_MAX_NUMPY_HEADER_BYTES = 4_096


def validate_canonical_npy(
    path: Path,
    *,
    expected_shape: tuple[int, ...],
    expected_dtype: np.dtype[np.generic],
) -> None:
    """Validate the embedded v1 header and exact payload size before ``np.load``."""

    read_canonical_npy(
        path,
        expected_shape=expected_shape,
        expected_dtype=expected_dtype,
    )


def read_canonical_npy(
    path: Path,
    *,
    expected_shape: tuple[int, ...],
    expected_dtype: np.dtype[np.generic],
) -> bytes:
    """Read and validate one bounded byte snapshot of a canonical NumPy payload."""

    canonical_dtype = np.dtype(expected_dtype)
    expected_payload_bytes = int(np.prod(expected_shape, dtype=np.int64)) * canonical_dtype.itemsize
    maximum_file_bytes = SONA_MAX_NUMPY_HEADER_BYTES + expected_payload_bytes
    try:
        with path.open("rb") as stream:
            payload = stream.read(maximum_file_bytes + 1)
    except OSError as error:
        raise ValueError("Sona NumPy payload cannot be read") from error
    if len(payload) > maximum_file_bytes:
        raise ValueError("Sona NumPy payload size exceeds its canonical bound")
    validate_canonical_npy_bytes(
        payload,
        expected_shape=expected_shape,
        expected_dtype=canonical_dtype,
    )
    return payload


def validate_canonical_npy_bytes(
    payload: bytes,
    *,
    expected_shape: tuple[int, ...],
    expected_dtype: np.dtype[np.generic],
) -> None:
    """Validate a byte snapshot without reopening its source path."""

    try:
        stream = BytesIO(payload)
        version = npy_format.read_magic(stream)
        if version != (1, 0):
            raise ValueError("Sona NumPy payload uses a noncanonical format version")
        shape, fortran_order, dtype = npy_format.read_array_header_1_0(
            stream,
            max_header_size=SONA_MAX_NUMPY_HEADER_BYTES,
        )
        data_offset = stream.tell()
    except (EOFError, OSError, ValueError) as error:
        raise ValueError("Sona NumPy payload header is invalid") from error
    canonical_dtype = np.dtype(expected_dtype)
    if (
        tuple(shape) != expected_shape
        or np.dtype(dtype) != canonical_dtype
        or fortran_order
        or data_offset > SONA_MAX_NUMPY_HEADER_BYTES
    ):
        raise ValueError("Sona NumPy payload header escapes canonical shape or dtype")
    expected_payload_bytes = int(np.prod(expected_shape, dtype=np.int64)) * canonical_dtype.itemsize
    if len(payload) != data_offset + expected_payload_bytes:
        raise ValueError("Sona NumPy payload size does not match its canonical header")


__all__ = (
    "SONA_MAX_NUMPY_HEADER_BYTES",
    "read_canonical_npy",
    "validate_canonical_npy",
    "validate_canonical_npy_bytes",
)
