package app.autplay.application.importing

import org.junit.Assert.assertEquals
import org.junit.Test

class ContentUriInspectionTest {
    @Test
    fun stableStatusesRemainExplicitAndNeverEncodeDeletion() {
        val revoked = ContentUriInspection("content://media/external/audio/1", ContentUriStatus.PERMISSION_REVOKED, null, null)
        val missing = ContentUriInspection("content://media/external/audio/1", ContentUriStatus.MISSING, null, null)
        assertEquals(ContentUriStatus.PERMISSION_REVOKED, revoked.status)
        assertEquals(ContentUriStatus.MISSING, missing.status)
    }
}
