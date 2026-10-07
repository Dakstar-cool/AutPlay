package app.autplay.application.statistics

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ProfileStatisticsGenresTest {
    @Test
    fun onlyNamedMetadataGenresCountAndRepeatedNamesDoNotMultiplyListeningTime() {
        val payload = """{"revision":1,"state":"READY","fields":{"genres":[" Rock ","rock","Jazz",null,42,"",true]}}"""
        assertEquals(listOf("Rock", "Jazz"), metadataGenres(payload))
    }

    @Test
    fun malformedAbsentFutureOrOversizedMetadataDoesNotInventGenres() {
        listOf(
            "broken",
            """{"revision":1,"state":"READY","fields":{"artist":"Rock"}}""",
            """{"revision":1,"state":"FUTURE_STATE","fields":{"genres":["Rock"]}}""",
            """{"revision":1,"state":"READY","fields":{"genres":"Rock"}}""",
            "x".repeat(140_001),
        ).forEach { assertTrue(metadataGenres(it).isEmpty()) }
    }
}
