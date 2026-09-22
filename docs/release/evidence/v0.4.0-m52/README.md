# AutPlay v0.4.0 — Samsung Galaxy M52 evidence

Captured on 22 September 2026 from physical device `SM-M526B` (`RFCRB0T749L`, Android 13 /
API 33) after installing the side-by-side QA debug package `app.autplay.qa`, version `0.4.0`,
`versionCode 12`.

The exported `M3VisualEvidenceActivity` is present only in debug builds. It renders deterministic
synthetic artists, tracks and server state, so these public images contain no personal library,
account, server address, token or notification content. The QA package is independent from the
normal `app.autplay` database. The final main-package update on the same device is verified
separately from the release APK.

| File | State |
| --- | --- |
| `m52-home-ru-light.png` | Home / active local playback, Russian, light theme |
| `m52-search-vault-offline-ru-dark.png` | Local search remains available while Vault is offline, Russian, dark theme |
| `m52-library-albums-ru-light.png` | Offline album library, Russian, light theme |
| `m52-settings-about-v040-ru-dark.png` | Settings / About with visible `AutPlay 0.4.0`, Russian, dark theme |

SHA-256:

```text
829c28131bab4224f5c83f25df7353e09138230a814758ba7972e61a402fe5e3  m52-home-ru-light.png
333768fefc55e867b7aa0fbf985103dc1113c3b631d2ebdd86483ccc9e7b7661  m52-library-albums-ru-light.png
325eefc6d88dba6b838075c03f5305709afbf11a429060947a74c1e499f75bd8  m52-search-vault-offline-ru-dark.png
43bc005a64230cf358005c38264e14088c35c9bda0e3f09957ea1d3c57ea92e3  m52-settings-about-v040-ru-dark.png
```
