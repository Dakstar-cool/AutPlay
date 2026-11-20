package app.autplay.application.recommendation

import app.autplay.application.sync.ClientEventBinding
import app.autplay.domain.LocalId
import java.time.LocalDate
import java.time.LocalDateTime
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/** Frozen when a wave starts. Queue restoration and transport retries use these same values. */
data class PersonalWaveSession(
    val id: LocalId,
    val mode: String,
    val date: LocalDate,
    val hour: Int,
) {
    init {
        require(mode in MODES && hour in 0..23)
    }

    fun requestContext() = buildJsonObject {
        put("version", 1)
        put("session_id", id.value)
        put("local_date", date.toString())
        put("local_hour", hour)
    }

    internal fun queueContext(binding: ClientEventBinding?): String = listOf(
        PREFIX, binding?.userId?.value.orEmpty(), binding?.deviceId?.value.orEmpty(),
        binding?.journalEpoch?.value.orEmpty(), id.value, mode, date.toString(), hour.toString(),
    ).joinToString("|")

    companion object {
        private const val PREFIX = "PERSONAL_WAVE_V1"
        internal fun isPersonalQueue(context: String?) = context?.startsWith("$PREFIX|") == true
        private val MODES = setOf("GENERAL", "SLEEP", "WORK")

        fun start(context: String?, now: LocalDateTime = LocalDateTime.now()) = PersonalWaveSession(
            LocalId.random(), context?.takeIf(MODES::contains) ?: "GENERAL", now.toLocalDate(), now.hour,
        )

        internal fun restore(raw: String?, binding: ClientEventBinding?): PersonalWaveSession? {
            if (raw == null || raw.length > 256 || !raw.startsWith("$PREFIX|")) return null
            val parts = raw.split('|')
            if (parts.size != 8 || parts[1] != binding?.userId?.value.orEmpty() ||
                parts[2] != binding?.deviceId?.value.orEmpty() ||
                (parts[3].isNotEmpty() && parts[3] != binding?.journalEpoch?.value)) return null
            return try {
                PersonalWaveSession(LocalId(parts[4]), parts[5], LocalDate.parse(parts[6]), parts[7].toInt())
            } catch (_: java.time.DateTimeException) {
                null
            } catch (_: IllegalArgumentException) {
                null
            }
        }
    }
}
