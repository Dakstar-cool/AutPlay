package app.autplay.playback

import org.junit.Assert.*
import org.junit.Test

class PlaybackRecoveryPolicyTest {
    private val owner = PlaybackRecoveryOwner(1, "entry", "profile")

    @Test fun errorsAreBoundedEvenWhenEachAttemptBrieflyBecomesReady() {
        val policy = PlaybackRecoveryPolicy()
        var now = 0L
        repeat(PlaybackRecoveryPolicy.MAX_ATTEMPTS) {
            policy.error(owner, now, true)
            val ticket = policy.tick(owner, now + 8_000, 12_345, true, false, false, true)
            assertEquals(12_345L, ticket?.positionMs)
            assertEquals(owner, ticket?.owner)
            policy.tick(owner, now + 8_100, 12_400, true, false, true, true)
            now += 10_000
        }
        policy.error(owner, now, true)
        assertNull(policy.tick(owner, now + 10_000, 12_400, true, false, false, true))
    }

    @Test fun pauseSeekProfileAndQueueChangesRevokeTickets() {
        val policy = PlaybackRecoveryPolicy()
        fun ticket(): PlaybackRecoveryTicket {
            policy.error(owner, 0, true)
            return requireNotNull(policy.tick(owner, 1_000, 7_000, true, false, false, true))
        }
        val paused = ticket()
        policy.tick(owner, 1_001, 7_000, false, false, false, true)
        assertFalse(policy.owns(paused))
        val seek = ticket()
        policy.cancel()
        assertFalse(policy.owns(seek))
        for (replacement in listOf(owner.copy(profileId = "new"), owner.copy(generation = 2), owner.copy(entryId = "next"))) {
            val stale = ticket()
            assertNull(policy.tick(replacement, 1_001, 0, true, false, false, true))
            assertFalse(policy.owns(stale))
        }
    }

    @Test fun offlineRecoveryWaitsForConnectivityButExpires() {
        val policy = PlaybackRecoveryPolicy()
        policy.error(owner, 0, true)
        assertNull(policy.tick(owner, 5_000, 321, true, false, false, false))
        assertEquals(321L, policy.tick(owner, 6_000, 321, true, false, false, true)?.positionMs)
        policy.error(owner, 7_000, true)
        assertNull(policy.tick(owner, 91_000, 321, true, false, false, true))
    }

    @Test fun starvationRestartsOnlyAfterDeadlineAndPermanentFailureNeverRetries() {
        val policy = PlaybackRecoveryPolicy()
        assertNull(policy.tick(owner, 0, 500, true, true, false, true))
        assertNull(policy.tick(owner, 19_999, 500, true, true, false, true))
        assertNull(policy.tick(owner, 20_000, 500, true, true, false, true))
        assertNotNull(policy.tick(owner, 21_000, 500, true, true, false, true))
        policy.error(owner, 22_000, false)
        assertNull(policy.tick(owner, 60_000, 500, true, true, false, true))
    }
}
