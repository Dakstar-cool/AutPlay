# AutPlay RC1 security and privacy review

## Outcome

No critical/high object-authorization or data-loss defect was found in the tested CPU/local-first
path. The targeted security suite passed 66 tests with one Windows-only symlink-privilege skip; the
canonical real-PostgreSQL suite supplies the cross-owner database cases. Physical deployment,
public TLS/edge policy and an approved GPU model remain outside this local RC evidence.

## Threat model

| Boundary | Principal threats | Controls and evidence |
| --- | --- | --- |
| Android ↔ API | bearer theft/replay, device revoke, BOLA | short access lifetime, rotating hashed refresh, current session/device reload, owner-scoped repositories and negative API tests |
| Upload/ingest | oversized/truncated media, path traversal, partial publication | bounded resumable staging, generated keys, decode/hash/fingerprint checks, crash windows, immutable CAS and quarantine |
| API/stream ↔ Vault | hash-as-authorization, Range leakage | owner authorization before object lookup, masked not-found, capability refresh, no raw path/URL response |
| Source/import adapters | SSRF, token leakage, archive/HTML/CSV abuse | provider-neutral file fixtures, bounded parsers, no remote auto-fetch, no live provider selected; any network adapter still requires allowlist/policy review |
| Shell media tools | command injection, hostile files, resource exhaustion | argument arrays without shell interpolation, pinned/checksummed FFmpeg/fpcalc, bounded timeout/output and hostile-media tests |
| GPU/model supply chain | unlicensed/malicious weights, CUDA outage/OOM | isolated optional lock/image/profile, hash/license registry, no arbitrary URL/path, no model installed or active; A-030 deferred visibly |
| Logs/exports | token, private URL, path or payload disclosure | typed secrets, recursive redaction, bounded stable errors, production-source scan and runtime/import negative tests |
| Admin/global data actions | accidental delete/merge | local bootstrap boundary, RBAC/audit seams, logical deletion/grace rules, auto-match disabled, no production delete command executed |

## Dependency and secret evidence

These are historical RC1 results. The release-candidate workflow and local release audit regenerate
the current dependency, license, vulnerability and secret-scan evidence.

- Committed CycloneDX 1.5 SBOMs cover the server and isolated GPU projects. The release-candidate
  workflow generates a fresh root contract-tool SBOM for each bundle.
- The RC1 license inventory resolved every recorded Python and Android release-runtime entry.
  LGPL/MPL notice/linking and
  NVIDIA proprietary redistribution obligations are explicit; publication still requires legal/
  notice review and is outside P14.
- `uv audit` queried OSV for 36 root, 46 server and 55 isolated-GPU packages: zero reported
  vulnerabilities and zero adverse statuses on 2026-08-17.
- Production-source scan covers server/GPU/Android main source, Compose/deployment and scripts. It
  found no private-key, AWS, GitHub, Slack or token-bearing private-URL pattern. The only allowlist
  is exact-path scoped to the documented disposable loopback database credential.
- Android backup is disabled (`allowBackup=false`, `fullBackupContent=false`); bearer material is
  Keystore-owned. Revoked URI/device credential behavior remains fail-closed and repairable.

## Residual security boundaries

- No public reverse proxy/domain/TLS, CSRF Web UI, production RBAC topology, backup destination or
  external provider is selected. These require a deployment-specific review.
- The local scanner is deterministic and high-confidence; it complements rather than replaces a
  hosted historical secret scanner when a Git host is selected.
- Container base images are digest-pinned and locally rebuilt; no image was pushed or externally
  signed.
