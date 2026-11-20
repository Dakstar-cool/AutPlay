package app.autplay.playback

import app.autplay.data.settings.PlaybackAudioSettings
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.pow
import kotlin.math.sin

/** Sample-preserving DSP. One instance belongs to one player's sink, never a global audio session. */
internal class PlaybackToneProcessor {
    private var sampleRate = 0
    private var channels = 0
    private var tags = ReplayGainTags()
    private var settings = PlaybackAudioSettings()
    private var bank = EqualizerBank(48_000, 2, settings)
    private var previousBank: EqualizerBank? = null
    private var eqFramesRemaining = 0
    private var rampFrames = 1
    private var gain = 1.0
    private var targetGain = 1.0
    private var gainFramesRemaining = 0

    val isIdentity: Boolean
        get() = gain == 1.0 && targetGain == 1.0 && bank.isIdentity && previousBank == null

    fun configure(rate: Int, channelCount: Int, metadata: ReplayGainTags, preferences: PlaybackAudioSettings) {
        require(rate > 0 && channelCount in 1..32)
        val changedFormat = sampleRate != rate || channels != channelCount
        sampleRate = rate
        channels = channelCount
        rampFrames = (rate / 50).coerceAtLeast(1)
        tags = metadata
        if (changedFormat || settings != preferences) {
            bank = EqualizerBank(rate, channelCount, preferences)
            previousBank = null
            eqFramesRemaining = 0
        }
        settings = preferences
        // The very first sample after configure/seek already has the track's gain.
        gain = tags.amplitude(settings)
        targetGain = gain
        gainFramesRemaining = 0
    }

    fun update(preferences: PlaybackAudioSettings) {
        if (preferences == settings) return
        if (preferences.equalizerEnabled != settings.equalizerEnabled ||
            preferences.equalizerBandsDb != settings.equalizerBandsDb) {
            previousBank = bank
            bank = EqualizerBank(sampleRate, channels, preferences)
            eqFramesRemaining = rampFrames
        }
        settings = preferences
        targetGain = tags.amplitude(settings)
        gainFramesRemaining = rampFrames
    }

    fun flush() {
        bank = EqualizerBank(sampleRate, channels, settings)
        previousBank = null
        eqFramesRemaining = 0
        gain = tags.amplitude(settings)
        targetGain = gain
        gainFramesRemaining = 0
    }

    /** The same frame gain is used for all channels; no samples are inserted or removed. */
    fun processFrame(frame: DoubleArray) {
        if (gainFramesRemaining > 0) {
            gain += (targetGain - gain) / gainFramesRemaining
            gainFramesRemaining--
        }
        val mix = if (eqFramesRemaining > 0) 1.0 - eqFramesRemaining.toDouble() / rampFrames else 1.0
        for (channel in 0 until channels) {
            val sample = frame[channel] * gain
            val filtered = bank.process(sample, channel)
            val prior = previousBank?.process(sample, channel) ?: filtered
            val value = prior + (filtered - prior) * mix
            frame[channel] = if (value.isFinite()) value.coerceIn(-1.0, 1.0) else 0.0
        }
        if (eqFramesRemaining > 0 && --eqFramesRemaining == 0) previousBank = null
    }
}

private class EqualizerBank(sampleRate: Int, channels: Int, settings: PlaybackAudioSettings) {
    private val bands = PlaybackAudioSettings.BAND_FREQUENCIES_HZ.zip(settings.equalizerBandsDb)
        .filter { (frequency, gain) -> settings.equalizerEnabled && gain != 0 && frequency < sampleRate * 0.45 }
    // Cascaded boosts can add: reserve their summed positive gain before filtering.
    private val headroom = 10.0.pow(-bands.sumOf { it.second.coerceAtLeast(0) } / 20.0)
    val isIdentity: Boolean get() = bands.isEmpty()
    private val filters = Array(channels) {
        bands.map { (frequency, gain) -> PeakingFilter(sampleRate, frequency, gain) }
    }

    fun process(sample: Double, channel: Int): Double {
        var value = sample * headroom
        for (filter in filters[channel]) value = filter.process(value)
        return value
    }
}

/** Peaking biquad, Q=0.7, from the W3C Audio EQ Cookbook transfer function. */
private class PeakingFilter(sampleRate: Int, frequency: Int, gainDb: Int) {
    private val a = 10.0.pow(gainDb / 40.0)
    private val omega = 2 * PI * frequency / sampleRate
    private val alpha = sin(omega) / (2 * 0.7)
    private val a0 = 1 + alpha / a
    private val b0 = (1 + alpha * a) / a0
    private val b1 = -2 * cos(omega) / a0
    private val b2 = (1 - alpha * a) / a0
    private val a1 = b1
    private val a2 = (1 - alpha / a) / a0
    private var z1 = 0.0
    private var z2 = 0.0

    fun process(sample: Double): Double {
        val result = b0 * sample + z1
        z1 = b1 * sample - a1 * result + z2
        z2 = b2 * sample - a2 * result
        return result
    }
}
