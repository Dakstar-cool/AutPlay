"""Small protocol child used to exercise supervisor races without importing Torch."""

from __future__ import annotations

import json
import os
import sys
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import rfc8785
from autplay.adapters.filesystem.vault_child import (
    decode_document,
    encode_document,
    read_frame,
    write_frame,
)
from autplay.domain.recommendations import JsonValue


def _send(destination: object, tag: bytes, document: dict[str, object]) -> None:
    write_frame(destination, tag, encode_document(document))  # type: ignore[arg-type]


def _acknowledged(source: object) -> dict[str, object]:
    tag, payload = read_frame(source)  # type: ignore[arg-type]
    if tag != b"K":
        raise RuntimeError("fixture authority acknowledgement missing")
    return decode_document(payload)


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) == 2 else "success"
    source, destination = sys.stdin.buffer, sys.stdout.buffer
    hello = encode_document({"pid": os.getpid(), "nonce": uuid4().hex})
    write_frame(destination, b"H", hello)
    tag, payload = read_frame(source)
    if tag != b"G":
        return 3
    command = decode_document(payload)
    if mode == "early-result":
        _send(
            destination,
            b"R",
            {
                "version": 1,
                "checkpoint_manifest_sha256": "c" * 64,
                "weights_sha256": "d" * 64,
                "dataset_manifest_sha256": "e" * 64,
                "optimizer_steps": 1,
                "device_type": "cpu",
            },
        )
        return 0
    input_root = Path(str(command["input_root"]))
    relative = Path(str(command["dataset_relative"]))
    envelope = json.loads((input_root / relative / "manifest.json").read_bytes())
    manifest = envelope["manifest"]
    dataset_sha256 = str(envelope["manifest_sha256"])
    _send(
        destination,
        b"A",
        {
            "version": 1,
            "source_sha256": manifest["source_manifest_sha256"],
            "dataset_sha256": dataset_sha256,
            "lineage_key_id": manifest["owner_lineage_key_id"],
            "owner_tokens": manifest["owner_lineage_tokens"],
        },
    )
    provenance = _acknowledged(source)["provenance"]
    weight = b"fixture-weight"
    weight_sha256 = sha256(weight).hexdigest()
    aggregate = sha256()
    aggregate.update(b"fixture.weight")
    aggregate.update(bytes.fromhex(weight_sha256))
    checkpoint_document: dict[str, JsonValue] = {
        "weights_sha256": aggregate.hexdigest(),
        "optimizer_steps": 1,
        "training_device_type": "cpu",
        "weights": [
            {
                "name": "fixture.weight",
                "file": "0000.npy",
                "sha256": weight_sha256,
                "dtype": "uint8",
                "shape": [len(weight)],
            }
        ],
    }
    checkpoint_sha256 = sha256(rfc8785.dumps(checkpoint_document)).hexdigest()
    staging = Path(str(command["output_root"])) / f".{command['checkpoint_name']}.staging"
    final = Path(str(command["output_root"])) / str(command["checkpoint_name"])
    (staging / "manifest.json").write_bytes(
        rfc8785.dumps({"manifest": checkpoint_document, "manifest_sha256": checkpoint_sha256})
    )
    (staging / "weights").mkdir()
    (staging / "weights" / "0000.npy").write_bytes(weight)
    _send(
        destination,
        b"S",
        {
            "version": 1,
            "provenance": provenance,
            "manifest_sha256": checkpoint_sha256,
        },
    )
    _acknowledged(source)
    os.replace(staging, final)
    if mode == "tampered-weight":
        (final / "weights" / "0000.npy").write_bytes(b"tampered-weight")
    if mode == "mismatched-result":
        checkpoint_sha256 = sha256(b"not-the-sealed-checkpoint").hexdigest()
    _send(
        destination,
        b"R",
        {
            "version": 1,
            "checkpoint_manifest_sha256": checkpoint_sha256,
            "weights_sha256": aggregate.hexdigest(),
            "dataset_manifest_sha256": dataset_sha256,
            "optimizer_steps": 1,
            "device_type": "cpu",
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
