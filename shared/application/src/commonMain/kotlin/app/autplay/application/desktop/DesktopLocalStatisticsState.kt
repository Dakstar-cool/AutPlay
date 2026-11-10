package app.autplay.application.desktop

/** A manually refreshed view of saved history on this device, without account authority. */
public class DesktopLocalStatisticsState(
    public val snapshot: DesktopLocalStatistics? = null,
    public val loading: Boolean = false,
    public val errorCode: String? = null,
    public val revision: Long = 0,
)
