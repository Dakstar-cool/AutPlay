package app.autplay.playback

import app.autplay.domain.LocalId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PlaybackCoreTest {
    @Test fun internalQueueMarkersAreNeverPlayableMediaSources() {
        assertFalse(isResolvedPlaybackSource("autplay-unavailable", "NO_AUDIO_SOURCE"))
        assertFalse(isResolvedPlaybackSource("autplay-unresolved", null))
        assertFalse(isResolvedPlaybackSource(null, null))
        assertTrue(isResolvedPlaybackSource("content", null))
    }

    @Test fun localSourceIsPreferredWithoutRequestingVault() {
        var requested = false
        val selected = PlaybackSourceResolver.select(LocalSourceProbe.Readable(ReadableLocalSource("content://music/1"))) {
            requested = true; FreshVaultStream("https://fresh.example/audio")
        }
        assertTrue(selected is PlaybackSourceSelection.Local)
        assertFalse(requested)
    }

    @Test fun revokedLocalUriUsesFreshVaultOrStableReason() {
        val fallback = PlaybackSourceResolver.select(LocalSourceProbe.PermissionRevoked) { FreshVaultStream("https://fresh.example/audio") }
        assertTrue(fallback is PlaybackSourceSelection.Vault)
        val unavailable = PlaybackSourceResolver.select(LocalSourceProbe.PermissionRevoked, null)
        assertEquals(PlaybackUnavailableReason.LOCAL_PERMISSION_REVOKED_AND_VAULT_UNAVAILABLE, (unavailable as PlaybackSourceSelection.Unavailable).reason)
    }

    @Test fun mediaItemIdentityUsesQueueEntrySoDuplicateTracksRemainDistinct() {
        val track = id(1)
        val first = PlaybackQueueEntry(id(2), track, 0)
        val second = PlaybackQueueEntry(id(3), track, 1)
        val restore = QueueMediaItemMapper.restore(PlaybackQueueSnapshot(id(4), listOf(first, second), second.queueEntryId, 512))
        assertEquals(listOf(first.queueEntryId.value, second.queueEntryId.value), restore.items.map { it.mediaId })
        assertEquals(1, restore.currentIndex)
        assertEquals(512, restore.currentPositionMs)
    }

    @Test fun restartKeepsEventAndFullAttributionThenFinalizesExactlyOnce() {
        val attribution = PlaybackRecommendationAttribution(uuid(8), uuid(9), 2, "home", "for_you", id(10), uuid(11))
        val entry = PlaybackQueueEntry(id(2), id(3), 0, attribution)
        val checkpoint = LogicalListeningSession.start(entry, id(4), 100, 20)
        val restored = LogicalListeningSession.checkpoint(checkpoint, 420, 400)
        val (finalized, event) = LogicalListeningSession.finalizeOnce(restored, 500, 1_000, 80)
        assertEquals(id(4), event!!.listeningEventId)
        assertEquals(attribution, event.attribution)
        assertEquals(480, event.playedMs)
        val (_, duplicate) = LogicalListeningSession.finalizeOnce(finalized, 600, 1_000)
        assertNull(duplicate)
    }

    @Test fun seeksDoNotOvercountOrCreateListeningEvents() {
        val entry = PlaybackQueueEntry(id(2), id(3), 0)
        val started = LogicalListeningSession.start(entry, id(4), 100, 20)
        val forwardSeek = LogicalListeningSession.seek(started, 800)
        val afterForwardPlayback = LogicalListeningSession.checkpoint(forwardSeek, 850, 50)
        val backwardSeek = LogicalListeningSession.seek(afterForwardPlayback, 30)
        val (finalized, event) = LogicalListeningSession.finalizeOnce(backwardSeek, 70, 1_000, 40)
        assertEquals(90, event!!.playedMs)
        assertEquals(id(4), event.listeningEventId)
        assertTrue(finalized.finalized)
        assertEquals(70, finalized.lastObservedPositionMs)
    }

    private fun id(value: Int): LocalId = LocalId(uuid(value))
    private fun uuid(value: Int): String = "00000000-0000-0000-0000-%012d".format(value)
}
