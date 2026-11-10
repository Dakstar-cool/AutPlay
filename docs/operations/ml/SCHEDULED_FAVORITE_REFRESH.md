# M11 scheduled favorite refresh

The ordinary `autplay-worker-cpu` process refreshes listening-derived favorite
artists immediately before scheduled discovery dispatch. Its existing five-minute
tick, including the first startup tick, attempts at most 20 owners; `--once` runs
one batch. The existing `discovery_automation_enabled` setting must be explicitly
enabled and remains off by default. This does not enable the separate watch auto
download gate, owner consent, model quality or serving activation.

`ReleaseWatchService.refresh_due_from_history(limit=20)` uses a fresh PostgreSQL
`clock_timestamp()` for each owner transaction. No refresh, algorithm mismatch,
future refresh, or an elapsed 24 hours makes an active owner due. Each attempt
selects the oldest due owner, with UUID tie breaking, and locks only that account
with `FOR UPDATE SKIP LOCKED`. Locked owners are skipped. Refresh rechecks cadence
under this lock, shared with follow and account deletion. Parallel workers cannot
publish duplicate daily generations. Inactive/deleted owners are never selected.
The refresh commits before discovery acquires its resource and schedule locks;
refresh never acquires those locks or performs provider I/O.

The existing [favorite algorithm](RELEASE_WATCH_FAVORITES.md) keeps its 90-day
window, complete 50,000-row budget, sparse-history behavior and owner isolation.
Continuing favorites preserve their original `projected_at` only when their previous
projection matches its refresh generation/current algorithm and its projected instant
does not exceed a nonfuture refresh instant. Stale daily refresh evidence may preserve
that valid continuity. Invalid ancestry starts a new projection at the current instant;
favorites whose history expires disappear. Follow, exclusion and explicit A1C policies are not changed
by refresh. Follow and explicit per-owner refresh never scan or enqueue acquisition.

Migration `0067a_release_watch_retry`, directly after `0067_release_watch_auto_download`,
adds only owner-scoped retry scheduling metadata: `user_id`, `attempted_at`, and
`retry_after`. A complete-evidence overflow rolls back the refresh savepoint, then
commits a 24-hour retry under the retained account lock. It never increments a
successful generation or replaces evidence/refreshed time with a failed attempt.
Subsequent batches and new worker instances skip that owner until retry, so more
than 20 permanently oversized owners cannot monopolize repeated startup batches.
The retry backoff also delays an algorithm-version change until its existing
retry instant. Progress assumes a finite due backlog and available worker capacity;
unbounded new arrivals have no established scheduling guarantee.
A later successful sweep removes the retry row. Database failures propagate and
prevent dispatch for that tick; committed prior owner refreshes remain valid.
The runtime's existing `database_statement_timeout_ms` bounds each SQL statement;
the normal 20-owner pass still has no measured full-query production capacity or
whole-pass wall-clock qualification. Only the stable `favorite_refresh_evidence_limit` event is logged, without owner
identifiers or personal evidence.

Effective reads, release feed, policy reconciliation and final scan/acquisition
authority require matching generation/current algorithm and
`refreshed_at <= clock_timestamp() < refreshed_at + 24 hours`.
`projected_at` must not exceed `refreshed_at`. Page/caller timestamps cannot extend
this authority. Manual follow remains effective independently of inferred freshness;
exclusion suppresses both. Exact active provider/artist mapping, policy revision,
rights, resource and separate consent/gate checks remain required at their existing
checkpoints. No provider search by name is introduced.

Retry metadata stores the owner ID and two scheduling timestamps.
Account purge deletes it before favorite projection and listening history.
The retry migration refuses a downgrade while retry rows remain. An empty-table
downgrade restores the predecessor's purge function.

This Release Watch baseline introduces the retry table at `0067a`. The later
development chain has the same table in `0099_favorite_refresh_retry`. Reconcile
those revisions before joining the chains or using a database that already
applied the development revision.
