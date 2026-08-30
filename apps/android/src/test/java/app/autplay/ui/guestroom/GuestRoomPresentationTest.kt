package app.autplay.ui.guestroom

import java.util.Locale
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class GuestRoomPresentationTest {
    @Test
    fun expiryFormattingUsesTheSelectedAppLocale() {
        val expiresAtMs = 1_777_020_000_000L

        val english = formatGuestExpiry(expiresAtMs, Locale.forLanguageTag("en-US"))
        val russian = formatGuestExpiry(expiresAtMs, Locale.forLanguageTag("ru-RU"))

        assertNotEquals(english, russian)
        assertTrue(english.contains("2026"))
        assertTrue(russian.contains("2026"))
    }
}
