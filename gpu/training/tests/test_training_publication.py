"""Real PG + CPU/ONNX + serving gate: lost commit reply and final withdrawal race.

These are synthetic owner-shaped tensors, not deployment data or GPU measurements.
Only the established loopback autplay_p02_* disposable fixtures may supply PG.
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import new as new_hmac
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.filesystem.training_consent_ledger import FilesystemTrainingConsentLedger
from autplay.adapters.postgresql.models import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.training_publication import (
    PostgresTrainingPublicationAuthority,
)
from autplay.application.training_consent import TrainingConsentService
from autplay.application.training_work import TrainingWorkError, TrainingWorkService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.profile_pairing import public_spki
from autplay.domain.recommendations import JsonValue
from autplay.domain.training_work import TrainingInputProvenance
from autplay_gpu.embedding import ModelArtifactError
from autplay_gpu.sona_artifacts import SonaArtifactStore
from autplay_sona_training.authority import PostgresSonaTrainingAuthority
from autplay_sona_training.dataset import SONA_SOURCE_KIND_OWNER_APPROVED
from autplay_sona_training.execution_protocol import checkpoint_output_bytes
from autplay_sona_training.export import SonaOnnxExport, SonaOnnxProvenance, export_sona_onnx
from autplay_sona_training.fixture import materialize_synthetic_fixture_bundle
from autplay_sona_training.model import SonaLiteConfig, SonaLiteModel
from autplay_sona_training.publication import (
    export_and_publish_sona_checkpoint,
    export_owned_sona_checkpoint,
    read_owned_sona_publication,
)
from autplay_sona_training.trainer import SonaTrainingConfig, train_sona_checkpoint
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

pytest_plugins = ["postgresql.conftest"]


@dataclass(frozen=True)
class TrainingHarness:
    engine: Engine
    actor: Principal
    server_id: UUID
    consent_ledger: FilesystemTrainingConsentLedger


@pytest.fixture
def pair(database_url: str, tmp_path: Path) -> Iterator[TrainingHarness]:
    engine = create_engine(database_url)
    ledger = FilesystemTrainingConsentLedger(tmp_path / "consent.sqlite3", b"s" * 32, "fixture-v1")
    ledger.initialize()
    now = datetime.now(UTC)
    server_id, owner, device, session_id = (uuid4() for _ in range(4))
    key = public_spki(ec.generate_private_key(ec.SECP256R1()))
    with sessionmaker(engine).begin() as session:
        session.add(UserAccountRow(user_id=owner, display_name="Synthetic trainer", role="USER"))
        session.add(
            ServerInstanceRow(
                server_instance_id=server_id,
                identity_epoch=1,
                identity_public_key_spki=key,
                identity_thumbprint_sha256=sha256(key).digest(),
                label_hint="Synthetic training",
                api_origin="https://api.test.invalid",
                stream_origin="https://stream.test.invalid",
                capability_revision=1,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        session.add(
            DeviceRow(
                device_id=device,
                user_id=owner,
                device_name="Synthetic",
                platform="ANDROID",
                app_version="fixture-1",
                public_key=key,
                public_key_thumbprint_sha256=sha256(key).digest(),
            )
        )
        session.flush()
        session.add(
            UserSessionRow(
                session_id=session_id,
                user_id=owner,
                device_id=device,
                refresh_token_hash=sha256(b"synthetic-session").digest(),
                issued_at=now,
                expires_at=now + timedelta(days=1),
                family_id=session_id,
                generation=0,
                session_mode="V2",
            )
        )
    try:
        yield TrainingHarness(
            engine, Principal(owner, device, session_id, AccountRole.USER), server_id, ledger
        )
    finally:
        engine.dispose()


def service(pair: TrainingHarness) -> TrainingConsentService:
    return TrainingConsentService(
        sessionmaker(pair.engine, expire_on_commit=False), pair.consent_ledger
    )


def registry(pair: TrainingHarness) -> TrainingWorkService:
    return TrainingWorkService(
        sessionmaker(pair.engine, expire_on_commit=False),
        server_instance_id=pair.server_id,
        identity_epoch=1,
        lineage_key_id="fixture-v1",
        lineage_key=b"k" * 32,
        consent_ledger=pair.consent_ledger,
    )


def command(pair: TrainingHarness, decision: str, revision: int = 0) -> dict[str, object]:
    return {
        "operation_id": str(uuid4()),
        "account_id": str(pair.actor.user_id),
        "decision": decision,
        "expected_revision": revision,
        "policy_version": 1,
    }


def test_owned_export_rejects_self_consistent_replacement_before_final_seal(
    pair: TrainingHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    materialize_synthetic_fixture_bundle(tmp_path / "fixture")
    dataset_directory = tmp_path / "fixture" / "dataset"
    dataset_manifest_path = dataset_directory / "manifest.json"
    dataset_envelope = cast(dict[str, JsonValue], json.loads(dataset_manifest_path.read_bytes()))
    dataset_manifest = cast(dict[str, JsonValue], dataset_envelope["manifest"])
    dataset_manifest.update(
        {
            "source_kind": SONA_SOURCE_KIND_OWNER_APPROVED,
            "source_manifest_sha256": "a" * 64,
            "owner_lineage_key_id": "fixture-v1",
            "owner_lineage_tokens": [
                new_hmac(b"k" * 32, pair.actor.user_id.bytes, sha256).hexdigest()
            ],
            "data_classification": "APPROVED_OWNER_SAFE",
            "split": "train",
        }
    )
    dataset_sha256 = sha256(rfc8785.dumps(dataset_manifest)).hexdigest()
    dataset_envelope["manifest_sha256"] = dataset_sha256
    dataset_manifest_path.write_bytes(rfc8785.dumps(dataset_envelope))
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    work, run = registry(pair), uuid4()
    work.register(run, source_sha256="a" * 64, participants={pair.actor.user_id: 1})
    work.ready(run, dataset_sha256)
    checkpoint = tmp_path / "checkpoint"
    authority = PostgresSonaTrainingAuthority(work, run)
    train_sona_checkpoint(
        dataset_directory,
        checkpoint,
        model_config=SonaLiteConfig(codebook_size=17, model_dimensions=16, encoder_layers=1),
        training_config=SonaTrainingConfig(batch_size=8, device="cpu"),
        shared_training_authority=authority,
    )

    original_read = read_owned_sona_publication
    tampered = False

    def replace_before_final_read(*args: object, **kwargs: object) -> object:
        nonlocal tampered
        assert not tampered
        tampered = True
        publication = checkpoint / "publication"
        artifact = publication / "candidate.onnx"
        intent_path = publication / "intent.json"
        assert intent_path.is_file()
        artifact.write_bytes(artifact.read_bytes() + b"replacement-before-final-seal")
        artifact_sha256 = sha256(artifact.read_bytes()).hexdigest()
        manifest_path = artifact.with_suffix(f"{artifact.suffix}.manifest.json")
        manifest_envelope = cast(dict[str, JsonValue], json.loads(manifest_path.read_bytes()))
        manifest = cast(dict[str, JsonValue], manifest_envelope["manifest"])
        manifest["artifact_sha256"] = artifact_sha256
        manifest_sha256 = sha256(rfc8785.dumps(manifest)).hexdigest()
        manifest_path.write_bytes(
            rfc8785.dumps({"manifest": manifest, "manifest_sha256": manifest_sha256})
        )
        commit: dict[str, JsonValue] = {
            "schema_version": 1,
            "state": "COMMITTED",
            "artifact_sha256": artifact_sha256,
            "model_manifest_sha256": manifest_sha256,
        }
        commit_sha256 = sha256(rfc8785.dumps(commit)).hexdigest()
        artifact.with_suffix(f"{artifact.suffix}.commit.json").write_bytes(
            rfc8785.dumps({"commit": commit, "commit_sha256": commit_sha256})
        )
        intent_envelope = cast(dict[str, JsonValue], json.loads(intent_path.read_bytes()))
        intent = cast(dict[str, JsonValue], intent_envelope["intent"])
        intent["artifact_sha256"] = artifact_sha256
        intent["model_manifest_sha256"] = manifest_sha256
        intent["commit_sha256"] = commit_sha256
        intent_path.write_bytes(
            rfc8785.dumps(
                {
                    "intent": intent,
                    "intent_sha256": sha256(rfc8785.dumps(intent)).hexdigest(),
                }
            )
        )
        return original_read(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "autplay_sona_training.publication.read_owned_sona_publication",
        replace_before_final_read,
    )
    with pytest.raises(ValueError, match="changed after verified export"):
        export_owned_sona_checkpoint(
            checkpoint,
            tmp_path / "fixture" / "tokenizer",
            artifact_name="candidate.onnx",
            execution_id=uuid4(),
            run_id=run,
            operation_id=uuid4(),
            execution_inventory_sha256="e" * 64,
            tokenizer_relative="tokenizer",
            maximum_output_bytes=64 * 1024 * 1024,
            checkpoint_output_bytes=checkpoint_output_bytes(checkpoint),
            authority=authority,
        )
    assert tampered
    assert not (checkpoint / "publication").exists()


@pytest.mark.parametrize(
    "failure", ["lost_reply", "withdraw_before_commit", "tampered_after_export"]
)
def test_current_publication_is_the_only_serving_authority_after_restart_or_withdrawal(
    pair: TrainingHarness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    fixture = materialize_synthetic_fixture_bundle(tmp_path / "fixture")
    dataset_directory = tmp_path / "fixture" / "dataset"
    path = dataset_directory / "manifest.json"
    envelope = cast(dict[str, JsonValue], json.loads(path.read_bytes()))
    manifest = cast(dict[str, JsonValue], envelope["manifest"])
    manifest.update(
        {
            "source_kind": SONA_SOURCE_KIND_OWNER_APPROVED,
            "source_manifest_sha256": "a" * 64,
            "owner_lineage_key_id": "fixture-v1",
            "owner_lineage_tokens": [
                new_hmac(b"k" * 32, pair.actor.user_id.bytes, sha256).hexdigest()
            ],
            "data_classification": "APPROVED_OWNER_SAFE",
            "split": "train",
        }
    )
    dataset_digest = sha256(rfc8785.dumps(manifest)).hexdigest()
    envelope["manifest_sha256"] = dataset_digest
    path.write_bytes(rfc8785.dumps(envelope))
    service(pair).decide(pair.actor, command(pair, "GRANTED"))
    work, run, operation = registry(pair), uuid4(), uuid4()
    work.register(run, source_sha256="a" * 64, participants={pair.actor.user_id: 1})
    work.ready(run, dataset_digest)
    trained = train_sona_checkpoint(
        dataset_directory,
        tmp_path / "checkpoint",
        model_config=SonaLiteConfig(codebook_size=17, model_dimensions=16, encoder_layers=1),
        training_config=SonaTrainingConfig(batch_size=8, device="cpu"),
        shared_training_authority=PostgresSonaTrainingAuthority(work, run),
    )
    assert trained.training_authority is not None
    assert trained.training_authority.run_id == run
    assert (
        str(pair.actor.user_id).encode()
        not in (tmp_path / "checkpoint" / "manifest.json").read_bytes()
    )
    # A valid RUNNING input receipt from a different run must not authorize this output.
    other_run = uuid4()
    work.register(other_run, source_sha256="a" * 64, participants={pair.actor.user_id: 1})
    work.ready(other_run, dataset_digest)
    other_provenance = work.authorize_input(
        other_run,
        source_sha256="a" * 64,
        dataset_sha256=dataset_digest,
        lineage_key_id="fixture-v1",
        owner_tokens=(new_hmac(b"k" * 32, pair.actor.user_id.bytes, sha256).hexdigest(),),
    )
    forged = tmp_path / "forged-checkpoint"
    forged.mkdir()
    transplanted = json.loads((tmp_path / "checkpoint" / "manifest.json").read_bytes())
    transplanted["manifest"]["training_authority"] = other_provenance.document()
    transplanted["manifest_sha256"] = sha256(rfc8785.dumps(transplanted["manifest"])).hexdigest()
    (forged / "manifest.json").write_bytes(rfc8785.dumps(transplanted))

    def forbid_checkpoint_load(*_: object, **__: object) -> None:
        pytest.fail("Unsealed checkpoint reached model allocation")

    with monkeypatch.context() as scope:
        scope.setattr(
            "autplay_sona_training.publication.load_sona_checkpoint", forbid_checkpoint_load
        )
        with pytest.raises(TrainingWorkError, match="training_checkpoint_seal_required"):
            export_and_publish_sona_checkpoint(
                forged,
                tmp_path / "fixture" / "tokenizer",
                tmp_path / "forged.onnx",
                authority=work,
                operation_id=uuid4(),
            )
    publish = work.publish

    def fail_at_publication(*args: object, **kwargs: object) -> object:
        if failure == "withdraw_before_commit":
            service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
        else:
            publish(
                run,
                operation,
                cast(dict[str, str], args[2]),
                input_provenance=cast(TrainingInputProvenance, kwargs["input_provenance"]),
            )
            raise OSError("simulated lost PG commit reply")
        return publish(
            run,
            operation,
            cast(dict[str, str], args[2]),
            input_provenance=cast(TrainingInputProvenance, kwargs["input_provenance"]),
        )

    if failure == "tampered_after_export":

        def tamper_export(
            model: SonaLiteModel,
            output_path: Path,
            *,
            provenance: SonaOnnxProvenance | None = None,
            before_publish: Callable[[], object] | None = None,
        ) -> SonaOnnxExport:
            result = export_sona_onnx(
                model, output_path, provenance=provenance, before_publish=before_publish
            )
            output_path.write_bytes(b"replaced-after-export")
            envelope = json.loads(output_path.with_suffix(".onnx.manifest.json").read_bytes())
            envelope["manifest"]["artifact_sha256"] = sha256(output_path.read_bytes()).hexdigest()
            envelope["manifest_sha256"] = sha256(rfc8785.dumps(envelope["manifest"])).hexdigest()
            output_path.with_suffix(".onnx.manifest.json").write_bytes(rfc8785.dumps(envelope))
            commit = {
                "schema_version": 1,
                "state": "COMMITTED",
                "artifact_sha256": envelope["manifest"]["artifact_sha256"],
                "model_manifest_sha256": envelope["manifest_sha256"],
            }
            output_path.with_suffix(".onnx.commit.json").write_bytes(
                rfc8785.dumps(
                    {
                        "commit": commit,
                        "commit_sha256": sha256(rfc8785.dumps(commit)).hexdigest(),
                    }
                )
            )
            return result

        monkeypatch.setattr("autplay_sona_training.publication.export_sona_onnx", tamper_export)
    else:
        monkeypatch.setattr(work, "publish", fail_at_publication)
    output = tmp_path / "candidate.onnx"
    expected = (
        OSError
        if failure == "lost_reply"
        else ValueError
        if failure == "tampered_after_export"
        else TrainingWorkError
    )
    with pytest.raises(
        expected, match=r"lost PG|training_run_invalidated|changed after verified export"
    ):
        export_and_publish_sona_checkpoint(
            tmp_path / "checkpoint",
            tmp_path / "fixture" / "tokenizer",
            output,
            authority=work,
            operation_id=operation,
        )
    monkeypatch.setattr(work, "publish", publish)
    assert output.with_suffix(".onnx.commit.json").is_file()
    artifact_digest = sha256(output.read_bytes()).hexdigest()
    model_envelope = cast(
        dict[str, JsonValue], json.loads(output.with_suffix(".onnx.manifest.json").read_bytes())
    )
    model_digest = cast(str, model_envelope["manifest_sha256"])
    cache = tmp_path / "cache"
    cached = cache / "objects" / artifact_digest[:2] / artifact_digest
    cached.parent.mkdir(parents=True)
    for suffix in ("", ".manifest.json", ".commit.json"):
        Path(f"{cached}{suffix}").write_bytes(Path(f"{output}{suffix}").read_bytes())
    store = SonaArtifactStore(
        cache.resolve(),
        publication_authority=PostgresTrainingPublicationAuthority(
            sessionmaker(pair.engine, expire_on_commit=False)
        ),
    )
    if failure != "lost_reply":
        with pytest.raises(ModelArtifactError, match="not published"):
            store.resolve(
                artifact_sha256=artifact_digest,
                model_manifest_sha256=model_digest,
                tokenizer_sha256=fixture.tokenizer_fit_manifest_sha256,
            )
        if failure == "withdraw_before_commit":
            service(pair).decide(pair.actor, command(pair, "GRANTED", 2))
        # Preserve the installed bytes until exact writer cleanup reconciles them.
        with pytest.raises(TrainingWorkError, match="reconciliation_required"):
            export_and_publish_sona_checkpoint(
                tmp_path / "checkpoint",
                tmp_path / "fixture" / "tokenizer",
                output,
                authority=work,
                operation_id=operation,
            )
    else:
        service(pair).decide(pair.actor, command(pair, "WITHDRAWN", 1))
        # No historical inputs/checkpoint/tokenizer reread is necessary for exact committed replay.
        replay = export_and_publish_sona_checkpoint(
            tmp_path / "missing-checkpoint",
            tmp_path / "missing-tokenizer",
            output,
            authority=work,
            operation_id=operation,
        )
        assert replay.artifact_sha256 == artifact_digest
        assert replay.model_manifest_sha256 == model_digest
        with pytest.raises(TrainingWorkError, match="training_publication_run_mismatch"):
            export_and_publish_sona_checkpoint(
                tmp_path / "missing-checkpoint",
                tmp_path / "missing-tokenizer",
                output,
                authority=work,
                operation_id=operation,
                expected_run_id=uuid4(),
            )
        assert (
            store.resolve(
                artifact_sha256=artifact_digest,
                model_manifest_sha256=model_digest,
                tokenizer_sha256=fixture.tokenizer_fit_manifest_sha256,
            ).payload
            == output.read_bytes()
        )
        with pytest.raises(TrainingWorkError, match="training_operation_conflict"):
            export_and_publish_sona_checkpoint(
                tmp_path / "missing-checkpoint",
                tmp_path / "missing-tokenizer",
                output,
                authority=work,
                operation_id=uuid4(),
            )
