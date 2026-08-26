package app.autplay.application.social

import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OkHttpSocialPortTest {
    @Test
    fun snapshotAndPresenceSettingsUseExactS1cWireFields() = runBlocking {
        val server = MockWebServer()
        server.enqueue(MockResponse().setBody(snapshotJson()))
        server.enqueue(MockResponse().setBody(settingsJson()))
        server.start()
        try {
            val port = OkHttpSocialPort(
                server.url("/").toString().trimEnd('/'),
                Store(SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("access", "refresh", 0))),
            )
            val snapshot = port.snapshot(PROFILE)
            assertTrue(snapshot is SocialResult.Success)
            val value = (snapshot as SocialResult.Success).value
            assertEquals(PresenceSettings(true, true, true), value.presence)
            assertEquals("2", value.receivedInvitations.single().roomEpoch)
            assertEquals(RoomInvitationStatus.FULL, value.receivedInvitations.single().status)
            assertEquals("/social/snapshot", server.takeRequest().path)

            val updated = port.updatePresence(
                PROFILE,
                OPERATION_ID,
                PresenceSettings(false, true, false),
            )
            assertTrue(updated is SocialResult.Success)
            val request = server.takeRequest()
            val body = request.body.readUtf8()
            assertEquals("PUT", request.method)
            assertEquals("/social/presence/settings", request.path)
            assertTrue(body.contains("\"operation_id\":\"$OPERATION_ID\""))
            assertTrue(body.contains("\"friend_presence_visibility_enabled\":false"))
            assertTrue(body.contains("\"room_activity_sharing_enabled\":true"))
            assertTrue(body.contains("\"invite_availability_enabled\":false"))
            assertFalse(body.contains("friends_can_see_presence"))
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun profileStatisticsUsesExactSettingsRevisionAndBoundedFriendWireShape() = runBlocking {
        val server = MockWebServer()
        server.enqueue(MockResponse().setBody("""{"schema_version":1,"friends_can_view_statistics":false,"revision":3}"""))
        server.enqueue(MockResponse().setBody("""{"schema_version":1,"operation_id":"$OPERATION_ID","friends_can_view_statistics":true,"revision":4}"""))
        server.enqueue(MockResponse().setBody(friendStatisticsJson()))
        server.start()
        try {
            val port = port(server)
            assertEquals(
                SocialResult.Success(ProfileStatisticsSettings(false, 3)),
                port.profileStatisticsSettings(PROFILE),
            )
            assertEquals("/social/profile-statistics/settings", server.takeRequest().path)

            assertEquals(
                SocialResult.Success(ProfileStatisticsSettingsReceipt(OPERATION_ID, ProfileStatisticsSettings(true, 4))),
                port.updateProfileStatisticsSettings(PROFILE, OPERATION_ID, 3, true),
            )
            val update = server.takeRequest()
            assertEquals("PUT", update.method)
            assertEquals("/social/profile-statistics/settings", update.path)
            val updateBody = update.body.readUtf8()
            assertTrue(updateBody.contains("\"operation_id\":\"$OPERATION_ID\""))
            assertTrue(updateBody.contains("\"expected_revision\":3"))
            assertTrue(updateBody.contains("\"friends_can_view_statistics\":true"))

            val friend = port.friendProfileStatistics(PROFILE, FRIEND_ID)
            assertTrue(friend is SocialResult.Success)
            val statistics = (friend as SocialResult.Success).value
            assertEquals("2026-08-24", statistics.throughUtcDate)
            assertEquals(3, statistics.windows.size)
            assertTrue(statistics.windows.last().kind is SharedStatisticsWindowKind.Unknown)
            assertEquals("FUTURE_WINDOW", statistics.windows.last().kind.raw)
            assertEquals("/social/friends/$FRIEND_ID/profile-statistics", server.takeRequest().path)
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun unknownProfileStatisticsSchemaFailsClosed() = runBlocking {
        val server = MockWebServer()
        server.enqueue(MockResponse().setBody("""{"schema_version":2,"friends_can_view_statistics":true,"revision":9}"""))
        server.start()
        try {
            assertTrue(port(server).profileStatisticsSettings(PROFILE) is SocialResult.Failure)
        } finally {
            server.shutdown()
        }
    }

    @Test
    fun profileStatisticsUpdateRejectsMissingOrMismatchedOperationReceipt() = runBlocking {
        val server = MockWebServer()
        server.enqueue(MockResponse().setBody("""{"schema_version":1,"friends_can_view_statistics":true,"revision":4}"""))
        server.enqueue(MockResponse().setBody("""{"schema_version":1,"operation_id":"66666666-6666-4666-8666-666666666666","friends_can_view_statistics":true,"revision":4}"""))
        server.start()
        try {
            val port = port(server)
            assertTrue(port.updateProfileStatisticsSettings(PROFILE, OPERATION_ID, 3, true) is SocialResult.Failure)
            assertTrue(port.updateProfileStatisticsSettings(PROFILE, OPERATION_ID, 3, true) is SocialResult.Failure)
        } finally {
            server.shutdown()
        }
    }

    private fun snapshotJson() = """{"friends":[],"incoming_requests":[],"outgoing_requests":[],"blocked":[],"sent_room_invitations":[],"received_room_invitations":[{"invitation_id":"$INVITATION_ID","state":"FULL","room_id":"$ROOM_ID","room_epoch":2,"expires_at":"2026-08-25T00:10:00Z"}],"presence_settings":${settingsJson()}}"""
    private fun settingsJson() = """{"friend_presence_visibility_enabled":true,"room_activity_sharing_enabled":true,"invite_availability_enabled":true}"""
    private fun friendStatisticsJson() = """{"schema_version":1,"through_utc_date":"2026-08-24","windows":[{"window":"LAST_7_COMPLETE_DAYS","play_session_count":2,"listened_ms":3000,"unique_track_count":1},{"window":"LAST_30_COMPLETE_DAYS","play_session_count":5,"listened_ms":9000,"unique_track_count":3},{"window":"FUTURE_WINDOW","play_session_count":6,"listened_ms":10000,"unique_track_count":4}]}"""

    private fun port(server: MockWebServer) = OkHttpSocialPort(
        server.url("/").toString().trimEnd('/'),
        Store(SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("access", "refresh", 0))),
    )

    private class Store(initial: ByteArray) : CredentialStore {
        private var value = initial.copyOf()
        override suspend fun read(profileId: ServerProfileId): ByteArray = value.copyOf()
        override suspend fun write(profileId: ServerProfileId, material: ByteArray) { value = material.copyOf() }
        override suspend fun clear(profileId: ServerProfileId) { value.fill(0) }
    }

    private companion object {
        val PROFILE = ServerProfileId("11111111-1111-4111-8111-111111111111")
        const val OPERATION_ID = "22222222-2222-4222-8222-222222222222"
        const val INVITATION_ID = "33333333-3333-4333-8333-333333333333"
        const val ROOM_ID = "44444444-4444-4444-8444-444444444444"
        const val FRIEND_ID = "55555555-5555-4555-8555-555555555555"
    }
}
