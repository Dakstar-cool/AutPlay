# Track metadata `NOT_FOUND` and `FAILED` audit — 2026-09-23

Read-only production PostgreSQL snapshot at **2026-09-23 12:03 UTC**. Database migration:
`0060_local_bridge_authority`. Source checkout examined: `9ac8778`; this does not establish the
binary identity of the stopped production worker. The repeatable aggregate query is
[`scripts/metadata_not_found_audit.sql`](../../scripts/metadata_not_found_audit.sql). It emits no
owner IDs, track names, paths, or provider search terms. Counts are a point-in-time snapshot;
the metadata and worker tasks may change them later.

| State | Rows |
| --- | ---: |
| READY | 1,883 |
| REVIEW | 6,433 |
| NOT_FOUND | 7,746 |
| FAILED | 213 |
| QUEUED | 10 |
| **Total** | **16,285** |

Across all states, 4,546 rows have album, 4,286 have release date, 1,884 have original release
date, and 4,950 have artwork. State is not a completeness measure: among `NOT_FOUND`, 1,583
already have at least one embedded field or artwork, including 765 release dates and 1,208 covers.

## Findings

- **Source context:** All 7,746 `NOT_FOUND` refs lack a raw album. Their source is overwhelmingly
  the `autplay.generic-user-export` JSON import, which also has no album in its retained raw
  fields. Embedded metadata supplies an album for 891 of these refs. The raw title exists for all;
  raw artist is missing for two; raw duration is missing for 31.
- **Audio:** 7,711 `NOT_FOUND` refs have a canonical Vault audio variant, and the metadata
  document records that same variant. They were already processed with current audio. The other
  35 are `PENDING` without canonical audio; these cannot gain embedded tags or a fingerprint
  until authorized audio is available. A plain retry of the 7,711 with the unchanged lookup
  method is not evidence of a better match.
- **Search input:** Among `NOT_FOUND`, 430 effective titles contain an official/video/lyrics/
  visualizer marker, 468 contain `feat`/`ft`/`featuring`, 680 contain remix/mix/edit, and 555
  contain live. These groups overlap. The current MusicBrainz query requires a title phrase and
  combined credited artist phrase; the inspected handler can also fall back to AcoustID when
  audio and a loaded key are present. The old worker container declares an AcoustID key setting,
  but no per-request receipt proves which fallback calls ran. The counts show potentially
  difficult source text, not proof that any particular result exists in the provider catalog.
  No raw search strings or provider responses were retained for that diagnosis.
- **Transient failures:** 167 `FAILED` rows have `metadata_provider_busy` and 45 have
  `metadata_network_unavailable`; each exhausted six attempts. All 212 retain the current audio
  variant and are candidates for one controlled retry after measured policies are activated and
  the worker is healthy. The remaining one has `metadata_release_recording_missing` after one
  attempt; it needs individual review because its selected release no longer demonstrated the
  claimed recording membership.
- **Admission block:** The resource policy has no playback or transfer ceiling. Internal I/O
  policy has no active limit and remains workload version 1; metadata execution requires version
  2 or later. There were no open metadata execution receipts. This audit did not start a worker,
  enqueue retries, make provider requests, or mutate persistent data.

## Safe continuation

1. Apply only measured, approved resource and internal-I/O policies, then verify the metadata
   worker's contained runtime and provider pacing.
2. Requeue the 212 transient failures once through the versioned metadata service, in bounded
   batches. Preserve field provenance, artwork, revision history, and user locks. Do not requeue
   the release-membership failure automatically.
3. For `NOT_FOUND`, test a bounded search-only query normalization or alternate MusicBrainz
   query against controlled examples. Keep any broader result in `REVIEW` unless the existing
   strict identity, duration, and unique-edition checks pass. Reprocess only after real policies
   are active and catalog traffic remains within the provider gate.
4. Re-audit coverage by field and state. The snapshot above is the unchanged baseline; it is not
   evidence of new metadata publication.

The [MusicBrainz recording search specification](https://musicbrainz.org/doc/MusicBrainz_API/Search)
distinguishes the combined credited `artist` field from `artistname` for any credited artist.
That offers a possible controlled alternate query, but this audit did not verify it against
private tracks or authorize bulk provider requests.
