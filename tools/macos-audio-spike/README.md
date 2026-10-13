# Early macOS audio probe

This isolated experiment does not select or install a production engine. Read the
[runbook](../../docs/operations/MACOS_NATIVE_SPIKE.md) for sources, limitations,
platform boundaries, commands and the acceptance matrix.

- `fixture_server.py`: in-process loopback auth/Range/redirect/drop/slow fixture,
  deterministic eight-second PCM WAV, real socket self-check.
- `AudioProbe.swift`: AVPlayer CLI and public AVAssetResourceLoaderDelegate /
  URLSession byte transport. Reads launch config only from stdin.
- `run_probe.py`: bounds subprocess execution, builds with the Mac host SDK,
  exercises each fixture, emits sanitized JSON and cleans temporary files.

Python is the repository's pinned `3.14.7`; only the standard library is needed.
macOS native runs require Xcode Command Line Tools already installed. The probe
plays a quiet synthetic tone. Passing the CLI does not prove audible output,
JVM interoperability, Keychain, packaged launch or release support.

From the repository root:

```powershell
$spikePython = (uv python find 3.14.7).Trim()
& $spikePython tools/macos-audio-spike/run_probe.py --self-check
```

On a Mac, using the same pinned Python:

```bash
spike_python="$(uv python find 3.14.7)"
"$spike_python" tools/macos-audio-spike/run_probe.py --run \
  --output tools/macos-audio-spike/.runs/mac-wav-001.json
```

No credentials, account endpoints, installed VLC, network downloads, Gradle,
CI edits, signing identities or production data are used.
