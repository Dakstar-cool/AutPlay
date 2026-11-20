package app.autplay.application.library

import androidx.room3.withWriteTransaction
import androidx.test.platform.app.InstrumentationRegistry
import app.autplay.data.local.AutPlayDatabase
import app.autplay.data.local.entity.UserTrackRefEntity
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class TrackMetadataProjectionTest {
    @Test fun currentTrackReadsThroughMetadataAndArtworkCacheOnce() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val name = "playback-metadata-cache.db"
        context.deleteDatabase(name)
        val db = AutPlayDatabase.open(context, name)
        var artworkFile: java.io.File? = null
        try {
            val profile = "test-playback-metadata"
            db.libraryDao().upsertTrackRef(UserTrackRefEntity("track", "server-track", null, null, "RESOLVED",
                "Original", "Artist", null, null, null, "CLEAN", 1, 7, 1, 1, null, profile))
            val image = android.graphics.Bitmap.createBitmap(8, 8, android.graphics.Bitmap.Config.ARGB_8888)
            val bytes = java.io.ByteArrayOutputStream().use {
                image.compress(android.graphics.Bitmap.CompressFormat.JPEG, 90, it)
                it.toByteArray()
            }
            image.recycle()
            val sha = java.security.MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
            val repository = TrackMetadataRepository(context, db)
            var textReads = 0
            var coverReads = 0
            suspend fun load(owner: String) = repository.loadForPlayback(owner, "track", { ref ->
                assertEquals("server-track", ref)
                textReads++
                buildJsonObject {
                    put("revision", 2); put("state", "READY"); put("artwork_sha256", sha)
                    put("fields", buildJsonObject { put("title", "Selected track") })
                }
            }, { ref, digest ->
                assertEquals("server-track", ref)
                assertEquals(sha, digest)
                coverReads++
                bytes
            })
            load("another-profile")
            assertEquals(0, textReads)
            load(profile)
            artworkFile = java.io.File(requireNotNull(db.trackMetadataDao().artwork(profile, sha)).filePath)
            load(profile)
            assertEquals(1, textReads)
            assertEquals(1, coverReads)
            assertEquals("Selected track", db.trackMetadataDao().get(profile, "track")!!.decoded().text("title"))
            assertArrayEquals(bytes, artworkFile.readBytes())
        } finally {
            artworkFile?.delete()
            db.close()
            context.deleteDatabase(name)
        }
    }

    @Test fun profileRevisionAndExplicitClearSurviveReopen() = runBlocking {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val name = "metadata-projection-proof.db"
        context.deleteDatabase(name)
        var db = AutPlayDatabase.open(context, name)
        try {
            db.libraryDao().upsertTrackRef(UserTrackRefEntity("track", "server-track", null, null, "UNRESOLVED", "Original", "Artist", null, null, null, "DIRTY", 1, 7, 1, 1, null, "profile"))
            fun payload(revision: Int, fields: JsonObject, local: Boolean = false) = buildJsonObject {
                put("revision", revision); put("state", if (local) "LOCAL" else "READY"); put("fields", fields)
                put("provenance", buildJsonObject { if (local) fields.keys.forEach { key -> put(key, buildJsonObject { put("source", "EMBEDDED"); put("source_id", "local-file") }) } })
            }
            db.withWriteTransaction {
                assertTrue(db.projectMetadata("profile", "track", payload(0, buildJsonObject { put("album", "File album") }, true)))
                assertFalse(db.projectMetadata("other", "track", payload(3, buildJsonObject { put("album", "Foreign") })))
                assertTrue(db.projectMetadata("profile", "track", payload(2, buildJsonObject { put("title", "New title") })))
            }
            assertEquals("File album", db.trackMetadataDao().get("profile", "track")!!.decoded().text("album"))
            db.withWriteTransaction {
                db.projectMetadata("profile", "track", payload(3, buildJsonObject { put("album", JsonNull); put("release_date", "2001-04") }))
                db.projectMetadata("profile", "track", payload(1, buildJsonObject { put("album", "Stale") }))
            }
            db.close(); db = AutPlayDatabase.open(context, name)
            val restored = db.trackMetadataDao().get("profile", "track")!!.decoded()
            assertEquals(3L, restored.revision)
            assertNull(restored.text("album", "Fallback"))
            assertEquals("2001-04", restored.text("release_date"))
            assertEquals("Original", db.libraryDao().trackRef("track")!!.rawTitle)
            assertEquals("DIRTY", db.libraryDao().trackRef("track")!!.syncState)
            db.withWriteTransaction {
                assertTrue(db.projectMetadata("profile", "track", payload(0, buildJsonObject { put("album", "Late local album"); put("label", "File label") }, true)))
            }
            val late = db.trackMetadataDao().get("profile", "track")!!.decoded()
            assertEquals(3L, late.revision)
            assertNull(late.text("album"))
            assertEquals("File label", late.text("label"))
        } finally { db.close(); context.deleteDatabase(name) }
    }
}
