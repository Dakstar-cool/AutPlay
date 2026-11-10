package app.autplay.ui.desktop

internal const val DESKTOP_BROWSE_PAGE_SIZE: Int = 100

/** One composition lifetime; retirement cannot be reversed by a matching old snapshot/owner. */
internal class DesktopBrowseLifetime {
    var isActive: Boolean = true
        private set

    fun retire(): Unit { isActive = false }

    fun matches(captured: Any?, current: Any?): Boolean = isActive && captured != null && captured === current
}

/** Non-composable factory: compiler lambda memoization must not retain an equal older owner. */
internal fun desktopBrowseIdentityGuard(
    lifetime: DesktopBrowseLifetime,
    captured: Any?,
    latest: () -> Any?,
    isNavigationCurrent: () -> Boolean,
): () -> Boolean = { lifetime.matches(captured, latest()) && isNavigationCurrent() }

/** Zero-based display page; end is exclusive, including for an empty result. */
internal data class DesktopBrowsePage(
    val index: Int,
    val pages: Int,
    val start: Int,
    val end: Int,
    val total: Int,
)

/** Display paging over an in-memory result, independent of queue admission and storage. */
internal fun desktopBrowsePage(total: Int, requestedPage: Int): DesktopBrowsePage {
    val count = total.coerceAtLeast(0)
    val pages = if (count == 0) 1 else (count - 1) / DESKTOP_BROWSE_PAGE_SIZE + 1
    val index = requestedPage.coerceIn(0, pages - 1)
    val start = index * DESKTOP_BROWSE_PAGE_SIZE
    val end = start + minOf(DESKTOP_BROWSE_PAGE_SIZE, count - start)
    return DesktopBrowsePage(index, pages, start, end, count)
}

/** Re-clamp against this result and copy at most one page, without retaining a mutable sublist. */
internal fun <T> desktopBrowseSlice(items: List<T>, page: DesktopBrowsePage): List<T> {
    val bounded = desktopBrowsePage(items.size, page.index)
    return items.subList(bounded.start, bounded.end).toList()
}
