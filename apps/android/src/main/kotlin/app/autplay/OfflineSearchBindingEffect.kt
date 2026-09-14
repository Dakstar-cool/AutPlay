package app.autplay

import android.content.Context
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import app.autplay.application.search.LocalTrackSearchRepository
import app.autplay.application.search.LocalTrackSearchResult
import app.autplay.application.search.VaultSearchProjector
import app.autplay.application.search.VaultSearchResult
import app.autplay.application.sync.ClientEventBinding
import app.autplay.ui.core.CoreProductUiState
import app.autplay.ui.core.SearchGenerationGuard
import app.autplay.ui.core.SearchResultStore
import app.autplay.ui.core.SearchScope

/** Replays a saved query after a binding change without growing the root composable CFG. */
@Composable
internal fun RefreshSearchOnBindingEffect(
    binding: ClientEventBinding?,
    context: Context,
    completed: Boolean,
    coreState: CoreProductUiState,
    generation: SearchGenerationGuard,
    resultStore: SearchResultStore<LocalTrackSearchResult>,
    vaultResultStore: SearchResultStore<VaultSearchResult>,
    repository: LocalTrackSearchRepository,
    vaultProjector: VaultSearchProjector,
    setResults: (List<LocalTrackSearchResult>) -> Unit,
    setLoading: (Boolean) -> Unit,
    setError: (Boolean) -> Unit,
    setVaultLoading: (Boolean) -> Unit,
    setVaultError: (Boolean) -> Unit,
    setVaultResults: (List<VaultSearchResult>) -> Unit,
    setVaultSearched: (Boolean) -> Unit,
    reportError: (String) -> Unit,
) {
    val bindingKey = binding?.serverProfileId?.value
    LaunchedEffect(bindingKey) {
        if (binding == null) coreState.scopes = setOf(SearchScope.Local)
        generation.invalidate()
        resultStore.invalidate()
        vaultResultStore.invalidate()
        setResults(emptyList())
        setLoading(false)
        setError(false)
        setVaultLoading(false)
        setVaultError(false)
        setVaultResults(emptyList())
        setVaultSearched(false)
        if (!completed) return@LaunchedEffect

        val activeScopes = coreState.scopes + SearchScope.Local
        val request = generation.begin(coreState.query, activeScopes, bindingKey)
        resultStore.start(request)
        vaultResultStore.start(request)
        setLoading(true)
        runCatching { repository.search(request.normalizedQuery, bindingKey) }
            .onSuccess { rows ->
                if (generation.accepts(request) && resultStore.accept(request, rows)) {
                    setResults(resultStore.results)
                    setLoading(false)
                }
            }
            .onFailure {
                if (generation.accepts(request)) {
                    setLoading(false)
                    setError(true)
                    reportError("SEARCH_UNAVAILABLE")
                }
            }
        if (SearchScope.Vault !in activeScopes || binding == null || !generation.accepts(request)) {
            return@LaunchedEffect
        }
        setVaultLoading(true)
        runCatching {
            AutPlayRuntime.serverFeatures(context, binding).searchLibrary(request.normalizedQuery)
        }.mapCatching { rows ->
            vaultProjector.project(binding.serverProfileId.value, rows)
        }.onSuccess { rows ->
            if (generation.accepts(request) && vaultResultStore.accept(request, rows)) {
                setVaultResults(vaultResultStore.results)
                setVaultSearched(true)
                setVaultLoading(false)
            }
        }.onFailure {
            if (generation.accepts(request)) {
                setVaultLoading(false)
                setVaultError(true)
                setVaultSearched(true)
            }
        }
    }
}
