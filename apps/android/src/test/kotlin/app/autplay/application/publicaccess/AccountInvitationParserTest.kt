package app.autplay.application.publicaccess

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class AccountInvitationParserTest {
    @Test fun parsesExactDocumentOnly() {
        AccountInvitationParser.parseDocument(AccountInvitationParser.MIME_TYPE, document().toByteArray()).use {
            assertEquals("USER", "USER")
            assertEquals("https://api.example", it.apiOrigin)
            assertEquals(32, it.secret.size)
            AccountInvitationParser.parseQr(it.toDocumentJson()).use { roundTrip ->
                assertEquals(it.invitationId, roundTrip.invitationId)
                assertEquals(it.issuedAt, roundTrip.issuedAt)
                assertEquals(it.expiresAt, roundTrip.expiresAt)
            }
        }
    }

    @Test fun rejectsUrlAndUnknownMember() {
        assertThrows(IllegalArgumentException::class.java) { AccountInvitationParser.parseQr("https://example.test/invite") }
        assertThrows(IllegalArgumentException::class.java) { AccountInvitationParser.parseQr(document().dropLast(1) + ",\"extra\":1}") }
    }

    @Test fun rejectsHttpAndWrongMime() {
        assertThrows(IllegalArgumentException::class.java) { AccountInvitationParser.parseDocument("application/json", document().toByteArray()) }
        assertThrows(IllegalArgumentException::class.java) { AccountInvitationParser.parseQr(document().replace("https://api.example", "http://api.example")) }
    }

    private fun document() = """{"contract_version":"v1","schema_version":1,"invitation_id":"10000000-0000-4000-8000-000000000001","server_instance_id":"10000000-0000-4000-8000-000000000002","identity_epoch":1,"identity_thumbprint_sha256":"${"a".repeat(64)}","api_origin":"https://api.example","stream_origin":"https://stream.example","account_display_name":"Friend","account_role":"USER","issued_at":"2026-01-01T00:00:00Z","expires_at":"2999-01-01T00:00:00Z","invitation_secret":"MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY","secret_handling":"DISPLAY_ONCE_QR_OR_AUTPLAYINVITE_NO_URL_NO_CLIPBOARD_NO_LOG"}"""
}
