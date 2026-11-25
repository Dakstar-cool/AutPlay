package app.autplay.playback

import android.Manifest
import android.content.ContentUris
import android.content.Context
import android.net.Uri
import android.os.Build
import android.provider.MediaStore

internal val phoneAudioReadPermission: String
    get() = if (Build.VERSION.SDK_INT >= 33) Manifest.permission.READ_MEDIA_AUDIO else Manifest.permission.READ_EXTERNAL_STORAGE

/** An already readable phone file can serve a library row without changing its catalog identity. */
internal fun findPhoneRecording(context: Context, title: String?, artist: String?, durationMs: Long?): Uri? {
    if (title.isNullOrBlank() || artist.isNullOrBlank() || artist == "<unknown>") return null
    val media = MediaStore.Audio.Media.EXTERNAL_CONTENT_URI
    val condition = "title = ? COLLATE NOCASE AND artist = ? COLLATE NOCASE AND _size > 0"
    return try {
        context.contentResolver.query(media, arrayOf("_id", "duration"), condition, arrayOf(title.trim(), artist.trim()), null)?.use { cursor ->
            var match: Uri? = null
            while (cursor.moveToNext()) {
                if (durationMs != null && durationMs > 0 && kotlin.math.abs(cursor.getLong(1) - durationMs) > 1000) continue
                if (match != null) return@use null
                match = ContentUris.withAppendedId(media, cursor.getLong(0))
            }
            match
        }
    } catch (_: SecurityException) {
        null
    } catch (_: IllegalArgumentException) {
        null
    }
}
