package app.autplay.ui.desktop

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.key
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

/** Bounded C1 presentation. Stable catalog positions carry no playback/media authority. */
@Composable
internal fun DesktopCanonicalPositionsContent(owner: DesktopAccountUiOwner, view: DesktopCanonicalPositionsView,
    language: DesktopLanguage): Unit {
    val ru = language == DesktopLanguage.Russian
    AccountCard(DesktopAccountText.Catalog, language) {
        Text(if (ru) "Канонические позиции" else "Canonical positions", style = MaterialTheme.typography.titleMedium)
        Text(if (ru) "Показаны только позиции, доступные текущему владельцу. Это часть релиза; отсутствующие позиции не означают полный состав."
            else "Only positions visible to the current owner are shown. This is a release subset; missing positions do not establish the full track list.")
        AccountStatus(view.condition, DesktopAccountIssue.None, language)
        view.notice?.let { Text(it) }
        if (view.entries.isEmpty() && view.nextTarget != null) Text(DesktopCatalogText.MoreScan.text(language))
        view.entries.forEach { row -> key(row.id) {
            Surface(shape = MaterialTheme.shapes.medium) {
                Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(row.releaseTitle, style = MaterialTheme.typography.titleSmall)
                    Text("${row.mediumPosition}.${row.sequence}  ${row.title}")
                    row.number?.let { Text((if (ru) "Обозначение позиции: " else "Position label: ") + it) }
                    row.durationMs?.let { Text((if (ru) "Длительность, мс: " else "Duration, ms: ") + it) }
                    row.releaseDate?.let { Text((if (ru) "Дата релиза: " else "Release date: ") + it) }
                    row.datePrecision?.let { Text((if (ru) "Точность даты: " else "Date precision: ") + it) }
                    Text("Release: ${row.releaseId}")
                    Text("Medium: ${row.mediumId}")
                    Text("ReleaseTrack: ${row.id}")
                    Text("Recording: ${row.recordingId}")
                    row.credits.forEach { credit -> key(credit.position, credit.artistId) {
                        Text(credit.name + credit.joinPhrase)
                        Text(desktopCatalogFieldText(DesktopCatalogField.CreditRole, language) + ": " + credit.role)
                    } }
                    AccountButton(owner, DesktopAccountAction.OpenRelease, language, row.releaseTarget,
                        label = if (ru) "Открыть релиз" else "Open Release")
                    AccountButton(owner, DesktopAccountAction.OpenRecording, language, row.recordingTarget,
                        label = if (ru) "Открыть запись" else "Open Recording")
                }
            }
        } }
        view.previousTarget?.let { AccountButton(owner, DesktopAccountAction.CatalogPage, language, it,
            label = DesktopCatalogText.First.text(language)) }
        view.nextTarget?.let { AccountButton(owner, DesktopAccountAction.CatalogPage, language, it,
            label = DesktopAccountText.Next.text(language)) }
        if (view.exhausted && view.condition != DesktopAccountCondition.Unavailable) Text(if (ru)
            "Достигнут конец текущего вида владельца. Полнота релиза этим не подтверждается."
            else "The end of this owner's current view was reached. This does not establish a complete release.")
    }
}
