package app.autplay.application.wave

import app.autplay.data.security.CredentialStore
import app.autplay.domain.ServerProfileId
import app.autplay.domain.wave.WaveAvailability
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class OkHttpWaveTransportTest {
    @Test fun snapshotUsesAuthorizationHeaderAndNoTokenUrl() = runBlocking {
        val server = MockWebServer(); server.enqueue(MockResponse().setBody("{\"room_id\":\"$ROOM_ID\",\"role\":\"MEMBER\",\"state\":\"OPEN\",\"sequence\":1,\"entries\":[]}")); server.start()
        val token = "secret".toByteArray()
        val credentials = object : CredentialStore { override suspend fun read(profileId: ServerProfileId) = token; override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit; override suspend fun clear(profileId: ServerProfileId) = Unit }
        try {
            OkHttpWaveTransport(server.url("/api").toString(), ServerProfileId(PROFILE), credentials).snapshot(ROOM_ID)
            val request = server.takeRequest()
            assertEquals("Bearer secret", request.getHeader("Authorization"))
            assertFalse(request.path.orEmpty().contains("secret"))
            assertEquals("/api/v1/wave/rooms/$ROOM_ID/snapshot", request.path)
        } finally { server.shutdown() }
    }

    @Test fun joinAndPreflightUseBoundedAuthenticatedJsonBodies() = runBlocking {
        val server = MockWebServer()
        server.enqueue(
            MockResponse().setBody(
                "{\"room_id\":\"$ROOM_ID\",\"room_epoch\":\"1\",\"queue_version\":2," +
                    "\"role\":\"MEMBER\",\"state\":\"OPEN\",\"sequence\":1,\"queue\":[]}",
            ),
        )
        server.enqueue(MockResponse().setResponseCode(204))
        server.start()
        val token = "secret".toByteArray()
        val credentials = object : CredentialStore {
            override suspend fun read(profileId: ServerProfileId) = token.copyOf()
            override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit
            override suspend fun clear(profileId: ServerProfileId) = Unit
        }
        try {
            val transport = OkHttpWaveTransport(
                server.url("/api").toString(),
                ServerProfileId(PROFILE),
                credentials,
            )
            transport.joinByCode("01ABCDEFGH")
            transport.preflight(
                ROOM_ID,
                listOf(
                    WavePreflightReport(
                        ENTRY_ID,
                        RECORDING_ID,
                        2,
                        WaveAvailability.LOCAL_READABLE,
                        true,
                    ),
                ),
            )
            val join = server.takeRequest()
            assertEquals("/api/v1/wave/rooms/join", join.path)
            assertFalse(join.body.readUtf8().contains("secret"))
            val preflight = server.takeRequest()
            assertEquals("Bearer secret", preflight.getHeader("Authorization"))
            assertEquals("/api/v1/wave/rooms/$ROOM_ID/availability", preflight.path)
            assertTrue(preflight.body.readUtf8().contains("\"availability\":\"LOCAL\""))
        } finally { server.shutdown() }
    }

    @Test fun createAndTransferUseInviteAndDeviceBoundLifecycleBodies() = runBlocking {
        val server = MockWebServer()
        server.enqueue(
            MockResponse().setBody(
                "{\"room_id\":\"$ROOM_ID\",\"room_code\":\"01ABCDEFGH\"," +
                    "\"role\":\"HOST\",\"state\":\"OPEN\",\"sequence\":0,\"queue\":[]}",
            ),
        )
        server.enqueue(MockResponse().setResponseCode(204))
        server.start()
        val credentials = object : CredentialStore {
            override suspend fun read(profileId: ServerProfileId) = "secret".toByteArray()
            override suspend fun write(profileId: ServerProfileId, material: ByteArray) = Unit
            override suspend fun clear(profileId: ServerProfileId) = Unit
        }
        try {
            val transport = OkHttpWaveTransport(
                server.url("/api").toString(),
                ServerProfileId(PROFILE),
                credentials,
            )
            assertEquals("01ABCDEFGH", transport.create(listOf(INVITED_USER_ID)).roomCode)
            transport.transferHost(ROOM_ID, TARGET_DEVICE_ID)
            val create = server.takeRequest()
            assertEquals("/api/v1/wave/rooms", create.path)
            assertTrue(create.body.readUtf8().contains(INVITED_USER_ID))
            val transfer = server.takeRequest()
            assertEquals("/api/v1/wave/rooms/$ROOM_ID/host-transfer", transfer.path)
            assertTrue(transfer.body.readUtf8().contains(TARGET_DEVICE_ID))
        } finally { server.shutdown() }
    }

    private companion object {
        const val PROFILE = "11111111-1111-4111-8111-111111111111"
        const val ROOM_ID = "22222222-2222-4222-8222-222222222222"
        const val ENTRY_ID = "33333333-3333-4333-8333-333333333333"
        const val RECORDING_ID = "44444444-4444-4444-8444-444444444444"
        const val INVITED_USER_ID = "55555555-5555-4555-8555-555555555555"
        const val TARGET_DEVICE_ID = "66666666-6666-4666-8666-666666666666"
    }
}
