package app.autplay.application.desktop

import kotlinx.coroutines.flow.StateFlow

object DesktopLocalLimits {
    const val MAX_QUEUE_ENTRIES: Int = 1_000
    const val MAX_SELECTION_ROOTS: Int = 256
}

/** Local collection identity; file capabilities never appear in the UI projection. */
data class DesktopTrack(
    val id: String,
    val title: String,
    val artist: String,
    val durationMs: Long?,
    val sha256: String,
    val byteSize: Long,
)

data class DesktopPlaylistEntry(val id: String, val trackId: String)
data class DesktopPlaylist(val id: String, val name: String, val entries: List<DesktopPlaylistEntry>)
data class DesktopQueueEntry(val id: String, val trackId: String)

/** Unknown persisted modes must survive reads; the playback owner rejects unsupported modes. */
data class DesktopQueue(
    val id: String,
    val entries: List<DesktopQueueEntry>,
    val currentEntryId: String?,
    val queueType: String = "LIBRARY",
    val positionMs: Long = 0,
    val shuffleEnabled: Boolean = false,
    val shuffleOrder: List<String> = entries.map { it.id },
    val repeatMode: String = "OFF",
    val sessionId: String? = null,
    val sessionStartedAtMs: Long? = null,
    val playedMs: Long = 0,
)

data class DesktopListeningEvent(
    val id: String,
    val queueEntryId: String,
    val trackId: String,
    val startedAtMs: Long,
    val playedMs: Long,
    val positionMs: Long,
    val finalized: Boolean,
)

data class DesktopLibrarySnapshot(
    val tracks: List<DesktopTrack> = emptyList(),
    val playlists: List<DesktopPlaylist> = emptyList(),
    val queue: DesktopQueue? = null,
    val history: List<DesktopListeningEvent> = emptyList(),
)

/** Verified managed copy produced by the explicit import adapter; never an original path. */
data class DesktopImportedTrack(
    val id: String,
    val title: String,
    val artist: String,
    val durationMs: Long?,
    val sha256: String,
    val byteSize: Long,
    val managedFileName: String,
)

/** Transactions commit mutations and local-only outbox facts together. No server binding is invented. */
interface DesktopLibraryStore {
    val state: StateFlow<DesktopLibrarySnapshot>
    suspend fun addImportedTrack(track: DesktopImportedTrack): DesktopTrack
    suspend fun updateTrackMetadata(trackId: String, title: String, artist: String, durationMs: Long?): Unit
    suspend fun createPlaylist(name: String): DesktopPlaylist
    suspend fun appendToPlaylist(playlistId: String, trackId: String): Unit
    suspend fun removePlaylistEntry(playlistId: String, entryId: String): Unit
    suspend fun saveQueue(queue: DesktopQueue, event: DesktopListeningEvent? = null): Unit
    /** Returned capability is runtime-only, checked against the owned media directory. */
    suspend fun resolveManagedFile(trackId: String): String?
    suspend fun close(): Unit
}

data class DesktopImportResult(val imported: Int, val duplicates: Int, val failed: Int, val errorCode: String? = null)

interface DesktopImportPort {
    /** Only user-selected sources. Implementations bound traversal, bytes and cancellation. */
    suspend fun importSelected(paths: List<String>): DesktopImportResult
}

data class DesktopAudioEvent(
    val generation: Long,
    val status: String,
    val positionMs: Long = 0,
    val durationMs: Long? = null,
    val isPlaying: Boolean = false,
    val errorCode: String? = null,
    val title: String? = null,
    val artist: String? = null,
)

/** One native execution owner; a generation identifies a single selected queue entry. */
interface DesktopAudioEngine {
    val state: StateFlow<DesktopAudioEvent>
    fun open(managedPath: String, generation: Long, positionMs: Long, autoplay: Boolean): Unit
    fun play(generation: Long): Unit
    fun pause(generation: Long): Unit
    fun seek(generation: Long, positionMs: Long): Unit
    fun stop(generation: Long): Unit
    suspend fun close(): Unit
}
