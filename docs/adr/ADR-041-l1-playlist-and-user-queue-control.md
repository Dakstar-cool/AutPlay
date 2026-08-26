# ADR-041: Manual playlist UX and durable ordinary-queue editing

- Status: Accepted for implementation on 2026-08-25
- Scope: Post-MVP L1 Android manual playlists and user-controlled ordinary playback queues

## Context

P07 already owns complete local-first manual-playlist mutations and P09 synchronizes their owning
events. The current UI exposes only an automatically named playlist and adds a selected track to
the first list, leaving the implemented rename/delete/exact-entry/reorder behavior inaccessible.

P08 already persists a duplicate-preserving queue and restores it into Media3, while P13 owns a
separate server-authoritative Wave queue. Direct DAO edits from Compose or direct Player edits from
the Activity would split durable intent from execution. Removing the current queue entry during an
active logical session would also create an avoidable attribution/finalization race.

## Decision

1. L1 reuses the P07 manual playlist aggregate, commands, fractional position keys and sync events.
   It adds presentation/application wiring only; no Room or PostgreSQL schema, sync contract or
   server route changes.
2. Playlist UI accepts a trimmed name of 1-120 characters and an optional description of at most
   500 characters. Manual playlists remain owner-scoped and `PRIVATE`; duplicates are intentional
   distinct entries.
3. Ordinary queue edits apply to `USER`, `SEARCH`, `LIBRARY` and `PLAYLIST` snapshots only. The
   first edit changes the snapshot type to `USER` and clears its collection context, while every
   existing entry retains stable identity, source origin and recommendation attribution.
4. Queue edits are application-owned Room write transactions. They rewrite the bounded entry set
   with stable entry IDs and dense positions only after validating the active snapshot, profile,
   current entry, target entry and maximum size. No URL, token, URI or media byte state is stored.
5. The current queue entry cannot be removed or moved by L1. `Clear upcoming` removes only entries
   after it. This keeps the active logical listening checkpoint stable; Stop or a natural Media3
   transition remains the boundary for ending the current listen.
6. After Room commits, a stable-ID `RefreshQueue` command asks the Media3 service to reload the same
   snapshot. The service preserves current entry, position, play/pause, shuffle/repeat and session
   state. A crash between commit and refresh is safe because service startup restores Room truth.
7. Previous and next remain service-owned Media3 commands. UI enablement is derived from an
   observable bounded queue projection, not optimistic local state.
8. `WAVE`, unknown queue types, stale snapshots and profile mismatches reject locally with stable
   non-disclosing errors. L1 never edits Wave Room tables or submits a server queue command.

## Consequences

The owner can finish playlists and customize upcoming playback offline without a migration or new
dependency. Editing a queue created from search/library/playlist intentionally detaches the queue
shape from that source but preserves attribution for the entries already present. The currently
playing item stays fixed until Media3 advances or playback stops; current-item removal can be added
only with a separately tested logical-session finalization transition.

Collaborative playlists, cross-device active-queue sync, playlist sharing, smart playlists and
playlist-level taste exclusion remain separate contracts.
