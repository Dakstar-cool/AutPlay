"""Real retained child + PostgreSQL authority for owner-derived training."""

import json
import os
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import new as new_hmac
from pathlib import Path
from threading import Barrier, Event, current_thread
from typing import cast
from uuid import UUID, uuid4

import pytest
import rfc8785
from autplay.adapters.child_process import training_child_launch
from autplay.adapters.filesystem.training_consent_ledger import FilesystemTrainingConsentLedger
from autplay.adapters.offline_process_evidence import OfflineProcessEvidenceProbe
from autplay.adapters.postgresql.internal_io import internal_io_usage
from autplay.adapters.postgresql.models import DeviceRow, UserAccountRow, UserSessionRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.models.training_work import (
    TrainingCheckpointRow,
    TrainingCleanupClaimRow,
    TrainingExecutionRow,
    TrainingRunRow,
)
from autplay.adapters.postgresql.offline_execution_drain import PostgresOfflineExecutionDrain
from autplay.adapters.postgresql.training_execution import PostgresTrainingExecutionRepository
from autplay.application.training_consent import TrainingConsentService
from autplay.application.training_work import TrainingWorkError, TrainingWorkService
from autplay.domain.auth import AccountRole, Principal
from autplay.domain.profile_pairing import public_spki
from autplay.domain.recommendations import JsonValue
from autplay.domain.resource_admission import ResourceAdmissionError
from autplay.domain.resource_execution import (
    ExecutionState,
    ExitKind,
    ProcessExitEvidence,
    ProcessIdentity,
)
from autplay.domain.training_execution import TrainingExecutionTicket
from autplay.domain.training_work import TrainingInputProvenance
from autplay.domain.vault import Sha256Digest
from autplay.runtime.settings import WorkerSettings
from autplay_sona_training import cli
from autplay_sona_training import controlled_execution as controlled_execution_module
from autplay_sona_training.authority import (
    PostgresSonaTrainingAuthority,
    SonaSharedTrainingAuthority,
    SonaTrainingInputBinding,
)
from autplay_sona_training.controlled_execution import (
    SonaTrainingProcessCoordinator,
    SonaTrainingStorageScopes,
)
from autplay_sona_training.dataset import SONA_SOURCE_KIND_OWNER_APPROVED
from autplay_sona_training.execution_protocol import (
    SonaControlledTrainingCommand,
    SonaControlledTrainingResult,
    finalize_checkpoint_output,
    finalize_training_input,
    reserve_checkpoint_output,
    sona_training_execution_inventory_sha256,
)
from autplay_sona_training.fixture import materialize_synthetic_fixture_bundle
from autplay_sona_training.model import SonaLiteConfig
from autplay_sona_training.root_inventory import inspect_sona_training_root
from autplay_sona_training.trainer import SonaTrainingConfig, load_sona_checkpoint
from cryptography.hazmat.primitives.asymmetric import ec
from process_tree_support import process_tree_factory
from pydantic import SecretStr
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

pytest_plugins = ["postgresql.conftest"]

_FIXTURE_CHILD = Path(__file__).with_name("fixtures") / "controlled_training_child.py"


def _fixture_launch(mode: str = "success") -> Callable[[], tuple[list[str], dict[str, str]]]:
    def launch() -> tuple[list[str], dict[str, str]]:
        arguments, environment = training_child_launch()
        return [arguments[0], "-I", str(_FIXTURE_CHILD), mode], environment

    return launch


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
        session.add(UserAccountRow(user_id=owner, display_name="Controlled trainer", role="USER"))
        session.add(
            ServerInstanceRow(
                server_instance_id=server_id,
                identity_epoch=1,
                identity_public_key_spki=key,
                identity_thumbprint_sha256=sha256(key).digest(),
                label_hint="Controlled training",
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
                device_name="Controlled",
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
                refresh_token_hash=sha256(b"controlled-session").digest(),
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


def consent_command(pair: TrainingHarness, decision: str, revision: int = 0) -> dict[str, object]:
    return {
        "operation_id": str(uuid4()),
        "account_id": str(pair.actor.user_id),
        "decision": decision,
        "expected_revision": revision,
        "policy_version": 1,
    }


def _owner_input(pair: TrainingHarness, root: Path) -> tuple[Path, str]:
    materialize_synthetic_fixture_bundle(root)
    dataset = root / "dataset"
    path = dataset / "manifest.json"
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
    digest = sha256(rfc8785.dumps(manifest)).hexdigest()
    envelope["manifest_sha256"] = digest
    path.write_bytes(rfc8785.dumps(envelope))
    return dataset, digest


@dataclass(frozen=True)
class _PreparedExecution:
    work: TrainingWorkService
    run_id: UUID
    ticket: TrainingExecutionTicket
    command: SonaControlledTrainingCommand
    repository: PostgresTrainingExecutionRepository
    coordinator: SonaTrainingProcessCoordinator
    output_root: Path
    dataset_sha256: str

    @property
    def storage_scopes(self) -> SonaTrainingStorageScopes:
        return SonaTrainingStorageScopes(
            self.command.input_root.parent,
            self.command.output_root,
        )


def _prepared_execution(
    pair: TrainingHarness,
    tmp_path: Path,
    *,
    maximum_output_bytes: int = 64 * 1024 * 1024,
) -> _PreparedExecution:
    execution = uuid4()
    input_scope = (tmp_path / "input").absolute()
    input_scope.mkdir()
    input_root = input_scope / str(execution)
    output_root = (tmp_path / "output").absolute()
    output_root.mkdir()
    _, dataset_digest = _owner_input(pair, input_root)
    service(pair).decide(pair.actor, consent_command(pair, "GRANTED"))
    work, run = registry(pair), uuid4()
    work.register(run, source_sha256="a" * 64, participants={pair.actor.user_id: 1})
    work.ready(run, dataset_digest)
    with Session(pair.engine) as session, session.begin():
        session.execute(
            text(
                "UPDATE account.internal_io_policy SET workload_version=3,"
                "server_instance_id=:server,identity_epoch=1 WHERE singleton_id=1"
            ),
            {"server": pair.server_id},
        )
    inventory = inspect_sona_training_root(input_root)
    input_scope_identity = input_scope.stat(follow_symlinks=False)
    output_scope_identity = output_root.stat(follow_symlinks=False)
    model_config = SonaLiteConfig(codebook_size=17, model_dimensions=16, encoder_layers=1)
    training_config = SonaTrainingConfig(batch_size=8, device="cpu")
    execution_inventory = sona_training_execution_inventory_sha256(
        execution_id=execution,
        run_id=run,
        input_root=input_root,
        dataset_relative=Path("dataset"),
        output_root=output_root,
        input_inventory=inventory,
        maximum_output_bytes=maximum_output_bytes,
        model_config=model_config,
        training_config=training_config,
    )
    ticket = TrainingExecutionTicket(
        execution,
        run,
        str(input_root),
        str(output_root),
        inventory.root_device,
        inventory.root_inode,
        str(input_scope_identity.st_dev),
        str(input_scope_identity.st_ino),
        str(output_scope_identity.st_dev),
        str(output_scope_identity.st_ino),
        Sha256Digest(bytes.fromhex(inventory.inventory_sha256)),
        Sha256Digest(bytes.fromhex(execution_inventory)),
        inventory.input_bytes,
        maximum_output_bytes,
    )
    command = SonaControlledTrainingCommand(
        execution,
        run,
        input_root,
        Path("dataset"),
        output_root,
        inventory.inventory_sha256,
        execution_inventory,
        inventory.input_bytes,
        ticket.maximum_output_bytes,
        model_config,
        training_config,
    )
    repository = PostgresTrainingExecutionRepository(
        sessionmaker(pair.engine, expire_on_commit=False), work
    )
    coordinator = SonaTrainingProcessCoordinator(
        repository,
        tree_factory=process_tree_factory(),
        storage_scopes=SonaTrainingStorageScopes(input_scope, output_root),
        maximum=1,
    )
    return _PreparedExecution(
        work, run, ticket, command, repository, coordinator, output_root, dataset_digest
    )


class _WithdrawingAuthority(SonaSharedTrainingAuthority):
    def __init__(
        self,
        base: PostgresSonaTrainingAuthority,
        withdraw: Callable[[], None],
    ) -> None:
        self._base, self._withdraw = base, withdraw
        self._checks = 0

    def authorize(self, binding: SonaTrainingInputBinding) -> TrainingInputProvenance:
        return self._base.authorize(binding)

    def check_running(self) -> None:
        self._checks += 1
        if self._checks == 2:
            self._withdraw()
        self._base.check_running()

    def seal_checkpoint(self, provenance: TrainingInputProvenance, manifest_sha256: str) -> None:
        self._base.seal_checkpoint(provenance, manifest_sha256)


def test_payload_free_root_inventory_is_stable_and_change_sensitive(tmp_path: Path) -> None:
    root = tmp_path / "input"
    root.mkdir()
    payload = root / "owner.npy"
    payload.write_bytes(b"owner-payload")
    first = inspect_sona_training_root(root.absolute())
    assert first.input_bytes == len(b"owner-payload") and first.entry_count == 1
    assert inspect_sona_training_root(root.absolute()) == first
    payload.write_bytes(b"changed-owner-payload")
    assert inspect_sona_training_root(root.absolute()).inventory_sha256 != first.inventory_sha256


@pytest.mark.usefixtures("internal_io_budget")
def test_real_controlled_training_child_retains_and_closes_global_capacity(
    pair: TrainingHarness,
    tmp_path: Path,
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    before_cleanup: list[Path] = []

    def observe_retained_publication(
        checkpoint: Path, result: SonaControlledTrainingResult
    ) -> None:
        status = prepared.repository.status(prepared.ticket)
        assert status is not None and status.state == ExecutionState.STOPPING
        with Session(pair.engine) as session:
            assert internal_io_usage(session) == 1
        assert prepared.command.input_root.is_dir()
        assert checkpoint.is_dir()
        assert result.checkpoint_manifest_sha256
        before_cleanup.append(checkpoint)

    result = prepared.coordinator.run(
        prepared.ticket,
        prepared.command,
        PostgresSonaTrainingAuthority(prepared.work, prepared.run_id),
        before_cleanup=observe_retained_publication,
    )
    assert result.dataset_manifest_sha256 == prepared.dataset_sha256
    assert prepared.coordinator.pending() == ()
    assert before_cleanup == [prepared.command.checkpoint_path]
    assert not prepared.command.input_root.exists()
    status = prepared.repository.status(prepared.ticket)
    assert status is not None and status.state == ExecutionState.CLOSED
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0
    _, checkpoint = load_sona_checkpoint(prepared.output_root / str(prepared.ticket.execution_id))
    assert checkpoint.checkpoint_manifest_sha256 == result.checkpoint_manifest_sha256
    assert checkpoint.training_authority is not None
    prepared.work.check_checkpoint(
        checkpoint.training_authority, checkpoint.checkpoint_manifest_sha256
    )
    with pytest.raises(ResourceAdmissionError, match="training_execution_stale"):
        prepared.coordinator.run(
            prepared.ticket,
            prepared.command,
            PostgresSonaTrainingAuthority(prepared.work, prepared.run_id),
        )
    assert prepared.coordinator.pending() == ()
    service(pair).decide(pair.actor, consent_command(pair, "WITHDRAWN", 1))
    with Session(pair.engine) as session:
        claim = session.get(TrainingCleanupClaimRow, prepared.run_id)
        assert claim is not None and claim.phase == "COMPLETE"


@pytest.mark.usefixtures("internal_io_budget")
def test_cli_composes_controlled_prepare_train_publish_and_exact_replay(
    pair: TrainingHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    service(pair).decide(pair.actor, consent_command(pair, "GRANTED"))
    run_id, execution_id, publication_id = uuid4(), uuid4(), uuid4()
    input_scope = (tmp_path / "production-input").absolute()
    input_scope.mkdir()
    input_root = input_scope / str(execution_id)
    _owner_input(pair, input_root)
    output_root = (tmp_path / "production-output").absolute()
    output_root.mkdir()
    lineage_key = (tmp_path / "lineage-key").absolute()
    lineage_key.write_bytes(b"k" * 32)
    vault_root = (tmp_path / "vault").absolute()
    vault_root.mkdir()
    monkeypatch.delenv("AUTPLAY_CONFIG_FILE", raising=False)
    monkeypatch.setenv(
        "AUTPLAY_DATABASE_URL", pair.engine.url.render_as_string(hide_password=False)
    )
    monkeypatch.setenv("AUTPLAY_VAULT_ROOT", str(vault_root))
    monkeypatch.setenv("AUTPLAY_TRAINING_CONSENT_LEDGER_PATH", str(pair.consent_ledger.path))
    monkeypatch.setenv("AUTPLAY_TRAINING_CONSENT_LEDGER_KEY", "s" * 32)
    monkeypatch.setenv("AUTPLAY_TRAINING_CONSENT_LEDGER_KEY_ID", "fixture-v1")
    with Session(pair.engine) as session, session.begin():
        session.execute(
            text("UPDATE account.internal_io_policy SET workload_version=3 WHERE singleton_id=1")
        )
    arguments = [
        "controlled-train-publish",
        "--run-id",
        str(run_id),
        "--execution-id",
        str(execution_id),
        "--publication-operation-id",
        str(publication_id),
        "--participant",
        f"{pair.actor.user_id}:1",
        "--input-root",
        str(input_root),
        "--output-root",
        str(output_root),
        "--artifact-name",
        "candidate.onnx",
        "--lineage-key-id",
        "fixture-v1",
        "--lineage-key-file",
        str(lineage_key),
        "--maximum-output-bytes",
        str(64 * 1024 * 1024),
        "--device",
        "cpu",
        "--batch-size",
        "8",
        "--codebook-size",
        "17",
        "--model-dimensions",
        "16",
        "--encoder-layers",
        "1",
    ]

    original_publish = TrainingWorkService.publish

    def fail_before_pg_commit(*_: object, **__: object) -> None:
        raise OSError("simulated publication transport failure")

    monkeypatch.setattr(TrainingWorkService, "publish", fail_before_pg_commit)
    assert cli.main(arguments) == 4
    capsys.readouterr()
    candidate = output_root / str(execution_id) / "publication" / "candidate.onnx"
    assert not input_root.exists()
    assert candidate.is_file()
    with Session(pair.engine) as session:
        run = session.get(TrainingRunRow, run_id)
        execution = session.get(TrainingExecutionRow, execution_id)
        assert run is not None and run.phase == "RUNNING"
        assert execution is not None and execution.state == "CLOSED"
        assert execution.publication_seal_sha256 is not None
        assert internal_io_usage(session) == 0

    monkeypatch.setattr(TrainingWorkService, "publish", original_publish)
    publication = candidate.parent
    retained_files = {
        path: path.read_bytes()
        for path in (
            candidate,
            candidate.with_suffix(f"{candidate.suffix}.manifest.json"),
            candidate.with_suffix(f"{candidate.suffix}.commit.json"),
            publication / "intent.json",
        )
    }

    unexpected = candidate.parent.parent / "unexpected-retained-file"
    unexpected.write_bytes(b"must be rejected by exact checkpoint replay")
    assert cli.main(arguments) == 4
    capsys.readouterr()
    unexpected.unlink()

    candidate.write_bytes(candidate.read_bytes() + b"self-consistent-tamper")
    artifact_sha256 = sha256(candidate.read_bytes()).hexdigest()
    manifest_path = candidate.with_suffix(f"{candidate.suffix}.manifest.json")
    manifest_envelope = cast(dict[str, JsonValue], json.loads(manifest_path.read_bytes()))
    manifest = cast(dict[str, JsonValue], manifest_envelope["manifest"])
    manifest["artifact_sha256"] = artifact_sha256
    manifest_sha256 = sha256(rfc8785.dumps(manifest)).hexdigest()
    manifest_path.write_bytes(
        rfc8785.dumps({"manifest": manifest, "manifest_sha256": manifest_sha256})
    )
    commit_path = candidate.with_suffix(f"{candidate.suffix}.commit.json")
    commit: dict[str, JsonValue] = {
        "schema_version": 1,
        "state": "COMMITTED",
        "artifact_sha256": artifact_sha256,
        "model_manifest_sha256": manifest_sha256,
    }
    commit_sha256 = sha256(rfc8785.dumps(commit)).hexdigest()
    commit_path.write_bytes(rfc8785.dumps({"commit": commit, "commit_sha256": commit_sha256}))
    intent_path = publication / "intent.json"
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
    assert cli.main(arguments) == 4
    capsys.readouterr()
    for path, payload in retained_files.items():
        path.write_bytes(payload)

    assert cli.main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["run_id"] == str(run_id)
    assert first["execution_id"] == str(execution_id)
    assert first["publication_operation_id"] == str(publication_id)
    assert not input_root.exists()
    assert candidate.is_file()
    with Session(pair.engine) as session:
        run = session.get(TrainingRunRow, run_id)
        execution = session.get(TrainingExecutionRow, execution_id)
        assert run is not None and run.phase == "PUBLISHED"
        assert execution is not None and execution.state == "CLOSED"
        assert internal_io_usage(session) == 0

    assert cli.main(arguments) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["artifact_sha256"] == first["artifact_sha256"]
    assert replay["model_manifest_sha256"] == first["model_manifest_sha256"]
    assert replay["optimizer_steps"] == first["optimizer_steps"]
    assert replay["device_type"] == first["device_type"]


@pytest.mark.usefixtures("internal_io_budget")
def test_withdrawal_stops_real_child_before_checkpoint_and_releases_only_after_exit(
    pair: TrainingHarness,
    tmp_path: Path,
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    retained_usage: list[int] = []

    def withdraw() -> None:
        service(pair).decide(pair.actor, consent_command(pair, "WITHDRAWN", 1))
        with Session(pair.engine) as session:
            retained_usage.append(internal_io_usage(session))

    authority = _WithdrawingAuthority(
        PostgresSonaTrainingAuthority(prepared.work, prepared.run_id),
        withdraw,
    )
    with pytest.raises(TrainingWorkError, match="training_run_invalidated"):
        prepared.coordinator.run(prepared.ticket, prepared.command, authority)
    assert prepared.coordinator.pending() == ()
    assert retained_usage == [1]
    assert not prepared.command.input_root.exists()
    assert not (prepared.output_root / str(prepared.ticket.execution_id)).exists()
    status = prepared.repository.status(prepared.ticket)
    assert status is not None and status.state == ExecutionState.CLOSED
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0
        claim = session.get(TrainingCleanupClaimRow, prepared.run_id)
        assert claim is not None and claim.phase == "COMPLETE"


@pytest.mark.usefixtures("internal_io_budget")
def test_exited_real_child_stays_charged_until_database_acknowledges_exact_exit(
    pair: TrainingHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    attempted, permit = Event(), Event()
    confirm = prepared.repository.confirm

    def delayed_confirm(*args: object, **kwargs: object) -> object:
        attempted.set()
        if not permit.is_set():
            raise SQLAlchemyError("synthetic training exit acknowledgement outage")
        return confirm(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(prepared.repository, "confirm", delayed_confirm)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            prepared.coordinator.run,
            prepared.ticket,
            prepared.command,
            PostgresSonaTrainingAuthority(prepared.work, prepared.run_id),
        )
        assert attempted.wait(30)
        assert prepared.coordinator.pending() == (prepared.ticket.execution_id,)
        status = prepared.repository.status(prepared.ticket)
        assert status is not None and status.state == ExecutionState.STOPPING
        with Session(pair.engine) as session:
            assert internal_io_usage(session) == 1
        permit.set()
        result = future.result(timeout=30)
    assert result.dataset_manifest_sha256 == prepared.dataset_sha256
    assert prepared.coordinator.pending() == ()
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0


@pytest.mark.usefixtures("internal_io_budget")
def test_output_bound_fails_before_checkpoint_seal_and_cleans_staging(
    pair: TrainingHarness,
    tmp_path: Path,
) -> None:
    prepared = _prepared_execution(pair, tmp_path, maximum_output_bytes=1)
    with pytest.raises(ResourceAdmissionError, match="training_child_failed"):
        prepared.coordinator.run(
            prepared.ticket,
            prepared.command,
            PostgresSonaTrainingAuthority(prepared.work, prepared.run_id),
        )
    assert not (prepared.output_root / str(prepared.ticket.execution_id)).exists()
    assert not list(prepared.output_root.glob(f".{prepared.ticket.execution_id}.*"))
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0
        assert session.get(TrainingCheckpointRow, prepared.run_id) is None


@pytest.mark.usefixtures("internal_io_budget")
def test_cross_run_authority_cannot_use_another_runs_execution(
    pair: TrainingHarness,
    tmp_path: Path,
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    other_run = uuid4()
    prepared.work.register(other_run, source_sha256="a" * 64, participants={pair.actor.user_id: 1})
    prepared.work.ready(other_run, prepared.dataset_sha256)
    with pytest.raises(ResourceAdmissionError, match="training_execution_stale"):
        prepared.coordinator.run(
            prepared.ticket,
            prepared.command,
            PostgresSonaTrainingAuthority(prepared.work, other_run),
        )
    assert prepared.coordinator.pending() == ()
    assert not (prepared.output_root / str(prepared.ticket.execution_id)).exists()
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0
        assert session.get(TrainingCheckpointRow, other_run) is None


@pytest.mark.usefixtures("internal_io_budget")
def test_two_coordinators_cannot_leak_losing_exact_child_or_double_start(
    pair: TrainingHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    first = SonaTrainingProcessCoordinator(
        prepared.repository,
        tree_factory=process_tree_factory(),
        storage_scopes=prepared.storage_scopes,
        maximum=1,
        launch=_fixture_launch(),
    )
    second = SonaTrainingProcessCoordinator(
        prepared.repository,
        tree_factory=process_tree_factory(),
        storage_scopes=prepared.storage_scopes,
        maximum=1,
        launch=_fixture_launch(),
    )
    barrier = Barrier(2)
    start = prepared.repository.start

    def raced_start(*args: object, **kwargs: object) -> object:
        barrier.wait(timeout=10)
        return start(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(prepared.repository, "start", raced_start)
    results: list[object] = []
    failures: list[ResourceAdmissionError] = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                coordinator.run,
                prepared.ticket,
                prepared.command,
                PostgresSonaTrainingAuthority(prepared.work, prepared.run_id),
            )
            for coordinator in (first, second)
        ]
        for future in futures:
            try:
                results.append(future.result(timeout=30))
            except ResourceAdmissionError as error:
                failures.append(error)
    assert len(results) == 1, [str(error) for error in failures]
    assert len(failures) == 1 and str(failures[0]) == "training_execution_stale"
    assert first.pending() == second.pending() == ()
    status = prepared.repository.status(prepared.ticket)
    assert status is not None and status.state == ExecutionState.CLOSED
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0


@pytest.mark.usefixtures("internal_io_budget")
@pytest.mark.parametrize("mode", ["early-result", "mismatched-result", "tampered-weight"])
def test_terminal_result_requires_matching_authorize_and_seal_chain(
    pair: TrainingHarness,
    tmp_path: Path,
    mode: str,
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    coordinator = SonaTrainingProcessCoordinator(
        prepared.repository,
        tree_factory=process_tree_factory(),
        storage_scopes=prepared.storage_scopes,
        maximum=1,
        launch=_fixture_launch(mode),
    )
    with pytest.raises(ResourceAdmissionError, match="training_child_failed"):
        coordinator.run(
            prepared.ticket,
            prepared.command,
            PostgresSonaTrainingAuthority(prepared.work, prepared.run_id),
        )
    assert coordinator.pending() == ()
    assert not prepared.command.checkpoint_path.exists()
    assert not prepared.command.checkpoint_staging_path.exists()
    status = prepared.repository.status(prepared.ticket)
    assert status is not None and status.state == ExecutionState.CLOSED
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0


@pytest.mark.usefixtures("internal_io_budget")
def test_output_verification_failure_retains_capacity_until_exact_retry_succeeds(
    pair: TrainingHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    coordinator = SonaTrainingProcessCoordinator(
        prepared.repository,
        tree_factory=process_tree_factory(),
        storage_scopes=prepared.storage_scopes,
        maximum=1,
        launch=_fixture_launch(),
    )
    attempted, permit = Event(), Event()
    finalize = finalize_checkpoint_output

    def delayed_finalize(*args: object, **kwargs: object) -> object:
        attempted.set()
        if not permit.is_set():
            raise OSError("synthetic checkpoint verification outage")
        return finalize(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        controlled_execution_module,
        "finalize_checkpoint_output",
        delayed_finalize,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            coordinator.run,
            prepared.ticket,
            prepared.command,
            PostgresSonaTrainingAuthority(prepared.work, prepared.run_id),
        )
        assert attempted.wait(15)
        assert coordinator.pending() == (prepared.ticket.execution_id,)
        status = prepared.repository.status(prepared.ticket)
        assert status is not None and status.state == ExecutionState.STOPPING
        with Session(pair.engine) as session:
            assert internal_io_usage(session) == 1
        permit.set()
        result = future.result(timeout=15)
    envelope = json.loads((prepared.command.checkpoint_path / "manifest.json").read_bytes())
    assert result.checkpoint_manifest_sha256 == envelope["manifest_sha256"]
    assert coordinator.pending() == ()
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0


@pytest.mark.usefixtures("internal_io_budget")
@pytest.mark.parametrize("tamper_checkpoint", [False, True])
def test_new_coordinator_recovers_exit_recorded_before_output_verification(
    pair: TrainingHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tamper_checkpoint: bool,
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    coordinator = SonaTrainingProcessCoordinator(
        prepared.repository,
        tree_factory=process_tree_factory(),
        storage_scopes=prepared.storage_scopes,
        maximum=1,
        launch=_fixture_launch(),
    )
    attempted = Event()
    finalize = finalize_checkpoint_output

    def stranded_finalize(*args: object, **kwargs: object) -> object:
        if current_thread().name == "training-work":
            attempted.set()
            raise OSError("synthetic supervisor crash after durable exact exit")
        return finalize(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        controlled_execution_module,
        "finalize_checkpoint_output",
        stranded_finalize,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            coordinator.run,
            prepared.ticket,
            prepared.command,
            PostgresSonaTrainingAuthority(prepared.work, prepared.run_id),
        )
        assert attempted.wait(15)
        status = prepared.repository.status(prepared.ticket)
        assert status is not None and status.state == ExecutionState.STOPPING
        with Session(pair.engine) as session:
            assert internal_io_usage(session) == 1
        if tamper_checkpoint:
            next(prepared.command.checkpoint_path.rglob("*.npy")).write_bytes(b"tampered")
        recovered = SonaTrainingProcessCoordinator(
            prepared.repository,
            tree_factory=process_tree_factory(),
            storage_scopes=prepared.storage_scopes,
            maximum=1,
        )
        assert recovered.recover_pending() == (prepared.ticket.execution_id,)
        if tamper_checkpoint:
            with pytest.raises(ResourceAdmissionError, match="training_child_failed"):
                future.result(timeout=15)
        else:
            result = future.result(timeout=15)
            assert result.weights_sha256 != "d" * 64
    assert coordinator.pending() == recovered.pending() == ()
    assert prepared.command.checkpoint_path.exists() == (not tamper_checkpoint)
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0


@pytest.mark.usefixtures("internal_io_budget")
def test_input_cleanup_failure_retains_capacity_until_exact_retry_succeeds(
    pair: TrainingHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    coordinator = SonaTrainingProcessCoordinator(
        prepared.repository,
        tree_factory=process_tree_factory(),
        storage_scopes=prepared.storage_scopes,
        maximum=1,
        launch=_fixture_launch(),
    )
    attempted, permit = Event(), Event()

    def delayed_cleanup(*args: object, **kwargs: object) -> str:
        attempted.set()
        if not permit.is_set():
            raise OSError("synthetic training input cleanup outage")
        return finalize_training_input(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        controlled_execution_module,
        "finalize_training_input",
        delayed_cleanup,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            coordinator.run,
            prepared.ticket,
            prepared.command,
            PostgresSonaTrainingAuthority(prepared.work, prepared.run_id),
        )
        assert attempted.wait(15)
        assert coordinator.pending() == (prepared.ticket.execution_id,)
        assert prepared.command.input_root.exists()
        status = prepared.repository.status(prepared.ticket)
        assert status is not None and status.state == ExecutionState.STOPPING
        with Session(pair.engine) as session:
            assert internal_io_usage(session) == 1
        permit.set()
        future.result(timeout=15)
    assert not prepared.command.input_root.exists()
    assert coordinator.pending() == ()
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0


@pytest.mark.usefixtures("internal_io_budget")
@pytest.mark.parametrize("partially_erased", [False, True])
def test_new_coordinator_replays_durable_cleanup_after_supervisor_crash(
    pair: TrainingHarness,
    tmp_path: Path,
    *,
    partially_erased: bool,
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    identity = ProcessIdentity(5252, b"r" * 32)
    prepared.repository.prepare(prepared.ticket)
    prepared.repository.start(prepared.ticket, identity)
    inventory = inspect_sona_training_root(prepared.command.input_root)
    reservation = reserve_checkpoint_output(prepared.command, inventory)
    prepared.repository.bind_checkpoint(
        prepared.ticket,
        identity,
        device=str(reservation.device),
        inode=str(reservation.inode),
    )
    prepared.repository.begin_cleanup(
        prepared.ticket,
        ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, -9, identity),
        retain_checkpoint=False,
        checkpoint_manifest_sha256=None,
    )
    tombstone = (
        prepared.command.input_root.parent / f".{prepared.ticket.execution_id}.input-cleanup"
    )
    if partially_erased:
        prepared.command.input_root.replace(tombstone)
        next(path for path in tombstone.rglob("*") if path.is_file()).unlink()
        prepared.command.checkpoint_staging_path.replace(
            prepared.command.checkpoint_staging_path.with_name(
                f"{prepared.command.checkpoint_staging_path.name}.cleanup"
            )
        )

    recovered = SonaTrainingProcessCoordinator(
        prepared.repository,
        tree_factory=process_tree_factory(),
        storage_scopes=prepared.storage_scopes,
        maximum=1,
    )
    assert recovered.recover_pending() == (prepared.ticket.execution_id,)
    assert recovered.recover_pending() == ()
    assert not prepared.command.input_root.exists()
    assert not tombstone.exists()
    assert not prepared.command.checkpoint_path.exists()
    assert not prepared.command.checkpoint_staging_path.exists()
    assert not prepared.command.checkpoint_path.with_name(
        f"{prepared.command.checkpoint_path.name}.cleanup"
    ).exists()
    assert not prepared.command.checkpoint_staging_path.with_name(
        f"{prepared.command.checkpoint_staging_path.name}.cleanup"
    ).exists()
    status = prepared.repository.status(prepared.ticket)
    assert status is not None and status.state == ExecutionState.CLOSED
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0


@pytest.mark.usefixtures("internal_io_budget")
def test_recovery_refuses_owned_input_inode_renamed_inside_exclusive_scope(
    pair: TrainingHarness,
    tmp_path: Path,
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    identity = ProcessIdentity(5252, b"r" * 32)
    prepared.repository.prepare(prepared.ticket)
    prepared.repository.start(prepared.ticket, identity)
    prepared.repository.begin_cleanup(
        prepared.ticket,
        ProcessExitEvidence(ExitKind.PROCESS_EXIT, b"e" * 32, -9, identity),
        retain_checkpoint=False,
        checkpoint_manifest_sha256=None,
    )
    escaped = prepared.command.input_root.parent / "unexpected-owned-directory"
    prepared.command.input_root.replace(escaped)
    recovered = SonaTrainingProcessCoordinator(
        prepared.repository,
        tree_factory=process_tree_factory(),
        storage_scopes=prepared.storage_scopes,
        maximum=1,
    )
    with pytest.raises(ValueError, match="escaped cleanup"):
        recovered.recover_pending()
    status = prepared.repository.status(prepared.ticket)
    assert status is not None and status.state == ExecutionState.STOPPING
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 1
    escaped.replace(prepared.command.input_root)
    assert recovered.recover_pending() == (prepared.ticket.execution_id,)
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0


@pytest.mark.skipif(os.name != "nt", reason="actual Windows offline process evidence")
@pytest.mark.usefixtures("internal_io_budget")
def test_offline_restore_drain_requires_absent_pid_then_replays_training_cleanup(
    pair: TrainingHarness,
    tmp_path: Path,
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    absent = ProcessIdentity(4_000_000_000, b"o" * 32)
    prepared.repository.prepare(prepared.ticket)
    prepared.repository.start(prepared.ticket, absent)

    report = PostgresOfflineExecutionDrain(
        sessionmaker(pair.engine, expire_on_commit=False),
        OfflineProcessEvidenceProbe(None),
    ).close_restored_reservations()
    assert report.training_stopping == 1
    assert report.checked_pids == 1
    status = prepared.repository.status(prepared.ticket)
    assert status is not None and status.state == ExecutionState.STOPPING
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 1

    assert prepared.coordinator.recover_pending() == (prepared.ticket.execution_id,)
    assert not prepared.command.input_root.exists()
    status = prepared.repository.status(prepared.ticket)
    assert status is not None and status.state == ExecutionState.CLOSED
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0


@pytest.mark.skipif(os.name != "nt", reason="actual Windows offline process evidence")
@pytest.mark.usefixtures("internal_io_budget")
def test_restore_drain_cli_composes_database_evidence_and_training_cleanup(
    pair: TrainingHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    absent = ProcessIdentity(4_000_000_000, b"c" * 32)
    prepared.repository.prepare(prepared.ticket)
    prepared.repository.start(prepared.ticket, absent)
    settings = WorkerSettings(
        database_url=SecretStr(pair.engine.url.render_as_string(hide_password=False))
    )
    monkeypatch.setattr("autplay.runtime.settings.load_worker_settings", lambda: settings)

    assert (
        cli.main(
            [
                "restore-drain",
                "--input-root",
                str(prepared.storage_scopes.input_root),
                "--output-root",
                str(prepared.storage_scopes.output_root),
                "--limit",
                "1",
            ]
        )
        == 0
    )

    output = capsys.readouterr()
    assert output.err == ""
    report = json.loads(output.out)
    assert report["training_executions"] == report["checked_pids"] == 1
    assert report["recovered_training_executions"] == 1
    assert not prepared.command.input_root.exists()
    status = prepared.repository.status(prepared.ticket)
    assert status is not None and status.state == ExecutionState.CLOSED
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0


@pytest.mark.usefixtures("internal_io_budget")
def test_start_failure_closes_owned_prepared_execution_after_exact_child_exit(
    pair: TrainingHarness,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepared_execution(pair, tmp_path)
    coordinator = SonaTrainingProcessCoordinator(
        prepared.repository,
        tree_factory=process_tree_factory(),
        storage_scopes=prepared.storage_scopes,
        maximum=1,
        launch=_fixture_launch(),
    )

    def failed_start(*args: object, **kwargs: object) -> object:
        raise SQLAlchemyError("synthetic start outage")

    monkeypatch.setattr(prepared.repository, "start", failed_start)
    with pytest.raises(ResourceAdmissionError, match="training_execution_unavailable"):
        coordinator.run(
            prepared.ticket,
            prepared.command,
            PostgresSonaTrainingAuthority(prepared.work, prepared.run_id),
        )
    assert coordinator.pending() == ()
    status = prepared.repository.status(prepared.ticket)
    assert status is not None and status.state == ExecutionState.CLOSED
    with Session(pair.engine) as session:
        assert internal_io_usage(session) == 0
