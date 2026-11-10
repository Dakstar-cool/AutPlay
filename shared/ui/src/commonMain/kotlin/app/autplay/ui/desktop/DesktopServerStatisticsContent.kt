package app.autplay.ui.desktop

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.key
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@Composable
internal fun DesktopServerStatisticsContent(details: DesktopServerStatisticsDetails,
    language: DesktopLanguage): Unit {
    Text(if (language == DesktopLanguage.Russian) "Срез текущего сервера" else "Current server sample")
    ServerStatisticsSection(if (language == DesktopLanguage.Russian) "Жанры: топ-5" else "Top 5 genres", details.genres, language)
    ServerStatisticsSection(if (language == DesktopLanguage.Russian) "Треки: топ-5" else "Top 5 tracks", details.tracks, language)
    ServerStatisticsSection(if (language == DesktopLanguage.Russian) "Исполнители: топ-5" else "Top 5 artists", details.artists, language)
}

@Composable
private fun ServerStatisticsSection(title: String, rows: List<DesktopServerStatisticsEntry>,
    language: DesktopLanguage): Unit {
    Text(title, style = MaterialTheme.typography.titleSmall)
    if (rows.isEmpty()) Text(if (language == DesktopLanguage.Russian) "Нет данных" else "No data")
    rows.forEachIndexed { index, row -> key(index) {
        Column(Modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Text(row.label ?: DesktopAccountText.Unconfirmed.text(language), style = MaterialTheme.typography.bodyLarge)
            row.secondary?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
            row.sessions?.let { count -> Text(if (language == DesktopLanguage.Russian) "Сессий: $count" else "Sessions: $count") }
            AccountDecisionValue(DesktopAccountText.PlayedTime, row.playedTime, language)
        }
    } }
}
