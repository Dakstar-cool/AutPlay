package app.autplay.desktop.presentation

import app.autplay.desktop.sync.DesktopServerStatistics
import app.autplay.ui.desktop.DesktopLanguage
import app.autplay.ui.desktop.DesktopServerStatisticsDetails
import app.autplay.ui.desktop.DesktopServerStatisticsEntry
import java.text.NumberFormat
import java.util.Locale

/** Formatting never combines the server sample with the deduplicated portable-account total. */
internal fun desktopServerStatisticsDetails(value: DesktopServerStatistics,
    language: DesktopLanguage): DesktopServerStatisticsDetails {
    require(value.genres.size <= 5 && value.tracks.size <= 5 && value.artists.size <= 5)
    val numbers = NumberFormat.getIntegerInstance(if (language == DesktopLanguage.Russian) Locale.forLanguageTag("ru-RU") else Locale.ENGLISH)
    fun duration(milliseconds: Long): String {
        require(milliseconds >= 0)
        val seconds = milliseconds / 1000
        val hours = numbers.format(seconds / 3600)
        val minutes = numbers.format(seconds / 60 % 60)
        val remainder = numbers.format(seconds % 60)
        return if (language == DesktopLanguage.Russian) "$hours \u0447 $minutes \u043c\u0438\u043d $remainder \u0441"
            else "$hours h $minutes min $remainder s"
    }
    fun sessions(count: Long): String { require(count >= 0); return numbers.format(count) }
    return DesktopServerStatisticsDetails(
        value.genres.map { DesktopServerStatisticsEntry(it.name, null, null, duration(it.listenedMs)) },
        value.tracks.map { DesktopServerStatisticsEntry(it.title, it.artist, sessions(it.sessions), duration(it.listenedMs)) },
        value.artists.map { DesktopServerStatisticsEntry(it.name, null, sessions(it.sessions), duration(it.listenedMs)) },
    )
}
