# ADR-014: Android bootstrap dependency boundaries

**Status:** Accepted

**Date:** 2026-08-13

**Owners:** AutPlay Android maintainers

## Context

P01 needs a launchable standalone Compose shell and must state DI/network/JSON direction, but it must not pull P05+ framework graphs or create a synchronous server dependency for local actions.

## Decision drivers

- Local-first behavior and optional personal server.
- Minimal P01 graph with no speculative abstractions.
- Clear first-use choices for later implementation.
- Separate control-plane API traffic from Media3 byte delivery.

## Options considered

| Option | Reliability | Complexity | Cost | Risks |
| --- | --- | --- | --- | --- |
| Manual constructors now; activate selected frameworks at first use | High | Low now | Low | Later wiring migration |
| Add Hilt/Retrofit/OkHttp/serialization in P01 without consumers | Medium | Medium | Low | Unused dependency drift and false architecture |
| Service locator and one generic HTTP client for all traffic | Low | Low | Low | Hidden coupling and poor media ownership |

## Decision

- P01 uses direct/manual constructor wiring only; it creates no application dependency graph.
- Hilt is the preferred DI framework when P05 introduces the first real Android graph. Its exact version is deferred until that compatibility gate.
- Retrofit plus OkHttp is the preferred typed control-plane HTTP stack; kotlinx.serialization is the preferred JSON codec. Activate and pin them only when P04/P05 introduces a real generated/typed contract consumer.
- Media3 owns playback and durable download execution. Media byte delivery uses Media3's data-source/HTTP Range path, not Retrofit response bodies.
- The Compose shell launches without a server, network call, Room database, Media3 service, WorkManager, Hilt, or Retrofit dependency.

## Consequences

### Positive

- P01 proves the local client build without pretending future features exist.
- Server unavailability cannot block bootstrap UI startup.
- Later dependencies enter with their owning contract and executable tests.

### Negative

- P01 does not prove Hilt/Retrofit/Media3 compatibility.
- P05 must record and validate exact activated versions before user schema v1.

## Compatibility and migration

The first framework activation must preserve manual domain/application boundaries and remain replaceable behind ports. Room/KSP/Media3/WorkManager candidates in `VERSIONS.md` are research notes, not accepted runtime dependencies.

## Validation evidence

- `apps/android` has only Compose/activity/JUnit dependencies.
- Static dependency inventory contains no Room, Media3, WorkManager, Hilt, Retrofit, OkHttp, KSP, CUDA or ML package.
- `lintDebug`, `testDebugUnitTest`, and `assembleDebug` pass; no device/server is needed.

## Reversal trigger

Revisit when a concrete owning phase demonstrates that the preferred stack violates offline behavior, Media3 ownership, performance, platform support, or generated-contract requirements.
