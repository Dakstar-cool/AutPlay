package app.autplay.ui.core

import androidx.compose.runtime.Composable
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue

/** Saveable interaction state only; derived lists remain owned by bounded application queries. */
@Stable
public class CoreProductUiState internal constructor(initial: CoreProductSavedState) {
    public var query: String by mutableStateOf(initial.query)
    public var scopes: Set<SearchScope> by mutableStateOf(initial.scopes)
    public var librarySection: LibrarySection by mutableStateOf(initial.librarySection)
    public var librarySort: LibrarySort by mutableStateOf(initial.librarySort)
    public var libraryFilter: LibraryFilter by mutableStateOf(initial.libraryFilter)
    public var selectedDetail: DetailTarget? by mutableStateOf(initial.selectedDetail)
    public var searchListAnchor: ListAnchor? by mutableStateOf(initial.searchListAnchor)
    public var libraryListAnchor: ListAnchor? by mutableStateOf(initial.libraryListAnchor)

    public fun selectDetail(target: DetailTarget) {
        selectedDetail = target
    }

    public fun clearDetail() {
        selectedDetail = null
    }

    public fun snapshot(): CoreProductSavedState = CoreProductSavedState(
        query = query,
        scopes = scopes,
        librarySection = librarySection,
        librarySort = librarySort,
        libraryFilter = libraryFilter,
        selectedDetail = selectedDetail,
        searchListAnchor = searchListAnchor,
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
) { CoreProductUiState(CoreProductSavedState()) }
