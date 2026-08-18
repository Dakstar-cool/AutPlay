package app.autplay.application.importing

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalImportProjectionTest {
    @Test
    fun singleUriEnvelopeIsDeterministicAndKeepsPrivateUriOutOfRawReportFields() {
        val inspection = ContentUriInspection(
            uri = "content://private.provider/music/42",
            status = ContentUriStatus.PERMISSION_REVOKED,
            displayName = "Live version.flac",
            byteSize = 1234,
        )

        val first = singleUriImportCommand(LEGACY_PROFILE_ID, inspection, false, 100)
        val retry = singleUriImportCommand(LEGACY_PROFILE_ID, inspection, false, 200)

        assertEquals(first.inputSha256, retry.inputSha256)
        assertEquals(64, first.inputSha256.length)
        assertEquals(ImportSourceAvailability.PERMISSION_REVOKED, first.sourceAvailability)
        assertEquals(ImportSourceAvailability.PERMISSION_REVOKED, first.rows.single().sourceAvailability)
        assertFalse(first.inputDigestVerified)
        assertEquals(inspection.uri, first.rows.single().contentUri)
        assertFalse(first.rows.single().rawProvenanceJson.contains(inspection.uri))
        assertTrue(first.rows.single().rawProvenanceJson.contains("PERMISSION_REVOKED"))
    }

    @Test
    fun verifiedContentDigestDistinguishesChangedBytesAtSameUriAndMetadata() {
        val first = singleUriImportCommand(
            LEGACY_PROFILE_ID,
            ContentUriInspection("content://provider/same", ContentUriStatus.AVAILABLE, "same.flac", 10, "a".repeat(64)),
            true,
            1,
        )
        val replacement = singleUriImportCommand(
            LEGACY_PROFILE_ID,
            ContentUriInspection("content://provider/same", ContentUriStatus.AVAILABLE, "same.flac", 10, "b".repeat(64)),
            true,
            2,
        )

        assertTrue(first.inputDigestVerified)
        assertTrue(replacement.inputDigestVerified)
        assertNotEquals(first.inputSha256, replacement.inputSha256)
    }

    @Test
    fun sourceIdentityChangesWithoutCollapsingSameMetadataRows() {
        val a = singleUriImportCommand(
            LEGACY_PROFILE_ID,
            ContentUriInspection("content://provider/a", ContentUriStatus.AVAILABLE, "same.mp3", 10),
            true,
            1,
        )
        val b = singleUriImportCommand(
            LEGACY_PROFILE_ID,
            ContentUriInspection("content://provider/b", ContentUriStatus.AVAILABLE, "same.mp3", 10),
            true,
            1,
        )

        assertEquals(a.rows.single().rawTitle, b.rows.single().rawTitle)
        assertNotEquals(a.inputSha256, b.inputSha256)
    }

    @Test
    fun verifiedContentDigestRemainsEvidenceForDistinctSourceUris() {
        val digest = "d".repeat(64)
        val a = singleUriImportCommand(
            LEGACY_PROFILE_ID,
            ContentUriInspection("content://provider/a", ContentUriStatus.AVAILABLE, "same.mp3", 10, digest),
            true,
            1,
        )
        val b = singleUriImportCommand(
            LEGACY_PROFILE_ID,
            ContentUriInspection("content://provider/b", ContentUriStatus.AVAILABLE, "same.mp3", 10, digest),
            true,
            1,
        )

        assertEquals(digest, a.inputSha256)
        assertEquals(digest, b.inputSha256)
        assertNotEquals(a.sourceUri, b.sourceUri)
    }
}
