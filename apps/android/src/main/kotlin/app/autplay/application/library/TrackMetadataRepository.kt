package app.autplay.application.library

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.media.MediaMetadataRetriever
import android.net.Uri
import androidx.core.graphics.scale
import app.autplay.AutPlayRuntime
import app.autplay.application.search.refreshTrackSearch
import app.autplay.application.server.ServerFeatureRepository
import androidx.room3.withWriteTransaction
import app.autplay.data.local.entity.MetadataArtworkEntity
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.security.MessageDigest
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*

class TrackMetadataRepository(private val context: Context) {
    private val database = AutPlayRuntime.database(context)

    suspend fun project(profile: String, track: String, root: JsonObject) {
        database.withWriteTransaction {
            check(database.projectMetadata(profile, track, root)) { "METADATA_INVALID" }
            database.refreshTrackSearch(track)
        }
    }

    suspend fun fetchArtwork(profile: String, api: ServerFeatureRepository, shard: ArtworkShard, offset: Int = 0): ArtworkBatchResult = withContext(Dispatchers.IO) {
        require(offset >= 0)
        val dao = database.trackMetadataDao()
        val before = dao.missingArtworkCount(profile, shard.lowerSha, shard.upperSha)
        val batch = dao.missingArtwork(profile, shard.lowerSha, shard.upperSha, 40, offset)
        for (metadata in batch) {
            val ref = database.libraryDao().trackRef(metadata.localUserTrackRefId) ?: continue
            if (ref.serverProfileId != profile || ref.deletedAtMs != null) continue
            val serverId = ref.serverUserTrackRefId ?: continue
            val sha = metadata.artworkSha256 ?: continue
            val bytes = try { api.metadataArtwork(serverId, sha) } catch (error: Exception) {
                if (error is kotlinx.coroutines.CancellationException) throw error
                if (error.message == "SERVER_HTTP_404") {
                    // A removal or a newer cover must not block the rest of the batch.
                    try { project(profile, ref.localUserTrackRefId, api.trackMetadata(serverId)) }
                    catch (refresh: Exception) { if (refresh is kotlinx.coroutines.CancellationException) throw refresh }
                    continue
                }
                throw error
            }
            check(digest(bytes) == sha) { "METADATA_ARTWORK_INTEGRITY" }
            storeArtwork(profile, bytes, sha)
        }
        val remaining = dao.missingArtworkCount(profile, shard.lowerSha, shard.upperSha)
        // A stored cover or a refreshed 404 may remove several references at once.
        // Restart at the head after progress; otherwise skip this unavailable page.
        ArtworkBatchResult(remaining, when {
            remaining < before -> 0
            batch.isEmpty() -> remaining
            else -> offset + batch.size
        })
    }

    suspend fun storeArtwork(profile: String, bytes: ByteArray, sha: String = digest(bytes)): String = withContext(Dispatchers.IO) {
        require(bytes.size in 1..2_097_152 && sha.matches(Regex("[a-f0-9]{64}")))
        require(digest(bytes) == sha)
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        require(bounds.outWidth in 1..500 && bounds.outHeight in 1..500 && bounds.outMimeType == "image/jpeg")
        val directory = File(context.filesDir, "metadata-art/${digest(profile.toByteArray())}").apply { mkdirs() }
        val target = File(directory, "$sha.jpg")
        val temporary = File.createTempFile("art-", ".part", directory)
        try {
            FileOutputStream(temporary).use { output -> output.write(bytes); output.fd.sync() }
            Files.move(temporary.toPath(), target.toPath(), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
            database.trackMetadataDao().upsertArtwork(MetadataArtworkEntity(profile, sha, target.absolutePath, bytes.size.toLong()))
        } finally { temporary.delete() }
        sha
    }

    /** Read only the user-selected local media; never retag or replace its bytes. */
    suspend fun readLocal(track: String, uri: Uri) = withContext(Dispatchers.IO) {
        val ref = database.libraryDao().trackRef(track) ?: return@withContext
        val existing = database.trackMetadataDao().get(ref.serverProfileId, track)?.decoded()
        if (existing?.payload?.get("local_reader_version")?.jsonPrimitive?.intOrNull == 1) return@withContext
        val reader = MediaMetadataRetriever()
        try {
            reader.setDataSource(context, uri)
            var hasDate = false
            val platformFields = buildJsonObject {
                mapOf("title" to MediaMetadataRetriever.METADATA_KEY_TITLE, "artist" to MediaMetadataRetriever.METADATA_KEY_ARTIST,
                    "album" to MediaMetadataRetriever.METADATA_KEY_ALBUM, "album_artist" to MediaMetadataRetriever.METADATA_KEY_ALBUMARTIST,
                    "release_date" to MediaMetadataRetriever.METADATA_KEY_DATE).forEach { (key, tag) ->
                    val value = reader.extractMetadata(tag)?.trim()?.takeIf { it.length in 1..500 }
                    if (value != null && (key != "release_date" || validMetadataDate(value))) { put(key, value); if (key == "release_date") hasDate = true }
                }
                if (!hasDate) reader.extractMetadata(MediaMetadataRetriever.METADATA_KEY_YEAR)
                    ?.takeIf { it.matches(Regex("[0-9]{4}")) }?.let { put("release_date", it) }
            }
            val values = JsonObject(platformFields + embeddedId3Fields(context, uri))
            val artwork = reader.embeddedPicture?.takeIf { it.size <= 4_194_304 }?.let { normalizeArtwork(it) }
            val sha = artwork?.let { storeArtwork(ref.serverProfileId, it) }
            val root = buildJsonObject {
                put("revision", 0); put("state", "LOCAL"); put("fields", values); put("local_reader_version", 1)
                put("provenance", buildJsonObject { values.keys.forEach { field -> put(field, buildJsonObject {
                    put("source", "EMBEDDED"); put("source_id", "local-file"); put("locked", false)
                }) } })
                put("candidates", JsonArray(emptyList())); sha?.let { put("artwork_sha256", it) }
            }
            project(ref.serverProfileId, track, root)
        } finally {
            reader.release()
        }
    }

    private fun normalizeArtwork(bytes: ByteArray): ByteArray? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth !in 1..4096 || bounds.outHeight !in 1..4096) return null
        val image = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: return null
        val factor = minOf(1f, 500f / maxOf(image.width, image.height))
        val resized = image.scale(maxOf(1, (image.width * factor).toInt()), maxOf(1, (image.height * factor).toInt()), true)
        return try { ByteArrayOutputStream().use { output -> resized.compress(Bitmap.CompressFormat.JPEG, 85, output); output.toByteArray() } }
        finally { if (resized !== image) resized.recycle(); image.recycle() }
    }

    private fun digest(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
}

data class ArtworkBatchResult(val remaining: Int, val nextOffset: Int)

/** SHA-256 prefixes partition references without moving rows between concurrent workers. */
enum class ArtworkShard(val lowerSha: String, val upperSha: String) {
    FIRST("0", "4"), SECOND("4", "8"), THIRD("8", "c"), FOURTH("c", "g");

    companion object {
        fun fromIndex(index: Int): ArtworkShard? = entries.getOrNull(index)
    }
}
