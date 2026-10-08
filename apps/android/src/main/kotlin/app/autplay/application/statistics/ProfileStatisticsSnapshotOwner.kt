package app.autplay.application.statistics

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.ensureActive
import kotlin.coroutines.coroutineContext

data class OwnerProfileStatisticsState(
    val snapshot: OwnerProfileStatistics? = null,
    val loading: Boolean = false,
    val refreshFailed: Boolean = false,
    val accountBased: Boolean = false,
)

/** Remember one owner per profile identity; only opening the profile and explicit refresh read Room. */
class ProfileStatisticsSnapshotOwner(
    private val scope: CoroutineScope,
    private val profileId: String?,
    private val accountBased: Boolean = false,
    private val load: suspend (String?) -> OwnerProfileStatistics,
) {
    private val mutableState = MutableStateFlow(OwnerProfileStatisticsState(accountBased = accountBased))
    val state: StateFlow<OwnerProfileStatisticsState> = mutableState.asStateFlow()
    private var attemptedInitialLoad = false
    private var refreshJob: Job? = null
    private var closed = false

    fun loadIfNeeded() {
        if (attemptedInitialLoad) return
        refresh()
    }

    /** UI commands are serialized on its scope; repeated taps while loading coalesce into one read. */
    fun refresh() = refreshWith { load(profileId) }

    /** Account cache invalidation can refresh without causing another synchronous server request. */
    fun refreshWith(readSnapshot: suspend () -> OwnerProfileStatistics) {
        if (closed || mutableState.value.loading) return
        attemptedInitialLoad = true
        val previous = mutableState.value.snapshot
        mutableState.value = OwnerProfileStatisticsState(snapshot = previous, loading = true,
            accountBased = previous?.accountId != null)
        refreshJob = scope.launch {
            try {
                val snapshot = readSnapshot()
                coroutineContext.ensureActive()
                if (!closed) mutableState.value = OwnerProfileStatisticsState(snapshot = snapshot, accountBased = snapshot.accountId != null)
            } catch (cancelled: CancellationException) {
                attemptedInitialLoad = previous != null
                if (!closed) mutableState.value = OwnerProfileStatisticsState(snapshot = previous, accountBased = previous?.accountId != null)
                throw cancelled
            } catch (_: Exception) {
                if (!closed) mutableState.value = OwnerProfileStatisticsState(snapshot = previous, refreshFailed = true, accountBased = previous?.accountId != null)
            }
        }
    }

    /** Dispose when the owning profile changes so a late old-profile result cannot be displayed. */
    fun close() {
        closed = true
        refreshJob?.cancel()
    }
}
