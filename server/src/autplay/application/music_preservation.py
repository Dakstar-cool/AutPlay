"""Weekly monitoring and original-edition checks, independent of recommendation serving."""

import json
import re
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from autplay.adapters.postgresql.jobs_runtime import PostgresJobRepository
from autplay.adapters.preservation_sources import adapters, https, observations, read_document
from autplay.application.job_worker import JobExecutionContext
from autplay.domain.jobs import JobKey, JobLease, RetryableJobError, TerminalJobError
from autplay.ports.jobs import EnqueueJob

PRESERVE = JobKey("music.preservation.original", 1)
_DIGEST = re.compile("[0-9a-f]{64}")


def original_in_vault(session: Session, digest: str | None) -> bool:
    if not digest or not _DIGEST.fullmatch(digest):
        return False
    return bool(
        session.scalar(
            text("""
        SELECT EXISTS(SELECT FROM vault.vault_object o
          JOIN vault.audio_variant v USING(vault_object_id)
          JOIN catalog.recording r USING(recording_id) WHERE o.sha256=decode(:digest,'hex')
          AND o.commit_status='COMMITTED' AND v.deleted_at IS NULL AND v.validation_status='VALID'
          AND r.deleted_at IS NULL)
    """),
            {"digest": digest},
        )
    )


def read_grants(root: Path) -> dict[str, Any]:
    try:
        grants = read_document(root / "originals.json")
    except FileNotFoundError:
        return {}
    if not isinstance(grants, dict) or len(grants) > 10000:
        raise ValueError("invalid_originals_file")
    return grants


def authorized_source(row: dict[str, Any], grants: dict[str, Any]) -> dict[str, Any] | None:
    entry = grants.get(row["event"])
    if not isinstance(entry, dict) or any(
        entry.get(k) != row[k] for k in ("artist", "title", "version")
    ):
        return None
    if not isinstance(entry.get("sha256"), str) or not _DIGEST.fullmatch(entry["sha256"]):
        return None
    if row.get("original_sha256", entry["sha256"]) != entry["sha256"]:
        return None
    return entry


def may_acquire(row: dict[str, Any], entry: dict[str, Any] | None) -> bool:
    if (
        row["confidence"] != "confirmed"
        or row["kind"] != "track"
        or row["status"] in {"restored", "retracted"}
        or not entry
        or entry.get("allow_vault") is not True
        or not re.fullmatch(r"[A-Za-z0-9_-]{11}", str(entry.get("candidate_id", "")))
        or type(entry.get("duration_ms")) is not int
        or not 0 < entry["duration_ms"] <= 3600000
    ):
        return False
    try:
        https(entry.get("rights_url"))
        until = datetime.fromisoformat(entry["expires_at"])
        return until.tzinfo is not None and until > datetime.now(UTC)
    except ValueError, KeyError, TypeError:
        return False


def request_run(session: Session, owner: UUID, run: UUID, *, weekly: bool = False) -> None:
    session.execute(
        text("""INSERT INTO discovery.preservation_run(owner,run_id)
        SELECT :owner,:run WHERE NOT :weekly OR NOT EXISTS (
          SELECT FROM discovery.preservation_run WHERE owner=:owner
          AND finished_at > now()-interval '7 days')
        ON CONFLICT DO NOTHING"""),
        {"owner": owner, "run": run, "weekly": weekly},
    )


class PreservationPublisher(Protocol):
    def acquire(
        self,
        candidate: dict[str, Any],
        context: JobExecutionContext,
        *,
        supplemental: bool,
        original_guard: Callable[[], None],
    ) -> UUID | None: ...


class PreservationWatcher:
    def __init__(self, sessions: sessionmaker[Session], owner: UUID, root: Path) -> None:
        self.sessions, self.owner, self.root = sessions, owner, root

    def tick(
        self, sources: Iterable[tuple[str, Callable[[], Iterable[dict[str, Any]]]]] | None = None
    ) -> None:
        with self.sessions.begin() as session:
            request_run(session, self.owner, uuid4(), weekly=True)
        with self.sessions.begin() as session:
            run = session.scalar(
                text("""SELECT run_id FROM discovery.preservation_run WHERE owner=:owner
                AND finished_at IS NULL FOR UPDATE SKIP LOCKED"""),
                {"owner": self.owner},
            )
            if not run:
                return
            results, new, observed = {}, set(), set()
            for name, read in adapters(self.root) if sources is None else sources:
                try:
                    source_rows = [item for row in read() for item in observations(row)]
                except (
                    ValueError,
                    OSError,
                    KeyError,
                    TypeError,
                    SyntaxError,
                    AttributeError,
                    HTTPException,
                ) as error:
                    results[name] = type(error).__name__
                    continue
                results[name] = "ok"
                for row in source_rows:
                    observed.add(row["event"])
                    args = {"owner": self.owner, "event": row["event"], "doc": json.dumps(row)}
                    inserted = session.scalar(
                        text("""INSERT INTO discovery.preservation_case(owner,event,document)
                        VALUES (:owner,:event,cast(:doc AS jsonb))
                        ON CONFLICT DO NOTHING RETURNING event"""),
                        args,
                    )
                    if inserted:
                        if not row.get("collection_event"):
                            new.add(inserted)
                    elif name != "archive" and (name != "musicbrainz" or row["event"] not in new):
                        session.execute(
                            text("""UPDATE discovery.preservation_case
                            SET document=cast(:doc AS jsonb)
                            WHERE owner=:owner AND event=:event
                            AND document IS DISTINCT FROM cast(:doc AS jsonb)
                            AND (:source != 'musicbrainz' OR document->>'confidence' != 'confirmed')
                            """),
                            {**args, "source": name},
                        )
            try:
                grants = read_grants(self.root)
            except (OSError, ValueError) as error:
                grants = {}
                results["originals"] = type(error).__name__
            cases = session.scalars(
                text("SELECT document FROM discovery.preservation_case WHERE owner=:owner"),
                {"owner": self.owner},
            )
            for row in cases:
                event = row["event"]
                outcome, job = self.check(session, row, grants, run)
                session.execute(
                    text("""INSERT INTO discovery.preservation_check
                    (owner,run_id,event,document,is_new,outcome,job_id)
                    VALUES (:owner,:run,:event,cast(:doc AS jsonb),:new,:outcome,:job)"""),
                    {
                        "owner": self.owner,
                        "run": run,
                        "event": event,
                        "doc": json.dumps({**row, "observed": event in observed}),
                        "new": event in new,
                        "outcome": outcome,
                        "job": job,
                    },
                )
            session.execute(
                text("""UPDATE discovery.preservation_run
                SET finished_at=now(),sources=cast(:sources AS jsonb)
                WHERE owner=:owner AND run_id=:run"""),
                {"sources": json.dumps(results), "owner": self.owner, "run": run},
            )

    def collection_status(self, session: Session, row: dict[str, Any]) -> str | None:
        if not row.get("collection_event"):
            return None
        parent = session.scalar(
            text(
                "SELECT document FROM discovery.preservation_case "
                "WHERE owner=:owner AND event=:event"
            ),
            {"owner": self.owner, "event": row["collection_event"]},
        )
        if (
            not parent
            or row["event"] not in parent.get("track_events", ())
            or parent["status"] in {"restored", "retracted"}
        ):
            return "historical"
        return "review" if parent["confidence"] == "review" else None

    def check(
        self, session: Session, row: dict[str, Any], grants: dict[str, Any], run: UUID
    ) -> tuple[str, UUID | None]:
        collection = self.collection_status(session, row)
        if collection:
            return collection, None
        if row["confidence"] == "review":
            return "review", None
        if row["kind"] != "track" or row["status"] in {"retracted", "restored"}:
            return "historical", None
        entry = authorized_source(row, grants)
        if original_in_vault(session, row.get("original_sha256") or (entry or {}).get("sha256")):
            return "present", None
        if entry is None or not may_acquire(row, entry):
            return "unavailable", None
        active = session.scalar(
            text("""SELECT j.job_id FROM jobs.job j WHERE j.job_type=:kind AND j.user_id=:owner
            AND j.payload->>'sha256'=:digest
            AND j.state IN ('QUEUED','RUNNING','RETRY_WAIT','PAUSED') LIMIT 1"""),
            {
                "kind": PRESERVE.job_type,
                "owner": self.owner,
                "digest": entry["sha256"],
            },
        )
        if active:
            return "pending", active
        job = PostgresJobRepository(session).enqueue(
            EnqueueJob(
                key=PRESERVE,
                user_id=self.owner,
                priority=1,
                payload={"event": row["event"], "sha256": entry["sha256"]},
                idempotency_scope=f"preservation:{self.owner}",
                idempotency_key=f"{run}:{row['event']}",
            )
        )
        return "queued", job.job_id

    def acquire(
        self, context: JobExecutionContext, lease: JobLease, publisher: PreservationPublisher
    ) -> None:
        def authorized() -> tuple[dict[str, Any], dict[str, Any]]:
            with self.sessions() as session:
                row = session.scalar(
                    text(
                        "SELECT document FROM discovery.preservation_case "
                        "WHERE owner=:owner AND event=:event"
                    ),
                    {"owner": self.owner, "event": lease.payload["event"]},
                )
                if row and self.collection_status(session, row):
                    raise TerminalJobError("preservation.collection_changed")
            entry = authorized_source(row, read_grants(self.root)) if row else None
            if (
                lease.user_id != self.owner
                or not row
                or entry is None
                or not may_acquire(row, entry)
                or lease.payload["sha256"] != entry["sha256"]
            ):
                raise TerminalJobError("preservation.source_not_authorized")
            return row, entry

        row, entry = authorized()
        with self.sessions() as session:
            if original_in_vault(session, entry["sha256"]):
                return
        candidate = {**entry, "title": row["title"], "artist": row["artist"]}

        def guard() -> None:
            _, current = authorized()
            if (
                current["candidate_id"] != entry["candidate_id"]
                or current["rights_url"] != entry["rights_url"]
            ):
                raise TerminalJobError("preservation.source_changed")

        publisher.acquire(candidate, context, supplemental=False, original_guard=guard)
        with self.sessions() as session:
            if not original_in_vault(session, entry["sha256"]):
                raise RetryableJobError("preservation.vault_pending")
        context.checkpoint({"downloaded": True})


def reports(session: Session, owner: UUID) -> list[dict[str, Any]]:
    runs = session.execute(
        text("""SELECT run_id,requested_at,finished_at,sources FROM discovery.preservation_run
        WHERE owner=:owner ORDER BY requested_at DESC LIMIT 12"""),
        {"owner": owner},
    ).mappings()
    reports = []
    for run in runs:
        checks = session.execute(
            text("""SELECT c.event,c.document,c.is_new,c.outcome,j.state,j.error_code,
                   j.checkpoint,p.discovered_at
            FROM discovery.preservation_check c
            JOIN discovery.preservation_case p USING(owner,event)
            LEFT JOIN jobs.job j USING(job_id)
            WHERE c.owner=:owner AND c.run_id=:run ORDER BY c.event"""),
            {"owner": owner, "run": run["run_id"]},
        ).mappings()
        counts: Counter[str] = Counter()
        details = []
        for check in checks:
            counts["new"] += int(check["is_new"])
            outcome = check["outcome"]
            if check["state"] == "COMPLETED":
                outcome = (
                    "downloaded"
                    if (check["checkpoint"] or {}).get("downloaded") and outcome == "queued"
                    else "present"
                )
            elif check["state"] in ("FAILED", "CANCELLED"):
                outcome = "error"
            counts[outcome] += 1
            details.append(
                (
                    {**check["document"], "first_seen": check["discovered_at"]},
                    outcome,
                    check["error_code"],
                )
            )
        reports.append({**run, "counts": dict(counts), "details": details})
    return reports
