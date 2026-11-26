package app.autplay.desktop.presentation

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalUriHandler
import app.autplay.application.desktop.account.AccountLease
import app.autplay.application.desktop.account.AccountUuid
import app.autplay.application.server.AlbumEditionChoice
import app.autplay.application.server.AlbumTracklist
import kotlinx.coroutines.CancellationException

@Composable
internal fun DesktopAlbumTracklist(service: DesktopOnlineMusic, lease: AccountLease, ru: Boolean,
    title: String?, artist: String?, mbid: AccountUuid? = null, year: String? = null) {
    key(service, lease, title, artist, mbid, year) {
        var choice by remember { mutableStateOf<AlbumEditionChoice?>(null) }
        var retry by remember { mutableIntStateOf(0) }
        var result by remember(choice, retry) { mutableStateOf<AlbumTracklist?>(null) }
        var failed by remember(choice, retry) { mutableStateOf(false) }
        var page by remember(choice, retry) { mutableIntStateOf(0) }
        LaunchedEffect(choice, retry) {
            try {
                result = service.albumTracklist(lease, title, artist,
                    choice?.takeIf { it.source == "musicbrainz" }?.let { AccountUuid(it.id) } ?: mbid,
                    choice?.takeIf { it.source == "discogs" }?.id, year)
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { failed = true }
        }
        val data = result
        Column {
            Text(if (ru) "Полный трек-лист издания" else "Full edition tracklist", style = MaterialTheme.typography.titleMedium)
            when {
                failed -> TextButton(onClick = { retry++ }) { Text(if (ru) "Не удалось загрузить. Повторить" else "Could not load. Retry") }
                data == null -> LinearProgressIndicator()
                data.choices.isNotEmpty() -> {
                    Text(if (ru) "Выберите издание" else "Choose an edition")
                    data.choices.forEach { option -> TextButton(onClick = { choice = option }) {
                        Text(option.label + " · " + option.source + " #" + option.id)
                    } }
                }
                data.edition == null -> Text(if (ru) "Треклист издания не найден" else "Edition tracklist not found")
                else -> {
                    val count = data.tracks.count { it.inVault == true }
                    Text(if (ru) "В Vault $count из ${data.tracks.size}" else "$count of ${data.tracks.size} in Vault")
                    val uri = LocalUriHandler.current
                    if (data.edition?.source == "discogs") TextButton(onClick = {
                        uri.openUri("https://www.discogs.com/release/" + data.edition?.id)
                    }) { Text("Data provided by Discogs ↗") }
                    val multiDisc = data.tracks.any { (it.disc ?: 1) > 1 }
                    data.tracks.drop(page * 25).take(25).forEachIndexed { index, row ->
                        val number = row.position.ifBlank { (page * 25 + index + 1).toString() }
                        val position = if (multiDisc && row.disc != null) "${row.disc}.$number" else number
                        val status = when (row.inVault) {
                            true -> if (ru) "В Vault" else "In Vault"
                            false -> if (ru) "Нет в доступном Vault" else "Not in your available Vault"
                            null -> if (ru) "Наличие в Vault не подтверждено" else "Vault availability unconfirmed"
                        }
                        ListItem(headlineContent = { Text("$position  ${row.title}") },
                            supportingContent = { Text(listOfNotNull(row.artist, status).joinToString(" · ")) })
                    }
                    Row {
                        if (page > 0) TextButton(onClick = { page-- }) { Text(if (ru) "Назад" else "Previous") }
                        if ((page + 1) * 25 < data.tracks.size) TextButton(onClick = { page++ }) { Text(if (ru) "Далее" else "Next") }
                    }
                }
            }
            if (choice != null && (failed || data != null)) {
                TextButton(onClick = { choice = null }) { Text(if (ru) "Сменить издание" else "Change edition") }
            }
            if (failed || data != null && data.edition == null) {
                var input by remember { mutableStateOf("") }
                OutlinedTextField(input, { input = it.take(120) }, singleLine = true,
                    label = { Text(if (ru) "Ссылка на издание Discogs или его ID" else "Discogs release link or ID") })
                app.autplay.application.server.discogsReleaseId(input)?.let { id ->
                    TextButton(onClick = { choice = AlbumEditionChoice("discogs", id, "") }) { Text(if (ru) "Открыть треклист" else "Open tracklist") }
                }
            }
        }
    }
}
