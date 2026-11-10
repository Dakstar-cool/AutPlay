package app.autplay.data.desktop

import java.util.Locale
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** Shared naming/publication policy for explicit imports and on-demand legacy-copy repair. */
object DesktopManagedMedia {
    private val publicationMutex: Mutex = Mutex()
    private val managedName: Regex = Regex("([0-9a-f]{64})\\.([a-z0-9]{2,5})")
    private val sourceExtensions: Map<String, String> = mapOf(
        "mp3" to "mp3", "flac" to "flac", "m4a" to "m4a", "m4b" to "m4b", "aac" to "aac",
        "wav" to "wav", "aif" to "aiff", "aiff" to "aiff", "ogg" to "ogg", "opus" to "opus", "alac" to "alac",
    )
    private val safeExtensions: Set<String> = sourceExtensions.keys + sourceExtensions.values + "media"

    /** Only this returned suffix is retained; a source name/path never becomes a locator. */
    fun canonicalSourceExtension(fileName: String): String? =
        sourceExtensions[fileName.substringAfterLast('.', "").lowercase(Locale.ROOT)]

    fun isSafeFileName(fileName: String): Boolean {
        val match = managedName.matchEntire(fileName) ?: return false
        return match.groupValues[2] in safeExtensions
    }

    fun hasExpectedHash(fileName: String, sha256: String): Boolean =
        isSafeFileName(fileName) && managedName.matchEntire(fileName)?.groupValues?.get(1) == sha256

    /** All callers acquire this before a store mutex, so import and legacy repair cannot deadlock. */
    suspend fun <T> withPublicationLock(action: suspend () -> T): T = publicationMutex.withLock { action() }
}
