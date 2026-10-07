package app.autplay.application.statistics

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

data class OwnerProfileStatisticsState(
    val snapshot: OwnerProfileStatistics? = null,
    val loading: Boolean = false,
    val refreshFailed: Boolean = false,
)

/** Remember one owner per profile identity; only opening the profile and explicit refresh read Room. */
class ProfileStatisticsSnapshotOwner(
    private val scope: CoroutineScope,
    private val profileId: String?,
    private val load: suspend (String?) -> OwnerProfileStatistics,
) {
    private val mutableState = MutableStateFlow(OwnerProfileStatisticsState())
    val state: StateFlow<OwnerProfileStatisticsState> = mutableState.asStateFlow()
    private var attemptedInitialLoad = false
    private var refreshJob: Job? = null

    fun loadIfNeeded() {
        if (attemptedInitialLoad) return
        refresh()
    }

    /** UI commands are serialized on its scope; repeated taps while loading coalesce into one read. */
    fun refresh() {
        if (mutableState.value.loading) return
        attemptedInitialLoad = true
        val previous = mutableState.value.snapshot
        mutableState.value = OwnerProfileStatisticsState(snapshot = previous, loading = true)
        refreshJob = scope.launch {
            try {
                mutableState.value = OwnerProfileStatisticsState(snapshot = load(profileId))
            } catch (cancelled: CancellationException) {
                attemptedInitialLoad = previous != null
                mutableState.value = OwnerProfileStatisticsState(snapshot = previous)
                throw cancelled
            } catch (_: Exception) {
                mutableState.value = OwnerProfileStatisticsState(snapshot = previous, refreshFailed = true)
            }
        }
    }

    /** Dispose when the owning profile changes so a late old-profile result cannot be displayed. */
    fun close() {
        refreshJob?.cancel()
    }
}
