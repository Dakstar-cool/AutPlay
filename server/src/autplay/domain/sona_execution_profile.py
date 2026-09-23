"""Frozen identity of one isolated Sona inference execution environment.

The profile is evidence, not a capability to run a model. Every execution detail
which may change model output is part of its domain-separated digest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Final, cast

import rfc8785

from autplay.domain.recommendations import JsonValue

SONA_EXECUTION_PROFILE_DOMAIN: Final = b"autplay.sona.execution-profile.v1\0"
_HEX = frozenset("0123456789abcdef")
_TEXT_FIELDS = frozenset(
    {
        "source_commit",
        "python_version",
        "model_runtime_version",
        "onnxruntime_version",
        "cuda_version",
        "cudnn_version",
        "nvidia_driver_version",
        "gpu_device_uuid",
        "gpu_device_model",
        "compute_capability",
        "execution_provider",
        "graph_optimization_level",
        "precision",
    }
)
_DIGEST_FIELDS = frozenset(
    {"tokenizer_adapter_sha256", "runtime_adapter_sha256", "postprocessor_sha256"}
)
_ALL_FIELDS = (
    _TEXT_FIELDS
    | _DIGEST_FIELDS
    | {
        "schema_version",
        "kind",
        "gpu_image_digest",
        "provider_options",
        "graph_optimization_options",
        "tensor_schemas",
        "determinism",
        "thread_limits",
        "cpu_fallback_allowed",
    }
)


@dataclass(frozen=True, slots=True)
class SonaExecutionProfileV1:
    canonical_bytes: bytes
    profile_sha256: str

    @property
    def document(self) -> dict[str, JsonValue]:
        """Return a copy; a caller cannot mutate the hashed identity."""

        return cast(dict[str, JsonValue], json.loads(self.canonical_bytes))


def freeze_sona_execution_profile(document: dict[str, JsonValue]) -> SonaExecutionProfileV1:
    """Reject omitted/ambiguous identity fields and hash exact JCS bytes."""

    if set(document) != _ALL_FIELDS or document.get("schema_version") != 1:
        raise ValueError("Sona execution profile fields or schema version are invalid")
    if document.get("kind") != "SONA_EXECUTION_PROFILE_V1":
        raise ValueError("Sona execution profile kind is invalid")
    for name in _TEXT_FIELDS:
        _text(document[name], name)
    for name in _DIGEST_FIELDS:
        _digest(document[name], name)
    image = document["gpu_image_digest"]
    if not isinstance(image, str) or not image.startswith("sha256:"):
        raise ValueError("Sona execution profile image digest is invalid")
    _digest(image.removeprefix("sha256:"), "gpu_image_digest")
    commit = cast(str, document["source_commit"])
    if len(commit) not in (40, 64) or any(char not in _HEX for char in commit):
        raise ValueError("Sona execution profile source commit is invalid")
    if document["execution_provider"] != "CUDAExecutionProvider":
        raise ValueError("Sona execution profile requires CUDA execution")
    if document["precision"] not in ("FP32", "FP16", "BF16"):
        raise ValueError("Sona execution profile precision is invalid")
    if document["cpu_fallback_allowed"] is not False:
        raise ValueError("Sona execution profile must forbid CPU fallback")
    _options(document["provider_options"], "provider_options")
    _options(document["graph_optimization_options"], "graph_optimization_options")
    _tensor_schemas(document["tensor_schemas"])
    _determinism(document["determinism"])
    _thread_limits(document["thread_limits"])
    try:
        canonical = rfc8785.dumps(document)
    except (rfc8785.CanonicalizationError, TypeError, ValueError) as error:
        raise ValueError("Sona execution profile cannot be canonicalized") from error
    return SonaExecutionProfileV1(
        canonical_bytes=canonical,
        profile_sha256=sha256(SONA_EXECUTION_PROFILE_DOMAIN + canonical).hexdigest(),
    )


def _text(value: JsonValue, name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or value.strip() != value:
        raise ValueError(f"Sona execution profile {name} is invalid")
    return value


def _digest(value: JsonValue, name: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(char not in _HEX for char in value):
        raise ValueError(f"Sona execution profile {name} is invalid")


def _options(value: JsonValue, name: str) -> None:
    if not isinstance(value, dict) or not value or len(value) > 32:
        raise ValueError(f"Sona execution profile {name} is invalid")
    for key, option in value.items():
        _text(key, name)
        if isinstance(option, str):
            _text(option, name)
        elif type(option) is bool or (type(option) is int and 0 <= option <= 2**31 - 1):
            continue
        else:
            raise ValueError(f"Sona execution profile {name} option is invalid")


def _tensor_schemas(value: JsonValue) -> None:
    if not isinstance(value, dict) or set(value) != {"inputs", "outputs"}:
        raise ValueError("Sona execution profile tensor schemas are invalid")
    for direction in ("inputs", "outputs"):
        tensors = value[direction]
        if not isinstance(tensors, list) or not 1 <= len(tensors) <= 64:
            raise ValueError("Sona execution profile tensor schemas are invalid")
        names: set[str] = set()
        for tensor in tensors:
            if not isinstance(tensor, dict) or set(tensor) != {"name", "dtype", "shape"}:
                raise ValueError("Sona execution profile tensor schema is invalid")
            name = _text(tensor["name"], "tensor name")
            _text(tensor["dtype"], "tensor dtype")
            shape = tensor["shape"]
            if name in names or not isinstance(shape, list) or len(shape) > 8:
                raise ValueError("Sona execution profile tensor shape is invalid")
            names.add(name)
            for dimension in shape:
                if type(dimension) is int and 1 <= dimension <= 2**31 - 1:
                    continue
                if isinstance(dimension, str):
                    _text(dimension, "tensor dimension")
                    continue
                raise ValueError("Sona execution profile tensor dimension is invalid")


def _determinism(value: JsonValue) -> None:
    if not isinstance(value, dict) or set(value) != {"enabled", "seed", "flags"}:
        raise ValueError("Sona execution profile determinism is invalid")
    seed, flags = value["seed"], value["flags"]
    if value["enabled"] is not True or type(seed) is not int or not 0 <= seed < 2**63:
        raise ValueError("Sona execution profile determinism is invalid")
    if not isinstance(flags, list) or len(flags) > 32:
        raise ValueError("Sona execution profile determinism flags are invalid")
    for flag in flags:
        _text(flag, "determinism flag")
    if flags != sorted(set(cast(list[str], flags))):
        raise ValueError("Sona execution profile determinism flags are not canonical")


def _thread_limits(value: JsonValue) -> None:
    if not isinstance(value, dict) or set(value) != {
        "intra_op",
        "inter_op",
        "max_concurrent_requests",
    }:
        raise ValueError("Sona execution profile thread limits are invalid")
    if any(type(count) is not int or not 1 <= count <= 1024 for count in value.values()):
        raise ValueError("Sona execution profile thread limits are invalid")
