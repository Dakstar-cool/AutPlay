package app.autplay.ui.core

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import app.autplay.application.search.LibrarySearchKind

/** Saveable interaction state only; derived lists remain owned by bounded application queries. */
@Stable
public class CoreProductUiState internal constructor(initial: CoreProductSavedState) {
    public var query: String by mutableStateOf(initial.query)
    public var searchKind: LibrarySearchKind by mutableStateOf(initial.searchKind)
    public var scopes: Set<SearchScope> by mutableStateOf(initial.scopes)
    public var librarySection: LibrarySection by mutableStateOf(initial.librarySection.browseSection())
    public var librarySort: LibrarySort by mutableStateOf(initial.librarySort)
    public var libraryFilter: LibraryFilter by mutableStateOf(initial.libraryFilter.browseFilter())
    private var pendingDownloadsRedirect: Boolean by mutableStateOf(initial.librarySection == LibrarySection.Offline)
    private var detail: DetailTarget? by mutableStateOf(initial.selectedDetail.collectionDetail())
    public var selectedDetail: DetailTarget?
        get() = detail
        set(value) { detail = value.collectionDetail() }
    public var searchListAnchor: ListAnchor? by mutableStateOf(initial.searchListAnchor)
    public var libraryListAnchor: ListAnchor? by mutableStateOf(initial.libraryListAnchor)

    public fun selectDetail(target: DetailTarget) {
        if (target.kind != DetailKind.Track) {
            pendingDownloadsRedirect = false
            selectedDetail = target
        }
    }

    /** Retired saved Offline navigation opens Downloads once while ordinary browse stays unfiltered. */
    public fun consumeLegacyDownloadsRedirect(): Boolean {
        val pending = pendingDownloadsRedirect
        pendingDownloadsRedirect = false
        return pending
    }

    public fun clearDetail() {
        selectedDetail = null
    }

    public fun snapshot(): CoreProductSavedState = CoreProductSavedState(
        query = query,
        scopes = scopes,
        librarySection = if (pendingDownloadsRedirect) LibrarySection.Offline else librarySection,
        librarySort = librarySort,
        libraryFilter = libraryFilter,
        selectedDetail = selectedDetail,
        searchListAnchor = searchListAnchor,
        searchKind = searchKind,
        libraryListAnchor = libraryListAnchor,
    )

    public companion object {
        public val Saver: Saver<CoreProductUiState, List<String>> = Saver(
            save = { it.snapshot().encode() },
            restore = { values -> CoreProductSavedState.decode(values)?.let(::CoreProductUiState) },
        )
    }
}

@Composable
public fun rememberCoreProductUiState(bindingKey: String?): CoreProductUiState = rememberSaveable(
    bindingKey,
    saver = CoreProductUiState.Saver,
) { CoreProductUiState(CoreProductSavedState(
    scopes = automaticSearchScopes(bindingKey),
)) }
