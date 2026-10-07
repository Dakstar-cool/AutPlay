# AutPlay coding standards

Read Common and the sections covering the affected behavior before implementation or runtime commands.
Review against every applicable rule. Linked contracts and ADRs supply the detailed decisions;
the [documentation map](START_HERE.md) routes to them by task.

## Common

- Keep code identifiers, configuration and comments in English/ASCII. Give public or non-obvious
  APIs explicit types.
- Bound retries, batches, payloads and timeouts. Preserve stable error codes and report success
  only after the intended effect is verified.
- Use the committed toolchain, locks, Gradle wrapper and version catalog. Keep dependency upgrades
  within the requested scope. Exact versions and check commands belong to configuration and
  [setup instructions](README.md#developers), rather than a second inventory here.
- Preserve unknown persisted and API values. Room and Alembic migrations must preserve data;
  destructive fallback is prohibited.

## Architecture and storage

Read for domain, server, dependency or storage changes.

- Preserve the modular monolith. Pure domain code cannot import frameworks, storage, network or
  GPU code. Application use cases own transactions; ports define adapter boundaries.
- PostgreSQL owns server metadata, sync and jobs. Filesystem/NAS is the initial Vault backend.
  Additional brokers, object stores or vector stores need measured justification and an ADR.
- CPU runtime and its dependency graph remain independent of optional GPU/model code.

The [architecture](docs/design/AutPlay%20System%20Architecture%20v1.md) explains these boundaries;
read the affected design section and ADRs, rather than its historical implementation sequence.

## Identity and Vault

Read for media identity, catalog matching, Vault access or byte storage changes.

- Keep `VaultObject`, `AudioVariant`, `Recording`, `ReleaseTrack` and `UserTrackRef` distinct.
- Vault bytes are immutable and SHA-256 verified. Knowledge of a hash never authorizes access.
- Fingerprints and external IDs are versioned evidence. Uncertain recordings require an explicit
  identity decision instead of a silent merge.

Use the [track identity contract](docs/design/AutPlay_Track_Identity_v1.md) for decision details.

## Android and sync

Read for Android behavior, Room, playback, downloads, deferred work or sync changes.

- Local playback and Android mutations work without a synchronous server trip.
- Commit each Android domain mutation and its Journal/outbox fact in one Room transaction.
- Media3 owns playback and download execution. WorkManager owns durable deferred work.

Read the relevant [Room design](docs/design/AutPlay_Android_Room_Schema_v1.md),
[outbox/Journal decision](docs/adr/ADR-018-standalone-outbox-and-journal-lineage.md),
[media ownership decision](docs/adr/ADR-021-p08-media3-room-playback-download-ownership.md) or
[sync protocol](docs/design/AutPlay_Sync_Protocol_v1.md) for the behavior being changed.

## Privacy and imports

Read for logging, evidence export, authentication, external integrations or music imports.

- Redact credentials, tokens, private origins, raw paths/device identifiers and personal payloads.
- Music imports require user authorization. DRM bypass is prohibited.

Use the [privacy runbook](docs/operations/PRIVACY_DELETE_EXPORT.md) or
[acquisition contract](docs/design/AutPlay_Discovery_Acquisition_Contract_v1.md) for the affected flow.

## Verification

Read before builds, checks, evidence claims, review or PR publication, including documentation changes.

- Map the diff to CI and run the complete affected gates with pinned tools and disposable
  resources before publishing a PR. Check definitions live in [CI workflows](.github/workflows)
  and the [canonical scripts](scripts); [CI/release mechanics](docs/operations/CI_RELEASE.md)
  explain the gates. Preserve their acceptance criteria.
- Checks must prove the changed behavior. Persistence and migration claims require real database
  evidence; screenshots alone cannot establish behavior.
- For documentation-only changes, verify changed links/anchors, tracked-file availability and
  preservation of applicable rules. Select executable gates according to the affected contracts
  or procedures.
- Run Gradle invocations sequentially with one worker and the heap in
  [gradle.properties](gradle.properties). The canonical scripts supply worker arguments. Split
  large work before increasing memory; a larger heap is diagnostic only.
- Record exact source, artifact and configuration identity. Evidence for changed inputs remains
  historical until those inputs are verified again. Use the
  [release evidence index](docs/release/README.md) when an exact prior result is needed.

## Docker

Read for Compose changes/debugging and Dockerfile/image changes or review.

- Use the available `docker-compose-patterns` skill for Compose services and
  `docker-build-strategies` for Dockerfiles and image builds.
- The repository's Docker layout, release gates and disposable test cleanup rules take precedence
  over general skill guidance. Use [Compose operations](deploy/compose/README.md) for the layout
  and the canonical check scripts for disposable-resource ownership and cleanup.
