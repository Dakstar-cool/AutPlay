package app.autplay

import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import app.autplay.application.releasewatch.ReleaseWatchException
import app.autplay.application.releasewatch.ReleaseWatchPort
import app.autplay.application.releasewatch.OwnerAutoDownloadSettings
import app.autplay.application.releasewatch.WatchFailure
import app.autplay.application.releasewatch.WatchedArtist
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

internal enum class ReleaseWatchStatus { DISCONNECTED, UNAVAILABLE, LOADING, READY, OFFLINE, SESSION, ERROR }

/** Server data is volatile and scoped to exactly one active profile. */
@Stable
internal class ReleaseWatchState(
    private val profileId: ServerProfileId?,
    private val port: ReleaseWatchPort?,
) {
    var status by mutableStateOf(if (profileId == null || port == null) ReleaseWatchStatus.DISCONNECTED else ReleaseWatchStatus.LOADING)
        private set
    var artists: List<WatchedArtist> by mutableStateOf(emptyList())
        private set
    var exclusions: Set<String> by mutableStateOf(emptySet())
        private set
    var busyArtistId: String? by mutableStateOf(null)
        private set
    var autoStatus by mutableStateOf(if (profileId == null || port == null) ReleaseWatchStatus.DISCONNECTED else ReleaseWatchStatus.LOADING)
        private set
    var autoSettings: OwnerAutoDownloadSettings? by mutableStateOf(null)
        private set
    var autoBusy by mutableStateOf(false)
        private set
    var autoCommandFailure: WatchFailure? by mutableStateOf(null)
        private set
    private val mutex = Mutex()

    suspend fun load() = mutex.withLock {
        autoCommandFailure = null
        loadLocked()
        loadAutoLocked()
    }

    suspend fun setAutoDownload(enabled: Boolean) = mutex.withLock {
        val profile = profileId ?: return@withLock
        val transport = port ?: return@withLock
        val current = autoSettings ?: return@withLock
        if (autoStatus != ReleaseWatchStatus.READY || autoBusy || current.enabled == enabled) return@withLock
        if (enabled && (!current.executionEnabled || current.consequenceConfirmation.isNullOrBlank())) return@withLock
        autoBusy = true
        autoCommandFailure = null
        try {
            transport.setAutoDownload(
                profile,
                enabled,
                current.revision,
                if (enabled) current.consequenceConfirmation else null,
            )
            loadAutoLocked()
            loadLocked()
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: ReleaseWatchException) {
            autoCommandFailure = error.failure
            loadAutoLocked()
            loadLocked()
        } catch (_: Exception) {
            autoCommandFailure = WatchFailure.SERVER
            loadAutoLocked()
            loadLocked()
        } finally {
            autoBusy = false
        }
    }

    suspend fun setFollow(artistId: String, follow: Boolean) = mutex.withLock {
        val profile = profileId ?: return@withLock
        val transport = port ?: return@withLock
        if (status != ReleaseWatchStatus.READY) return@withLock
        busyArtistId = artistId
        try {
            transport.setFollow(profile, artistId, follow)
            loadLocked()
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: ReleaseWatchException) {
            status = error.failure.toStatus()
        } catch (_: Exception) {
            status = ReleaseWatchStatus.ERROR
        } finally {
            busyArtistId = null
        }
    }

    private suspend fun loadLocked() {
        val profile = profileId ?: return
        val transport = port ?: return
        status = ReleaseWatchStatus.LOADING
        try {
            val loadedArtists = mutableListOf<WatchedArtist>()
            val loadedExclusions = mutableSetOf<String>()
            var afterArtistId: String? = null
            var pageCount = 0
            do {
                val page = transport.artists(profile, afterArtistId).items
                loadedArtists += page
                pageCount++
                afterArtistId = if (page.size == PAGE_SIZE) page.last().artistId else null
                require(pageCount < MAX_PAGES || afterArtistId == null) { "release-watch artist list exceeds bound" }
            } while (afterArtistId != null)
            afterArtistId = null
            pageCount = 0
            do {
                val page = transport.exclusions(profile, afterArtistId).items
                loadedExclusions += page
                pageCount++
                afterArtistId = if (page.size == PAGE_SIZE) page.last() else null
                require(pageCount < MAX_PAGES || afterArtistId == null) { "release-watch exclusion list exceeds bound" }
            } while (afterArtistId != null)
            artists = loadedArtists.distinctBy { it.artistId }
            exclusions = loadedExclusions
            status = ReleaseWatchStatus.READY
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: ReleaseWatchException) {
            status = error.failure.toStatus()
        } catch (_: Exception) {
            status = ReleaseWatchStatus.ERROR
        }
    }

    private suspend fun loadAutoLocked() {
        val profile = profileId ?: return
        val transport = port ?: return
        autoStatus = ReleaseWatchStatus.LOADING
        autoSettings = null
        try {
            autoSettings = transport.autoDownloadSettings(profile)
            autoStatus = ReleaseWatchStatus.READY
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: ReleaseWatchException) {
            autoStatus = error.failure.toStatus()
        } catch (_: Exception) {
            autoStatus = ReleaseWatchStatus.ERROR
        }
    }

    private fun WatchFailure.toStatus(): ReleaseWatchStatus = when (this) {
        WatchFailure.OFFLINE -> ReleaseWatchStatus.OFFLINE
        WatchFailure.SESSION -> ReleaseWatchStatus.SESSION
        WatchFailure.UNAVAILABLE -> ReleaseWatchStatus.UNAVAILABLE
        WatchFailure.SERVER -> ReleaseWatchStatus.ERROR
    }

    private companion object {
        const val PAGE_SIZE = 100
        const val MAX_PAGES = 100
    }
}
