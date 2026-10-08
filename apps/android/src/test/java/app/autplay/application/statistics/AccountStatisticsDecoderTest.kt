package app.autplay.application.statistics

import org.junit.Assert.assertEquals
import org.junit.Assert.fail
import org.junit.Test

class AccountStatisticsDecoderTest {
    private val account = "01234567-1234-4321-8234-012345678901"
    private val document get() = """{"schema_version":1,"account_id":"$account","through_ms":123,"listened_ms":30000,"top_genres":[],"top_tracks":[],"top_artists":[]}"""

    @Test fun accountIdentityAndAggregateAreRetained() {
        val result = decodeAccountStatistics(document.toByteArray(), account)
        assertEquals(account, result.accountId)
        assertEquals(30000L, result.listenedMs)
    }

    @Test fun anotherAccountNegativeCountsAndDuplicateKeysCannotBeDisplayed() {
        reject(document, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        reject(document.replace("30000", "-1"))
        reject(document.replace("\"listened_ms\":30000", "\"listened_ms\":30000,\"listened_ms\":1"))
        reject(document.replace("\"schema_version\":1", "\"schema_version\":\"1\""))
    }

    private fun reject(value: String, expected: String = account) {
        try { decodeAccountStatistics(value.toByteArray(), expected); fail("Expected rejection") }
        catch (_: Exception) { }
    }
}
