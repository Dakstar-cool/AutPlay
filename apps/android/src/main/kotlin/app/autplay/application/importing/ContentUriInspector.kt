package app.autplay.application.importing

import android.content.ContentResolver
import android.content.Intent
import android.database.Cursor
import android.provider.OpenableColumns
import androidx.core.net.toUri

/** Stable, non-sensitive outcome of probing a user-selected MediaStore or SAF URI. */
data class ContentUriInspection(
    val uri: String,
    val status: ContentUriStatus,
    val displayName: String?,
    val byteSize: Long?,
    val contentSha256: String? = null,
)

enum class ContentUriStatus { AVAILABLE, MISSING, PERMISSION_REVOKED, INVALID }

/**
 * Reads only public metadata and one byte. Failed probes are states, never an instruction to
 * delete the user's library intent. Callers persist the URI and update availability separately.
 */
class ContentUriInspector(
    private val resolver: ContentResolver,
    private val streamOpener: (android.net.Uri) -> java.io.InputStream? = resolver::openInputStream,
) {
    /** Attempts SAF persistence only when the picker granted a persistable read capability. */
    fun acquirePersistableReadPermission(rawUri: String, grantFlags: Int): Boolean {
        val uri = rawUri.toUri()
        if (uri.scheme != ContentResolver.SCHEME_CONTENT ||
            grantFlags and Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION == 0 ||
            grantFlags and Intent.FLAG_GRANT_READ_URI_PERMISSION == 0
        ) return false
        return try {
            resolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
            true
        } catch (_: SecurityException) {
            false
        }
    }
    fun inspect(rawUri: String): ContentUriInspection = inspectInternal(rawUri, computeDigest = false)

    /** Reads a bounded complete stream off the main thread so readable input has an exact digest. */
    suspend fun inspectWithDigest(rawUri: String): ContentUriInspection = kotlinx.coroutines.withContext(
        kotlinx.coroutines.Dispatchers.IO,
    ) {
        inspectInternal(rawUri, computeDigest = true)
    }

    private fun inspectInternal(rawUri: String, computeDigest: Boolean): ContentUriInspection {
        val uri = runCatching { rawUri.toUri() }.getOrNull()
            ?: return ContentUriInspection(rawUri, ContentUriStatus.INVALID, null, null)
        if (uri.scheme != ContentResolver.SCHEME_CONTENT || uri.authority.isNullOrBlank()) {
            return ContentUriInspection(rawUri, ContentUriStatus.INVALID, null, null)
        }
        return try {
            val metadata = resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)
                .useMetadata()
            val byteSize = metadata.second
            if (byteSize != null && byteSize > MAX_SOURCE_BYTES) {
                return ContentUriInspection(rawUri, ContentUriStatus.MISSING, metadata.first, byteSize)
            }
            val input = streamOpener(uri)
                ?: return ContentUriInspection(rawUri, ContentUriStatus.MISSING, metadata.first, metadata.second)
            val digest = input.use {
                if (!computeDigest) {
                    it.read()
                    null
                } else {
                    val sha256 = java.security.MessageDigest.getInstance("SHA-256")
                    val buffer = ByteArray(BUFFER_BYTES)
                    var total = 0L
                    while (true) {
                        val count = it.read(buffer)
                        if (count < 0) break
                        total += count
                        if (total > MAX_SOURCE_BYTES) {
                            return ContentUriInspection(rawUri, ContentUriStatus.MISSING, metadata.first, metadata.second)
                        }
                        sha256.update(buffer, 0, count)
                    }
                    sha256.digest().joinToString("") { byte -> "%02x".format(byte.toInt() and 0xff) }
                }
            }
            ContentUriInspection(rawUri, ContentUriStatus.AVAILABLE, metadata.first, metadata.second, digest)
        } catch (_: SecurityException) {
            ContentUriInspection(rawUri, ContentUriStatus.PERMISSION_REVOKED, null, null)
        } catch (_: java.io.FileNotFoundException) {
            ContentUriInspection(rawUri, ContentUriStatus.MISSING, null, null)
        } catch (_: java.io.IOException) {
            ContentUriInspection(rawUri, ContentUriStatus.MISSING, null, null)
        } catch (_: IllegalArgumentException) {
            ContentUriInspection(rawUri, ContentUriStatus.INVALID, null, null)
        }
    }

    private companion object {
        const val BUFFER_BYTES = 64 * 1024
        const val MAX_SOURCE_BYTES = 8L * 1024 * 1024 * 1024
    }

    private fun Cursor?.useMetadata(): Pair<String?, Long?> {
        this ?: return null to null
        use { cursor ->
            if (!cursor.moveToFirst()) return null to null
            val name = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 }?.let(cursor::getString)
            val size = cursor.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 }?.let { index ->
                if (cursor.isNull(index)) null else cursor.getLong(index)
            }
            return name to size
        }
    }
}
