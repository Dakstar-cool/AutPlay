package app.autplay.ui.desktop

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

internal enum class DesktopCatalogText(val ru: String, val en: String) {
    Search("Поиск по выбранному виду", "Search the selected kind"),
    Apply("Искать", "Search"), Clear("Очистить запрос", "Clear query"),
    QueryHelp("До 256 символов, 8 слов и 48 символов в слове; превышение отклоняется целиком. Общие списки исполнителей и альбомов используют полный поиск: слово от 2 символов ищется по началу, односимвольное — целиком. Остальные виды фильтруют читаемую страницу; пустая страница с «Далее» не означает конец.",
        "Up to 256 characters, 8 words and 48 characters per word; over-limit input is rejected in full. Top-level Artists/Albums use full search: prefixes from 2 characters, exact single-character words. Other views filter the scanned page; an empty page with Next is not the end."),
    MoreScan("На этой странице совпадений нет. Продолжите чтение следующей страницы.", "No matches on this page. Continue to the next page."),
    First("К первой странице", "Back to first page"),
    Browse("Открыть список", "Browse list"),
    Preview("Ограниченный просмотр по порядку ID; полный список открывается отдельно.", "Bounded preview in ID order; open the list to browse further."),
    NoTitle("Название не получено", "Title not supplied"),
    CreditOnly("Подтверждена проекция credits. Название, дата и порядок позиций канонического релиза не получены.",
        "This is a confirmed credit projection. The canonical release title, date and track sequence have not been supplied."),
    Availability("Доступ к аудио проверяется отдельно при воспроизведении или загрузке.", "Audio access is checked separately when playing or downloading."),
    Partial("Показана ограниченная часть контекста доступности.", "Only a bounded part of the availability context is shown."),
    AlbumPage("Общий список объединяет одинаковые подтверждённые ключи из всех страниц проекции. Противоречивые метаданные делают список недоступным. Порядок позиций релиза не установлен.",
        "The top-level list deduplicates validated edition keys across every projection page. Conflicting metadata makes the list unavailable. Release track order is not established."),
}

internal fun DesktopCatalogText.text(language: DesktopLanguage): String = if (language == DesktopLanguage.Russian) ru else en

internal fun desktopCatalogRelationText(relation: DesktopCatalogRelation, language: DesktopLanguage): String {
    val pair = when (relation) {
        DesktopCatalogRelation.ArtistAppearances -> "Участие исполнителя в credits записей и релизов" to "Artist appearances in Recording and Release credits"
        DesktopCatalogRelation.PlaylistOccurrences -> "Вхождения плейлиста в сохранённом порядке; повторные треки остаются отдельными" to "Playlist occurrences in stored order; repeated tracks remain distinct"
        DesktopCatalogRelation.MetadataAlbumTracks -> "Треки с тем же подтверждённым ключом группы метаданных" to "Tracks with the same validated metadata group key"
        DesktopCatalogRelation.RecordingReferences -> "Ссылки аккаунта на эту запись; это не позиции релиза" to "Account references to this Recording; these are not release positions"
        DesktopCatalogRelation.ReleaseCredits -> "Credits релиза; текущие канонические позиции показаны отдельным блоком, когда доступны" to "Release credits; current canonical positions appear in a separate block when available"
    }
    return if (language == DesktopLanguage.Russian) pair.first else pair.second
}

@Composable
internal fun DesktopAccountCatalogSearch(owner: DesktopAccountUiOwner, language: DesktopLanguage): Unit {
    val state = owner.state.catalog
    val target = state.searchTargetId ?: return
    // Leaving this surface destroys the draft. Submitted queries live only in the host page owner.
    var draft by remember(owner.state.scope.lifetime, state.selectedKind, state.query) { mutableStateOf(state.query) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedTextField(value = draft, onValueChange = {
            if (owner.available(DesktopAccountAction.CatalogPage, target)) draft = it.take(256)
        }, label = { Text(DesktopCatalogText.Search.text(language)) }, singleLine = true,
            enabled = owner.available(DesktopAccountAction.CatalogPage, target), modifier = Modifier.fillMaxWidth())
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(enabled = owner.available(DesktopAccountAction.CatalogPage, target), onClick = {
                owner.send(DesktopAccountCommand.CatalogSearch(target, draft))
            }) { Text(DesktopCatalogText.Apply.text(language)) }
            OutlinedButton(enabled = owner.available(DesktopAccountAction.CatalogPage, target), onClick = {
                owner.send(DesktopAccountCommand.CatalogSearch(target, ""))
            }) { Text(DesktopCatalogText.Clear.text(language)) }
        }
        Text(DesktopCatalogText.QueryHelp.text(language))
    }
}

internal fun desktopCatalogFieldText(field: DesktopCatalogField, language: DesktopLanguage): String {
    val pair = when (field) {
        DesktopCatalogField.StableId -> "Стабильный ID" to "Stable ID"
        DesktopCatalogField.SortName -> "Имя для сортировки" to "Sort name"
        DesktopCatalogField.ArtistType -> "Тип исполнителя" to "Artist type"
        DesktopCatalogField.Country -> "Страна" to "Country"
        DesktopCatalogField.IdentityStatus -> "Статус идентичности" to "Identity status"
        DesktopCatalogField.Provider -> "Источник метаданных" to "Metadata provider"
        DesktopCatalogField.ProviderEdition -> "ID издания у источника" to "Provider edition ID"
        DesktopCatalogField.ReleaseDate -> "Дата по метаданным" to "Metadata date"
        DesktopCatalogField.DiscNumber -> "Диск по метаданным трека" to "Track metadata disc number"
        DesktopCatalogField.TrackNumber -> "Номер по метаданным трека" to "Track metadata number"
        DesktopCatalogField.RecordingId -> "ID записи" to "Recording ID"
        DesktopCatalogField.DurationMs -> "Длительность, мс" to "Duration, ms"
        DesktopCatalogField.MetadataState -> "Статус метаданных" to "Metadata state"
        DesktopCatalogField.Source -> "Источник библиотечной ссылки" to "Library reference source"
        DesktopCatalogField.Availability -> "Статус библиотечной ссылки" to "Library reference availability"
        DesktopCatalogField.OccurrenceId -> "ID вхождения в плейлист" to "Playlist occurrence ID"
        DesktopCatalogField.CreditRole -> "Роль в credits" to "Credit role"
        DesktopCatalogField.ProjectionState -> "Статус проекции" to "Projection state"
        DesktopCatalogField.Preference -> "Оценка аккаунта" to "Account preference"
        DesktopCatalogField.TasteExclusion -> "Учёт во вкусе" to "Taste participation"
        DesktopCatalogField.CanonicalDate -> "Дата канонического релиза" to "Canonical release date"
        DesktopCatalogField.DatePrecision -> "Точность даты" to "Date precision"
        DesktopCatalogField.VersionText -> "Версия записи" to "Recording version"
        DesktopCatalogField.Disambiguation -> "Уточнение" to "Disambiguation"
        DesktopCatalogField.RecordingKind -> "Тип записи" to "Recording kind"
        DesktopCatalogField.ReleaseStatus -> "Статус релиза" to "Release status"
        DesktopCatalogField.RowVersion -> "Версия строки" to "Row version"
    }
    return if (language == DesktopLanguage.Russian) pair.first else pair.second
}
