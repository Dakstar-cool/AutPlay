"""Assemble exact teacher-independent historical cases inside one isolated restore."""

from __future__ import annotations

from sqlalchemy import Connection

from autplay.adapters.postgresql.sona_source_planning import (
    SqlAlchemySonaSourcePlanningReader,
)
from autplay.adapters.postgresql.sona_source_reconstruction import (
    SqlAlchemySona0026SourceEventReader,
)
from autplay.application.sona_source_acceptance import (
    SonaSourceProvenanceAcceptance,
    verify_sona_source_provenance_acceptance,
)
from autplay.application.sona_source_materialization import (
    SonaSourceHistoricalInputs,
    build_sona_source_historical_inputs,
)
from autplay.application.sona_source_reconstruction import normalize_sona_0026_events


class SqlAlchemySona0026HistoricalInputReader:
    """Build transient owner-bearing cases without current-state substitution."""

    def __init__(self, connection: Connection) -> None:
        self._planning = SqlAlchemySonaSourcePlanningReader(connection)
        self._events = SqlAlchemySona0026SourceEventReader(connection)

    def read(
        self,
        *,
        source_captured_at_ms: int,
        provenance_acceptance: SonaSourceProvenanceAcceptance,
    ) -> tuple[SonaSourceHistoricalInputs, ...]:
        verify_sona_source_provenance_acceptance(provenance_acceptance)
        records = self._planning.read_records(
            source_captured_at_ms=source_captured_at_ms,
        )
        cases: list[SonaSourceHistoricalInputs] = []
        seen_requests: set[str] = set()
        for record in records:
            request_sha256 = record.observation.request_sha256
            if request_sha256 in seen_requests:
                raise RuntimeError("sona_0026_historical_request_duplicate")
            seen_requests.add(request_sha256)
            accepted_events = self._events.read(
                owner_user_id=record.observation.owner_user_id,
                cutoff_at_ms=record.observation.cutoff_at_ms,
                interaction_watermark=record.interaction_watermark,
            )
            evidence = normalize_sona_0026_events(
                accepted_events,
                acceptance=provenance_acceptance,
            )
            cases.append(
                build_sona_source_historical_inputs(
                    record,
                    evidence=evidence,
                )
            )
        return tuple(cases)


__all__ = ("SqlAlchemySona0026HistoricalInputReader",)
