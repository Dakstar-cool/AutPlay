package app.autplay.application.recommendation

import org.junit.Assert.assertEquals
import org.junit.Test

class HomeWheelRankingTest {
    @Test fun topThreeOccupyCenterAndItsTwoNeighborsWithoutLosingOtherChoices() {
        val morning = HomeWheelRanking.ring(8)
        assertEquals(listOf("energy", "running", "pop"), listOf(morning.first(), morning[1], morning.last()))
        val night = HomeWheelRanking.ring(23)
        assertEquals(listOf("sleep", "calm", "jazz"), listOf(night.first(), night[1], night.last()))
        val personal = HomeWheelRanking.ring(8, mapOf("rock" to 12.0, "jazz" to 5.0))
        assertEquals(listOf("rock", "jazz", "energy"), listOf(personal.first(), personal[1], personal.last()))
        assertEquals(morning.toSet(), personal.toSet())
        assertEquals(morning.size, personal.toSet().size)
    }
}
