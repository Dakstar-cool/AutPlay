package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopLocalStatistics
import app.autplay.application.desktop.DesktopLocalStatisticsState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue

/** Retained callback policy in the existing UI composite; no Compose or runtime execution here. */
internal fun assertDesktopLocalStatisticsTargets(): Unit {
    val state = DesktopLocalStatisticsState()
    var liveState = state
    val owner = DesktopLocalStatisticsCallbacks(isCurrent = { it === liveState })
    val captured = DesktopLocalStatisticsInput(state, owner, blocked = false)
    val lifetime = DesktopLocalStatisticsLifetime()
    val revision = lifetime.observe(captured)
    var latest = captured
    var navigation = true
    val valid = desktopLocalStatisticsGuard(lifetime, revision, captured, { latest }, { navigation })
    assertTrue(valid())
    liveState = DesktopLocalStatisticsState(revision = 1)
    assertFalse(valid()) // The host can change before recomposition observes it.
    liveState = state
    val equalOwner = DesktopLocalStatisticsCallbacks(owner.onRefresh, owner.onRetry, owner.isCurrent)
    assertNotEquals(owner, equalOwner)
    assertNotSame(owner, equalOwner)
    latest = DesktopLocalStatisticsInput(state, equalOwner, blocked = false)
    assertFalse(valid())
    latest = captured
    navigation = false
    assertFalse(valid())
    navigation = true
    latest = DesktopLocalStatisticsInput(state, owner, blocked = true)
    assertFalse(valid())
    latest = captured
    lifetime.observe(DesktopLocalStatisticsInput(DesktopLocalStatisticsState(), owner, blocked = false))
    lifetime.observe(captured)
    assertFalse(valid()) // Returning to the old references does not restore an old closure.
    val renewed = desktopLocalStatisticsGuard(lifetime, lifetime.observe(captured), captured, { latest }, { true })
    assertTrue(renewed())
    lifetime.retire()
    assertFalse(renewed())

    val zero = DesktopLocalStatistics(10, 0, 0, emptyList(), emptyList())
    assertEquals(DesktopStatisticsText.NotLoaded, desktopLocalStatisticsStatus(state, blocked = false))
    assertEquals(DesktopStatisticsText.Empty, desktopLocalStatisticsStatus(DesktopLocalStatisticsState(zero), blocked = false))
    assertEquals(DesktopStatisticsText.Failure,
        desktopLocalStatisticsStatus(DesktopLocalStatisticsState(errorCode = "desktop_local_statistics_load_failed"), blocked = false))
    assertEquals(DesktopStatisticsText.RetainedFailure,
        desktopLocalStatisticsStatus(DesktopLocalStatisticsState(zero, errorCode = "desktop_local_statistics_load_failed"), blocked = false))
    assertEquals(DesktopStatisticsText.Loading, desktopLocalStatisticsStatus(DesktopLocalStatisticsState(zero, loading = true), blocked = false))
    assertNull(desktopLocalStatisticsStatus(DesktopLocalStatisticsState(zero), blocked = true))
    assertFalse(DesktopLocalStatisticsInput(DesktopLocalStatisticsState(loading = true), owner, blocked = false).available())
    assertEquals("1 h 1 min", desktopStatisticsTime(3_660_000, DesktopLanguage.English))
    assertEquals("1 ч 1 мин", desktopStatisticsTime(3_660_000, DesktopLanguage.Russian))
}
