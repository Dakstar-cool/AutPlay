# CI and Release-Candidate Delivery

## Configured workflows

| Workflow | Trigger | Gate or artifact |
| --- | --- | --- |
| `ci-server.yml` | Pull request, `master`, manual, weekly cold run | Canonical Linux server/database gate with disposable PostgreSQL cleanup |
| `ci-android.yml` | Pull request, `master`, manual | Canonical Linux Android host gate and seven-day APK/test evidence |
| `ci-gpu-static.yml` | GPU/server-path pull request/`master`, manual | Isolated GPU lock/lint/format/type/unit checks on a CPU runner |
| `release-candidate.yml` | Version tag or manual selection of an existing tag | Unsigned APK, CPU Docker image archive, commit/artifact-bound release-audit package and SHA-256 manifest retained for 14 days |

Normal CI and candidate delivery use only `contents: read`. Checkout credentials are not persisted.
Manual candidate delivery checks out the named existing tag, verifies that it resolves to the
recorded commit and derives artifact identity from that commit. Candidate delivery cannot create a
GitHub Release, push an image, read a signing/deployment secret or deploy.

## Release-audit gate

`scripts/release_audit_gate.py` turns the credential-free collectors in
`scripts/p14_release_audit.py` into a fresh, independently verifiable evidence package. Generation
creates CycloneDX 1.5 SBOMs, clean `uv audit` results, the complete Python/Android license
inventory, the repository secret scan and provenance for the exact named release inputs. The
provenance records the immutable source commit/tree and each input artifact's filename, byte size
and SHA-256.

The generator refuses a source commit other than checked-out `HEAD`, tracked source changes,
pre-existing evidence output, missing inputs or non-clean audit results. The separate `verify`
invocation accepts only a lowercase full commit, an exact named artifact set and evidence no older
than 24 hours. It re-hashes every evidence file and input artifact, rejects missing/extra evidence,
validates the semantic PASS conditions, and rejects future-dated, expired, malformed, symlinked or
path-escaping evidence. Generation and verification need dependency metadata/network access for
the audits, but no signing key, production credential, live deployment or physical device.

Both supported release paths invoke `generate` and then `verify` before writing their outer
manifest/checksum. `package-release.ps1` binds the unsigned, development-signed and trusted-LAN
APKs, development certificate, CPU server archive and installer ZIP. `release-candidate.yml` binds
its unsigned APK and CPU server archive. The evidence index is then itself bound by the outer
manifest and `SHA256SUMS`.

## Supply-chain pins

| Action | Exact commit | Upstream release |
| --- | --- | --- |
| `actions/checkout` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | `v7.0.1` |
| `astral-sh/setup-uv` | `20cfd1bf945f4377ade1205e4dbc17946fc9a30d` | `v10.0.1` |
| `actions/setup-java` | `dd06d9cba3e5552c54d9f8ea23572deb30010f7c` | `v6.0.0` |
| `actions/upload-artifact` | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` | `v7.0.1` |

The action commits were resolved from official upstream tags and verified commits. The hosted Linux
jobs download the versioned Microsoft OpenJDK `17.0.20+8-LTS` x64 archive from the official
Microsoft URL and verify SHA-256
`69479b83a0e4408cc24d4dfb551db3759ba145ddce6131c6806a97d7bd8604cd` before passing it to
`setup-java` as a local JDK file. This preserves ADR-013 while the action's Microsoft version
catalog does not expose that exact release. Dependabot may propose monthly action updates, but
proposals still require exact-pin review and the affected gates.

## Validation state

- YAML syntax, full-SHA action references and read-only workflow permissions: PASS.
- Release-audit validator unit coverage proves acceptance of a matching package and fail-closed
  rejection of stale evidence, source mismatch, artifact replacement, missing evidence and a
  vulnerability result that is non-clean even when its index hash is updated.
- Canonical Windows gate on the `v1.0.0` candidate source tree: PASS; Android
  lint/unit/debug/trusted-LAN/release-R8, 301 contract/release tests, 300 acquisition tests, 87
  training tests and 2,238 server tests against PostgreSQL 18.4/pgvector 0.8.6, with documented
  platform/image skips and exact cleanup.
- Isolated GPU static gate in that run: PASS; lint/format/mypy and 41 tests, with two expected
  Windows symlink skips. No accelerator/model claim is made.
- Independent read-only review: version/source binding and GPU path-trigger findings fixed; no
  remaining Critical/Major workflow finding.
- Previous hosted Android, server and GPU runs exposed a missing Microsoft JDK catalog entry,
  runner-image SDK drift, missing Playwright browser installation, Linux-only subprocess typing and
  Vault fixture failures, a lifecycle concurrency assertion mismatch, and stale GPU lock metadata.
  The replacement uses the verified exact JDK archive, an isolated pinned Android SDK, pinned
  Chromium installation, platform-safe fixtures and imports, and a regenerated GPU lock.
- Replacement pull request hosted evidence: [Android PASS with the v7 artifact upload step executed](https://github.com/Dakstar-cool/AutPlay/actions/runs/32493803039),
  [Server PASS](https://github.com/Dakstar-cool/AutPlay/actions/runs/32493802712), and
  [GPU static PASS](https://github.com/Dakstar-cool/AutPlay/actions/runs/32493803015).

## Release and deployment boundary

The candidate bundle is delivery evidence, not a production release. It is intentionally unsigned
and private to the workflow run. Production signing, GitHub Release/registry publication and live
deployment require the explicit decisions and approval in [`DEPLOYMENT.md`](DEPLOYMENT.md).

The repository's explicitly approved development distributions are assembled by
`scripts/package-release.ps1` from an immutable local tag. The `v0.4.0` path produces a hardened
APK, a separately identified trusted-LAN APK, and a CPU-only `linux/amd64` server-installer ZIP.
The script runs the canonical gates, verifies both APK manifests and the retained development
signer, exports the public certificate, creates and reloads the Docker image archive, runs the
combined admin/mobile runtime smoke, requires the release-audit gate, and generates
`release-manifest.json` and `SHA256SUMS`. Manual `gh release create --prerelease` uploads only the
verified generated files.
This local path is also the supported fallback when hosted CI minutes are unavailable. It does
not push an image to a registry, perform production signing, or deploy/migrate a persistent
target. Installation and pairing are documented in [`INSTALL_AND_PAIR.md`](INSTALL_AND_PAIR.md).

After the Android production packager creates the exact APK and its
`android-production-manifest.json`, `scripts/finalize_production_release.py` is the credential-free
integration point. It accepts those files, the exact Android dependency report, a Docker image
archive and optional additional publishable payloads. It independently verifies Android
package/version/source/signer/Room identity, derives and verifies the Docker config digest from the
archive, requires a single linear Alembic head, and runs release-audit `generate` plus `verify` over
the copied immutable payloads with the `PRODUCTION_INPUTS_AUDIT_ONLY` boundary.

The finalizer publishes atomically under `dist/production/` and writes
`production-release-manifest.json` plus `SHA256SUMS`. The manifest is deliberately classified as
`PRODUCTION_RELEASE_CANDIDATE`: it may record `production_signed=true` for the exact APK, but always
records `production_deployed=false` and blocks activation pending target acceptance and operator
approval. The audit sub-package also retains its evidence-only boundary and never claims signing or
deployment. No caller may rewrite either boundary into `PRODUCTION_RELEASE` merely because the
local finalizer passed.
