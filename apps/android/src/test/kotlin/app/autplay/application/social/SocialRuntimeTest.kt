package app.autplay.application.social

import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SocialRuntimeTest {
    @Test fun `failed refresh retains the prior server snapshot`() = runBlocking {
        val port = FakePort(SocialResult.Success(SocialSnapshot(friends = listOf(friend))))
        val runtime = SocialRuntime(ServerProfileId("22222222-2222-4222-8222-222222222222"), port, CoroutineScope(Dispatchers.Unconfined))
        runtime.load()
        port.snapshotResult = SocialResult.Failure("server_unavailable")
        runtime.load()
        assertEquals(listOf(friend), runtime.state.value.snapshot.friends)
        assertEquals("server_unavailable", runtime.state.value.errorCode)
        assertFalse(runtime.state.value.loading)
    }

    @Test fun `heartbeat is bounded to one call every thirty seconds`() {
        val port = FakePort(SocialResult.Success(SocialSnapshot()))
        var clock = 100_000L
        val runtime = SocialRuntime(ServerProfileId("33333333-3333-4333-8333-333333333333"), port, CoroutineScope(Dispatchers.Unconfined), nowMs = { clock })
        runtime.heartbeatWhileActive(); runtime.heartbeatWhileActive()
        clock += 30_000L; runtime.heartbeatWhileActive()
        assertEquals(2, port.heartbeats)
    }

    @Test fun `visibility waits for acknowledgement and failed off remains confirmed on`() {
        val port = FakePort(SocialResult.Success(SocialSnapshot())).apply {
            settingsResult = SocialResult.Success(ProfileStatisticsSettings(enabled = true, revision = 4))
        }
        val runtime = SocialRuntime(PROFILE, port, CoroutineScope(Dispatchers.Unconfined))
        runtime.loadProfileStatisticsSettings()
        assertEquals(ProfileStatisticsSettingsState.Confirmed(true, 4), runtime.state.value.statisticsSettings)

        port.updateSettingsResult = SocialResult.Failure("server_unavailable")
        runtime.setProfileStatisticsVisibility(false)

        assertEquals(ProfileStatisticsSettingsState.Confirmed(true, 4), runtime.state.value.statisticsSettings)
        assertEquals("server_unavailable", runtime.state.value.statisticsSettingsErrorCode)
        assertEquals(4L, port.lastExpectedRevision)
        assertFalse(port.lastRequestedEnabled!!)
    }

    @Test fun `enable uses confirmed revision and only changes after server receipt`() {
        val port = FakePort(SocialResult.Success(SocialSnapshot())).apply {
            settingsResult = SocialResult.Success(ProfileStatisticsSettings(enabled = false, revision = 7))
            updateSettingsTransform = { operationId ->
                SocialResult.Success(ProfileStatisticsSettingsReceipt(operationId, ProfileStatisticsSettings(enabled = true, revision = 8)))
            }
        }
        val runtime = SocialRuntime(PROFILE, port, CoroutineScope(Dispatchers.Unconfined))
        runtime.loadProfileStatisticsSettings()
        runtime.setProfileStatisticsVisibility(true)

        assertEquals(ProfileStatisticsSettingsState.Confirmed(true, 8), runtime.state.value.statisticsSettings)
        assertEquals(7L, port.lastExpectedRevision)
        assertTrue(port.lastRequestedEnabled!!)
        assertNull(runtime.state.value.statisticsSettingsErrorCode)
    }

    @Test fun `mismatched update receipt never changes confirmed visibility`() {
        val port = FakePort(SocialResult.Success(SocialSnapshot())).apply {
            settingsResult = SocialResult.Success(ProfileStatisticsSettings(enabled = false, revision = 7))
            updateSettingsResult = SocialResult.Success(
                ProfileStatisticsSettingsReceipt("33333333-3333-4333-8333-333333333333", ProfileStatisticsSettings(enabled = true, revision = 8)),
            )
        }
        val runtime = SocialRuntime(PROFILE, port, CoroutineScope(Dispatchers.Unconfined))
        runtime.loadProfileStatisticsSettings()
        runtime.setProfileStatisticsVisibility(true)
        assertEquals(ProfileStatisticsSettingsState.Confirmed(false, 7), runtime.state.value.statisticsSettings)
        assertEquals("server_unavailable", runtime.state.value.statisticsSettingsErrorCode)
    }

    @Test fun `friend aggregate is volatile and denial clears prior values`() {
        val accountId = friend.accountId
        val visible = SharedProfileStatistics(
            throughUtcDate = "2026-08-24",
            windows = listOf(
                SharedProfileStatisticsWindow(
                    SharedStatisticsWindowKind.Last7CompleteDays,
                    playSessionCount = 2,
                    listenedMs = 3_000,
                    uniqueTrackCount = 1,
                ),
            ),
        )
        val port = FakePort(SocialResult.Success(SocialSnapshot(friends = listOf(friend)))).apply {
            friendStatisticsResult = SocialResult.Success(visible)
        }
        val runtime = SocialRuntime(PROFILE, port, CoroutineScope(Dispatchers.Unconfined))
        runtime.load()
        runtime.loadFriendProfileStatistics(accountId)
        assertEquals(FriendProfileStatisticsState.Visible(accountId, visible), runtime.state.value.friendStatistics)

        port.friendStatisticsResult = SocialResult.Failure("profile_statistics_unavailable")
        runtime.loadFriendProfileStatistics(accountId)
        assertEquals(FriendProfileStatisticsState.Unavailable(accountId), runtime.state.value.friendStatistics)
        runtime.removeFriend(accountId)
        assertEquals(FriendProfileStatisticsState.Idle, runtime.state.value.friendStatistics)
    }

    @Test fun `late friend response cannot restore after clear or removal`() = runBlocking {
        val deferred = CompletableDeferred<SocialResult<SharedProfileStatistics>>()
        val port = FakePort(SocialResult.Success(SocialSnapshot(friends = listOf(friend)))).apply {
            friendStatisticsCall = { deferred.await() }
        }
        val runtime = SocialRuntime(PROFILE, port, CoroutineScope(Dispatchers.Unconfined))
        runtime.load()
        runtime.loadFriendProfileStatistics(friend.accountId)
        runtime.clearFriendProfileStatistics()
        deferred.complete(SocialResult.Success(sharedStatistics()))
        assertEquals(FriendProfileStatisticsState.Idle, runtime.state.value.friendStatistics)

        val second = CompletableDeferred<SocialResult<SharedProfileStatistics>>()
        port.friendStatisticsCall = { second.await() }
        runtime.loadFriendProfileStatistics(friend.accountId)
        runtime.removeFriend(friend.accountId)
        second.complete(SocialResult.Success(sharedStatistics()))
        assertEquals(FriendProfileStatisticsState.Idle, runtime.state.value.friendStatistics)
    }

    @Test fun `superseding friend request only accepts newest response for confirmed friend`() = runBlocking {
        val first = CompletableDeferred<SocialResult<SharedProfileStatistics>>()
        val second = CompletableDeferred<SocialResult<SharedProfileStatistics>>()
        var calls = 0
        val port = FakePort(SocialResult.Success(SocialSnapshot(friends = listOf(friend)))).apply {
            friendStatisticsCall = { if (calls++ == 0) first.await() else second.await() }
        }
        val runtime = SocialRuntime(PROFILE, port, CoroutineScope(Dispatchers.Unconfined))
        runtime.load()
        runtime.loadFriendProfileStatistics(friend.accountId)
        runtime.loadFriendProfileStatistics(friend.accountId)
        first.complete(SocialResult.Success(sharedStatistics(1)))
        second.complete(SocialResult.Success(sharedStatistics(2)))
        assertEquals(FriendProfileStatisticsState.Visible(friend.accountId, sharedStatistics(2)), runtime.state.value.friendStatistics)
    }

    private fun sharedStatistics(count: Long = 2) = SharedProfileStatistics(
        "2026-08-24", listOf(SharedProfileStatisticsWindow(SharedStatisticsWindowKind.Last7CompleteDays, count, 3_000, 1)),
    )

    private class FakePort(var snapshotResult: SocialResult<SocialSnapshot>) : SocialPort {
        var heartbeats = 0
        var settingsResult: SocialResult<ProfileStatisticsSettings> = SocialResult.Failure("server_unavailable")
        var updateSettingsResult: SocialResult<ProfileStatisticsSettingsReceipt> = SocialResult.Failure("server_unavailable")
        var updateSettingsTransform: ((String) -> SocialResult<ProfileStatisticsSettingsReceipt>)? = null
        var friendStatisticsResult: SocialResult<SharedProfileStatistics> = SocialResult.Failure("profile_statistics_unavailable")
        var friendStatisticsCall: suspend () -> SocialResult<SharedProfileStatistics> = { friendStatisticsResult }
        var lastExpectedRevision: Long? = null
        var lastRequestedEnabled: Boolean? = null
        override suspend fun contactCard(profileId: ServerProfileId) = SocialResult.Failure("server_unavailable")
        override suspend fun snapshot(profileId: ServerProfileId) = snapshotResult
        override suspend fun friendshipCommand(profileId: ServerProfileId, command: FriendshipCommand): SocialResult<Unit> = SocialResult.Success(Unit)
        override suspend fun updatePresence(profileId: ServerProfileId, operationId: String, settings: PresenceSettings): SocialResult<PresenceSettings> = SocialResult.Success(settings)
        override suspend fun profileStatisticsSettings(profileId: ServerProfileId) = settingsResult
        override suspend fun updateProfileStatisticsSettings(profileId: ServerProfileId, operationId: String, expectedRevision: Long, enabled: Boolean): SocialResult<ProfileStatisticsSettingsReceipt> {
            lastExpectedRevision = expectedRevision
            lastRequestedEnabled = enabled
            return updateSettingsTransform?.invoke(operationId) ?: updateSettingsResult
        }
        override suspend fun friendProfileStatistics(profileId: ServerProfileId, friendAccountId: String) = friendStatisticsCall()
        override suspend fun heartbeat(profileId: ServerProfileId, operationId: String): SocialResult<Unit> { heartbeats++; return SocialResult.Success(Unit) }
        override suspend fun createRoomInvitation(profileId: ServerProfileId, roomId: String, targetAccountId: String, operationId: String): SocialResult<RoomInvitationSummary> = SocialResult.Failure("room_invitation_unavailable")
        override suspend fun cancelRoomInvitation(profileId: ServerProfileId, invitationId: String, operationId: String): SocialResult<RoomInvitationSummary> = SocialResult.Failure("room_invitation_unavailable")
        override suspend fun acceptRoomInvitation(profileId: ServerProfileId, invitationId: String, operationId: String) = SocialResult.Failure("room_invitation_unavailable")
    }
    private companion object {
        val friend = FriendSummary("11111111-1111-4111-8111-111111111111", "Friend", FriendshipStatus.FRIEND)
        val PROFILE = ServerProfileId("22222222-2222-4222-8222-222222222222")
    }
}
