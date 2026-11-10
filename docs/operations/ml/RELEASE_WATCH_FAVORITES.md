# M11 favorite-artist projection v1

`favorite_artist_v1` is a deterministic, owner-scoped release-monitoring input. It
does not enqueue discovery, request a provider scan, download audio, grant library
access, or produce training feedback. The ordinary CPU worker invokes
`ReleaseWatchService.refresh_due_from_history(limit=20)` before discovery dispatch
on its existing five-minute tick and once in `--once`, under the existing explicit
`discovery_automation_enabled` setting (default off). The repository refreshes at
most once per 24 hours per owner unless the algorithm version changes or the stored
refresh instant is in the future. The explicit per-owner entry point remains
`refresh_from_history(owner_id)`. See
[scheduled refresh](SCHEDULED_FAVORITE_REFRESH.md) for retry and locking details.

The evidence window is the trailing **90 days**, ending at the refresh instant. A
listening event is eligible only when it belongs to that owner and has a canonical
active recording with an active **primary** canonical artist. The linked user track
reference must belong to the same owner. The play must last at least **30 seconds**
and, where a completion ratio is known, reach **50%**. Events excluded from taste,
explicit dislikes, unresolved recordings, provisional/deleted identities, events
outside the window, names alone, Face/emotion signals, and feed impressions are not
inputs.

An artist needs **three eligible plays across two different recordings**. Sparse
history therefore yields no inferred favorite; a manual follow works without any
history. Each recording contributes at most its three most recent eligible plays to
the score. Plays younger than 7 days contribute 1000, younger than 30 days 700,
and older eligible plays 400. The score is capped at 1,000,000 and ties are broken
by canonical artist ID. A refresh fails closed if its query would return over
50,000 evidence rows, instead of silently publishing a partial ranking.

`library.artist_watch_choice` stores manual follow and explicit exclusion separately
from `library.favorite_artist_projection`. Unfollow writes an exclusion, which
continues to suppress an inferred favorite across refreshes until the owner follows
again. `library.favorite_artist_refresh` records algorithm version, generation,
window, event count, and SHA-256 evidence digest. Each projected artist stores its
own generation, score, counts, latest eligible play, and digest. The hashes are
diagnostic evidence; they are not authorization or a replacement for the original
listening event. The effective list rechecks current artist status, exclusion, and
matching projection generation and algorithm at read time. Inferred authority requires
`refreshed_at <= SQL clock < refreshed_at + 24 hours` and a projected instant no later
than its refresh. Release feed, policy reconciliation and final scan/acquisition
checkpoints apply the same rule. Stale, future, or incompatible inferred evidence
cannot grant watch authority or affinity. Manual follow and exclusion remain
independent. Account purge removes choices, refresh, projection and retry state
before listening history.

The bounded A1C dispatch now reconciles effective watches into safe `SCHEDULED` /
`REVIEW_REQUIRED` policies. It runs only on a scheduled pass; follow and listening
writes do not call the provider or enqueue scans. A new policy's first eligible time
is the manual follow or first inferred-favorite projection instant plus the global
scan interval. Later preference refreshes preserve the first projected instant for
an artist that remains a favorite. Reconciliation requires one active, exact Jamendo
`ARTIST` external reference; it never searches by name. The read-only monitoring
status reports provider unavailable, missing mapping, or multiple mappings requiring
identity review. Existing explicit A1C policies are not rewritten.

Watch-derived policy checks re-evaluate follow, exclusion, canonical artist, and
exact provider mapping before enqueue, worker claim, provider I/O, and page commit.
Unfollow fences queued or in-flight scans; a new follow cannot revive a run created
under the prior follow. The scheduler and worker retain A1C rate, resource, job, and
policy-revision fences. The separately gated owner consent and watch-origin
acquisition checks are documented in
[RELEASE_WATCH_AUTO_DOWNLOAD.md](RELEASE_WATCH_AUTO_DOWNLOAD.md).
