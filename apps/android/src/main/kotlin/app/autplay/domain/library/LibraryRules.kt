package app.autplay.domain.library

import java.math.BigInteger

/**
 * Framework-free rules for the P07 local library.  These values deliberately retain unknown
 * attribution strings: a newer producer can be displayed and persisted, but cannot acquire a
 * meaning in this client merely by being present.
 */
data class RecommendationAttribution(
    val recommendationRequestId: String,
    val recordingId: String,
    val sourceRank: Int,
    val source: String,
    val surface: String,
    val unknownFields: Map<String, String> = emptyMap(),
) {
    init {
        requireUuid(recommendationRequestId)
        requireUuid(recordingId)
        require(sourceRank in 1..1_000)
        require(SAFE_TOKEN.matches(source) && SAFE_TOKEN.matches(surface))
        require(unknownFields.size <= MAX_UNKNOWN_FIELDS)
        require(unknownFields.all { (key, value) -> key.length <= MAX_TEXT && value.length <= MAX_TEXT })
    }

    val isAttributed: Boolean get() = true

    private companion object {
        const val MAX_TEXT = 200
        const val MAX_UNKNOWN_FIELDS = 32
        val SAFE_TOKEN = Regex("^[a-z][a-z0-9_]{0,99}$")
        fun requireUuid(value: String) = require(runCatching { java.util.UUID.fromString(value) }.isSuccess)
    }
}

enum class TrackPreference { NEUTRAL, LIKED, DISLIKED }

data class PreferenceDecision(
    val preference: TrackPreference,
    val excludedFromTaste: Boolean,
    val attribution: RecommendationAttribution?,
) {
    init {
        // A negative preference must never train taste when the user explicitly opted out.
        require(!(excludedFromTaste && attribution?.recommendationRequestId?.isBlank() == true))
    }

    val contributesToTaste: Boolean get() = !excludedFromTaste && preference != TrackPreference.NEUTRAL
}

data class ListeningDecision(
    val playedMs: Long,
    val durationMs: Long?,
    val excludedFromTaste: Boolean,
    val origin: String,
    val explicitFeedback: TrackPreference = TrackPreference.NEUTRAL,
    val attribution: RecommendationAttribution? = null,
) {
    init {
        require(playedMs >= 0)
        require(durationMs == null || durationMs > 0)
        require(origin.isNotBlank() && origin.length <= 100)
        require(origin != "RECOMMENDED" || attribution != null)
    }

    val completionRatio: Double? get() = durationMs?.let { (playedMs.toDouble() / it).coerceIn(0.0, 1.0) }
    val contributesToTaste: Boolean get() = !excludedFromTaste && (playedMs > 0 || explicitFeedback != TrackPreference.NEUTRAL)
}

/** Fixed-width base-62 keys make lexical SQLite ordering deterministic and cheap to rebalance. */
object PlaylistPositionKeys {
    private const val WIDTH = 16
    private const val ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    private val base = BigInteger.valueOf(ALPHABET.length.toLong())
    private val maximum = base.pow(WIDTH).subtract(BigInteger.ONE)

    fun initial(index: Int): String {
        require(index >= 0)
        val stride = maximum.divide(BigInteger.valueOf(10_000L))
        return encode(stride.multiply(BigInteger.valueOf(index.toLong() + 1L)))
    }

    /** Returns a key strictly between nullable boundaries, or null when a bounded rebalance is due. */
    fun between(before: String?, after: String?): String? {
        val lower = before?.let(::decode) ?: BigInteger.ZERO
        val upper = after?.let(::decode) ?: maximum
        require(lower < upper) { "Playlist boundaries are not ordered." }
        val candidate = lower.add(upper).divide(BigInteger.valueOf(2L))
        return if (candidate == lower || candidate == upper) null else encode(candidate)
    }

    /** Generates evenly spaced positions, keeping a durable gap for future moves. */
    fun rebalance(entryIdsInOrder: List<String>): Map<String, String> {
        require(entryIdsInOrder.size <= 10_000)
        require(entryIdsInOrder.distinct().size == entryIdsInOrder.size)
        val stride = maximum.divide(BigInteger.valueOf(entryIdsInOrder.size.toLong() + 1L))
        return entryIdsInOrder.mapIndexed { index, id -> id to encode(stride.multiply(BigInteger.valueOf(index + 1L))) }.toMap()
    }

    private fun decode(value: String): BigInteger {
        require(value.length == WIDTH && value.all { it in ALPHABET }) { "Invalid playlist position key." }
        return value.fold(BigInteger.ZERO) { total, character ->
            total.multiply(base).add(BigInteger.valueOf(ALPHABET.indexOf(character).toLong()))
        }
    }

    private fun encode(value: BigInteger): String {
        require(value > BigInteger.ZERO && value < maximum)
        var remaining = value
        val characters = CharArray(WIDTH)
        for (index in WIDTH - 1 downTo 0) {
            val pair = remaining.divideAndRemainder(base)
            characters[index] = ALPHABET[pair[1].toInt()]
            remaining = pair[0]
        }
        return String(characters)
    }
}
