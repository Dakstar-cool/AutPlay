# macOS automated build and test gate

`ci-desktop.yml` runs on the standard Apple Silicon `macos-15` runner. Standard
hosted compute is free while this repository is public. No larger runner, signing
secret, deployment credential or production service is used.

Pushes to `codex/macos-port-*` with desktop/shared/build changes trigger the gate.
The workflow also supports manual dispatch once registered by GitHub. Its job has
a 90-minute timeout, read-only repository permission, and cancels superseded runs
on the same branch. Gradle runs sequentially with one worker, the committed 2 GiB
heap, pinned JDK/wrapper/dependencies and strict checksum verification.

The full gate builds the unsigned `.app` and `.dmg`, executes all four shared JVM
test suites, then repeats those suites with the desktop tests and the packaged
ARM64 audio helper. The actual AVFoundation test must execute and pass; skips or
missing XML cannot produce a successful evidence summary. The packaged app must
open its window/render frames with its bundled JDK in a disposable data root. The
independent synthetic WAV/HTTP probe also runs and must pass. Failures do not
prevent independent probe diagnostics from being collected.

Only JUnit results, generated Room schemas, packaging logs and sanitized JSON
evidence are uploaded, retained for three days. DMGs are built and SHA-256 hashed
in `ci-summary.json`, but are not uploaded, keeping artifact storage small.
GitHub's ordinary job logs retain compiler/test output. No Gradle or uv cache is
uploaded by this workflow. A successful run qualifies automated development
checks only: audible output, all audio codecs, signed account/Keychain/Secure
Enclave paths, Gatekeeper and the manual feature matrix remain separate gates.

The initial CI branch snapshots `apps/desktop`, `shared`, and
`tools/macos-audio-spike` from the existing local macOS integration worktree based
on `b72498f6f569d98f37a97cee4ebceab571cf434b`. Local Android and server continuations
are not part of this CI snapshot. The workflow summary records the exact tested
commit and run identity; the source worktree is retained unchanged.

## Initial runner preparation repair

Run `42806646438` exposed an inherited setup issue: setup-uv's `python-version`
sets the requested interpreter but does not install it, so `uv python find` failed
in three subsequent steps. Reviewed fixes were explicit `uv python install`,
automatic installation through `uv run`, setup-uv environment activation, and
`actions/setup-python`. The gate uses explicit installation with the already pinned
uv, then verifies Python 3.14.7/ARM64 and exports its resolved path once. This
preserves the pinned distribution and avoids adding an action or project environment.
See [Astral's Actions guide](https://docs.astral.sh/uv/guides/integration/github/),
[environment activation](https://github.com/astral-sh/setup-uv/blob/main/docs/environment-and-tools.md)
and [setup-python](https://github.com/actions/setup-python).
