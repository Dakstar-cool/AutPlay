package app.autplay.ui.playlist

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ManualPlaylistEditorTest {
    @Test
    fun `metadata trims name and collapses blank description`() {
        assertEquals(
            ManualPlaylistMetadata("Road trip", null),
            normalizeManualPlaylistMetadata("  Road trip  ", "   "),
        )
    }

    @Test
    fun `metadata rejects name outside one to 120 and long description`() {
        assertNull(normalizeManualPlaylistMetadata("   ", null))
        assertNull(normalizeManualPlaylistMetadata("x".repeat(121), null))
        assertNull(normalizeManualPlaylistMetadata("Valid", "x".repeat(501)))
    }
}
