# CI and Release-Candidate Delivery

## Configured workflows

| Workflow | Trigger | Gate or artifact |
| --- | --- | --- |
| `ci-server.yml` | Pull request, `master`, manual, weekly cold run | Canonical Linux server/database gate with disposable PostgreSQL cleanup |
| `ci-android.yml` | Pull request, `master`, manual | Canonical Linux Android host gate and seven-day APK/test evidence |
| `ci-gpu-static.yml` | GPU/server-path pull request/`master`, manual | Isolated GPU lock/lint/format/type/unit checks on a CPU runner |
| `release-candidate.yml` | Version tag or manual selection of an existing tag | Unsigned APK, CPU OCI archive, fresh SBOMs, release evidence and SHA-256 manifest retained for 14 days |

Normal CI and candidate delivery use only `contents: read`. Checkout credentials are not persisted.
Manual candidate delivery checks out the named existing tag, verifies that it resolves to the
recorded commit and derives artifact identity from that commit. Candidate delivery cannot create a
GitHub Release, push an image, read a signing/deployment secret or deploy.

## Supply-chain pins

| Action | Exact commit | Upstream release |
| --- | --- | --- |
| `actions/checkout` | `3d3c42e5aac5ba805825da76410c181273ba90b1` | `v7.0.1` |
| `astral-sh/setup-uv` | `20cfd1bf945f4377ade1205e4dbc17946fc9a30d` | `v10.0.1` |
| `actions/setup-java` | `b6effb05e454b25005698d916606bdc6ffcbf961` | `v5.7.0` |
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
- Canonical Windows server-equivalent gate: PASS; 53 contract and 425 server tests against
  PostgreSQL 18.4/pgvector 0.8.6, with one expected Windows symlink skip and exact cleanup.
- Isolated GPU static gate: PASS; lint/format/mypy and 21 tests, with two expected Windows symlink
  skips. No accelerator/model claim is made.
- Independent read-only review: version/source binding and GPU path-trigger findings fixed; no
  remaining Critical/Major workflow finding.
- Previous hosted Android, server and GPU runs exposed, respectively, a missing Microsoft JDK
  catalog entry, a Linux-only mypy error before the artifact upload step and stale GPU lock metadata
  after server dependency changes. The replacement uses the verified exact JDK archive, a
  platform-safe subprocess constant import and a regenerated pinned GPU lock; replacement hosted
  evidence is pending.

## Release and deployment boundary

The candidate bundle is delivery evidence, not a production release. It is intentionally unsigned
and private to the workflow run. Production signing, GitHub Release/registry publication and live
deployment require the explicit decisions and approval in [`DEPLOYMENT.md`](DEPLOYMENT.md).
