package app.autplay.application.portableaccount

import app.autplay.data.local.PortableLocalListeningRow
import app.autplay.data.security.CredentialStore
import app.autplay.data.security.SessionCredentialEnvelope
import app.autplay.data.security.SessionCredentialEnvelopeCodec
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.*
import org.junit.Test

class PortableAccountDataTransportTest {
    @Test fun boundedLargePagesEncodedCursorAndOwnerValidationShareTheAuthenticatedTransport() = runBlocking {
        val account = id(1); val profile = ServerProfileId(id(2))
        val records = (10..41).map { number -> PortableAccountDataCodec.listening(
            PortableLocalListeningRow(id(number), null, id(3), "T".repeat(1900), "A".repeat(1900), null, 1, 30000, 60000, false, "ORGANIC", "GENERAL", 2)) }
        val cursor = buildJsonObject { put("time", "2026-10-08T12:00:00.123456+00:00"); put("id", id(100)) }
        val page = buildJsonObject { put("account_id", account); put("records", JsonArray(records.map { it.wire() })); put("cursor", cursor); put("next", cursor) }
        assertTrue(page.toString().toByteArray().size > 16_384)
        val credentials = object : CredentialStore {
            private val encoded = SessionCredentialEnvelopeCodec.encode(SessionCredentialEnvelope("synthetic-test-access", null, 0))
            override suspend fun read(profileId: ServerProfileId): ByteArray? =
                if (profileId == profile) encoded.copyOf() else null
            override suspend fun write(profileId: ServerProfileId, material: ByteArray) { error("Unexpected rotation") }
            override suspend fun clear(profileId: ServerProfileId) { error("Unexpected clear") }
        }
        MockWebServer().use { server ->
            server.start()
            server.enqueue(MockResponse().setBody(page.toString()))
            server.enqueue(MockResponse().setBody(buildJsonObject {
                put("account_id", account); put("accepted", JsonArray(records.map { JsonPrimitive(it.recordId) }))
            }.toString()))
            server.enqueue(MockResponse().setBody(JsonObject(page + ("account_id" to JsonPrimitive(id(9)))).toString()))
            server.enqueue(MockResponse().setBody(" ".repeat(PortableAccountDataCodec.MAX_PAYLOAD_BYTES + 1)))
            val transport = OkHttpPortableAccountDataTransport(server.url("/api/v1").toString(), profile, credentials)
            val after = PortableDataCursor("2026-10-07T12:00:00.123456+00:00", id(0))
            assertEquals(32, transport.download(account, "records", after).records.size)
            val get = server.takeRequest()
            assertEquals(after.time, get.requestUrl!!.queryParameter("after_time"))
            assertEquals("records", get.requestUrl!!.queryParameter("source"))
            assertEquals("no-store", get.getHeader("Cache-Control"))
            assertEquals("Bearer synthetic-test-access", get.getHeader("Authorization"))
            assertFalse(get.path!!.contains("synthetic-test-access"))
            transport.upload(account, records)
            val post = server.takeRequest()
            assertEquals("POST", post.method)
            assertEquals("Bearer synthetic-test-access", post.getHeader("Authorization"))
            assertFalse(post.body.readUtf8().contains("synthetic-test-access"))
            assertTrue(runCatching { transport.download(account, "history", after) }.isFailure)
            assertTrue(runCatching { transport.download(account, "records", after) }.isFailure)
        }
        assertTrue(runCatching { OkHttpPortableAccountDataTransport.decodePage(account, page, PortableDataCursor(cursor["time"]!!.jsonPrimitive.content, id(101))) }.isFailure)
        val unsafe = records.first().copy(document = JsonObject(records.first().document + ("password" to JsonPrimitive("synthetic-forbidden-member"))))
        assertTrue(runCatching { PortableAccountDataCodec.decode(unsafe.wire()) }.isFailure)
    }
    private fun id(number: Int): String = "00000000-0000-0000-0000-%012d".format(number)
}
