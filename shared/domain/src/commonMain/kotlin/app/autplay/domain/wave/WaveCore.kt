package app.autplay.domain.wave

/** Framework-neutral sequencing, timing and correction rules for a Wave room. */
data class WaveCommand(val sequence: Long, val kind: String, val payload: String = "") {
    init { require(sequence > 0); require(kind.matches(Regex("[A-Z_]{1,48}"))) }
}

sealed interface CommandAcceptance {
    data class Applied(val nextSequence: Long) : CommandAcceptance
    data object Duplicate : CommandAcceptance
    data object CatchUpRequired : CommandAcceptance
}

object WaveSequenceRecovery {
    fun accept(lastApplied: Long, command: WaveCommand): CommandAcceptance = when {
        command.sequence <= lastApplied -> CommandAcceptance.Duplicate
        command.sequence == lastApplied + 1 -> CommandAcceptance.Applied(command.sequence)
        else -> CommandAcceptance.CatchUpRequired
    }
}

enum class WaveAvailability { LOCAL_READABLE, DOWNLOADED, VAULT_STREAMABLE, UNAVAILABLE }
enum class WavePrefetchMode { OFF, NEXT, NEXT_3, AGGRESSIVE_WIFI }

object WavePrefetchPlanner {
    fun count(mode: WavePrefetchMode, unmetered: Boolean): Int = when (mode) {
        WavePrefetchMode.OFF -> 0
        WavePrefetchMode.NEXT -> 1
        WavePrefetchMode.NEXT_3 -> 3
        WavePrefetchMode.AGGRESSIVE_WIFI -> if (unmetered) 3 else 1
    }
}

/** NTP midpoint estimator: 7 initial samples, retain 20, median of five lowest RTT values. */
class ServerClockEstimator(private val maxSamples: Int = 20) {
    private data class Sample(val offset: Long, val rtt: Long, val receivedAt: Long)
    private val samples = ArrayDeque<Sample>()
    fun addSample(clientSentMs: Long, serverReceivedMs: Long, serverSentMs: Long, clientReceivedMs: Long): Boolean {
        val rtt = (clientReceivedMs - clientSentMs) - (serverSentMs - serverReceivedMs)
        if (rtt < 0) return false
        val offset = ((serverReceivedMs - clientSentMs) + (serverSentMs - clientReceivedMs)) / 2
        samples.addLast(Sample(offset, rtt, clientReceivedMs))
        while (samples.size > maxSamples) samples.removeFirst()
        return true
    }
    fun isEligible(nowMs: Long): Boolean = samples.size >= 7 && nowMs - samples.last().receivedAt <= 60_000 && p95Rtt() <= 1_000 && uncertaintyMs() <= 100
    fun serverNow(clientNowMs: Long): Long = clientNowMs + selected().let { if (it.isEmpty()) 0 else it.map(Sample::offset).sorted()[it.size / 2] }
    fun p95Rtt(): Long = samples.map(Sample::rtt).sorted().let {
        if (it.isEmpty()) Long.MAX_VALUE else it[kotlin.math.ceil(it.size * 0.95).toInt() - 1]
    }
    fun uncertaintyMs(): Long = selected().let { values ->
        if (values.isEmpty()) Long.MAX_VALUE else values.maxOf { it.rtt / 2 } +
            values.map(Sample::offset).let { offsets -> (offsets.max() - offsets.min()) / 2 }
    }
    private fun selected(): List<Sample> = samples.sortedBy(Sample::rtt).take(5)
}

data class ScheduledStart(val localStartAtMs: Long, val isLateJoin: Boolean)
object ScheduledStartPlanner {
    fun plan(serverStartAtMs: Long, estimator: ServerClockEstimator, localNowMs: Long): ScheduledStart? {
        if (!estimator.isEligible(localNowMs)) return null
        val lead = (3 * estimator.p95Rtt() + 2 * estimator.uncertaintyMs() + 250).coerceIn(2_000, 8_000)
        if (serverStartAtMs - estimator.serverNow(localNowMs) < lead) return null
        return ScheduledStart(localNowMs + (serverStartAtMs - estimator.serverNow(localNowMs)), false)
    }
}

sealed interface DriftAction { data object None : DriftAction; data class Speed(val multiplier: Float) : DriftAction; data class Seek(val positionMs: Long) : DriftAction }
/** Hysteretic policy, evaluated by caller at most once per two seconds. */
class DriftCorrector {
    private var speedActive = false
    private var lastSeekAtMs = Long.MIN_VALUE / 2
    private var lastDirectionAtMs = Long.MIN_VALUE / 2
    private var direction = 0
    private var mediumCount = 0
    private var settledCount = 0
    fun correct(driftMs: Long, expectedPositionMs: Long, nowMs: Long): DriftAction {
        val abs = kotlin.math.abs(driftMs)
        if (speedActive && abs <= 40) { settledCount++; if (settledCount >= 2) { speedActive = false; return DriftAction.Speed(1f) } }
        else settledCount = 0
        if (abs <= 80) return DriftAction.None
        val nextDirection = if (driftMs > 0) -1 else 1
        mediumCount = if (abs <= 250) mediumCount + 1 else 3
        if ((abs > 250 || mediumCount >= 3) && nowMs - lastSeekAtMs >= 10_000) { lastSeekAtMs = nowMs; speedActive = false; mediumCount = 0; return DriftAction.Seek(expectedPositionMs.coerceAtLeast(0)) }
        if (abs > 250 || (direction != 0 && direction != nextDirection && nowMs - lastDirectionAtMs < 6_000)) return DriftAction.None
        direction = nextDirection; lastDirectionAtMs = nowMs; speedActive = true
        return DriftAction.Speed(if (nextDirection < 0) .98f else 1.02f)
    }
}

enum class WaveRuntimeState { IDLE, PREFLIGHT, SCHEDULED, PLAYING, DEGRADED, REJOINING, CLOSED }
