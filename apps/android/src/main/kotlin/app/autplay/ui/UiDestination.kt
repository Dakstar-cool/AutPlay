package app.autplay.ui

/**
 * Stable application destinations. Screens own data loading; this model only describes navigation.
 *
 * The set intentionally mirrors the delivered Android surfaces through P13 and the P14 release
 * operations, so the shell does not invent a server-only feature or make a local action depend on
 * connectivity.
 */
public sealed class UiDestination(
    public val route: String,
    public val label: String,
    public val glyph: String,
) {
    public data object Home : UiDestination("home", "Home", "⌂")
    public data object Library : UiDestination("library", "Library", "♫")
    public data object Search : UiDestination("search", "Search", "⌕")
    public data object Playlists : UiDestination("playlists", "Playlists", "≡")
    public data object History : UiDestination("history", "History", "◷")
    public data object Downloads : UiDestination("downloads", "Downloads", "⇩")
    public data object ImportReview : UiDestination("import-review", "Import review", "⇪")
    public data object NowPlaying : UiDestination("now-playing", "Now playing", "▶")
    public data object WaveRooms : UiDestination("wave-rooms", "Listen together", "〰")
    public data object SyncStatus : UiDestination("sync-status", "Sync status", "↻")
    public data object ServerFeatures : UiDestination("server-features", "Personal server", "◎")
    public data object Profile : UiDestination("profile", "Profile", "●")
    public data object Settings : UiDestination("settings", "More", "•••")
    public data object PrivacyAndData : UiDestination("privacy-data", "Privacy and data", "⌑")

    public companion object {
        /** Primary destinations kept reachable in compact bottom navigation. */
        public val compactNavigation: List<UiDestination>
            get() = listOf(Home, Search, Library, WaveRooms, Settings)

        /** Full rail order for tablets, foldables and landscape phones. */
        public val railNavigation: List<UiDestination>
            get() = listOf(
                Home,
                Library,
                Search,
                Playlists,
                Downloads,
                History,
                ImportReview,
                WaveRooms,
                SyncStatus,
                ServerFeatures,
                Settings,
            )
    }
}

/** Width categories based on the Android adaptive-layout guidance breakpoints. */
public enum class UiWidthClass {
    Compact,
    Medium,
    Expanded,
}
