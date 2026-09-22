package app.autplay.application.importing

import android.content.ContentUris
import android.content.Context
import android.net.Uri
import android.provider.MediaStore
import androidx.core.net.toUri
import app.autplay.AutPlayRuntime
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.sync.ClientEventBinding
import app.autplay.domain.LocalId
import java.util.UUID
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.flow.first
import app.autplay.data.settings.applicationNonSecretSettingsStore

data class PhoneMusicTrack(val uri: String, val title: String, val artist: String, val size: Long, val mime: String)

/** MediaStore discovery and verified references. Source files are never copied, moved, or deleted. */
class PhoneMusicRepository(private val context: Context) {
    private val resolver = context.contentResolver

    suspend fun scan(): List<PhoneMusicTrack> = withContext(Dispatchers.IO) {
        val columns = arrayOf(MediaStore.Audio.Media._ID, MediaStore.Audio.Media.TITLE,
            MediaStore.Audio.Media.ARTIST, MediaStore.Audio.Media.SIZE, MediaStore.Audio.Media.MIME_TYPE)
        val rows = mutableListOf<PhoneMusicTrack>()
        resolver.query(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, columns,
            "${MediaStore.Audio.Media.IS_MUSIC} != 0 AND ${MediaStore.Audio.Media.SIZE} > 0", null,
            "${MediaStore.Audio.Media.TITLE} COLLATE NOCASE ASC")?.use { cursor ->
            while (cursor.moveToNext() && rows.size < 5000) {
                currentCoroutineContext().ensureActive()
                rows += PhoneMusicTrack(
                    ContentUris.withAppendedId(MediaStore.Audio.Media.EXTERNAL_CONTENT_URI, cursor.getLong(0)).toString(),
                    cursor.getString(1)?.take(500).orEmpty(),
                    cursor.getString(2)?.takeUnless { it == "<unknown>" }?.take(500).orEmpty(),
                    cursor.getLong(3), cursor.getString(4) ?: "audio/mpeg",
                )
            }
        }
        rows
    }

    suspend fun linkIntoLibrary(track: PhoneMusicTrack, binding: ClientEventBinding?): String = linkMutex.withLock {
        linkSerialized(track, binding)
    }

    private suspend fun linkSerialized(track: PhoneMusicTrack, binding: ClientEventBinding?): String = withContext(Dispatchers.IO) {
        require(track.uri.toUri().scheme == "content")
        val inspection = ContentUriInspector(resolver).inspectWithDigest(track.uri)
        check(inspection.status == ContentUriStatus.AVAILABLE) { "LOCAL_AUDIO_UNAVAILABLE" }
        val sha = checkNotNull(inspection.contentSha256) { "LOCAL_AUDIO_UNAVAILABLE" }
        check((inspection.byteSize ?: track.size) > 0L) { "LOCAL_AUDIO_EMPTY" }
            val profile = binding?.serverProfileId?.value ?: "local"
            fun id(kind: String) = LocalId(UUID.nameUUIDFromBytes("phone-music:$profile:$sha:$kind".toByteArray()).toString())
            val audioId = id("audio")
            val database = AutPlayRuntime.database(context)
            val library = LibraryVerticalSliceRepository(database, syncScheduler = AutPlayRuntime.syncScheduler(context))
            suspend fun restoreIfNeeded() {
                val active = applicationNonSecretSettingsStore(context).settings.first()
                check(active.activeServerProfileId == binding?.serverProfileId && (binding == null || active.activeUserId == binding.userId)) { "IMPORT_PROFILE_CHANGED" }
                val entry = database.libraryDao().entry(id("library").value)
                if (entry?.removedAtMs != null) library.restoreLibrary(binding, id("library"), LocalId.random(), System.currentTimeMillis())
            }
            database.localAudioDao().state(audioId.value)?.let { existing ->
                if (existing.contentUri == track.uri && ContentUriInspector(resolver).inspectWithDigest(existing.contentUri).contentSha256 == sha) {
                    restoreIfNeeded()
                    database.localAudioDao().upsertState(existing.copy(status = "AVAILABLE", lastVerifiedAtMs = System.currentTimeMillis()))
                    readMetadata(existing.localUserTrackRefId, existing.contentUri.toUri())
                    return@withContext audioId.value
                }
            }
            restoreIfNeeded()
            val existing = database.localAudioDao().state(audioId.value)
            if (existing != null) {
                database.localAudioDao().upsertState(existing.copy(
                    contentUri = track.uri,
                    persistedUriPermission = false,
                    localSha256 = sha.chunked(2).map { it.toInt(16).toByte() }.toByteArray(),
                    byteSize = inspection.byteSize ?: track.size,
                    status = "AVAILABLE",
                    lastVerifiedAtMs = System.currentTimeMillis(),
                    updatedAtMs = System.currentTimeMillis(),
                ))
            } else {
                library.importUri(
                    binding, id("track"), id("library"), audioId, id("change"),
                    track.title.ifBlank { "Audio" }, track.artist, inspection, false, System.currentTimeMillis(),
                )
            }
            readMetadata(id("track").value, track.uri.toUri())
            audioId.value
    }

    private suspend fun readMetadata(track: String, uri: Uri) {
        try { app.autplay.application.library.TrackMetadataRepository(context).readLocal(track, uri)
        } catch (error: kotlinx.coroutines.CancellationException) { throw error
        } catch (_: Exception) { /* Optional metadata cannot fail a verified audio import. */ }
    }

    companion object { private val linkMutex = Mutex() }
}
