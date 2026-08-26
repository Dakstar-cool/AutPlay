# ADR-040: Private-by-default friend-visible profile statistics

- Status: Accepted for implementation on 2026-08-25
- Scope: Post-MVP S2 local Profile statistics and same-server friend visibility

## Context

AutPlay already records immutable profile-scoped listening events locally and synchronizes their
server representation. S1C supplies explicit same-server friendship and directed blocking, but it
deliberately excludes profile-statistics sharing. The user accepted a separate S2 boundary where
statistics remain private until the owner enables visibility for confirmed friends. The user also
clarified that “open in settings” means opening the privacy permission, not navigating to the
statistics screen from Settings.

Repeated queries over a live cumulative counter can approximate current listening activity even
when raw events are absent. A copied device-local preference can also publish the wrong profile.
The contract therefore needs a server-owned policy, coarse completed-day windows and live
authorization checks.

## Decision

1. The Android Profile derives the owner's statistics from the active profile's Room history. It
   works in local mode and offline and may include taste-excluded listens because those remain
   private to the owner. No Room schema change or aggregate cache is introduced.
2. Settings exposes a default-off `friends_can_view_statistics` switch. PostgreSQL stores it in a
   dedicated `social.profile_statistics_settings` row with revision and update time. The flag is not a
   fourth presence setting, is not stored in `NonSecretSettingsStore` and is excluded from ordinary
   settings export.
3. The server exposes strict authenticated v1 operations to read/update the caller's policy and to
   read one exact friend's statistics. An absent policy row means disabled at revision zero. A
   visibility update uses an exact operation UUID, durable request hash and expected revision.
   Enabling requires the exact revision; disabling is always allowed, advances revision and cannot
   be undone by a delayed stale enable. Reads and responses are `no-store`.
4. A friend read succeeds only when both accounts and the actor's exact device session are active,
   the unordered friendship exists, neither directed block is active and the target policy is
   enabled. These facts are reloaded under the existing sorted account/pair transaction ordering.
   Toggle-off, unfriend, block and account retirement therefore revoke access without cache delay.
5. Friend-visible values are computed directly from synchronized `library.listening_event` rows
   with `excluded_from_taste = false` and `started_at` before the current UTC day. The fixed windows
   are the preceding 7, 30 and 365 completed UTC days. Each window contains only
   `play_session_count`, `listened_ms` and `unique_track_count`.
6. The payload contains a coarse `through_utc_date` and exactly three aggregate windows only. It
   contains no track, artist, release, catalog or owner-scoped identifiers or names. The distinct
   key is the current owner-scoped Recording when resolved, otherwise the owner-scoped
   `UserTrackRef`; display text is never used as identity.
7. The payload contains no raw
   event, exact timestamp, current/day activity, device/session/Room identifier, context, origin,
   feedback, source, path, private origin, recommendation request or arbitrary metadata.
8. Friend-statistics reads are bounded at 30/viewer and 10/exact pair per 15 minutes; policy
   writes are bounded at 10/account per 15 minutes. The fixed response remains below 2 KiB.
   PostgreSQL remains the only server authority; no broker, analytics service, materialized
   statistics table or new dependency is added.

## Consequences

The owner sees current local statistics, including private top tracks/artists, even without a
server. Friends see only explicitly shared, synced and completed-day count/duration aggregates, so
the shared view can lag local Profile data by sync and UTC day boundaries. Disabling visibility or
changing the relationship closes the read immediately. Internet-public statistics, URLs and
federation remain deferred topology/identity work.
