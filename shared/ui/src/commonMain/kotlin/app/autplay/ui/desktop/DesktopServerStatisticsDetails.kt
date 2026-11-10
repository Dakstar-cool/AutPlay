package app.autplay.ui.desktop

/** Descriptive server statistics only. A label is never a catalog identity or playback target. */
public class DesktopServerStatisticsEntry(
    public val label: String?,
    public val secondary: String?,
    public val sessions: String?,
    public val playedTime: String,
) {
    override fun toString(): String = "DesktopServerStatisticsEntry(<redacted>)"
}

public class DesktopServerStatisticsDetails(
    genres: List<DesktopServerStatisticsEntry>,
    tracks: List<DesktopServerStatisticsEntry>,
    artists: List<DesktopServerStatisticsEntry>,
) {
    public val genres: List<DesktopServerStatisticsEntry> = genres.toList()
    public val tracks: List<DesktopServerStatisticsEntry> = tracks.toList()
    public val artists: List<DesktopServerStatisticsEntry> = artists.toList()
    init { require(genres.size <= 5 && tracks.size <= 5 && artists.size <= 5) }
    override fun toString(): String = "DesktopServerStatisticsDetails(<redacted>)"
}
