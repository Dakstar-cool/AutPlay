package app.autplay.ui

import androidx.annotation.StringRes
import app.autplay.R

/**
 * Stable application destinations. Screens own data loading; this model only describes navigation.
 *
 * The set intentionally mirrors the delivered Android surfaces through P13 and the P14 release
 * operations, so the shell does not invent a server-only feature or make a local action depend on
 * connectivity.
 */
public sealed class UiDestination(
    public val route: String,
    @StringRes public val labelRes: Int,
    public val icon: AutPlayIcon,
) {
    public data object Home : UiDestination("home", R.string.nav_home, AutPlayIcon.Home)
    public data object Library : UiDestination("library", R.string.nav_library, AutPlayIcon.Library)
    public data object Search : UiDestination("search", R.string.nav_search, AutPlayIcon.Search)
    public data object Playlists : UiDestination("playlists", R.string.nav_playlists, AutPlayIcon.Playlist)
    public data object History : UiDestination("history", R.string.nav_history, AutPlayIcon.History)
    public data object Downloads : UiDestination("downloads", R.string.nav_downloads, AutPlayIcon.Download)
    public data object ImportReview : UiDestination("import-review", R.string.nav_import_review, AutPlayIcon.Import)
    public data object NowPlaying : UiDestination("now-playing", R.string.nav_now_playing, AutPlayIcon.Play)
    public data object WaveRooms : UiDestination("wave-rooms", R.string.nav_wave_rooms, AutPlayIcon.Wave)
    public data object SyncStatus : UiDestination("sync-status", R.string.nav_sync_status, AutPlayIcon.Sync)
    public data object ServerFeatures : UiDestination("server-features", R.string.nav_server_features, AutPlayIcon.Server)
    public data object Profile : UiDestination("profile", R.string.nav_profile, AutPlayIcon.Profile)
    public data object Settings : UiDestination("settings", R.string.nav_settings, AutPlayIcon.Settings)
    public data object PrivacyAndData : UiDestination("privacy-data", R.string.nav_privacy_data, AutPlayIcon.Privacy)

    public companion object {
        /** Exactly the three primary destinations required by the accepted compact contract. */
        public val compactNavigation: List<UiDestination>
            get() = listOf(Home, Search, Library)

        /** Product destinations in rail order; Profile and Settings remain top-chrome actions. */
        public val railNavigation: List<UiDestination>
            get() = listOf(
                Home,
                Search,
                Library,
                Playlists,
                Downloads,
                History,
                ImportReview,
                WaveRooms,
                SyncStatus,
                ServerFeatures,
            )

        /** Secondary routes that must remain deterministically reachable on compact layouts. */
        public val secondaryNavigation: List<UiDestination>
            get() = listOf(
                Playlists,
                Downloads,
                History,
                ImportReview,
                WaveRooms,
                SyncStatus,
                ServerFeatures,
                PrivacyAndData,
            )

        public val all: List<UiDestination>
            get() = listOf(
                Home, Search, Library, Playlists, History, Downloads, ImportReview,
                NowPlaying, WaveRooms, SyncStatus, ServerFeatures, Profile, Settings,
                PrivacyAndData,
            )

        public fun fromRoute(route: String): UiDestination? = all.firstOrNull { it.route == route }
    }
}

/** Small platform-icon vocabulary; it avoids a runtime icon/CDN dependency. */
public enum class AutPlayIcon {
    Home,
    Search,
    Library,
    Playlist,
    History,
    Download,
    Import,
    Play,
    Wave,
    Sync,
    Server,
    Profile,
    Settings,
    Privacy,
    Back,
    Pause,
    Previous,
    Next,
    Shuffle,
    Repeat,
    Check,
    Favorite,
}

/** Width categories based on the Android adaptive-layout guidance breakpoints. */
public enum class UiWidthClass {
    Compact,
    Medium,
    Expanded,
}
