package app.autplay.application.statistics

import java.time.Clock
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import org.junit.Assert.assertEquals
import org.junit.Test

class ProfileStatisticsCutoffsTest {
    @Test
    fun `calendar windows start at local midnight and stop at injected now`() {
        val zone = ZoneId.of("Europe/Moscow")
        val now = Instant.parse("2026-08-25T12:34:56Z")
        val cutoffs = ProfileStatisticsCutoffs.current(Clock.fixed(now, zone), zone)

        assertEquals(now.toEpochMilli(), cutoffs.throughMs)
        assertEquals(start(LocalDate.of(2026, 8, 19), zone), cutoffs.last7DaysFromMs)
        assertEquals(start(LocalDate.of(2026, 7, 27), zone), cutoffs.last30DaysFromMs)
        assertEquals(start(LocalDate.of(2025, 8, 26), zone), cutoffs.last365DaysFromMs)
    }

    private fun start(date: LocalDate, zone: ZoneId): Long = date.atStartOfDay(zone).toInstant().toEpochMilli()
}
