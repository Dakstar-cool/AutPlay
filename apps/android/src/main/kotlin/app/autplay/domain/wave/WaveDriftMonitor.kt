package app.autplay.domain.wave

sealed interface WaveDriftDecision {
    data object Wait : WaveDriftDecision
    data object Degraded : WaveDriftDecision
    data class Correct(val action: DriftAction) : WaveDriftDecision
}

/** Consumes fresh executor positions against a monotonic anchor; never trusts a cached UI position. */
class WaveDriftMonitor {
    private val corrector = DriftCorrector()
    private var lastSampleAtMs = Long.MIN_VALUE / 2

    fun sample(
        nowMs: Long,
        startAtMs: Long,
        validUntilMs: Long,
        positionMs: Long,
        playing: Boolean,
        buffering: Boolean,
    ): WaveDriftDecision {
        if (nowMs >= validUntilMs || buffering) return WaveDriftDecision.Degraded
        if (nowMs < startAtMs || !playing || nowMs - lastSampleAtMs < 2_000) return WaveDriftDecision.Wait
        lastSampleAtMs = nowMs
        val expected = (nowMs - startAtMs).coerceAtLeast(0)
        val drift = positionMs - expected
        if (kotlin.math.abs(drift) > 1_000) return WaveDriftDecision.Degraded
        return WaveDriftDecision.Correct(corrector.correct(drift, expected, nowMs))
    }
}
