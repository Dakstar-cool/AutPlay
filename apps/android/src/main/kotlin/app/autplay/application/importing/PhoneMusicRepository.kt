package app.autplay.application.importing

import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import androidx.core.net.toUri
import app.autplay.AutPlayRuntime
import app.autplay.application.library.LibraryVerticalSliceRepository
import app.autplay.application.sync.ClientEventBinding
import app.autplay.domain.LocalId
import java.io.File
import java.security.MessageDigest
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

/** MediaStore discovery and verified copies. Source files are never modified or deleted. */
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

    suspend fun copyIntoLibrary(track: PhoneMusicTrack, binding: ClientEventBinding?): String = copyMutex.withLock {
        copySerialized(track, binding)
    }

    private suspend fun copySerialized(track: PhoneMusicTrack, binding: ClientEventBinding?): String = withContext(Dispatchers.IO) {
        require(track.uri.toUri().scheme == "content")
        val staging = File(context.cacheDir, "phone-music-copy").apply { mkdirs() }
        val temporary = File.createTempFile("audio-", ".part", staging)
        try {
            val hash = MessageDigest.getInstance("SHA-256")
            var size = 0L
            resolver.openInputStream(track.uri.toUri()).use { input ->
                checkNotNull(input) { "LOCAL_AUDIO_UNAVAILABLE" }
                temporary.outputStream().use { output ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        currentCoroutineContext().ensureActive()
                        val count = input.read(buffer)
                        if (count < 0) break
                        size += count
                        check(size <= 2L * 1024 * 1024 * 1024) { "LOCAL_AUDIO_TOO_LARGE" }
                        hash.update(buffer, 0, count)
                        output.write(buffer, 0, count)
                    }
                }
            }
            check(size > 0) { "LOCAL_AUDIO_EMPTY" }
            val sha = hash.digest().joinToString("") { "%02x".format(it) }
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
                if (ContentUriInspector(resolver).inspectWithDigest(existing.contentUri).contentSha256 == sha) {
                    restoreIfNeeded()
                    database.localAudioDao().upsertState(existing.copy(status = "AVAILABLE", lastVerifiedAtMs = System.currentTimeMillis()))
                    readMetadata(existing.localUserTrackRefId, existing.contentUri.toUri())
                    return@withContext audioId.value
                }
            }
            val uri = publishCopy(temporary, track, sha)
            val inspection = ContentUriInspector(resolver).inspectWithDigest(uri.toString())
            check(inspection.status == ContentUriStatus.AVAILABLE && inspection.contentSha256 == sha) { "LOCAL_COPY_VERIFICATION_FAILED" }
            restoreIfNeeded()
            val existing = database.localAudioDao().state(audioId.value)
            if (existing != null) {
                database.localAudioDao().upsertState(existing.copy(contentUri = uri.toString(), status = "AVAILABLE", lastVerifiedAtMs = System.currentTimeMillis()))
            } else {
                library.importUri(
                    binding, id("track"), id("library"), audioId, id("change"),
                    track.title.ifBlank { "Audio" }, track.artist, inspection, false, System.currentTimeMillis(),
                )
            }
            readMetadata(id("track").value, uri)
            audioId.value
        } finally {
            temporary.delete()
        }
    }

    private suspend fun readMetadata(track: String, uri: Uri) {
        try { app.autplay.application.library.TrackMetadataRepository(context).readLocal(track, uri)
        } catch (error: kotlinx.coroutines.CancellationException) { throw error
        } catch (_: Exception) { /* Optional metadata cannot fail a verified audio import. */ }
    }

    @Suppress("DEPRECATION")
    private suspend fun publishCopy(source: File, track: PhoneMusicTrack, sha: String): Uri {
        val suffix = when (track.mime) {
            "audio/flac", "audio/x-flac" -> "flac"
            "audio/mp4", "audio/x-m4a" -> "m4a"
            "audio/ogg", "application/ogg" -> "ogg"
            "audio/wav", "audio/x-wav" -> "wav"
            "audio/opus" -> "opus"
            "audio/webm" -> "webm"
            else -> "mp3"
        }
        val title = track.title.map { if (it.isLetterOrDigit() || it in " -_") it else '_' }
            .joinToString("").trim().take(60).ifBlank { "Audio" }
        val name = "$title-$sha.$suffix"
        val collection = if (Build.VERSION.SDK_INT >= 29) MediaStore.Audio.Media.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY)
            else MediaStore.Audio.Media.EXTERNAL_CONTENT_URI
        val values = ContentValues().apply {
            put(MediaStore.Audio.Media.DISPLAY_NAME, name)
            put(MediaStore.Audio.Media.TITLE, track.title)
            put(MediaStore.Audio.Media.ARTIST, track.artist)
            put(MediaStore.Audio.Media.MIME_TYPE, track.mime)
            put(MediaStore.Audio.Media.IS_MUSIC, 1)
            if (Build.VERSION.SDK_INT >= 29) {
                put(MediaStore.Audio.Media.RELATIVE_PATH, "${Environment.DIRECTORY_MUSIC}/AutPlay/")
                put(MediaStore.Audio.Media.IS_PENDING, 1)
            } else {
                val directory = File(Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_MUSIC), "AutPlay").apply { mkdirs() }
                put(MediaStore.Audio.Media.DATA, File(directory, name).absolutePath)
            }
        }
        // Reuse only a fully verified copy, including recovery after publication before Room commit.
        val queryUri = if (Build.VERSION.SDK_INT >= 29) MediaStore.setIncludePending(collection) else collection
        val selection = if (Build.VERSION.SDK_INT >= 29)
            "${MediaStore.Audio.Media.DISPLAY_NAME} = ? AND ${MediaStore.Audio.Media.RELATIVE_PATH} = ? AND ${MediaStore.Audio.Media.OWNER_PACKAGE_NAME} = ?"
            else "${MediaStore.Audio.Media.DATA} = ?"
        val arguments = if (Build.VERSION.SDK_INT >= 29) arrayOf(name, "${Environment.DIRECTORY_MUSIC}/AutPlay/", context.packageName)
            else arrayOf(values.getAsString(MediaStore.Audio.Media.DATA))
        val columns = if (Build.VERSION.SDK_INT >= 29) arrayOf(MediaStore.Audio.Media._ID, MediaStore.Audio.Media.IS_PENDING) else arrayOf(MediaStore.Audio.Media._ID)
        resolver.query(queryUri, columns, selection, arguments, null)?.use { cursor ->
            while (cursor.moveToNext()) {
                val candidate = ContentUris.withAppendedId(collection, cursor.getLong(0))
                if (ContentUriInspector(resolver).inspectWithDigest(candidate.toString()).contentSha256 == sha) {
                    if (Build.VERSION.SDK_INT >= 29) resolver.update(candidate, ContentValues().apply { put(MediaStore.Audio.Media.IS_PENDING, 0) }, null, null)
                    return candidate
                }
                if (Build.VERSION.SDK_INT >= 29 && cursor.getInt(1) == 1) resolver.delete(candidate, null, null)
            }
        }
        val uri = checkNotNull(resolver.insert(collection, values)) { "LOCAL_COPY_CREATE_FAILED" }
        try {
            resolver.openOutputStream(uri, "w").use { output ->
                checkNotNull(output) { "LOCAL_COPY_CREATE_FAILED" }
                source.inputStream().use { input ->
                    val buffer = ByteArray(64 * 1024)
                    while (true) {
                        currentCoroutineContext().ensureActive()
                        val count = input.read(buffer)
                        if (count < 0) break
                        output.write(buffer, 0, count)
                    }
                }
            }
            check(ContentUriInspector(resolver).inspectWithDigest(uri.toString()).contentSha256 == sha) { "LOCAL_COPY_VERIFICATION_FAILED" }
            if (Build.VERSION.SDK_INT >= 29) resolver.update(uri, ContentValues().apply { put(MediaStore.Audio.Media.IS_PENDING, 0) }, null, null)
            return uri
        } catch (error: Exception) {
            // This URI was created by this attempt. It is never the source URI.
            resolver.delete(uri, null, null)
            throw error
        }
    }

    companion object { private val copyMutex = Mutex() }
}
