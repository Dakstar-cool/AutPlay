"""Resume a Vault display-name rewrite inside the API environment (Linux, Python 3.14)."""

import argparse
import fcntl
import json
import os
import sqlite3
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from autplay.application.vault_names import refresh_owner, refresh_recording, targets
from autplay.domain.track_names import DISPLAY_VERSION
from autplay.runtime.settings import load_api_settings
from autplay.runtime.worker_health import CgroupWorkerSampler


def report(**data):
    print(json.dumps(data, sort_keys=True), flush=True)


def counts(journal):
    return dict(journal.execute("SELECT status,count(*) FROM work GROUP BY status"))


def inventory(journal, engine, apply):
    journal.execute("CREATE TABLE IF NOT EXISTS inventory (mode TEXT PRIMARY KEY, database_key TEXT)")
    journal.execute("""CREATE TABLE IF NOT EXISTS work (
        kind TEXT, target TEXT, owner TEXT, status TEXT DEFAULT 'pending',
        attempts INTEGER DEFAULT 0, error TEXT, PRIMARY KEY(kind,target))""")
    journal.execute("CREATE INDEX IF NOT EXISTS work_status ON work(status,attempts,kind,target)")
    mode = DISPLAY_VERSION + ("-apply" if apply else "-preview")
    with Session(engine) as session, journal:
        key = session.scalar(text("SELECT current_database() || ':' || oid::text FROM pg_database "
                                  "WHERE datname=current_database()"))
        saved = journal.execute("SELECT mode,database_key FROM inventory").fetchone()
        if saved is None:
            journal.executemany("INSERT INTO work(kind,target,owner,status) VALUES (?,?,?,?)", targets(session))
            journal.execute("INSERT INTO inventory VALUES (?,?)", (mode, key))
        elif saved != (mode, key):
            raise SystemExit("Use a separate state file for each database and mode.")
        # A later invocation retries previously exhausted rows after the cause has been repaired.
        journal.execute("UPDATE work SET status='pending',attempts=0 WHERE status='failed'")


def cpu_limit():
    limit = len(os.sched_getaffinity(0))
    cpu_max = Path("/sys/fs/cgroup/cpu.max")
    if cpu_max.is_file():
        quota, period = cpu_max.read_text().split()
        if quota != "max":
            limit = min(limit, max(1, int(quota) // int(period)))
    return limit


def capacity(engine, sampler, ceiling, use_all_cpus):
    sample = sampler.sample()
    host_cpus = os.cpu_count() or 1
    host_idle = max(0, 1 - os.getloadavg()[0] / host_cpus)
    cpu = ceiling if use_all_cpus else max(
        1, int(cpu_limit() * min(host_idle, 1 - (sample.cpu_percent or 0) / 100))
    )
    meminfo = Path("/proc/meminfo").read_text().splitlines()
    memory = next(int(line.split()[1]) * 1024 for line in meminfo if line.startswith("MemAvailable:"))
    if sample.memory_limit_bytes is not None and sample.memory_bytes is not None:
        memory = min(memory, sample.memory_limit_bytes - sample.memory_bytes)
    with engine.connect() as db:
        available = db.scalar(text("""SELECT current_setting('max_connections')::int
            - current_setting('superuser_reserved_connections')::int - count(*)
            FROM pg_stat_activity WHERE backend_type='client backend'"""))
    # Allow PostgreSQL maintenance connections and bound per-worker memory.
    return max(0, min(ceiling, cpu, (memory - 64 * 1024**2) // (32 * 1024**2), available - 4))


def process_segment(engine, apply, entries):
    results = []
    for kind, target, owner, attempts in entries:
        try:
            with Session(engine) as session, session.begin():
                session.execute(text("SET LOCAL lock_timeout='1500ms'"))
                if kind == "owner":
                    result = refresh_owner(session, UUID(target), UUID(owner), apply)
                else:
                    result = refresh_recording(session, UUID(target), apply)
            error = None
        except SQLAlchemyError as exc:
            # Do not log bound metadata, titles, credentials or provider responses.
            error = getattr(getattr(exc, "orig", None), "sqlstate", None) or type(exc).__name__
            result = "failed" if attempts >= 2 else "pending"
        results.append((result, error, kind, target))
    return results


def run(args, journal, engine):
    inventory(journal, engine, args.apply)
    sampler = CgroupWorkerSampler()
    ceiling = min(cpu_limit(), args.workers) if args.workers else cpu_limit()
    workers = 0
    sampled = float("-inf")
    reported = float("-inf")
    with ThreadPoolExecutor(max_workers=ceiling) as pool:
        while journal.execute("SELECT 1 FROM work WHERE status='pending' LIMIT 1").fetchone():
            now = time.monotonic()
            if now - sampled >= 5:
                new_limit = capacity(engine, sampler, ceiling, args.use_all_cpus)
                if new_limit != workers or now - reported >= 30:
                    report(workers=new_limit, **counts(journal))
                    reported = now
                workers, sampled = new_limit, now
            if not workers:
                time.sleep(5)
                continue
            rows = journal.execute(
                "SELECT kind,target,owner,attempts FROM work WHERE status='pending' "
                "ORDER BY attempts,kind DESC,target LIMIT ?", (workers * 4,)
            ).fetchall()
            # Publication for one account is serialized by the sync service.
            segments = defaultdict(list)
            for entry in rows:
                segments[entry[2] or entry[1]].append(entry)
            groups = list(segments.values())
            for i in range(0, len(groups), workers):
                futures = [pool.submit(process_segment, engine, args.apply, group)
                           for group in groups[i:i + workers]]
                for future in futures:
                    with journal:
                        journal.executemany("UPDATE work SET status=?,error=?,attempts=attempts+1 "
                                            "WHERE kind=? AND target=?", future.result())
    final = counts(journal)
    report(complete=not final.get("failed"), **final)
    return int(bool(final.get("failed")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True, help="persistent private SQLite journal")
    parser.add_argument("--database-url-file", type=Path, help="mounted database connection secret")
    parser.add_argument("--apply", action="store_true", help="commit name revisions")
    parser.add_argument("--use-all-cpus", action="store_true",
                        help="use all allocated CPUs even while interactive traffic is active")
    parser.add_argument("--workers", type=int, help="upper bound; default adapts to available resources")
    parser.add_argument("--status", action="store_true", help="read the local journal only")
    args = parser.parse_args()
    if args.workers is not None and args.workers < 1:
        parser.error("--workers must be positive")
    os.umask(0o077)
    if args.status:
        with closing(sqlite3.connect(args.state.resolve().as_uri() + "?mode=ro", uri=True)) as journal:
            report(**counts(journal))
        return 0
    args.state.parent.mkdir(parents=True, exist_ok=True)
    database_url = (args.database_url_file.read_text().strip() if args.database_url_file else
                    load_api_settings().database_url.get_secret_value())
    engine = create_engine(database_url, pool_size=min(cpu_limit(), args.workers or cpu_limit()) + 1,
                           max_overflow=0, pool_pre_ping=True,
                           hide_parameters=True, connect_args={"application_name": "autplay-vault-names",
                           "connect_timeout": 5, "options": "-c statement_timeout=120000"})
    with args.state.with_suffix(args.state.suffix + ".lock").open("a") as lock, closing(
        sqlite3.connect(args.state)
    ) as journal, engine.connect() as guard:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.apply and not guard.scalar(text(
            "SELECT pg_try_advisory_lock(hashtextextended('autplay.vault-display-v1',0))"
        )):
            raise SystemExit("Another Vault name rewrite is active.")
        guard.commit()
        return run(args, journal, engine)


if __name__ == "__main__":
    raise SystemExit(main())
