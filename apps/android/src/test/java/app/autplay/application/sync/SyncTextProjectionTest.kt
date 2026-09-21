package app.autplay.application.sync

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.*
import org.junit.Test

class SyncTextProjectionTest {
    @Test fun missingNullAndLiteralNullRemainDistinct() {
        fun parse(text: String) = Json.parseToJsonElement(text).jsonObject
        assertEquals("old", parse("{}").projectNullableText("album", "old"))
        assertNull(parse("""{"album":null}""").projectNullableText("album", "old"))
        assertEquals("null", parse("""{"album":"null"}""").projectNullableText("album", "old"))
        assertEquals("FUTURE", parse("""{"status":"FUTURE"}""").projectRequiredText("status", "OLD"))
        listOf("true", "42", "[]", "{}").forEach { value ->
            assertTrue(runCatching { parse("""{"album":$value}""").projectNullableText("album", "old") }.isFailure)
        }
        assertTrue(runCatching { parse("""{"name":null}""").projectRequiredText("name", "old") }.isFailure)
    }
}
