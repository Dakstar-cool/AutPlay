# Playback transitions

AutPlay uses Media3's single-player gapless playlist playback. Tracks retain their
complete source boundaries; only encoder delay/padding declared by the codec is
trimmed. There is no inter-track crossfade, second player, or fixed head/tail cut.

## Tempus reference

Reviewed the official Android Subsonic client
[eddyizm/tempus](https://github.com/eddyizm/tempus) at revision
`be56e98452e6a4b995d7e3a48e53438c3b4cfd8d` (2026-11-11).

- [BaseMediaService](https://github.com/eddyizm/tempus/blob/be56e98452e6a4b995d7e3a48e53438c3b4cfd8d/app/src/main/java/com/eddyizm/tempus/service/BaseMediaService.kt)
  uses one ExoPlayer with a playlist, audio focus, noisy handling and network wake
  mode. Its default 60-second load buffer is not a transition duration.
- [QueuePreloader](https://github.com/eddyizm/tempus/blob/be56e98452e6a4b995d7e3a48e53438c3b4cfd8d/app/src/main/java/com/eddyizm/tempus/service/QueuePreloader.kt)
  optionally precaches future streaming tracks. It follows repeat/shuffle order,
  excludes radio and downloaded tracks, checks cache/network preferences, and
  cancels stale work through generations and service teardown.
- [ParametricEqAudioProcessor](https://github.com/eddyizm/tempus/blob/be56e98452e6a4b995d7e3a48e53438c3b4cfd8d/app/src/main/java/com/eddyizm/tempus/equalizer/ParametricEqAudioProcessor.kt)
  runs inside the PCM processing chain and retains compatible filter history
  across gapless boundaries.
- [ReplayGainAudioProcessor](https://github.com/eddyizm/tempus/blob/be56e98452e6a4b995d7e3a48e53438c3b4cfd8d/app/src/main/java/com/eddyizm/tempus/util/ReplayGainAudioProcessor.java)
  coordinates current and pending track gain with seek/boundary handling.
- [SleepTimerManager](https://github.com/eddyizm/tempus/blob/be56e98452e6a4b995d7e3a48e53438c3b4cfd8d/app/src/main/java/com/eddyizm/tempus/util/SleepTimerManager.java)
  has a separate ten-second fade-out ending in pause. This is not an audio
  crossfade to the next song. Artwork transitions also do not affect audio.

No inter-track audio mixer or crossfade-duration setting was found in that
revision. Tempus is GPL-3.0; no Tempus source or dependency is incorporated here.
AutPlay implements the observed behavior using its existing DSP and pinned
Media3 1.10.1 APIs.

## AutPlay execution

`PlaybackAudioSink` installs an always-active `PlaybackPcmProcessor` in
`DefaultAudioSink`. The order is codec delay/padding trimming, channel mapping,
Media3 PCM conversion, ReplayGain/EQ, Media3 playback processing, then AudioTrack.
This keeps discarded codec samples out of the EQ's recursive filter history.
Output remains 16-bit PCM; compressed passthrough/offload stays disabled so live
effect changes do not restart playback.

Decoder `Format` metadata is staged on the playback thread. The processor applies
it only when Media3 has drained the departing stream and activates the successor
configuration. An untagged successor returns to unity gain. Compatible track
boundaries retain EQ state; an explicit sink flush for seek/stop clears it.
Format changes rebuild the filter bank. ReplayGain and EQ preference changes
retain the existing short live-update ramps and clipping/headroom policies.

Media3's own 20 ms AudioTrack startup ramp remains. It is not repeated at a
compatible gapless boundary. `WAKE_MODE_NETWORK` holds the playback resources
through screen-off playback; Media3 owns their acquisition/release.

The existing next-track preload switch controls a bounded eight-second sample
preload. It does not switch gapless playback on/off. URI preflight remains bounded
to the current entry and two successors in Media3 timeline order. Wave and guest
Wave preload, room authority and source positions retain their existing behavior.

Queue identity, listening history, notifications, repeat/shuffle, sleep timer and
Room checkpoints continue to follow the one Media3 timeline. No parallel clock,
synthetic early transition or additional persisted playback session is introduced.

## Verification and limits

`PlaybackGaplessTest` compares actual AudioOutput writes with the expected full
PCM stream and requires one reused AudioTrack and no rebuffer at compatible WAV
and tagged-to-untagged FLAC boundaries. Expectations include Media3's initial
startup ramp. The FLAC test also exercises ReplayGain metadata through the real
extractor/decoder and verifies continuous EQ state.

`PlaybackAudioSinkTest` exercises encoder delay/padding, partial output writes,
gain changes, seek resets and format changes through the real sink/output.
`PlaybackNetworkRecoveryTest` exercises a failed successor while the current
track plays and subsequent recovery. Existing service lifecycle, MP3, source
selection, persistence, settings and process-death tests cover surrounding behavior.

Gapless requires compatible output formats and timely media availability. A
different output format may require a new AudioTrack, and an unready network
source may buffer. Silence recorded in the music is preserved. These tests do
not establish seamless output on every codec, Android release or Bluetooth route.
