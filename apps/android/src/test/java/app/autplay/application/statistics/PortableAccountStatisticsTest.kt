package app.autplay.application.statistics

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Test

class PortableAccountStatisticsTest {
    private val account = "01234567-1234-4321-8234-012345678901"
    private val first = "10000000-0000-4000-8000-000000000001"
    private val second = "10000000-0000-4000-8000-000000000002"

    @Test fun transitiveServerAliasesCountOnceAndFutureHistoryDoesNotInflateAccount() {
        val stats = PortableStatisticsAccumulator(account, 1000)
        stats.add(listening(listOf(first), 1))
        stats.add(listening(listOf(second), 1))
        stats.add(listening(listOf(first, second), 2))
        stats.add(listening(listOf("10000000-0000-4000-8000-000000000003"), 3, started = 1001))
        val result = stats.snapshot()
        assertEquals(30_000L, result.listenedMs)
        assertEquals(1L, result.topTracks.single().playSessionCount)
        assertEquals(30_000L, result.topGenres.single().listenedMs)
        assertEquals(account, result.accountId)
        assertEquals(result, decodeAccountStatistics(encodeAccountStatistics(result).toByteArray(), account))
    }

    private fun listening(aliases: List<String>, updated: Long, started: Long = 1): String = buildJsonObject {
        put("version", 1); put("updated_at_ms", updated)
        put("event_ids", JsonArray(aliases.map(::JsonPrimitive)))
        put("started_at_ms", started); put("played_ms", 30_000L)
        put("track_key", "20000000-0000-4000-8000-000000000001")
        put("title", "Song"); put("artist", "Artist")
        put("genres", JsonArray(listOf(JsonPrimitive("Rock"), JsonPrimitive("rock"))))
    }.toString()
}
