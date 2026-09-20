"""Current grant binding and real CPU batch/checkpoint refusal boundaries."""

import argparse
import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast
from uuid import uuid4

import numpy as np
import pytest
import rfc8785
from autplay.application.training_work import TrainingWorkError
from autplay.domain.recommendations import JsonValue
from autplay.domain.training_work import TrainingInputProvenance
from autplay_sona_training import cli
from autplay_sona_training.authority import SonaTrainingInputBinding
from autplay_sona_training.dataset import (
    SONA_SOURCE_KIND_OWNER_APPROVED,
    SonaTensorDataset,
    load_sona_dataset,
    load_sona_dataset_header,
)
from autplay_sona_training.fixture import (
    materialize_synthetic_fixture_bundle,
    verify_synthetic_fixture_header,
)
from autplay_sona_training.model import SonaLiteConfig
from autplay_sona_training.npy import read_canonical_npy
from autplay_sona_training.quality_bundle import load_quality_approved_sona_dataset_bundle
from autplay_sona_training.trainer import (
    SonaTrainingConfig,
    load_sona_checkpoint,
    train_quality_sona_checkpoint,
    train_sona_checkpoint,
)
from test_approval import _build_bundle
from test_approval import _deployment_reviewer_trust as _deployment_reviewer_trust
from training_authority_fixtures import RecordingAuthority


def owner_dataset(tmp_path: Path) -> SonaTensorDataset:
    materialize_synthetic_fixture_bundle(tmp_path / "fixture")
    path = tmp_path / "fixture" / "dataset" / "manifest.json"
    envelope = cast(dict[str, JsonValue], json.loads(path.read_bytes()))
    manifest = cast(dict[str, JsonValue], envelope["manifest"])
    manifest["source_kind"] = SONA_SOURCE_KIND_OWNER_APPROVED
    envelope["manifest_sha256"] = sha256(rfc8785.dumps(manifest)).hexdigest()
    path.write_bytes(rfc8785.dumps(envelope))
    return load_sona_dataset(path.parent)


def test_plain_owner_data_requires_current_authority_even_without_quality_eligibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_dataset(tmp_path)

    def forbid_payload(*_: object, **__: object) -> bytes:
        pytest.fail("Owner payload read before current authority")

    monkeypatch.setattr("autplay_sona_training.dataset.read_canonical_npy", forbid_payload)
    with pytest.raises(ValueError, match="Current shared-training authority"):
        train_sona_checkpoint(
            tmp_path / "fixture" / "dataset",
            tmp_path / "checkpoint",
            model_config=SonaLiteConfig(codebook_size=17, model_dimensions=16, encoder_layers=1),
            training_config=SonaTrainingConfig(device="cpu"),
        )
    assert not (tmp_path / "checkpoint").exists()


def test_quality_owner_data_also_requires_current_authority(tmp_path: Path) -> None:
    bundle = load_quality_approved_sona_dataset_bundle(**_build_bundle(tmp_path / "approved"))
    with pytest.raises(ValueError, match="Current shared-training authority"):
        train_quality_sona_checkpoint(
            bundle,
            tmp_path / "checkpoint",
            model_config=SonaLiteConfig(codebook_size=17, model_dimensions=16, encoder_layers=1),
            training_config=SonaTrainingConfig(device="cpu"),
        )
    assert not (tmp_path / "checkpoint").exists()


def test_full_split_contributors_are_bound_before_any_optimizer_work(tmp_path: Path) -> None:
    bundle = load_quality_approved_sona_dataset_bundle(**_build_bundle(tmp_path / "approved"))
    # Distinct validation/test-only tokens exercise the complete union at this adapter seam.
    bundle = replace(
        bundle,
        validation=replace(bundle.validation, owner_lineage_tokens=("d" * 64,)),
        test=replace(bundle.test, owner_lineage_tokens=("e" * 64,)),
    )
    authority = RecordingAuthority(refuse_start=True)
    with pytest.raises(TrainingWorkError, match="training_input_binding_mismatch"):
        train_quality_sona_checkpoint(
            bundle,
            tmp_path / "checkpoint",
            model_config=SonaLiteConfig(codebook_size=17, model_dimensions=16, encoder_layers=1),
            training_config=SonaTrainingConfig(device="cpu"),
            shared_training_authority=authority,
        )
    binding = authority.bindings[0]
    assert set(binding.owner_tokens) == set(bundle.train.owner_lineage_tokens) | {
        "d" * 64,
        "e" * 64,
    }
    assert binding.dataset_sha256 == bundle.dataset_approval.dataset_bundle_sha256
    assert binding.source_sha256 == bundle.train.source_manifest_sha256
    assert binding.lineage_key_id == bundle.train.owner_lineage_key_id
    assert not (tmp_path / "checkpoint").exists()


@pytest.mark.parametrize("refuse_check", [2, 4])
def test_mid_batch_or_candidate_refusal_cannot_return_a_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    refuse_check: int,
) -> None:
    dataset = owner_dataset(tmp_path)
    manifest = json.loads((tmp_path / "fixture" / "dataset" / "manifest.json").read_bytes())
    loading_checks = len(manifest["manifest"]["tensors"]) + 1
    authority = RecordingAuthority(refuse_check=loading_checks + refuse_check)
    with pytest.raises(TrainingWorkError, match="training_run_invalidated"):
        train_sona_checkpoint(
            tmp_path / "fixture" / "dataset",
            tmp_path / "checkpoint",
            model_config=SonaLiteConfig(codebook_size=17, model_dimensions=16, encoder_layers=1),
            training_config=SonaTrainingConfig(batch_size=4, device="cpu"),
            shared_training_authority=authority,
        )
    assert authority.checks == loading_checks + refuse_check
    assert authority.bindings[0].dataset_sha256 == dataset.manifest_sha256
    assert not (tmp_path / "checkpoint").exists()
    assert not list(tmp_path.glob(".checkpoint.*"))


def test_current_binding_refusal_precedes_all_owner_payload_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = owner_dataset(tmp_path)
    authority = RecordingAuthority(refuse_start=True)

    def forbid_payload(*_: object, **__: object) -> bytes:
        pytest.fail("Refused owner binding reached tensor payload")

    monkeypatch.setattr("autplay_sona_training.dataset.read_canonical_npy", forbid_payload)
    with pytest.raises(TrainingWorkError, match="training_input_binding_mismatch"):
        train_sona_checkpoint(
            tmp_path / "fixture" / "dataset",
            tmp_path / "checkpoint",
            model_config=SonaLiteConfig(codebook_size=17, model_dimensions=16, encoder_layers=1),
            training_config=SonaTrainingConfig(device="cpu"),
            shared_training_authority=authority,
        )
    assert len(authority.bindings) == 1
    assert authority.bindings[0].dataset_sha256 == dataset.manifest_sha256


def test_withdrawal_during_loading_prevents_the_next_payload_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from autplay_sona_training import dataset as dataset_module

    owner_dataset(tmp_path)
    authority = RecordingAuthority(refuse_check=2)
    read_original = read_canonical_npy
    reads: list[Path] = []

    def read_payload(
        path: Path, *, expected_shape: tuple[int, ...], expected_dtype: np.dtype[np.generic]
    ) -> bytes:
        reads.append(path)
        return read_original(path, expected_shape=expected_shape, expected_dtype=expected_dtype)

    monkeypatch.setattr(dataset_module, "read_canonical_npy", read_payload)
    with pytest.raises(TrainingWorkError, match="training_run_invalidated"):
        train_sona_checkpoint(
            tmp_path / "fixture" / "dataset",
            tmp_path / "checkpoint",
            model_config=SonaLiteConfig(codebook_size=17, model_dimensions=16, encoder_layers=1),
            training_config=SonaTrainingConfig(device="cpu"),
            shared_training_authority=authority,
        )
    assert len(reads) == 1 and authority.checks == 2
    assert not (tmp_path / "checkpoint").exists()


def test_manifest_replacement_during_authority_check_cannot_reach_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dataset(tmp_path)
    path = tmp_path / "fixture" / "dataset" / "manifest.json"

    class ReplacingAuthority(RecordingAuthority):
        def authorize(self, binding: SonaTrainingInputBinding) -> TrainingInputProvenance:
            result = super().authorize(binding)
            envelope = json.loads(path.read_bytes())
            envelope["manifest"]["source_manifest_sha256"] = "a" * 64
            envelope["manifest_sha256"] = sha256(rfc8785.dumps(envelope["manifest"])).hexdigest()
            path.write_bytes(rfc8785.dumps(envelope))
            return result

    def forbid_payload(*_: object, **__: object) -> bytes:
        pytest.fail("Replaced manifest reached owner payload")

    monkeypatch.setattr("autplay_sona_training.dataset.read_canonical_npy", forbid_payload)
    with pytest.raises(ValueError, match="manifest changed"):
        train_sona_checkpoint(
            path.parent,
            tmp_path / "checkpoint",
            model_config=SonaLiteConfig(codebook_size=17, model_dimensions=16, encoder_layers=1),
            training_config=SonaTrainingConfig(device="cpu"),
            shared_training_authority=ReplacingAuthority(),
        )
    assert not (tmp_path / "checkpoint").exists()


@pytest.mark.parametrize("codebook_size", [None, 17])
def test_standalone_owner_training_never_reads_payload_even_for_automatic_codebook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    codebook_size: int | None,
) -> None:
    owner_dataset(tmp_path)

    def forbid_payload(*_: object, **__: object) -> bytes:
        pytest.fail("Standalone CLI read owner tensors without controlled authority")

    monkeypatch.setattr("autplay_sona_training.dataset.read_canonical_npy", forbid_payload)
    arguments = [
        "train",
        "--dataset",
        str(tmp_path / "fixture" / "dataset"),
        "--checkpoint",
        str(tmp_path / "checkpoint"),
    ]
    if codebook_size is not None:
        arguments.extend(["--codebook-size", str(codebook_size)])
    assert cli.main(arguments) == 4
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == '{"error":"shared_training_execution_not_configured"}\n'
    assert not (tmp_path / "checkpoint").exists()


def test_standalone_quality_training_refuses_before_historical_bundle_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def forbid_bundle(*_: object, **__: object) -> None:
        pytest.fail("Standalone CLI read historical owner bundle without current execution")

    monkeypatch.setattr(cli, "load_quality_approved_sona_dataset_bundle", forbid_bundle)
    arguments = ["train-quality", "--checkpoint", str(tmp_path / "checkpoint")]
    for option in (
        "train-dataset",
        "validation-dataset",
        "test-dataset",
        "tokenizer",
        "source-manifest",
        "catalog-manifest",
        "source-rekey-plan",
        "source-provenance-acceptance",
        "teacher-calibration",
        "teacher-manifest",
        "source-approval",
        "dataset-approval",
    ):
        arguments.extend(["--" + option, str(tmp_path / "missing")])
    assert cli.main(arguments) == 4
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == '{"error":"shared_training_execution_not_configured"}\n'
    assert not (tmp_path / "checkpoint").exists()


def test_recovery_cli_routes_configured_exclusive_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    execution_id = uuid4()
    input_root, output_root = tmp_path / "input", tmp_path / "output"

    def recover(namespace: argparse.Namespace) -> cli.SonaExecutionRecovery:
        assert namespace.input_root == str(input_root)
        assert namespace.output_root == str(output_root)
        assert namespace.limit == 17
        return cli.SonaExecutionRecovery(1, (str(execution_id),))

    monkeypatch.setattr(cli, "_recover_executions", recover)
    assert (
        cli.main(
            [
                "recover-executions",
                "--input-root",
                str(input_root),
                "--output-root",
                str(output_root),
                "--limit",
                "17",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "execution_ids": [str(execution_id)],
        "recovered_count": 1,
    }


def test_restore_drain_cli_routes_configured_exclusive_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_root, output_root = tmp_path / "input", tmp_path / "output"

    def drain(namespace: argparse.Namespace) -> cli.SonaRestoreDrain:
        assert namespace.input_root == str(input_root)
        assert namespace.output_root == str(output_root)
        assert namespace.limit == 19
        return cli.SonaRestoreDrain(1, 2, 3, 4, 5, 6, 7, 8, 9)

    monkeypatch.setattr(cli, "_restore_drain", drain)
    assert (
        cli.main(
            [
                "restore-drain",
                "--input-root",
                str(input_root),
                "--output-root",
                str(output_root),
                "--limit",
                "19",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out) == {
        "checked_cgroups": 8,
        "checked_pids": 7,
        "ingest_cleanup_executions": 3,
        "ingest_executions": 2,
        "maintenance_executions": 5,
        "metadata_executions": 4,
        "recovered_training_executions": 9,
        "resource_executions": 1,
        "training_executions": 6,
    }


def test_dataset_header_does_not_read_tensors_and_duplicate_manifest_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = owner_dataset(tmp_path)
    path = tmp_path / "fixture" / "dataset" / "manifest.json"

    def forbid_payload(*_: object, **__: object) -> bytes:
        pytest.fail("Manifest-only preflight read tensors")

    monkeypatch.setattr("autplay_sona_training.dataset.read_canonical_npy", forbid_payload)
    header = load_sona_dataset_header(path.parent)
    assert header.manifest_sha256 == dataset.manifest_sha256
    assert header.owner_lineage_tokens == dataset.owner_lineage_tokens
    path.write_bytes(path.read_bytes().replace(b'{"manifest":', b'{"manifest":null,"manifest":', 1))
    with pytest.raises(ValueError, match="duplicate keys"):
        load_sona_dataset_header(path.parent)


def test_standalone_synthetic_training_retains_automatic_codebook_support(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    materialize_synthetic_fixture_bundle(tmp_path / "fixture")
    assert (
        cli.main(
            [
                "train",
                "--dataset",
                str(tmp_path / "fixture" / "dataset"),
                "--checkpoint",
                str(tmp_path / "checkpoint"),
                "--device",
                "cpu",
                "--model-dimensions",
                "16",
                "--encoder-layers",
                "1",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert output.err == ""
    assert json.loads(output.out)["source_kind"] != SONA_SOURCE_KIND_OWNER_APPROVED
    assert (tmp_path / "checkpoint" / "manifest.json").is_file()


@pytest.mark.parametrize("entrypoint", ["trainer", "cli"])
@pytest.mark.parametrize("owner_labels", [True, False])
def test_unsigned_synthetic_relabel_or_rehashed_tensors_cannot_bypass_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner_labels: bool, entrypoint: str
) -> None:
    materialize_synthetic_fixture_bundle(tmp_path / "fixture")
    directory = tmp_path / "fixture" / "dataset"
    manifest_path = directory / "manifest.json"
    envelope = cast(dict[str, JsonValue], json.loads(manifest_path.read_bytes()))
    manifest = cast(dict[str, JsonValue], envelope["manifest"])
    if owner_labels:
        manifest["data_classification"] = "APPROVED_OWNER_SAFE"
        manifest["source_manifest_sha256"] = "a" * 64
        manifest["owner_lineage_key_id"] = "owner-source-v1"
    else:
        # Keep all synthetic labels but replace valid tensor content and all unsigned hashes.
        seed_path = directory / "seed.npy"
        seed = np.load(seed_path, allow_pickle=False)
        seed[0] += 1
        np.save(seed_path, seed, allow_pickle=False)
        for entry in cast(list[dict[str, JsonValue]], manifest["tensors"]):
            if entry["name"] == "seed":
                entry["sha256"] = sha256(seed_path.read_bytes()).hexdigest()
    envelope["manifest_sha256"] = sha256(rfc8785.dumps(manifest)).hexdigest()
    manifest_path.write_bytes(rfc8785.dumps(envelope))
    # This is structurally valid input, not a corrupt file caught by an unrelated loader check.
    load_sona_dataset(directory)

    def forbid_allocation(*_: object, **__: object) -> None:
        pytest.fail("Untrusted synthetic input reached a payload read or model allocation")

    monkeypatch.setattr("autplay_sona_training.trainer.SonaLiteModel", forbid_allocation)
    monkeypatch.setattr("autplay_sona_training.dataset.read_canonical_npy", forbid_allocation)
    with pytest.raises(ValueError, match="exact generated synthetic fixtures"):
        if entrypoint == "cli":
            cli.main(
                [
                    "train",
                    "--dataset",
                    str(directory),
                    "--checkpoint",
                    str(tmp_path / "checkpoint"),
                    "--device",
                    "cpu",
                ]
            )
        else:
            train_sona_checkpoint(
                directory,
                tmp_path / "checkpoint",
                model_config=SonaLiteConfig(
                    codebook_size=17, model_dimensions=16, encoder_layers=1
                ),
                training_config=SonaTrainingConfig(device="cpu"),
            )
    assert not (tmp_path / "checkpoint").exists()


@pytest.mark.parametrize("recording_count", [1, 8, 16])
def test_generated_synthetic_identity_can_be_verified_without_caller_payload_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recording_count: int
) -> None:
    fixture = materialize_synthetic_fixture_bundle(
        tmp_path / "fixture", recording_count=recording_count
    )

    def forbid_payload(*_: object, **__: object) -> bytes:
        pytest.fail("Synthetic provenance preflight read caller tensors")

    monkeypatch.setattr("autplay_sona_training.dataset.read_canonical_npy", forbid_payload)
    header = load_sona_dataset_header(tmp_path / "fixture" / "dataset")
    verify_synthetic_fixture_header(header)
    assert header.manifest_sha256 == fixture.dataset_manifest_sha256


def test_legacy_v3_checkpoint_load_does_not_infer_current_training_authority(
    tmp_path: Path,
) -> None:
    materialize_synthetic_fixture_bundle(tmp_path / "fixture")
    train_sona_checkpoint(
        tmp_path / "fixture" / "dataset",
        tmp_path / "checkpoint",
        model_config=SonaLiteConfig(codebook_size=17, model_dimensions=16, encoder_layers=1),
        training_config=SonaTrainingConfig(device="cpu"),
    )
    path = tmp_path / "checkpoint" / "manifest.json"
    envelope = cast(dict[str, JsonValue], json.loads(path.read_bytes()))
    document = cast(dict[str, JsonValue], envelope["manifest"])
    document["schema_version"] = 3
    del document["source_kind"]
    del document["training_authority"]
    envelope["manifest_sha256"] = sha256(rfc8785.dumps(document)).hexdigest()
    path.write_bytes(rfc8785.dumps(envelope))
    _, loaded = load_sona_checkpoint(tmp_path / "checkpoint")
    assert loaded.source_kind is None
    assert loaded.training_authority is None
