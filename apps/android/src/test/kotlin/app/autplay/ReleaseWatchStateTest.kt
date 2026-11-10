package app.autplay

import app.autplay.application.releasewatch.ReleaseWatchException
import app.autplay.application.releasewatch.ReleaseWatchPort
import app.autplay.application.releasewatch.WatchFailure
import app.autplay.application.releasewatch.WatchPage
import app.autplay.application.releasewatch.WatchReason
import app.autplay.application.releasewatch.WatchedArtist
import app.autplay.application.releasewatch.OwnerAutoDownloadSettings
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ReleaseWatchStateTest {
    private val profile = ServerProfileId("11111111-1111-4111-8111-111111111111")
    private val artistId = "22222222-2222-4222-8222-222222222222"

    @Test fun followAndUnfollowUseServerResultAndKeepManualAndHistoryReasonsDistinct() = runBlocking {
        val port = FakePort()
        val state = ReleaseWatchState(profile, port)
        state.load()
        assertEquals(ReleaseWatchStatus.READY, state.status)
        assertEquals(WatchReason.LISTENING_HISTORY, state.artists.single().reason)

        state.setFollow(artistId, true)
        assertEquals(listOf(true), port.commands)
        assertEquals(WatchReason.MANUAL_FOLLOW, state.artists.single().reason)

        state.setFollow(artistId, false)
        assertEquals(listOf(true, false), port.commands)
        assertTrue(state.artists.isEmpty())
        assertEquals(setOf(artistId), state.exclusions)
    }

    @Test fun failedMutationDoesNotClaimFollowAndShowsUnavailableCapability() = runBlocking {
        val port = FakePort().apply { watch = null }
        val state = ReleaseWatchState(profile, port)
        state.load()
        port.failMutation = true
        state.setFollow(artistId, true)
        assertEquals(ReleaseWatchStatus.UNAVAILABLE, state.status)
        assertTrue(state.artists.isEmpty())
    }

    @Test fun followedListLoadsPastFirstServerPage() = runBlocking {
        val ids = (1..101).map { "00000000-0000-4000-8000-%012d".format(it) }
        val port = object : ReleaseWatchPort {
            override suspend fun artists(profileId: ServerProfileId, afterArtistId: String?): WatchPage<WatchedArtist> {
                val start = if (afterArtistId == null) 0 else ids.indexOf(afterArtistId) + 1
                return WatchPage(ids.drop(start).take(100).map { WatchedArtist(it, WatchReason.MANUAL_FOLLOW, 0, null) })
            }
            override suspend fun exclusions(profileId: ServerProfileId, afterArtistId: String?) = WatchPage(emptyList<String>())
            override suspend fun setFollow(profileId: ServerProfileId, artistId: String, follow: Boolean) = Unit
        }
        val state = ReleaseWatchState(profile, port)
        state.load()
        assertEquals(ReleaseWatchStatus.READY, state.status)
        assertEquals(101, state.artists.size)
    }

    @Test fun operatorGateBlocksEnableAndFailedCasNeverClaimsConsent() = runBlocking {
        val port = FakePort()
        val state = ReleaseWatchState(profile, port)
        state.load()
        assertEquals(ReleaseWatchStatus.READY, state.autoStatus)
        state.setAutoDownload(true)
        assertTrue(port.autoCommands.isEmpty())
        assertEquals(false, state.autoSettings?.enabled)

        port.auto = port.auto.copy(executionEnabled = true)
        state.load()
        port.failAuto = true
        state.setAutoDownload(true)
        assertEquals(false, state.autoSettings?.enabled)
        assertEquals(WatchFailure.SERVER, state.autoCommandFailure)

        port.failAuto = false
        state.setAutoDownload(true)
        assertEquals(true, state.autoSettings?.enabled)
        state.setAutoDownload(false)
        assertEquals(false, state.autoSettings?.enabled)
        assertEquals(listOf(Triple(true, 1L, "exact-token"), Triple(true, 1L, "exact-token"), Triple(false, 2L, null)), port.autoCommands)
    }

    private inner class FakePort : ReleaseWatchPort {
        var watch: WatchedArtist? = WatchedArtist(artistId, WatchReason.LISTENING_HISTORY, 500, 1)
        var excluded = false
        var failMutation = false
        var auto = OwnerAutoDownloadSettings(false, 1, false, "exact-token")
        var failAuto = false
        val autoCommands = mutableListOf<Triple<Boolean, Long, String?>>()
        val commands = mutableListOf<Boolean>()

        override suspend fun artists(profileId: ServerProfileId, afterArtistId: String?) =
            WatchPage(listOfNotNull(watch))

        override suspend fun exclusions(profileId: ServerProfileId, afterArtistId: String?) =
            WatchPage(if (excluded) listOf(artistId) else emptyList())

        override suspend fun setFollow(profileId: ServerProfileId, artistId: String, follow: Boolean) {
            if (failMutation) throw ReleaseWatchException(WatchFailure.UNAVAILABLE)
            commands += follow
            watch = if (follow) WatchedArtist(artistId, WatchReason.MANUAL_FOLLOW, 500, 1) else null
            excluded = !follow
        }

        override suspend fun autoDownloadSettings(profileId: ServerProfileId) = auto

        override suspend fun setAutoDownload(
            profileId: ServerProfileId,
            enabled: Boolean,
            expectedRevision: Long,
            consequenceConfirmation: String?,
        ) {
            autoCommands += Triple(enabled, expectedRevision, consequenceConfirmation)
            if (failAuto) throw ReleaseWatchException(WatchFailure.SERVER)
            auto = auto.copy(enabled = enabled, revision = expectedRevision + 1)
        }
    }
}
