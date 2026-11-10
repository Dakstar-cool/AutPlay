package app.autplay

import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import app.autplay.application.releasewatch.ReleaseFeedException
import app.autplay.application.releasewatch.ReleaseFeedFailure
import app.autplay.application.releasewatch.ReleaseFeedItem
import app.autplay.application.releasewatch.ReleaseFeedPort
import app.autplay.domain.ServerProfileId
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

internal enum class HomeReleaseFeedStatus { DISCONNECTED, LOADING, READY, OFFLINE, SESSION, UNAVAILABLE, ERROR }

/** Volatile owner feed; each refresh starts a new server snapshot and cursor chain. */
@Stable
internal class HomeReleaseFeedState(
    private val profileId: ServerProfileId?,
    private val port: ReleaseFeedPort?,
) {
    var status by mutableStateOf(if (profileId == null || port == null) HomeReleaseFeedStatus.DISCONNECTED else HomeReleaseFeedStatus.LOADING)
        private set
    var items: List<ReleaseFeedItem> by mutableStateOf(emptyList())
        private set
    var nextCursor: String? by mutableStateOf(null)
        private set
    var loadingMore by mutableStateOf(false)
        private set
    var pageFailure: HomeReleaseFeedStatus? by mutableStateOf(null)
        private set
    private val mutex = Mutex()

    suspend fun refresh() = mutex.withLock {
        val profile = profileId ?: return@withLock
        val transport = port ?: return@withLock
        status = HomeReleaseFeedStatus.LOADING
        items = emptyList()
        nextCursor = null
        pageFailure = null
        try {
            val page = transport.page(profile)
            items = page.items
            nextCursor = page.nextCursor
            status = HomeReleaseFeedStatus.READY
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: ReleaseFeedException) {
            status = error.failure.toStatus()
        } catch (_: Exception) {
            status = HomeReleaseFeedStatus.ERROR
        }
    }

    suspend fun loadMore() = mutex.withLock {
        val profile = profileId ?: return@withLock
        val transport = port ?: return@withLock
        val cursor = nextCursor ?: return@withLock
        if (status != HomeReleaseFeedStatus.READY || loadingMore) return@withLock
        loadingMore = true
        pageFailure = null
        try {
            val page = transport.page(profile, cursor)
            require(page.nextCursor != cursor)
            val known = items.mapTo(HashSet()) { it.candidateId }
            items = items + page.items.filter { known.add(it.candidateId) }
            nextCursor = page.nextCursor
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: ReleaseFeedException) {
            if (error.failure == ReleaseFeedFailure.UNAVAILABLE || error.failure == ReleaseFeedFailure.SESSION) {
                items = emptyList()
                nextCursor = null
                status = error.failure.toStatus()
            } else {
                pageFailure = error.failure.toStatus()
            }
        } catch (_: Exception) {
            pageFailure = HomeReleaseFeedStatus.ERROR
        } finally {
            loadingMore = false
        }
    }

    private fun ReleaseFeedFailure.toStatus(): HomeReleaseFeedStatus = when (this) {
        ReleaseFeedFailure.OFFLINE -> HomeReleaseFeedStatus.OFFLINE
        ReleaseFeedFailure.SESSION -> HomeReleaseFeedStatus.SESSION
        ReleaseFeedFailure.UNAVAILABLE -> HomeReleaseFeedStatus.UNAVAILABLE
        ReleaseFeedFailure.SERVER -> HomeReleaseFeedStatus.ERROR
    }
}
