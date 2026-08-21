package app.autplay

import app.autplay.ui.core.DetailKind
import app.autplay.ui.core.DetailTarget
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OfflineCoreDetailStateTest {
    @Test
    fun lateDetailRequestCannotPublishAcrossTargetOrProfileChange() {
        val guard = OfflineDetailRequestGuard()
        val oldArtist = guard.begin(
            "profile-a",
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            DetailTarget(DetailKind.Artist, "11111111-1111-4111-8111-111111111111"),
        )
        val currentRelease = guard.begin(
            "profile-b",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            DetailTarget(DetailKind.Release, "local-release"),
        )

        assertFalse(guard.accepts(oldArtist))
        assertTrue(guard.accepts(currentRelease))
    }

    @Test
    fun presentedDetailMustMatchBothContextAndExactTarget() {
        val state = OfflineCoreDetailState()
        val artist = DetailTarget(DetailKind.Artist, "11111111-1111-4111-8111-111111111111")
        val release = DetailTarget(DetailKind.Release, "local-release")
        state.loadedContextKey = "profile-a"
        state.loadedTarget = artist

        assertTrue(state.matches("profile-a", artist))
        assertFalse(state.matches("profile-a", release))
        assertFalse(state.matches("profile-b", artist))
    }
}
