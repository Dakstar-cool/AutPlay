"""Read-only current PostgreSQL authority for serving completed Sona models."""

from __future__ import annotations

import re
from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.models.profile_pairing import ServerInstanceRow
from autplay.adapters.postgresql.models.training_work import (
    TrainingCheckpointRow,
    TrainingPublicationRevocationRow,
    TrainingRunRow,
)
from autplay.application.training_work import PUBLICATION_HASH_KEYS
from autplay.domain.profile_pairing import canonical_sha256
from autplay.domain.training_work import TrainingInputProvenance

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PostgresTrainingPublicationAuthority:
    """Verify one exact published tuple without reviving live consent authority."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def is_published(
        self,
        run_id: UUID,
        hashes: Mapping[str, str],
        *,
        input_provenance: TrainingInputProvenance | None = None,
    ) -> bool:
        if (
            input_provenance is None
            or run_id != input_provenance.run_id
            or set(hashes) != PUBLICATION_HASH_KEYS
            or any(
                not isinstance(value, str) or _SHA256.fullmatch(value) is None
                for value in hashes.values()
            )
        ):
            return False
        expected = dict(hashes)
        with self._sessions.begin() as session:
            identity = session.get(
                ServerInstanceRow,
                input_provenance.server_instance_id,
                with_for_update={"read": True},
                populate_existing=True,
            )
            if identity is None or identity.identity_epoch != input_provenance.identity_epoch:
                return False
            run = session.get(
                TrainingRunRow,
                run_id,
                with_for_update={"read": True},
                populate_existing=True,
            )
            checkpoint = session.get(TrainingCheckpointRow, run_id)
            revoked = session.scalar(
                select(TrainingPublicationRevocationRow.run_id)
                .where(TrainingPublicationRevocationRow.run_id == run_id)
                .limit(1)
            )
            if (
                run is None
                or checkpoint is None
                or run.dataset_sha256 is None
                or revoked is not None
            ):
                return False
            binding = canonical_sha256(
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
            return (
                run.server_instance_id == input_provenance.server_instance_id
                and run.identity_epoch == input_provenance.identity_epoch
                and binding == input_provenance.binding_sha256
                and run.phase == "PUBLISHED"
                and run.publication_hashes == expected
                and checkpoint.manifest_sha256.hex() == expected["checkpoint_sha256"]
            )


__all__ = ("PostgresTrainingPublicationAuthority",)
