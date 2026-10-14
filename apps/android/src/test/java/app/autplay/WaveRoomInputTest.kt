package app.autplay

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class WaveRoomInputTest {
    @Test fun invitesRejectDuplicatesShortUuidsAndMoreThanSevenAccounts() {
        val id = "12345678-1234-4234-8234-123456789abc"
        assertEquals(emptyList<String>(), parseWaveInviteIds(" \n"))
        assertEquals(listOf(id), parseWaveInviteIds(id.uppercase()))
        assertNull(parseWaveInviteIds("$id, ${id.uppercase()}"))
        assertNull(parseWaveInviteIds("1-1-1-1-1"))
        assertNull(parseWaveInviteIds((0..7).joinToString(",") { java.util.UUID(0, it.toLong()).toString() }))
    }

    @Test fun pastedCodesNormalizeWhitespaceButRejectAmbiguousCharacters() {
        assertEquals("01ABCDEFGH", normalizeWaveRoomCode(" 01abcd efgh\n"))
        assertTrue(isValidWaveRoomCode("01ABCDEFGH"))
        assertFalse(isValidWaveRoomCode("01ABCDEILO"))
        assertFalse(isValidWaveRoomCode("01ABCDE"))
    }
}
