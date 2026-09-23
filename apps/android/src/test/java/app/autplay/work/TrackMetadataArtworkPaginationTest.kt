package app.autplay.work

import app.autplay.application.library.ArtworkBatchResult
import app.autplay.application.library.ArtworkShard
import org.junit.Assert.assertEquals
import org.junit.Test

class TrackMetadataArtworkPaginationTest {
    @Test fun aLargeLibraryCanContinuePastTheRetryBudget() {
        var remaining = 4_950
        var pages = 0
        while (remaining > 0) {
            remaining = (remaining - 40).coerceAtLeast(0)
            val step = artworkNextStep(ArtworkBatchResult(remaining, 0))
            assertEquals(if (remaining == 0) ArtworkNextStep.COMPLETE else ArtworkNextStep.CONTINUE, step)
            pages++
        }
        assertEquals(124, pages)
    }

    @Test fun unavailablePagesAdvanceButStopAtTheEndOfOnePass() {
        assertEquals(ArtworkNextStep.CONTINUE, artworkNextStep(ArtworkBatchResult(100, 40)))
        assertEquals(ArtworkNextStep.CONTINUE, artworkNextStep(ArtworkBatchResult(100, 80)))
        assertEquals(ArtworkNextStep.UNAVAILABLE, artworkNextStep(ArtworkBatchResult(100, 100)))
        assertEquals(ArtworkNextStep.CONTINUE, artworkNextStep(ArtworkBatchResult(60, 0)))
    }

    @Test fun shardsCoverEveryHexPrefixExactlyOnce() {
        for (prefix in "0123456789abcdef") {
            assertEquals(1, ArtworkShard.entries.count {
                prefix.toString() >= it.lowerSha && prefix.toString() < it.upperSha
            })
        }
        assertEquals(4, ArtworkShard.entries.size)
        assertEquals(null, ArtworkShard.fromIndex(-1))
        assertEquals(null, ArtworkShard.fromIndex(4))
    }
}
