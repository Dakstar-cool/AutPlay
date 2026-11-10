package app.autplay.application.desktop

object DesktopLocalStatisticsLimits {
    const val TOP_SIZE: Int = 5
    /** Same UTF-16 label bound as desktop metadata commands. */
    const val MAX_LABEL_LENGTH: Int = 4_096
}

/**
 * Explicit snapshot of all retained, locally saved desktop listens started at or before the cutoff.
 * Positive saved active checkpoints and taste-excluded listens count once per listening row.
 * This is neither account statistics nor a portable aggregate; unsaved playback is unavailable.
 * There is no genre ranking because desktop has no authoritative genre metadata.
 */
data class DesktopLocalStatistics(
    val throughInclusiveMs: Long,
    val listenedMs: Long,
    val playSessionCount: Long,
    val topTracks: List<DesktopLocalTopTrack>,
    val topArtistLabels: List<DesktopLocalTopArtistLabel>,
) {
    init {
        require(throughInclusiveMs >= 0 && listenedMs >= 0 && playSessionCount >= 0)
        require(topTracks.size <= DesktopLocalStatisticsLimits.TOP_SIZE)
        require(topArtistLabels.size <= DesktopLocalStatisticsLimits.TOP_SIZE)
    }
}

data class DesktopLocalTopTrack(
    /** Exact local track ID, never a title, hash, alias or inferred Recording identity. */
    val trackId: String,
    val title: String,
    val artist: String,
    val playSessionCount: Long,
    val listenedMs: Long,
)

/** Trimmed existing raw artist display labels; equal labels do not establish an artist identity. */
data class DesktopLocalTopArtistLabel(
    val artistLabel: String,
    val playSessionCount: Long,
    val listenedMs: Long,
)
