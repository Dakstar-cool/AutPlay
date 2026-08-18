# ADR-020: P07 Android Test Runner and General JCS Compatibility

- Status: Accepted
- Date: 2026-08-16
- Decision owner: standing in-scope technical-decision authorization

## Context

P07 activates general nested JSON payloads for preference, playlist and listening intents. The P05
string-only canonicalizer is intentionally too narrow for those values. A handwritten P07 renderer
also cannot claim RFC 8785 compatibility merely by sorting keys and using `BigDecimal`: JCS number
serialization follows ECMAScript/IEEE-754 rules and duplicate property names must be rejected before
building a JSON tree.

The first P07 host-test run also exposed a toolchain regression. With AGP `9.1.1` and Kotlin
`2.4.10` built-in Kotlin, test classes were compiled but omitted from the AndroidUnitTest runtime
classpath. Clean builds, standard `src/test/java` layout and explicit source-set registration all
reproduced the same pre-assertion failure. Kotlin's published compatibility table names AGP `9.1.0`
as the supported upper bound for Kotlin `2.4.10`; `VERSIONS.md` had already recorded `9.1.0` as the
fallback if executable gates regressed.

## Decision

1. Pin AGP `9.1.0`; retain Gradle `9.3.1`, Kotlin/Compose compiler `2.4.10`, KSP `2.3.9`, Room
   `3.0.1` and the existing SDK/JDK pins.
2. Use `io.github.erdtman:java-json-canonicalization:1.1`, the Java implementation referenced by
   RFC 8785, for general P07 canonical serialization. Keep AutPlay's bounded strict scanner before
   canonicalization so duplicate properties, nesting, malformed JSON and lone surrogates retain
   stable local error codes.
3. Apply the P04 recursive safe-property-name/privacy policy after canonicalization and preserve
   unknown additive safe values as opaque JSON.
4. Toolchain or canonicalizer changes remain exact-pin operations and require host tests, API 26,
   lint, debug, minified release/R8, P04 vectors and the complete canonical gate.

## Consequences

- Host tests execute normally under the published Kotlin/AGP compatibility boundary.
- P07 payload hashes use the same RFC 8785 representation across Android and server runtimes,
  including ECMAScript number boundaries.
- Adding one small Java dependency is preferred to maintaining a security-sensitive numeric
  canonicalizer locally; its exact jar hash is recorded in `VERSIONS.md`.
- No wire schema, Room schema, PostgreSQL migration, sync engine or future phase behavior changes.

## Rejected alternatives

- Keep AGP `9.1.1` and accept compile-only unit evidence: assertions would never execute.
- Disable built-in Kotlin or broadly change the Kotlin/Gradle stack: larger compatibility surface
  than the already documented one-patch fallback.
- Continue the `BigDecimal` renderer: it diverges at exponent and IEEE-754 rounding boundaries.
- Parse with kotlinx.serialization alone: duplicate object properties are lost when the tree is
  constructed.
