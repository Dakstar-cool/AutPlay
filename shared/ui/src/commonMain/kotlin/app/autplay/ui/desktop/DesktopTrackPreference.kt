package app.autplay.ui.desktop

import app.autplay.application.desktop.DesktopCopyInspection
import app.autplay.application.desktop.DesktopTrack

/** Known persisted values only. Unknown values have no selected choice and are never normalized. */
internal enum class DesktopTrackPreference(val storedValue: String) {
    NEUTRAL("NEUTRAL"), LIKED("LIKED"), DISLIKED("DISLIKED");

    fun label(language: DesktopLanguage): String = when (this) {
        NEUTRAL -> DesktopText.TrackPreferenceNeutral
        LIKED -> DesktopText.TrackPreferenceLiked
        DISLIKED -> DesktopText.TrackPreferenceDisliked
    }.inLanguage(language)
}

internal fun desktopTrackPreference(value: String): DesktopTrackPreference? =
    DesktopTrackPreference.entries.firstOrNull { it.storedValue == value }

/** Process-local library view state; ALL includes future persisted values. */
internal enum class DesktopTrackPreferenceFilter {
    ALL, LIKED, DISLIKED, NEUTRAL;

    fun matches(value: String): Boolean = when (this) {
        ALL -> true
        LIKED -> value == "LIKED"
        DISLIKED -> value == "DISLIKED"
        NEUTRAL -> value == "NEUTRAL"
    }

    fun label(language: DesktopLanguage): String = when (this) {
        ALL -> DesktopText.TrackPreferenceAll.inLanguage(language)
        LIKED -> DesktopTrackPreference.LIKED.label(language)
        DISLIKED -> DesktopTrackPreference.DISLIKED.label(language)
        NEUTRAL -> DesktopTrackPreference.NEUTRAL.label(language)
    }
}

/** Never renders the raw persisted value, including hidden controls or unsupported future strings. */
internal fun desktopTrackPreferenceStatus(value: String, language: DesktopLanguage): String =
    desktopTrackPreference(value)?.label(language) ?: DesktopText.TrackPreferenceUnknown.inLanguage(language)

/** Filters the entire snapshot before search, stable ordering, the display cap and playback IDs. */
internal fun prepareDesktopLibraryMatches(
    tracks: List<DesktopTrack>,
    query: String,
    filter: DesktopTrackPreferenceFilter,
    sort: DesktopLibrarySort,
    direction: DesktopSortDirection,
    collectionFilter: DesktopTrackCollectionFilter = DesktopTrackCollectionFilter.ACTIVE,
    copyFilter: DesktopCopyAvailabilityFilter = DesktopCopyAvailabilityFilter.ALL,
    copyResults: Map<String, DesktopCopyInspection> = emptyMap(),
    checkpoint: () -> Unit = {},
): List<DesktopTrack> {
    checkpoint()
    val candidates = mutableListOf<DesktopTrack>()
    val boundedQuery = desktopSearchQuery(query)
    tracks.forEachIndexed { index, track ->
        if (index % 256 == 0) checkpoint()
        if (collectionFilter.matches(track.collectionState) && filter.matches(track.preference) &&
            copyFilter.matches(desktopCopyAvailability(track, copyResults)) &&
            (boundedQuery.isBlank() || track.title.contains(boundedQuery, true) || track.artist.contains(boundedQuery, true))) {
            candidates += track
        }
    }
    return sortDesktopLibraryTracks(candidates, sort, direction, checkpoint)
}

/** Metadata writes need a supplied fresh library and live selection, independently of audio. */
internal fun desktopTrackPreferenceCanWrite(
    input: DesktopTrackInput,
    prepared: DesktopTrackView,
    reopenRequired: Boolean,
): Boolean = !reopenRequired && input.library != null && input.selectedId != null &&
    prepared.input == input && prepared.selectedTrack?.id == input.selectedId && input.isSelectionCurrent()
