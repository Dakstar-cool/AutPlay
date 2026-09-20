"""Training control-plane authority; filesystem candidates alone are never published."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from hmac import new as new_hmac
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models.account import UserAccountRow
from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.models.training_consent import TrainingConsentRow
from autplay.adapters.postgresql.models.training_work import (
    TrainingCheckpointRow,
    TrainingParticipantRow,
    TrainingPublicationRevocationRow,
    TrainingRunRow,
)
from autplay.application.training_consent import MAX_REVISION, TrainingConsentService
from autplay.domain.profile_pairing import canonical_sha256
from autplay.domain.training_work import TrainingInputProvenance
from autplay.ports.training_consent import TrainingConsentLedger

PUBLICATION_HASH_KEYS = frozenset(
    {
        "artifact_sha256",
        "manifest_sha256",
        "checkpoint_sha256",
        "tokenizer_manifest_sha256",
        "tokenizer_sha256",
    }
)


class TrainingWorkError(RuntimeError):
    pass


@dataclass(frozen=True)
class TrainingRunView:
    run_id: UUID
    phase: str
    revision: int
    dataset_sha256: str | None


def _digest(value: str) -> bytes:
    if not isinstance(value, str) or re.fullmatch("[0-9a-f]{64}", value) is None:
        raise ValueError("invalid training digest")
    return bytes.fromhex(value)


class TrainingWorkService:
    """Separate current DB authority, called only after complete lineage verification.

    This registry issues no process permit and performs no file I/O. The preparation,
    worker and cleanup compositions must retain their actual process/byte authority.
    """

    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        server_instance_id: UUID,
        identity_epoch: int,
        lineage_key_id: str,
        lineage_key: bytes,
        consent_ledger: TrainingConsentLedger,
    ) -> None:
        if (
            type(identity_epoch) is not int
            or not 1 <= identity_epoch <= MAX_REVISION
            or len(lineage_key) != 32
            or re.fullmatch("[A-Za-z0-9_.-]{1,128}", lineage_key_id) is None
        ):
            raise ValueError("invalid current training authority")
        self._sessions = sessions
        self._ledger = consent_ledger
        self._consent = TrainingConsentService(sessions, consent_ledger)
        self._server_id, self._epoch = server_instance_id, identity_epoch
        self._key_id, self._key = lineage_key_id, bytes(lineage_key)

    def _identity(self, session: Session) -> None:
        if session.scalar(text("SHOW transaction_isolation")) != "read committed":
            raise TrainingWorkError("training_work_isolation_required")
        identity = session.get(
            ServerInstanceRow, self._server_id, with_for_update=True, populate_existing=True
        )
        if identity is None or identity.identity_epoch != self._epoch:
            raise TrainingWorkError("training_identity_changed")

    def register(
        self, run_id: UUID, *, source_sha256: str, participants: Mapping[UUID, int]
    ) -> TrainingRunView:
        if not 1 <= len(participants) <= 4096 or any(
            not isinstance(owner, UUID)
            or type(revision) is not int
            or not 1 <= revision <= MAX_REVISION
            for owner, revision in participants.items()
        ):
            raise ValueError("invalid training participant set")
        owners = sorted(participants, key=lambda owner: owner.int)
        source = _digest(source_sha256)
        tags = {owner: new_hmac(self._key, owner.bytes, sha256).digest() for owner in owners}
        request_hash = canonical_sha256(
            {
                "run_id": str(run_id),
                "source_sha256": source_sha256,
                "server_instance_id": str(self._server_id),
                "identity_epoch": self._epoch,
                "lineage_key_id": self._key_id,
                "participants": [
                    {"owner": str(owner), "revision": participants[owner], "tag": tags[owner].hex()}
                    for owner in owners
                ],
            }
        )
        with self._sessions.begin() as session:
            self._identity(session)
            for owner in owners:
                TrainingConsentService.lock_account(session, owner)
                session.get(TrainingConsentRow, owner, with_for_update=True, populate_existing=True)
            history = self._ledger.read()
            for owner in owners:
                self._consent.require_granted(session, owner, participants[owner], history=history)
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,55))"),
                {"key": "training-run:" + str(run_id)},
            )
            existing = session.get(
                TrainingRunRow, run_id, with_for_update=True, populate_existing=True
            )
            if existing is not None:
                if existing.request_sha256 != request_hash:
                    raise TrainingWorkError("training_operation_conflict")
                if existing.phase == "INVALIDATED":
                    raise TrainingWorkError("training_run_invalidated")
                return self._view(existing)
            now = TrainingConsentService._now(session)
            run = TrainingRunRow(
                run_id=run_id,
                server_instance_id=self._server_id,
                identity_epoch=self._epoch,
                lineage_key_id=self._key_id,
                source_sha256=source,
                request_sha256=request_hash,
                participant_count=len(owners),
                phase="PREPARING",
                revision=1,
                created_at=now,
                changed_at=now,
            )
            session.add(run)
            session.flush()
            session.add_all(
                [
                    TrainingParticipantRow(
                        run_id=run_id,
                        user_id=owner,
                        consent_revision=participants[owner],
                        owner_tag=tags[owner],
                    )
                    for owner in owners
                ]
            )
            session.flush()
            return self._view(run)

    def _locked(self, session: Session, run_id: UUID) -> TrainingRunRow:
        self._identity(session)
        members = list(
            session.scalars(
                select(TrainingParticipantRow)
                .where(TrainingParticipantRow.run_id == run_id)
                .order_by(TrainingParticipantRow.user_id)
            )
        )
        # Lock raw policy before the run even when currently private: an exact published
        # receipt must remain recoverable after withdrawal, without reversing lock order.
        for member in members:
            session.get(
                UserAccountRow, member.user_id, with_for_update=True, populate_existing=True
            )
            session.get(
                TrainingConsentRow, member.user_id, with_for_update=True, populate_existing=True
            )
        run = session.get(TrainingRunRow, run_id, with_for_update=True, populate_existing=True)
        if (
            run is None
            or run.server_instance_id != self._server_id
            or run.identity_epoch != self._epoch
            or run.lineage_key_id != self._key_id
        ):
            raise TrainingWorkError("training_run_unavailable")
        if run.phase != "PUBLISHED":
            if run.phase == "INVALIDATED" or len(members) != run.participant_count:
                raise TrainingWorkError("training_run_invalidated")
            history = self._ledger.read()
            for member in members:
                self._consent.require_granted(
                    session, member.user_id, member.consent_revision, history=history
                )
        return run

    @staticmethod
    def _view(run: TrainingRunRow) -> TrainingRunView:
        return TrainingRunView(
            run.run_id,
            run.phase,
            run.revision,
            None if run.dataset_sha256 is None else run.dataset_sha256.hex(),
        )

    @staticmethod
    def _transition(session: Session, run: TrainingRunRow, phase: str) -> None:
        if run.revision >= MAX_REVISION:
            raise TrainingWorkError("training_revision_exhausted")
        run.phase, run.revision = phase, run.revision + 1
        run.changed_at = TrainingConsentService._now(session)
        session.flush()

    def ready(self, run_id: UUID, dataset_sha256: str) -> TrainingRunView:
        digest = _digest(dataset_sha256)
        with self._sessions.begin() as session:
            run = self._locked(session, run_id)
            if run.phase == "READY" and run.dataset_sha256 == digest:
                return self._view(run)
            if run.phase != "PREPARING":
                raise TrainingWorkError("training_phase_conflict")
            run.dataset_sha256 = digest
            self._transition(session, run, "READY")
            return self._view(run)

    def start(self, run_id: UUID) -> TrainingRunView:
        with self._sessions.begin() as session:
            run = self._locked(session, run_id)
            if run.phase == "READY":
                self._transition(session, run, "RUNNING")
            elif run.phase != "RUNNING":
                raise TrainingWorkError("training_phase_conflict")
            return self._view(run)

    def check_running(self, run_id: UUID) -> None:
        with self._sessions.begin() as session:
            if self._locked(session, run_id).phase != "RUNNING":
                raise TrainingWorkError("training_phase_conflict")

    def lock_execution_authority(
        self, session: Session, run_id: UUID, *, running: bool
    ) -> TrainingRunView:
        """Lock SQL and independent-consent authority inside a worker transaction."""

        run = self._locked(session, run_id)
        permitted = {"RUNNING"} if running else {"READY", "RUNNING"}
        if run.phase not in permitted:
            raise TrainingWorkError("training_phase_conflict")
        return self._view(run)

    def lock_execution_identity(self, session: Session) -> None:
        """Take the server-identity lock before the shared admission lock."""

        self._identity(session)

    @property
    def execution_identity(self) -> tuple[UUID, int]:
        return self._server_id, self._epoch

    def _input_binding(
        self,
        session: Session,
        run: TrainingRunRow,
        *,
        source_sha256: str,
        dataset_sha256: str,
        lineage_key_id: str,
        owner_tokens: tuple[str, ...],
    ) -> None:
        if not 1 <= len(owner_tokens) <= 4096 or len(set(owner_tokens)) != len(owner_tokens):
            raise ValueError("invalid training input contributors")
        supplied = {_digest(token) for token in owner_tokens}
        registered = set(
            session.scalars(
                select(TrainingParticipantRow.owner_tag).where(
                    TrainingParticipantRow.run_id == run.run_id
                )
            )
        )
        if (
            run.source_sha256 != _digest(source_sha256)
            or run.dataset_sha256 != _digest(dataset_sha256)
            or run.lineage_key_id != lineage_key_id
            or supplied != registered
        ):
            raise TrainingWorkError("training_input_binding_mismatch")

    def authorize_input(
        self,
        run_id: UUID,
        *,
        source_sha256: str,
        dataset_sha256: str,
        lineage_key_id: str,
        owner_tokens: tuple[str, ...],
    ) -> TrainingInputProvenance:
        """Atomically bind the actual verified inputs before admitting new training."""
        with self._sessions.begin() as session:
            run = self._locked(session, run_id)
            self._input_binding(
                session,
                run,
                source_sha256=source_sha256,
                dataset_sha256=dataset_sha256,
                lineage_key_id=lineage_key_id,
                owner_tokens=owner_tokens,
            )
            if run.phase == "READY":
                self._transition(session, run, "RUNNING")
            elif run.phase != "RUNNING":
                raise TrainingWorkError("training_phase_conflict")
            return self._input_provenance(run)

    @staticmethod
    def _input_provenance(run: TrainingRunRow) -> TrainingInputProvenance:
        if run.dataset_sha256 is None:
            raise TrainingWorkError("training_input_authority_missing")
        # Registration seals the full owner/revision/tag set. Keep its digest, not raw
        # identities or thousands of owner tokens, in restartable model metadata.
        digest = canonical_sha256(
            {
                "schema_version": 1,
                "run_id": str(run.run_id),
                "server_instance_id": str(run.server_instance_id),
                "identity_epoch": run.identity_epoch,
                "lineage_key_id": run.lineage_key_id,
                "registration_sha256": run.request_sha256.hex(),
                "source_sha256": run.source_sha256.hex(),
                "dataset_sha256": run.dataset_sha256.hex(),
            }
        ).hex()
        return TrainingInputProvenance(
            run.run_id, run.server_instance_id, run.identity_epoch, digest
        )

    def check_candidate(self, provenance: TrainingInputProvenance) -> None:
        with self._sessions.begin() as session:
            run = self._locked(session, provenance.run_id)
            if self._input_provenance(run) != provenance:
                raise TrainingWorkError("training_input_binding_mismatch")
            if run.phase != "RUNNING":
                raise TrainingWorkError("training_phase_conflict")

    def seal_checkpoint(self, provenance: TrainingInputProvenance, manifest_sha256: str) -> None:
        digest = _digest(manifest_sha256)
        with self._sessions.begin() as session:
            run = self._locked(session, provenance.run_id)
            if self._input_provenance(run) != provenance:
                raise TrainingWorkError("training_input_binding_mismatch")
            if run.phase != "RUNNING":
                raise TrainingWorkError("training_phase_conflict")
            existing = session.get(TrainingCheckpointRow, run.run_id)
            if existing is not None:
                if existing.manifest_sha256 != digest:
                    raise TrainingWorkError("training_checkpoint_conflict")
                return
            session.add(TrainingCheckpointRow(run_id=run.run_id, manifest_sha256=digest))
            session.flush()

    def check_checkpoint(self, provenance: TrainingInputProvenance, manifest_sha256: str) -> None:
        digest = _digest(manifest_sha256)
        with self._sessions.begin() as session:
            run = self._locked(session, provenance.run_id)
            sealed = session.get(TrainingCheckpointRow, run.run_id)
            if (
                self._input_provenance(run) != provenance
                or sealed is None
                or sealed.manifest_sha256 != digest
            ):
                raise TrainingWorkError("training_checkpoint_seal_required")
            if run.phase != "RUNNING":
                raise TrainingWorkError("training_phase_conflict")

    def check_input(
        self,
        run_id: UUID,
        *,
        source_sha256: str,
        dataset_sha256: str,
        lineage_key_id: str,
        owner_tokens: tuple[str, ...],
    ) -> None:
        with self._sessions.begin() as session:
            run = self._locked(session, run_id)
            self._input_binding(
                session,
                run,
                source_sha256=source_sha256,
                dataset_sha256=dataset_sha256,
                lineage_key_id=lineage_key_id,
                owner_tokens=owner_tokens,
            )
            if run.phase != "RUNNING":
                raise TrainingWorkError("training_phase_conflict")

    def publish(
        self,
        run_id: UUID,
        operation_id: UUID,
        hashes: Mapping[str, str],
        *,
        input_provenance: TrainingInputProvenance | None = None,
    ) -> TrainingRunView:
        if set(hashes) != PUBLICATION_HASH_KEYS:
            raise ValueError("invalid training publication tuple")
        for value in hashes.values():
            _digest(value)
        publication = dict(hashes)
        request_hash = canonical_sha256(
            {"run_id": str(run_id), "operation_id": str(operation_id), "hashes": publication}
        )
        with self._sessions.begin() as session:
            run = self._locked(session, run_id)
            if input_provenance is not None and self._input_provenance(run) != input_provenance:
                raise TrainingWorkError("training_input_binding_mismatch")
            checkpoint = session.get(TrainingCheckpointRow, run_id)
            if (
                checkpoint is None
                or checkpoint.manifest_sha256.hex() != hashes["checkpoint_sha256"]
            ):
                raise TrainingWorkError("training_checkpoint_seal_required")
            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,55))"),
                {"key": "training-publication:" + str(operation_id)},
            )
            if run.phase == "PUBLISHED":
                if (
                    run.publication_operation_id != operation_id
                    or run.publication_sha256 != request_hash
                    or run.publication_hashes != publication
                ):
                    raise TrainingWorkError("training_operation_conflict")
                return self._view(run)
            if run.phase != "RUNNING":
                raise TrainingWorkError("training_phase_conflict")
            prior = session.scalar(
                select(TrainingRunRow.run_id).where(
                    TrainingRunRow.publication_operation_id == operation_id
                )
            )
            if prior is not None:
                raise TrainingWorkError("training_operation_conflict")
            run.publication_operation_id, run.publication_sha256 = operation_id, request_hash
            run.publication_hashes = publication
            self._transition(session, run, "PUBLISHED")
            return self._view(run)

    def is_published(
        self,
        run_id: UUID,
        hashes: Mapping[str, str],
        *,
        input_provenance: TrainingInputProvenance | None = None,
    ) -> bool:
        """Completed models remain authorized after ordinary withdrawal, not by live grants."""
        if set(hashes) != PUBLICATION_HASH_KEYS:
            return False
        for value in hashes.values():
            _digest(value)
        with self._sessions.begin() as session:
            try:
                self._identity(session)
            except TrainingWorkError:
                return False
            run = session.get(TrainingRunRow, run_id)
            checkpoint = session.get(TrainingCheckpointRow, run_id)
            revoked = session.scalar(
                select(TrainingPublicationRevocationRow.run_id)
                .where(TrainingPublicationRevocationRow.run_id == run_id)
                .limit(1)
            )
            return (
                run is not None
                and run.server_instance_id == self._server_id
                and run.identity_epoch == self._epoch
                and run.lineage_key_id == self._key_id
                and run.phase == "PUBLISHED"
                and run.publication_hashes == dict(hashes)
                and checkpoint is not None
                and checkpoint.manifest_sha256.hex() == hashes["checkpoint_sha256"]
                and revoked is None
                and (input_provenance is None or self._input_provenance(run) == input_provenance)
            )
