package app.autplay.application.desktop

object DesktopHistoryLimits {
    const val PAGE_SIZE: Int = 100
    const val PREVIEW_SIZE: Int = 100
    const val MAX_VISIBLE_EVENTS: Int = 1_000
}

/** Keyset order is startedAtMs descending, then event ID ascending. No media capability. */
data class DesktopHistoryCursor(val startedAtMs: Long, val eventId: String)

data class DesktopHistoryPage(
    val events: List<DesktopListeningEvent>,
    val nextCursor: DesktopHistoryCursor?,
)

/** Host-owned read window; older pages never create or modify listening events. */
data class DesktopHistoryState(
    val events: List<DesktopListeningEvent> = emptyList(),
    val nextCursor: DesktopHistoryCursor? = null,
    val loading: Boolean = false,
    val initialized: Boolean = false,
    val errorCode: String? = null,
    val revision: Long = 0,
    /** Explicit loading can advance beyond the bounded window; Refresh returns to the newest page. */
    val newerItemsHidden: Boolean = false,
)
