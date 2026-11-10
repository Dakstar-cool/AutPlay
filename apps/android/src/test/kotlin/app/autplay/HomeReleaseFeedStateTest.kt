package app.autplay

import app.autplay.application.releasewatch.ReleaseFeedAction
import app.autplay.application.releasewatch.ReleaseFeedException
import app.autplay.application.releasewatch.ReleaseFeedFailure
import app.autplay.application.releasewatch.ReleaseFeedItem
import app.autplay.application.releasewatch.ReleaseFeedPage
import app.autplay.application.releasewatch.ReleaseFeedPort
import app.autplay.application.releasewatch.ReleaseFeedState
import app.autplay.application.releasewatch.WatchReason
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class HomeReleaseFeedStateTest {
    private val profile = ServerProfileId("11111111-1111-4111-8111-111111111111")

    @Test fun stablePagesAppendWithoutDuplicateCandidates() = runBlocking {
        val first = fixture("22222222-2222-4222-8222-222222222222")
        val second = fixture("33333333-3333-4333-8333-333333333333")
        val port = object : ReleaseFeedPort {
            override suspend fun page(profileId: ServerProfileId, cursor: String?): ReleaseFeedPage =
                if (cursor == null) ReleaseFeedPage(listOf(first), "cursor-one")
                else ReleaseFeedPage(listOf(first, second), null)
        }
        val state = HomeReleaseFeedState(profile, port)
        state.refresh()
        assertEquals(listOf(first), state.items)
        state.loadMore()
        assertEquals(listOf(first, second), state.items)
        assertNull(state.nextCursor)
        assertEquals(HomeReleaseFeedStatus.READY, state.status)
    }

    @Test fun unavailableRouteCannotLeakPreviousPageAndOfflinePageKeepsRetryableCursor() = runBlocking {
        val item = fixture("22222222-2222-4222-8222-222222222222")
        var failure = ReleaseFeedFailure.OFFLINE
        val port = object : ReleaseFeedPort {
            override suspend fun page(profileId: ServerProfileId, cursor: String?): ReleaseFeedPage {
                if (cursor == null) return ReleaseFeedPage(listOf(item), "next")
                throw ReleaseFeedException(failure)
            }
        }
        val state = HomeReleaseFeedState(profile, port)
        state.refresh()
        state.loadMore()
        assertEquals(HomeReleaseFeedStatus.OFFLINE, state.pageFailure)
        assertEquals(listOf(item), state.items)
        assertEquals("next", state.nextCursor)
        failure = ReleaseFeedFailure.UNAVAILABLE
        state.loadMore()
        assertEquals(HomeReleaseFeedStatus.UNAVAILABLE, state.status)
        assertTrue(state.items.isEmpty())
        assertNull(state.nextCursor)
    }

    private fun fixture(id: String) = ReleaseFeedItem(
        candidateId = id,
        canonicalArtistId = "44444444-4444-4444-8444-444444444444",
        artistName = "Artist",
        title = "Release",
        releasedAt = "2026-09-24T00:00:00+00:00",
        reason = WatchReason.MANUAL_FOLLOW,
        state = ReleaseFeedState.DISCOVERED,
        scoreMilli = 100,
        releaseId = null,
        recordingId = null,
        userTrackRefId = null,
        providerId = "55555555-5555-4555-8555-555555555555",
        evidenceRunId = "66666666-6666-4666-8666-666666666666",
        evidenceAdapterVersion = "1.0.0",
        availableAction = ReleaseFeedAction.OPEN_DISCOVERY,
    )
}
