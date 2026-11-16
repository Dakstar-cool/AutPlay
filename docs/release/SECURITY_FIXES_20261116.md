# Dependency security maintenance — 2026-11-16

The GitHub default-branch inventory contained 36 open Dependabot alerts: 12 critical,
13 high, nine moderate and two low. These were repeated occurrences of six package
families across the server, GPU, training and acquisition lockfiles.

| Package | Corrected locked version | Scope |
| --- | --- | --- |
| brotli | 1.2.1 | Server, GPU, training, acquisition |
| brotlicffi | 1.2.1.0 | Server, GPU, training, acquisition |
| certifi | 2026.10.18 | Server, GPU, training, acquisition |
| pyasn1 | 0.6.5 | Server, GPU, training |
| filelock | 4.2.1 | Training |
| setuptools | 84.1.0 | Training |

The newer Mac/server branch already carried the server corrections; integration brings
them to the default-branch release. Other affected lockfiles were updated with targeted
`uv lock --upgrade-package` operations using the pinned uv 0.12.3.

Frozen OSV-backed `uv audit` checks returned zero known vulnerabilities and zero adverse
statuses for all five project graphs: root tooling (34 audited packages), server (74),
GPU (82), training (111) and local acquisition (35). These are per-graph counts, not a
unique-package count or a claim that software has no unknown vulnerabilities.

The fixes cover the [Brotli decoder advisory](https://github.com/advisories/GHSA-9v8h-qqgm-4m35),
[certifi trust-store advisory](https://github.com/advisories/GHSA-6q48-6c7w-9gw2), the four
pyasn1 decoding-complexity advisories and the filelock/setuptools local-file advisories
reported by GitHub. Alerts are resolved by corrected dependencies, not dismissed.

PRs [#16](https://github.com/Dakstar-cool/AutPlay/pull/16),
[#17](https://github.com/Dakstar-cool/AutPlay/pull/17) and
[#18](https://github.com/Dakstar-cool/AutPlay/pull/18) were reviewed against official upstream
tag commit identities and their passing CI, then merged. New Mac workflows use the same
full-SHA action pins. Pinned Python, uv, JDK and Android toolchains are unchanged.

This records repository/package maintenance. Running servers require their own controlled
update and backup/restore process; this release does not deploy them automatically.
