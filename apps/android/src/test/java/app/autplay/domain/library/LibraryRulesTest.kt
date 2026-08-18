package app.autplay.domain.library

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Random

class LibraryRulesTest {
    @Test
    fun duplicateTracksRemainDistinctPlaylistEntriesAndRebalanceIsOrdered() {
        val ids = listOf("entry-a", "entry-b", "entry-c") // a and b may point to the same track.
        val keys = PlaylistPositionKeys.rebalance(ids)
        assertEquals(3, keys.size)
        assertTrue(keys.getValue("entry-a") < keys.getValue("entry-b"))
        assertTrue(keys.getValue("entry-b") < keys.getValue("entry-c"))
    }

    @Test
    fun randomReordersAlwaysProduceStrictlyOrderedFixedWidthKeys() {
        val random = Random(7)
        repeat(100) {
            val order = (0 until 200).map { "entry-$it" }.shuffled(random)
            val keys = PlaylistPositionKeys.rebalance(order)
            assertEquals(order.size, keys.size)
            assertTrue(order.zipWithNext().all { (left, right) -> keys.getValue(left) < keys.getValue(right) })
        }
    }

    @Test
    fun insertionBetweenExistingKeysIsBounded() {
        val left = PlaylistPositionKeys.initial(0)
        val right = PlaylistPositionKeys.initial(1)
        val inserted = PlaylistPositionKeys.between(left, right)
        assertNotNull(inserted)
        assertTrue(left < inserted!! && inserted < right)
    }

    @Test
    fun excludedPreferenceAndHistoryNeverTrainTaste() {
        assertFalse(PreferenceDecision(TrackPreference.LIKED, true, null).contributesToTaste)
        assertFalse(ListeningDecision(1_000, 5_000, true, "ORGANIC").contributesToTaste)
        assertTrue(ListeningDecision(1_000, 5_000, false, "ORGANIC").contributesToTaste)
    }

    @Test
    fun unknownAttributionIsRetainedButDoesNotImplyKnownSemantics() {
        val attribution = RecommendationAttribution(
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            1,
            "p07",
            "library",
            mapOf("new_key" to "new_value"),
        )
        assertTrue(attribution.isAttributed)
        assertEquals("new_value", attribution.unknownFields.getValue("new_key"))
    }
}
