package app.autplay.desktop.presentation

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.account.AccountLease
import app.autplay.application.library.ExternalCollection
import java.awt.Desktop
import java.net.URI
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

@Composable
internal fun DesktopCollectionImport(music: DesktopOnlineMusic, lease: AccountLease, ru: Boolean) = key(lease) {
    var opened by remember { mutableStateOf(false) }
    TextButton(onClick = { opened = true }) { Text(if (ru) "Импорт по ссылке" else "Import by link") }
    if (opened) CollectionDialog(music, lease, ru) { opened = false }
}

@Composable
internal fun CollectionDialog(music: DesktopOnlineMusic, lease: AccountLease, ru: Boolean, initialFavorites: Boolean = false, close: () -> Unit) {
    var link by remember { mutableStateOf("") }
    var favorites by remember { mutableStateOf(initialFavorites) }
    var copy by remember { mutableStateOf(false) }
    var operation by remember { mutableStateOf<String?>(null) }
    var preview by remember { mutableStateOf<ExternalCollection?>(null) }
    var recent by remember { mutableStateOf(emptyList<Pair<String, String>>()) }
    var busy by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    fun load(block: suspend () -> ExternalCollection) {
        if (busy) return
        busy = true
        failed = false
        scope.launch {
            try {
                var page = block()
                preview = page
                while (page.state == "READING") {
                    page = music.resumeCollection(lease, page.operation)
                    preview = page
                }
                if (initialFavorites && page.state == "SAVED" && page.action == "FAVORITES") close()
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { failed = true }
            finally { busy = false }
        }
    }
    LaunchedEffect(lease) {
        try { recent = music.recentCollections(lease) }
        catch (e: CancellationException) { throw e }
        catch (_: Exception) { /* A fresh link remains available. */ }
    }
    val page = preview
    AlertDialog(onDismissRequest = close, title = { Text(page?.name ?: if (ru) "Импорт по ссылке" else "Import by link") },
        text = { Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) {
            if (page == null) {
                OutlinedTextField(link, { link = it.take(2048); operation = null }, singleLine = true,
                    label = { Text(if (ru) "Ссылка на коллекцию" else "Collection link") })
                if (!initialFavorites) Row { Checkbox(favorites, { favorites = it; operation = null }, enabled = !busy)
                    Text(if (ru) "Перенести любимые" else "Transfer favorites") }
                if (!favorites) Row { Checkbox(copy, { copy = it; operation = null }, enabled = !busy)
                    Text(if (ru) "Создать ещё одну копию" else "Create another copy") }
                if (recent.isNotEmpty()) Text(if (ru) "Недавние импорты" else "Recent imports")
                recent.take(8).forEach { (id, name) ->
                    TextButton(enabled = !busy, onClick = { load { music.resumeCollection(lease, id) } }) { Text(name) }
                }
            } else {
                Text(when (page.action) {
                    "FAVORITES" -> if (ru) "Любимые" else "Favorites"
                    "ALBUM" -> if (ru) "Альбом" else "Album"
                    else -> if (ru) "Плейлист" else "Playlist"
                })
                Text(if (ru) "Получено ${page.read} из ${page.expected ?: "?"} · Определено ${page.identified} · Доступно аудио ${page.playable}"
                    else "Read ${page.read} of ${page.expected ?: "?"} · Identified ${page.identified} · Playable ${page.playable}")
                page.likes?.let { Text(if (ru) "Отметок в источнике: $it" else "Source likes: $it") }
                page.sets?.let { Text(if (ru) "Сетов: $it из ${page.expectedSets ?: "?"}" else "Sets: $it of ${page.expectedSets ?: "?"}") }
                if (!page.complete && page.state in setOf("PREVIEW", "SESSION_PREVIEW")) Text(if (ru) "Источник вернул неполную коллекцию." else "The source returned an incomplete collection.")
                if (page.state == "SESSION_PREVIEW") Text(if (ru) "Предпросмотр SoundCloud доступен до закрытия окна." else "SoundCloud preview is available for this session.")
                page.setLinks.forEach { (url, name) ->
                    TextButton(enabled = !busy, onClick = {
                        load { music.startCollection(lease, url, false, UUID.randomUUID().toString(), false) }
                    }) { Text(name) }
                }
                page.authorization?.let { url -> TextButton(onClick = {
                    runCatching { Desktop.getDesktop().browse(URI(url)) }.onFailure { failed = true }
                }) { Text(if (ru) "Подключить Spotify" else "Connect Spotify") } }
                if (page.state == "SAVED") Text(if (ru) "Сохранено в аккаунте" else "Saved to your account")
                if (page.state == "SAVED" && page.identified < page.read) {
                    page.job?.let { id ->
                        Text(if (ru) "Для разбора версий вставьте этот номер в поле задания сервера на экране сопоставления." else "To review versions, enter this ID in the server job field on the matching screen.")
                        SelectionContainer { Text(id) }
                    }
                }
                page.missingOccurrences?.takeIf { it > 0 }?.let {
                    Text(if (ru) "Исчезло из источника: $it. Сохранено в вашей библиотеке." else "Missing from the source: $it. Kept in your library.")
                }
                if (page.state == "CANCELLED") Text(if (ru) "Импорт отменён" else "Import cancelled")
                page.tracks.forEach { Text(it.ifEmpty { if (ru) "Недоступный трек" else "Unavailable track" }) }
            }
            if (page != null) {
                Row {
                    if (page.offset > 0) TextButton(enabled = !busy, onClick = {
                        load { music.collectionPage(lease, page.operation, (page.offset - 50).coerceAtLeast(0)) }
                    }) { Text(if (ru) "Назад" else "Previous") }
                    page.nextOffset?.let { offset ->
                        TextButton(enabled = !busy, onClick = { load { music.collectionPage(lease, page.operation, offset) } }) {
                            Text(if (ru) "Ещё треки" else "More tracks")
                        }
                    }
                }
                if (page.state !in setOf("SAVED", "CANCELLED")) TextButton(enabled = !busy, onClick = {
                    load { music.cancelCollection(lease, page.operation) }
                }) { Text(if (ru) "Отменить импорт" else "Cancel import") }
            }
            if (failed) Text(if (ru) "Проверьте соединение, ссылку и доступ к источнику." else "Check the connection, link and source access.")
            if (busy) LinearProgressIndicator()
        } }, confirmButton = {
            when {
                page == null -> TextButton(enabled = !busy && ExternalCollection.isProviderLink(link), onClick = {
                    val id = operation ?: UUID.randomUUID().toString().also { operation = it }
                    load { music.startCollection(lease, link.trim(), favorites, id, copy && !favorites) }
                }) { Text(if (ru) "Открыть" else "Open") }
                page.state == "PREVIEW" || page.selectionKey != null -> TextButton(enabled = !busy, onClick = { load { music.confirmCollection(lease, page) } }) {
                    Text(if (page.selectionKey != null) (if (ru) "Сохранить из каталога (${page.playable})" else "Save from catalog (${page.playable})") else if (ru && page.action == "FAVORITES") "Добавить в любимые" else if (ru) "Сохранить" else "Save")
                }
                page.state in setOf("AUTH_REQUIRED", "PARTIAL", "READING") -> TextButton(enabled = !busy, onClick = {
                    load { music.resumeCollection(lease, page.operation) }
                }) { Text(if (ru) "Продолжить" else "Continue") }
            }
        }, dismissButton = { TextButton(onClick = close) { Text(if (ru) "Закрыть" else "Close") } })
}
