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

    @Test fun legacyOwnerCacheDropsSkipCounts() {
        val row = """{"surface":"home","kind":"REMIX","plays":5,"skips":3}"""
        val raw = document.dropLast(1) + ""","variation_skips":[$row]}"""
        val snapshot = decodeAccountStatistics(raw.toByteArray(), account)
        assertEquals(decodeAccountStatistics(document.toByteArray(), account), snapshot)
        assertEquals(false, "variation_skips" in encodeAccountStatistics(snapshot))
    }

    @Test fun liveSonaReportRequiresAccountPositiveLifetimeAndValidCounts() {
        val row = """{"surface":"home","kind":"REMIX","plays":5,"skips":3}"""
        val raw = """{"schema_version":1,"account_id":"$account","remaining_ms":1000,"variation_skips":[$row]}"""
        val report = decodeSonaVariationSkips(raw.toByteArray(), account)
        assertEquals(3L, report.rows.single().skips)
        assertEquals(1000L, report.remainingMs)
        for (bad in listOf(raw.replace(account, "other"), raw.replace("1000", "0"),
            raw.replace("[$row]", "[$row,$row]"), raw.replace("REMIX", "DEMO"), raw.replace("3}", "6}"))) {
            assertEquals(true, runCatching { decodeSonaVariationSkips(bad.toByteArray(), account) }.isFailure)
        }
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
