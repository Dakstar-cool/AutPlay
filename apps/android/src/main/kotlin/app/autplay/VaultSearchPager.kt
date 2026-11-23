package app.autplay

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import app.autplay.application.search.VaultSearchProjector
import app.autplay.application.search.VaultSearchResult
import app.autplay.application.sync.ClientEventBinding
import app.autplay.ui.core.SearchGenerationGuard
import app.autplay.ui.core.SearchRequestKey
import app.autplay.ui.core.SearchResultStore
import kotlinx.coroutines.CancellationException

internal class VaultSearchPager(
    private val context: Context,
    private val projector: VaultSearchProjector,
    private val generation: SearchGenerationGuard,
    private val results: SearchResultStore<VaultSearchResult>,
) {
    private data class Continuation(val owner: ClientEventBinding, val key: SearchRequestKey, val cursor: String)

    private var continuation by mutableStateOf<Continuation?>(null)
    val cursor: String? get() = continuation?.takeIf { generation.accepts(it.key) }?.cursor
    var loadingMore by mutableStateOf(false)
        private set
    var pageError by mutableStateOf(false)
        private set

    suspend fun first(owner: ClientEventBinding, request: SearchRequestKey): List<VaultSearchResult> {
        if (!generation.accepts(request)) return emptyList()
        continuation = null
        loadingMore = false
        pageError = false
        return fetch(owner, request, null)
    }

    suspend fun more() {
        val next = continuation ?: return
        if (loadingMore || !generation.accepts(next.key)) return
        loadingMore = true
        pageError = false
        try {
            val following = fetch(next.owner, next.key, next.cursor)
            if (generation.accepts(next.key)) {
                results.accept(next.key, (results.results + following).distinctBy { it.remoteLibraryEntryId })
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            if (generation.accepts(next.key)) pageError = true
        } finally {
            if (generation.accepts(next.key)) loadingMore = false
        }
    }

    private suspend fun fetch(owner: ClientEventBinding, request: SearchRequestKey, after: String?): List<VaultSearchResult> {
        val page = AutPlayRuntime.serverFeatures(context, owner).searchLibraryPage(
            request.normalizedQuery, VaultSearchProjector.MAX_RESULTS, request.kind, includeCatalog = true, cursor = after,
        )
        val rows = projector.project(owner.serverProfileId.value, page.items)
        if (generation.accepts(request)) {
            check(after == null || page.nextCursor != after)
            continuation = page.nextCursor?.let { Continuation(owner, request, it) }
        }
        return rows
    }
}
