package app.autplay.application.server

import org.junit.Assert.*
import org.junit.Test

class ServerFeatureHttpExceptionTest {
    @Test fun typedCodeRetainsLegacyMessageAndDoesNotExposeServerDescription() {
        val error = ServerFeatureHttpException.from(409,
            """{"error":{"code":"music_catalogue_context_expired","retryable":false,"message":"private detail","request_id":"private"}}""")
        assertEquals("SERVER_HTTP_409", error.message)
        assertEquals("music_catalogue_context_expired", error.errorCode)
        assertEquals(false, error.retryable)
        assertFalse(error.toString().contains("private"))
    }

    @Test fun unknownPublicCodeStaysAvailableWithoutInventingRetryability() {
        val error = ServerFeatureHttpException.from(503, """{"error":{"code":"future_catalogue_error"}}""")
        assertEquals("future_catalogue_error", error.errorCode)
        assertNull(error.retryable)
    }

    @Test fun malformedOverlongOrSensitiveCodeFallsBackToLegacyStatus() {
        listOf(null, "broken", "x".repeat(8_193), """{"error":{"code":"https://private.invalid","retryable":"true"}}""")
            .forEach { body ->
                val error = ServerFeatureHttpException.from(503, body)
                assertEquals("SERVER_HTTP_503", error.message)
                assertNull(error.errorCode); assertNull(error.retryable)
            }
    }
}
