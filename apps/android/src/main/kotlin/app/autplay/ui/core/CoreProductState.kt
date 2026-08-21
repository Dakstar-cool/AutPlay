package app.autplay.ui.core

import java.nio.charset.StandardCharsets
import java.util.Base64

public enum class SearchScope {
    Local,
    Vault,
}

public data class SearchRequestKey(
    public val generation: Long,
    public val normalizedQuery: String,
    public val scopes: Set<SearchScope>,
    public val bindingKey: String?,
)

public fun normalizeSearchQuery(rawQuery: String): String = rawQuery.trim().replace(Regex("\\s+"), " ")

/** Rejects results from an older query, scope selection, or account binding. */
public class SearchGenerationGuard(initialGeneration: Long = 0L) {
    private var nextGeneration: Long = initialGeneration
    private var active: SearchRequestKey? = null

    public fun begin(rawQuery: String, scopes: Set<SearchScope>, bindingKey: String?): SearchRequestKey {
        val key = SearchRequestKey(
            generation = ++nextGeneration,
            normalizedQuery = normalizeSearchQuery(rawQuery),
            scopes = scopes.toSet(),
            bindingKey = bindingKey,
        )
        active = key
        return key
    }

    public fun accepts(key: SearchRequestKey): Boolean = key == active

    public fun invalidate() {
        nextGeneration++
        active = null
    }
}

/** Keeps visible rows bound to exactly one immutable Search request key. */
public class SearchResultStore<T> {
    private var active: SearchRequestKey? = null
    public var results: List<T> = emptyList()
        private set

    public fun start(key: SearchRequestKey) {
        active = key
        results = emptyList()
    }

    public fun accept(key: SearchRequestKey, values: List<T>): Boolean {
        if (key != active) return false
        results = values.toList()
        return true
    }

    /** Suppresses rows synchronously when composition has already moved to another request context. */
    public fun visibleFor(rawQuery: String, scopes: Set<SearchScope>, bindingKey: String?): List<T> {
        return if (matchesContext(rawQuery, scopes, bindingKey)) results else emptyList()
    }

    public fun matchesContext(rawQuery: String, scopes: Set<SearchScope>, bindingKey: String?): Boolean =
        active?.let { key ->
            key.normalizedQuery == normalizeSearchQuery(rawQuery) &&
                key.scopes == scopes &&
                key.bindingKey == bindingKey
        } == true

    public fun invalidate() {
        active = null
        results = emptyList()
    }
}

public enum class LibrarySection {
    Tracks,
    Artists,
    Albums,
    Playlists,
    Offline,
    Unavailable,
    Review,
}

public enum class LibrarySort {
    RecentlyAdded,
    Title,
    Artist,
}

public enum class LibraryFilter {
    All,
    Loved,
    Downloaded,
    Available,
    Unavailable,
}

public enum class DetailKind {
    Track,
    Artist,
    Release,
    Playlist,
}

/** The id must be an identifier owned by an existing application projection, never display text. */
public data class DetailTarget(public val kind: DetailKind, public val stableId: String)

/** Stable row identity and viewport offset used to restore a list after its data reloads. */
public data class ListAnchor(
    public val contextKey: String,
    public val itemKey: String,
    public val scrollOffset: Int = 0,
)

public data class CoreProductSavedState(
    public val query: String = "",
    public val scopes: Set<SearchScope> = setOf(SearchScope.Local),
    public val librarySection: LibrarySection = LibrarySection.Tracks,
    public val librarySort: LibrarySort = LibrarySort.RecentlyAdded,
    public val libraryFilter: LibraryFilter = LibraryFilter.All,
    public val selectedDetail: DetailTarget? = null,
    public val searchListAnchor: ListAnchor? = null,
    public val libraryListAnchor: ListAnchor? = null,
) {
    public fun encode(): List<String> = listOf(
        STATE_VERSION,
        encodeText(query),
        scopes.sortedBy(SearchScope::ordinal).joinToString(",", transform = SearchScope::name),
        librarySection.name,
        librarySort.name,
        libraryFilter.name,
        selectedDetail?.kind?.name.orEmpty(),
        selectedDetail?.stableId?.let(::encodeText).orEmpty(),
        searchListAnchor?.encode().orEmpty(),
        libraryListAnchor?.encode().orEmpty(),
    )

    public companion object {
        private const val STATE_VERSION: String = "2"
        private const val LEGACY_STATE_VERSION: String = "1"

        public fun decode(values: List<String>): CoreProductSavedState? {
            if (values.firstOrNull() == LEGACY_STATE_VERSION) return decodeV1(values)
            if (values.size != 10 || values[0] != STATE_VERSION) return null
            return runCatching {
                val detailKind = values[6].takeIf(String::isNotEmpty)?.let(DetailKind::valueOf)
                val detailId = values[7].takeIf(String::isNotEmpty)?.let(::decodeText)
                if ((detailKind == null) != (detailId == null)) return null
                CoreProductSavedState(
                    query = decodeText(values[1]),
                    scopes = values[2].split(',').filter(String::isNotEmpty).map(SearchScope::valueOf)
                        .toSet().ifEmpty { setOf(SearchScope.Local) },
                    librarySection = LibrarySection.valueOf(values[3]),
                    librarySort = LibrarySort.valueOf(values[4]),
                    libraryFilter = LibraryFilter.valueOf(values[5]),
                    selectedDetail = detailKind?.let { DetailTarget(it, checkNotNull(detailId)) },
                    searchListAnchor = values[8].takeIf(String::isNotEmpty)?.let(::decodeListAnchor),
                    libraryListAnchor = values[9].takeIf(String::isNotEmpty)?.let(::decodeListAnchor),
                )
            }.getOrNull()
        }

        private fun decodeV1(values: List<String>): CoreProductSavedState? {
            if (values.size != 9) return null
            return runCatching {
                val detailKind = values[6].takeIf(String::isNotEmpty)?.let(DetailKind::valueOf)
                val detailId = values[7].takeIf(String::isNotEmpty)?.let(::decodeText)
                if ((detailKind == null) != (detailId == null)) return null
                CoreProductSavedState(
                    query = decodeText(values[1]),
                    scopes = values[2].split(',').filter(String::isNotEmpty).map(SearchScope::valueOf)
                        .toSet().ifEmpty { setOf(SearchScope.Local) },
                    librarySection = LibrarySection.valueOf(values[3]),
                    librarySort = LibrarySort.valueOf(values[4]),
                    libraryFilter = LibraryFilter.valueOf(values[5]),
                    selectedDetail = detailKind?.let { DetailTarget(it, checkNotNull(detailId)) },
                    libraryListAnchor = values[8].takeIf(String::isNotEmpty)?.let(::decodeText)?.let {
                        ListAnchor("legacy", it)
                    },
                )
            }.getOrNull()
        }

        private fun encodeText(value: String): String = Base64.getUrlEncoder().withoutPadding()
            .encodeToString(value.toByteArray(StandardCharsets.UTF_8))

        private fun decodeText(value: String): String = String(
            Base64.getUrlDecoder().decode(value),
            StandardCharsets.UTF_8,
        )
    }
}

private fun ListAnchor.encode(): String = listOf(
    contextKey,
    itemKey,
    scrollOffset.toString(),
).joinToString(":") { value ->
    Base64.getUrlEncoder().withoutPadding().encodeToString(value.toByteArray(StandardCharsets.UTF_8))
}

private fun decodeListAnchor(encoded: String): ListAnchor {
    val values = encoded.split(':')
    require(values.size == 3)
    fun decodePart(value: String): String = String(
        Base64.getUrlDecoder().decode(value),
        StandardCharsets.UTF_8,
    )
    return ListAnchor(
        contextKey = decodePart(values[0]),
        itemKey = decodePart(values[1]),
        scrollOffset = decodePart(values[2]).toInt(),
    )
}

public enum class TrackAvailability {
    Available,
    PermissionRevoked,
    Missing,
    MetadataOnly,
}

public data class CoreTrackSummary(
    public val stableId: String,
    public val title: String,
    public val artist: String?,
    public val addedAtMs: Long,
    public val sourceOrder: Int,
    public val loved: Boolean,
    public val downloaded: Boolean,
    public val availability: TrackAvailability,
)

public fun filterAndSortTracks(
    tracks: List<CoreTrackSummary>,
    filter: LibraryFilter,
    sort: LibrarySort,
): List<CoreTrackSummary> {
    val filtered = tracks.filter { track ->
        when (filter) {
            LibraryFilter.All -> true
            LibraryFilter.Loved -> track.loved
            LibraryFilter.Downloaded -> track.downloaded
            LibraryFilter.Available -> track.availability == TrackAvailability.Available
            LibraryFilter.Unavailable -> track.availability != TrackAvailability.Available
        }
    }
    val stableTieBreak = compareBy<CoreTrackSummary> { it.sourceOrder }.thenBy { it.stableId }
    return when (sort) {
        LibrarySort.RecentlyAdded -> filtered.sortedWith(
            compareByDescending<CoreTrackSummary> { it.addedAtMs }.then(stableTieBreak),
        )
        LibrarySort.Title -> filtered.sortedWith(
            compareBy<CoreTrackSummary, String>(String.CASE_INSENSITIVE_ORDER) { it.title }.then(stableTieBreak),
        )
        LibrarySort.Artist -> filtered.sortedWith(
            compareBy<CoreTrackSummary, String>(String.CASE_INSENSITIVE_ORDER) { it.artist.orEmpty() }
                .thenBy(String.CASE_INSENSITIVE_ORDER) { it.title }
                .then(stableTieBreak),
        )
    }
}

public data class PlaylistOccurrence(
    public val playlistEntryId: String,
    public val trackRefId: String,
    public val positionKey: String,
)

/** Orders occurrences without coalescing duplicate track references. */
public fun orderedPlaylistOccurrences(items: List<PlaylistOccurrence>): List<PlaylistOccurrence> =
    items.sortedWith(compareBy(PlaylistOccurrence::positionKey, PlaylistOccurrence::playlistEntryId))

/** Prevents duplicate dispatch while the same logical action is already in flight. */
public class SingleFlightActionGate(private val maxInFlight: Int = 32) {
    private val inFlight: MutableSet<String> = linkedSetOf()

    init {
        require(maxInFlight in 1..256)
    }

    public fun begin(actionKey: String): Boolean {
        require(actionKey.isNotBlank())
        if (actionKey in inFlight || inFlight.size >= maxInFlight) return false
        inFlight += actionKey
        return true
    }

    public fun complete(actionKey: String) {
        inFlight -= actionKey
    }
}
