# Production metadata worker

The opt-in `metadata` profile in `deploy/compose/compose.metadata-worker.yaml` runs one
`music.metadata.enrich` consumer. It has no published ports. Its Vault mount is read-only;
the deletion and training-consent ledgers remain writable because both restore guards
open SQLite write transactions at startup. The worker may use its own reviewed image
tag, so activating it does not replace images of the running API, stream or other
workers. Preserve the image ID and source-snapshot identity in deployment evidence.

## Album tracklist lookup

The API serves the full edition at `GET /api/v1/music/discovery/album-tracklist` when internet music is enabled. It checks MusicBrainz first and follows an exact Discogs release link when the primary tracklist is unavailable. The album screen also accepts a Discogs release URL or numeric ID. These two Discogs paths work without a token.

To enable Discogs search by artist and album title, mount a file containing the Discogs personal access token in the API service and set `AUTPLAY_DISCOGS_TOKEN_FILE` to its path in that service. The API reads the file at startup. Multiple candidate editions are returned for user selection. Vault marks are computed per account on each request. [Discogs developer documentation](https://www.discogs.com/developers/)

An application key pair also works for catalogue search. In that case the same secret file contains one JSON object with the string fields `consumer_key` and `consumer_secret`. The catalogue child sends the credential only to the Discogs database search endpoint.

For Compose deployments, set `AUTPLAY_RUNTIME_DISCOGS_TOKEN_FILE` to the absolute host path of the credential file in the deployment environment. Append `-f compose.discogs.yaml` after the other Compose files when validating, starting or upgrading the stack. The overlay gives `api` and `mobile-api` read-only access to the same credential. Keep the host file readable by the UID running those services.

This application uses Discogs’ API but is not affiliated with, sponsored or endorsed by Discogs. ‘Discogs’ is a trademark of Zink Media, LLC.

## Track names and performer credits

The upload path derives display names from the current release credit, local tags and the source label. Vault stores the public recording label when it accepts verified audio. The account receives its own metadata revision when its display fields change. A later enrichment or an existing Vault name rewrite uses the same rules.

- Use the performer credited on this release. A confirmed legal name or an explicit stage-name tag can supply the stage name. A different project credited to the same artist keeps its release spelling. Spelling, transliteration and initials alone cannot establish identity.
- Keep the credited order and guest join phrases. A group and a member with different artist IDs remain separate. Preserve punctuation in names such as AC/DC. A composer tag belongs in the composer field. Cover, remix and live annotations identify the version.
- Decode filename separators where the source supports it. Strip an appended performer after an explicit delimiter or a verified pair of names for one performer. A single matching word or name at the end of a snake_case title is insufficient.
- Keep source labels for later corrections and respect user edits, explicit clears and locked metadata. The shared public label uses catalog evidence. Account-specific edits apply to that account.

## Before activation

1. Preserve the PostgreSQL and Vault backup generation and record the release image
   digest, Alembic head and rendered Compose configuration hash. Drain legacy
   unregistered byte workers before budget activation. Stop any earlier metadata
   containers and confirm they have no retained children.
2. Verify that `account.resource_quota_policy` has reviewed, non-null global limits,
   ceilings and budget evidence. Verify `account.internal_io_policy` has a reviewed
   active limit, ceiling, matching server identity and `workload_version = 3` from the
   same target environment. The operator must have explicitly applied both reports
   through the documented local commands. Do not substitute fixture values or change
   policy rows directly.
3. Load the reviewed `AUTPLAY_METADATA_WORKER_IMAGE` containing the metadata bootstrap
   selection and readiness probe. Layer the new Compose file **after**
   `compose.release.yaml`. Supply the same file-backed database and ledger secrets
   and ledger key IDs used by the running production stack. Include every site-specific Compose
   override before the new metadata overlay so the rendered project matches the
   active deployment. The metadata service is started only after the core runtime
   is healthy; its own readiness probe checks PostgreSQL and the schema. Targeted
   activation does not start or rerun the existing one-shot migration, Vault
   initializer or other runtime services. Do not copy secret values into Compose
   or logs.

From `deploy/compose`, validate the rendered model with the existing production inputs:

```bash
docker compose -p autplay-production \
  -f compose.yaml -f compose.runtime.yaml -f compose.admin-local.yaml \
  -f compose.public-edge.yaml -f compose.release.yaml \
  -f compose.metadata-worker.yaml \
  --profile runtime --profile metadata config --quiet
```

Inspect the rendered metadata service for `cgroup: private`, root only during bootstrap,
`cap_drop: ALL`, the five explicit bootstrap capabilities, `no-new-privileges`,
`apparmor=unconfined`, a read-only root filesystem, bounded memory/CPU/PIDs, the
read-only Vault mount, and no `ports`. The private cgroup2 mount requires this one
AppArmor exception on the validated Ubuntu Docker host. The bootstrap delegates a
subtree to `autplay`, then drops to that UID/GID with zero effective capabilities.

## Start and observe

```bash
docker compose -p autplay-production \
  -f compose.yaml -f compose.runtime.yaml -f compose.admin-local.yaml \
  -f compose.public-edge.yaml -f compose.release.yaml \
  -f compose.metadata-worker.yaml \
  --profile runtime --profile metadata up -d --no-build --wait metadata-worker
```

Check the service health, PID 1 `Uid`, `Gid`, `CapEff=0`, and the private cgroup2 mount.
The readiness probe checks migration compatibility and the applied metadata-inclusive
internal budget without claiming work. An `internal_io_budget_unconfigured` or
`metadata_budget_unconfigured` result is a stop condition, not a retry instruction.

Query only aggregate states before and after each bounded observation interval:

```sql
SELECT state, count(*) FROM library.track_metadata GROUP BY state ORDER BY state;
SELECT state, count(*) FROM jobs.job
 WHERE job_type = 'music.metadata.enrich' GROUP BY state ORDER BY state;
SELECT count(*) FROM library.metadata_execution WHERE closed_at IS NULL;
```

The worker sweeps missing metadata in batches of at most 100 and handles one job at a
time. Compare job completions with `READY`, `REVIEW`, `NOT_FOUND`, `RETRY` and `FAILED`
movement and inspect only sanitized error-code aggregates. Previously failed jobs
need their own diagnosis and authorized requeue path; starting this worker does not
silently requeue them. Stop the service with `docker compose ... stop metadata-worker`
if health fails or retained executions do not close, and preserve the SQL/child evidence
before another attempt.

## Cover images and artist portraits

The metadata worker reads embedded artwork and artwork supplied by the acquisition provider.
For a confirmed MusicBrainz edition it also checks Cover Art Archive. For other recordings
the Deezer lookup compares the full title including the version, performer credit, duration
within five seconds and album title when present. A verified edition or ISRC can extend the allowance to ten seconds. Short clips use a smaller tolerance. Ambiguous artist identities yield no
image. Distinct covers for the same recording can still yield a performer portrait.
Yandex Music provides another search by complete artist credit, version and duration.
With no album tag, its earliest returned matching release credited to the performer can
supply a track illustration. Bandcamp discovery follows the publisher's track page and
requires agreement between its structured recording, playing time, native track and art
identifiers. When the track page is unavailable, the publisher's own unique track result
can supply the image if its thumbnail agrees with the publisher's art ID and its album
and credits match. Public search covers must be at least 200 pixels on each side and close to
square. A corroborated recording in the metadata review queue can also supply a Cover
Art Archive image while the edition choice stays open. For duration differences above
three seconds, every matching review candidate must identify the same recording and
release group, and the difference must stay within both eight seconds and 3%. Artist pages can resolve a
MusicBrainz artist's explicit Deezer relationship or use portraits from linked recordings.
ListenBrainz can resolve candidate recording IDs to illustrated releases. Internet Archive lookup checks the original audio and front image in attributed Free Music Archive, Jamendo and netlabel items. The stored artwork provenance includes the item and license URLs.

For an existing collection, run `scripts/vault_artwork_backfill.py` in the server Python
environment with `--database-url-file PATH --cache DIRECTORY --workers N`. Keep the cache
directory private. With zero workers specified, the importer allocates six concurrent
I/O slots per available CPU up to 64. Requests to each provider share rate-limit backoff;
identical resources share one fetch. Positive images are reused by digest and successful search responses
for seven days. The collector revisits interrupted lookups. Sustained Bandcamp rate limits
defer that batch; a later invocation with the same cache resumes its missing artwork. It rechecks the owner, recording,
duration and current labels under the sync publication lock before publishing each change.
The final step also refreshes existing account references to published Vault recordings.
Use `--yandex`, `--publisher` or `--archive-candidates` for the additional sources. Use `--catalog-handles-only` for that step alone, or `--cached-searches` to apply updated
matching rules to the saved search responses. With `--offline`, image bytes also come exclusively from the cache. Progress counts distinguish newly saved
covers from track references with a portrait.

For a residual cohort, use `scripts/vault_artwork_recovery.py` with the same database and cache
options, a new `--cohort NAME`, and `--mode audit`. The immutable manifest records each
initial reason and recording reference. The `deezer` mode repeats direct searches with a
fresh cache. The `timing`, `brainz` and `archive` modes reuse valid results and fill missing
covers. The `musicbrainz` mode searches by artist and recording in batches and checks the
result against ListenBrainz. Use `retry` for interrupted Deezer searches and image downloads
and `retry-sources` for interrupted archive lookups. The `report` mode reconciles the
original cohort with committed artwork and counts distinct image digests.
