package app.autplay.application.guestroom

import java.time.Instant
import java.util.Base64
import java.util.UUID
import kotlinx.coroutines.runBlocking
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class GuestRoomContractTest {
    @Test
    fun staleRedemptionCannotReplaceCancelledOrNewerDocument() {
        val now = Instant.parse("2026-08-26T12:00:00Z")
        fun document(seed: Int): GuestRoomDocument {
            val receipt = GuestInvitationReceipt(
                invitationId = UUID.randomUUID().toString(),
                roomId = UUID.randomUUID().toString(),
                expiresAt = now.plusSeconds(900),
                documentBearer = ByteArray(32) { seed.toByte() },
            )
            return GuestRoomDocumentCodec.decode(
                GuestRoomDocumentCodec.encode(
                    receipt,
                    "https://music.example.test",
                    UUID.randomUUID().toString(),
                    1,
                ),
                now,
            ).also { receipt.close() }
        }

        val first = document(1)
        val replacement = document(2)
        assertTrue(isCurrentGuestRedemption(4, 4, first, first))
        assertFalse(isCurrentGuestRedemption(5, 4, null, first))
        assertFalse(isCurrentGuestRedemption(5, 4, replacement, first))
        assertFalse(isCurrentGuestRedemption(4, 4, replacement, first))
        first.close()
        replacement.close()
    }

    @Test
    fun documentRoundTripIsStrictAndRepresentationsAreRedacted() {
        val now = Instant.parse("2026-08-26T12:00:00Z")
        val secret = ByteArray(32) { 7 }
        val receipt = GuestInvitationReceipt(
            invitationId = UUID.randomUUID().toString(),
            roomId = UUID.randomUUID().toString(),
            expiresAt = now.plusSeconds(900),
            documentBearer = secret.copyOf(),
        )
        val raw = GuestRoomDocumentCodec.encode(
            receipt,
            "https://music.example.test",
            UUID.randomUUID().toString(),
            4,
        )
        val document = GuestRoomDocumentCodec.decode(raw, now)
        val bearer = Base64.getUrlEncoder().withoutPadding().encodeToString(secret)

        assertEquals(receipt.roomId, document.roomId)
        assertFalse(document.toString().contains(bearer))
        assertFalse(receipt.toString().contains(bearer))
        assertThrows(IllegalArgumentException::class.java) {
            GuestRoomDocumentCodec.decode(raw.dropLast(1) + ",\"extra\":true}", now)
        }
        document.close()
        receipt.close()
    }

    @Test
    fun redemptionKeepsBothBearersOutOfUrlAndUsesGuestHeaderAfterExchange() = runBlocking {
        val server = MockWebServer()
        server.start()
        val now = Instant.now()
        val roomId = UUID.randomUUID().toString()
        val invitationId = UUID.randomUUID().toString()
        val serverInstanceId = UUID.randomUUID().toString()
        val documentBearer = Base64.getUrlEncoder().withoutPadding()
            .encodeToString(ByteArray(32) { 11 })
        val origin = server.url("/").toString().trimEnd('/')
        val raw = """{"type":"autplay-guest-v1","version":1,"server_origin":"$origin","server_instance_id":"$serverInstanceId","identity_epoch":2,"room_id":"$roomId","invitation_id":"$invitationId","document_bearer":"$documentBearer","expires_at":"${now.plusSeconds(900)}"}"""
        val document = GuestRoomDocumentCodec.decode(
            raw,
            now,
            allowUnsafeDevelopmentHttp = true,
        )
        val guestSessionId = UUID.randomUUID().toString()
        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"operation_id":"${UUID.randomUUID()}","guest_session_id":"$guestSessionId","invitation_id":"$invitationId","room_id":"$roomId","room_epoch":1,"role":"GUEST","allowed_actions":["ROOM_SNAPSHOT","ROOM_EVENTS","ROOM_PRESENCE","ROOM_PREFLIGHT","ROOM_TIMING","ROOM_LEAVE"],"display_name":"Guest","expires_at":"${now.plusSeconds(600)}","media_boundary":"INDEPENDENT_DEVICE_AUTHORIZATION_ONLY"}""",
            ),
        )
        var terminalCode: String? = null
        val (_, transport) = OkHttpGuestWaveTransport.redeem(
            document,
            " Guest ",
            localMediaProfileId = "trusted-local-profile",
            onAuthorityLost = { terminalCode = it },
        )
        val redemption = server.takeRequest()
        val redemptionBody = redemption.body.readUtf8()
        assertEquals("/api/v1/wave/guest/redeem", redemption.path)
        assertFalse(redemption.requestUrl.toString().contains(documentBearer))
        assertTrue(redemptionBody.contains(documentBearer))
        assertEquals("no-store", redemption.getHeader("Cache-Control"))

        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"room_id":"$roomId","room_epoch":1,"state":"OPEN","role":"GUEST","queue_version":1,"sequence":0,"queue":[],"preflight":{}}""",
            ),
        )
        document.close()
        assertFalse(document.bearer() == documentBearer)
        assertFalse(
            OkHttpGuestWaveTransport::class.java.declaredFields.any {
                it.type == GuestRoomDocument::class.java
            },
        )
        val guestSnapshot = transport.snapshot(roomId)
        assertEquals(roomId, guestSnapshot.roomId)
        assertEquals("trusted-local-profile", guestSnapshot.profileId)
        val snapshot = server.takeRequest()
        val sessionBearer = requireNotNull(snapshot.getHeader("X-AutPlay-Guest-Capability"))
        assertEquals(43, sessionBearer.length)
        assertFalse(snapshot.requestUrl.toString().contains(sessionBearer))

        server.enqueue(
            MockResponse().setResponseCode(200).setBody(
                """{"server_receive_epoch_ms":1000,"server_send_epoch_ms":1001}""",
            ),
        )
        val clock = transport.clock()
        assertEquals(1000, clock.serverReceivedMs)
        val clockRequest = server.takeRequest()
        assertEquals("/api/v1/wave/guest/clock", clockRequest.path)
        assertTrue(clockRequest.body.readUtf8().contains("\"room_id\":\"$roomId\""))

        server.enqueue(
            MockResponse().setResponseCode(410).setBody(
                """{"error":{"code":"guest_revoked"}}""",
            ),
        )
        assertThrows(GuestRoomTransportException::class.java) {
            runBlocking { transport.snapshot(roomId) }
        }
        assertEquals("guest_revoked", terminalCode)
        transport.close()
        server.shutdown()
    }
}
