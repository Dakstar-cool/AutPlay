# ADR-056: macOS client foundation

Status: Accepted for the foundation block; audio engine and device protocol remain gated.
Date: 2026-10-13
Authorization: user requested a separate branch and implementation of the macOS port plan.

## Context

The macOS port begins from source snapshot `102380418421f87679732406f8052150e8226da8`,
including the current product changes, rather than the older checkout HEAD. Android remains a
standalone client with Media3 and WorkManager. Desktop must reuse client policies without
importing Android frameworks or creating a second playback state owner.

## Decision

- Introduce Kotlin Multiplatform `shared/domain`, `shared/application`, and `shared/ui` modules
  with a JVM target and a separate JVM `apps/desktop` entry point. Android consumes the domain
  and application JVM artifacts; its existing Compose UI and platform adapters remain in place.
- Move existing framework-free rules and presentation types without changing their package names
  or behavior. Java-dependent UUID and playlist arithmetic stay in `jvmMain`; platform-neutral
  policies and presentation models belong in `commonMain`. Do not rewrite established crypto.
- Preserve Kotlin/Compose compiler `2.4.10`, Gradle `9.3.1`, JDK `17.0.20+8-LTS`, AGP `9.1.0`,
  Room `3.0.1`, and the Android Compose BOM. Add Compose Multiplatform `1.12.1` for desktop.
  The version catalog is authoritative for the actual pins.
- The foundation playback interface exposes the existing presentation `StateFlow` and
  play/pause/seek/previous/next commands. It does not own another queue or persist source URLs,
  tokens, Android content URIs, or credentials. The future desktop media adapter owns execution.
- The prototype target is macOS 13 or newer on Apple Silicon. Intel support is not qualified.
  A host-independent JVM compilation does not qualify a Mac installer, audio format, or native
  integration. Audio engine selection requires the stage-1 real Mac matrix.
- `autplay.desktopOnly=true` skips Android project configuration for the desktop gate without
  requiring an Android SDK on a Mac. The default continues to include Android and its gates.
- Local source changes are implemented in separate worktrees from one snapshot. The coordinator
  owns build files and integration. Gradle runs sequentially, with one worker and the existing
  heap. Reuse existing policy tests after completing the extraction block.

## Consequences and remaining decisions

This block introduces no Room schema, server protocol, signing, or production deployment change.
ADR-018 and ADR-021 retain their Android ownership and identity guarantees. Extending device
platform contracts and choosing the desktop media execution/download implementation require
separate decisions and their actual database/native evidence before integration.

Desktop initially shows a disconnected player until a real media adapter exists. It cannot claim
successful playback, saved imports, persisted queues, account support, or functional parity.
The macOS package includes its JVM runtime; clean installation, native libraries, signing,
notarization, and Gatekeeper remain release qualification tasks.

## Primary compatibility sources

- [Kotlin Gradle compatibility](https://kotlinlang.org/docs/gradle-configure-project.html)
- [Compose platform compatibility](https://kotlinlang.org/docs/multiplatform/compose-compatibility-and-versioning.html)
- [Compose native packaging](https://kotlinlang.org/docs/multiplatform/compose-native-distribution.html)
- [Port implementation plan](../design/explorations/AutPlay_macOS_Port_Plan_Draft_v1.md)
