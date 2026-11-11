"""M1's dedicated read-only database owner, on the authoritative database only."""

from __future__ import annotations

from sqlalchemy import URL

from .sync_binding_v2 import BindingDatabaseRuntime


class MediaSourceDatabaseRuntime(BindingDatabaseRuntime):
    """Retain two complete auth/read operations through actual DNS/DB cleanup.

    Reuse the established owner-loop, nonqueued admission, greenlet bridge and
    retained shutdown protocol. This is a separate pool, resolver executor and
    lifetime, never B1's pool or the general application engine. The public
    ``engine.sync_engine`` is usable only inside ``run``'s greenlet; both the
    real authenticator's UoW and the relationship/clock reader use it.

    Connection defaults enforce READ ONLY before the first auth statement.
    The caller passes the remaining ingress budget to run(timeout_seconds=...),
    so its 15 seconds includes framing, scheduling, DNS and authentication.
    Response timeout never means cleanup or shutdown has completed.

    SQLAlchemy documents run_sync's greenlet adaptation (not a thread executor):
    https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
    Psycopg documents async DNS and Windows SelectorEventLoop requirements:
    https://www.psycopg.org/psycopg3/docs/advanced/async.html
    """

    def __init__(self, authoritative_url: URL) -> None:
        super().__init__(authoritative_url, read_only=True, application_name="autplay-media-source")
