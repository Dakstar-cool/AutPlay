package app.autplay.desktop.presentation

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.draw.clip
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import app.autplay.application.desktop.account.AccountLease
import app.autplay.application.desktop.account.AccountUuid
import app.autplay.application.server.InternetMetadataDiscoveryPage
import app.autplay.ui.desktop.DesktopLanguage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import java.util.prefs.Preferences

internal enum class MusicSource(val ru: String, val en: String) {
    Vault("Vault", "Vault"), Artists("Исполнители", "Artists"), Albums("Альбомы", "Albums")
}

private val wheelLabels = mapOf(
    "energy" to ("Энергия" to "Energy"), "running" to ("Для бега" to "Running"),
    "focus" to ("Концентрация" to "Focus"), "evening" to ("Вечер" to "Evening"),
    "calm" to ("Спокойствие" to "Calm"), "sleep" to ("Перед сном" to "Before sleep"),
    "pop" to ("Поп" to "Pop"), "rock" to ("Рок" to "Rock"), "hip_hop" to ("Хип-хоп" to "Hip hop"),
    "electronic" to ("Электроника" to "Electronic"), "jazz" to ("Джаз" to "Jazz"), "classical" to ("Классика" to "Classical")
)

@Composable
internal fun DesktopWaveWheel(service: DesktopOnlineMusic?, lease: AccountLease?, language: DesktopLanguage) {
    val ru = language == DesktopLanguage.Russian
    val order = remember { app.autplay.application.recommendation.WaveWheel.ring(java.time.LocalTime.now().hour) }
    var choice by remember { mutableStateOf<String?>(null) }
    var tracks by remember(choice) { mutableStateOf<List<AccountUuid>?>(null) }
    var failure by remember(choice) { mutableStateOf(false) }
    var playing by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val selected = choice
    val genre = selected?.takeIf { it in setOf("pop", "rock", "hip_hop", "electronic", "jazz", "classical") }
    LaunchedEffect(selected, lease, service) {
        if (service != null && lease != null && selected != null) {
            try {
                tracks = when {
                    genre != null -> service.wheelMix(lease, genre)
                    selected == "focus" || selected == "sleep" -> service.rankedWave(lease, if (selected == "focus") "WORK" else "SLEEP")
                    else -> emptyList()
                }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) { failure = true }
        }
    }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            order.forEach { id ->
                val label = wheelLabels.getValue(id)
                FilterChip(selected = selected == id, enabled = !playing, onClick = { choice = id },
                    label = { Text(if (ru) label.first else label.second) })
            }
        }
        if (selected != null) {
            val result = tracks
            when {
                failure -> Text(if (ru) "Не удалось получить подборку" else "Could not load mix")
                !result.isNullOrEmpty() && service != null && lease != null -> TextButton(enabled = !playing, onClick = {
                    playing = true
                    scope.launch {
                        try { service.playMix(lease, result) } catch (cancelled: CancellationException) { throw cancelled }
                        catch (_: Exception) { failure = true } finally { playing = false }
                    }
                }) { Text(if (ru) "Слушать · ${wheelLabels.getValue(selected).first}" else "Play · ${wheelLabels.getValue(selected).second}") }
                result == null && service != null && lease != null -> LinearProgressIndicator()
                else -> Text(if (ru) "Предпросмотр подборки" else "Mix preview", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}

@Composable
internal fun OnlineWave(service: DesktopOnlineMusic, lease: AccountLease, language: DesktopLanguage) {
    val ru = language == DesktopLanguage.Russian
    var busy by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(if (ru) "Моя волна · Sona" else "My wave · Sona", style = MaterialTheme.typography.titleLarge)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            for ((mode, label) in listOf("GENERAL" to (if (ru) "Для меня" else "For me"),
                "WORK" to (if (ru) "Фокус" else "Focus"), "SLEEP" to (if (ru) "Сон" else "Sleep"))) {
                FilledTonalButton(enabled = !busy, onClick = {
                    if (!busy) {
                        busy = true
                        failed = false
                        scope.launch {
                            try { service.wave(lease, mode) }
                            catch (e: CancellationException) { throw e }
                            catch (_: Exception) { failed = true }
                            finally { busy = false }
                        }
                    }
                }) { Text(label) }
            }
        }
        if (busy) LinearProgressIndicator()
        if (failed) Text(if (ru) "Не удалось запустить волну Sona. Проверьте подключение и синхронизацию."
            else "Could not start the Sona mix. Check your connection and sync.")
    }
}

@Composable
internal fun OnlineMusicSearch(service: DesktopOnlineMusic, lease: AccountLease, language: DesktopLanguage, cache: java.nio.file.Path?) {
    val ru = language == DesktopLanguage.Russian
    var source by remember { mutableStateOf(MusicSource.Vault) }
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            MusicSource.entries.forEach { choice ->
                FilterChip(selected = choice == source, onClick = { source = choice },
                    label = { Text(if (ru) choice.ru else choice.en) })
            }
        }
        key(source) { OnlineMusicResults(service, lease, source, ru, cache) }
    }
}

@Composable
private fun ColumnScope.OnlineMusicResults(service: DesktopOnlineMusic, lease: AccountLease, source: MusicSource, ru: Boolean, cache: java.nio.file.Path?) {
    val scope = rememberCoroutineScope()
    var input by remember { mutableStateOf("") }
    var target by remember { mutableStateOf<MusicBrowse?>(null) }
    var vault by remember { mutableStateOf<VaultPage?>(null) }
    var savedOnly by remember { mutableStateOf(false) }
    var savedPage by remember { mutableIntStateOf(0) }
    var showSaved by remember { mutableStateOf(false) }
    var savedEditions by remember { mutableStateOf<List<MusicBrowse>>(emptyList()) }
    var cached by remember { mutableStateOf(false) }
    var discovery by remember { mutableStateOf<InternetMetadataDiscoveryPage?>(null) }
    var stack by remember { mutableStateOf<List<MusicBrowse>>(emptyList()) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf(false) }
    var playing by remember { mutableStateOf<AccountUuid?>(null) }
    var download by remember { mutableStateOf<Pair<String, AccountUuid>?>(null) }
    var offset by remember { mutableIntStateOf(0) }
    var previous by remember { mutableStateOf<List<String?>>(emptyList()) }
    var cursor by remember { mutableStateOf<String?>(null) }
    var refresh by remember { mutableIntStateOf(0) }
    fun open(value: MusicBrowse) {
        target = value; vault = null; discovery = null; offset = 0; cursor = null; previous = emptyList(); error = false
        refresh++
    }
    LaunchedEffect(target, cursor, offset, refresh) {
        val value = target ?: return@LaunchedEffect
        busy = true; error = false; vault = null; discovery = null
        try {
            if (source == MusicSource.Vault) vault = service.vault(lease, value.query, cursor)
            else {
                val (page, offline) = service.discover(lease, value, offset, cache, savedOnly)
                discovery = page
                cached = offline
            }
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { error = true }
        finally { busy = false }
    }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        OutlinedTextField(value = input, onValueChange = { if (it.length <= 200) input = it }, singleLine = true,
            modifier = Modifier.weight(1f), label = { Text(if (ru) "Поиск на сервере" else "Search server") })
        Button(enabled = input.isNotBlank() && playing == null, onClick = {
            stack = emptyList(); showSaved = false; savedOnly = false
            open(MusicBrowse(input.trim(), if (source == MusicSource.Artists) "artist" else "album"))
        }) { Text(if (ru) "Найти" else "Search") }
    }
    if (source != MusicSource.Vault && cache != null) {
        TextButton(onClick = { showSaved = !showSaved; savedPage = 0 }) { Text(if (ru) "Сохранённые страницы" else "Saved pages") }
        LaunchedEffect(showSaved, savedPage) {
            if (showSaved) {
                try { savedEditions = service.savedEditions(lease, cache, if (source == MusicSource.Artists) "artist" else "album", savedPage) }
                catch (e: CancellationException) { throw e } catch (_: Exception) { error = true }
            }
        }
        if (showSaved) {
            savedEditions.take(12).forEach { item -> TextButton(onClick = {
                savedOnly = true; showSaved = false; stack = emptyList(); open(item)
            }) { Text(item.query + if (item.releases) (if (ru) " · Альбомы" else " · Albums") else "") } }
            if (savedEditions.isEmpty()) Text(if (ru) "Сохранённых страниц пока нет" else "No saved pages yet")
            Row {
                if (savedPage > 0) TextButton(onClick = { savedPage-- }) { Text(if (ru) "Назад" else "Previous") }
                if (savedEditions.size > 12) TextButton(onClick = { savedPage++ }) { Text(if (ru) "Далее" else "Next") }
            }
        }
    }
    if (stack.isNotEmpty()) TextButton(onClick = { val last = stack.last(); stack = stack.dropLast(1); open(last) }) {
        Text(if (ru) "Назад" else "Back")
    }
    if (busy || playing != null) LinearProgressIndicator()
    if (error) TextButton(onClick = { refresh++ }) { Text(if (ru) "Не удалось выполнить действие. Повторить поиск" else "Action failed. Retry search") }
    val page = vault
    val cards = discovery
    if (cards != null && cached) Text(if (ru) "Сохранённая страница каталога" else "Saved catalog page")
    if (page?.items?.isEmpty() == true || cards?.items?.isEmpty() == true) Text(if (ru) "Ничего не найдено" else "No results")
    Column(Modifier.fillMaxWidth().weight(1f, false).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        page?.items?.forEach { hit -> key(hit.publication, hit.ref) {
            ListItem(headlineContent = { Text(hit.title) }, supportingContent = { Text(hit.artist) },
                trailingContent = { TextButton(enabled = playing == null && hit.available, onClick = {
                    if (playing == null) {
                        playing = hit.ref
                        scope.launch {
                            try { service.stream(lease, hit) }
                            catch (e: CancellationException) { throw e }
                            catch (_: Exception) { error = true }
                            finally { playing = null }
                        }
                    }
                }) { Text(if (ru) "Слушать" else "Play") } })
        } }
        cards?.items?.forEach { card -> key(card.id) {
            val browse = card.browseTarget
            val targetDownload = if (!cards.knownActionsAllowed) null else when {
                browse != null -> card.entityType to AccountUuid(browse.entityId)
                card.trackLookup != null && card.recordingId != null -> "recording" to AccountUuid(checkNotNull(card.recordingId))
                else -> null
            }
            ListItem(leadingContent = {
                    if (card.knownEntity == app.autplay.application.server.InternetMetadataDiscoveryEntity.RELEASE)
                        ReleaseCover(service, lease, card.entityId)
                }, headlineContent = { Text(card.title) },
                supportingContent = { Text(listOfNotNull(card.artist, card.disambiguation, card.releaseDate).joinToString(" · ")) },
                trailingContent = {
                    Row(horizontalArrangement = Arrangement.spacedBy(3.dp)) {
                        if (browse != null) {
                            val artist = card.entityType == "artist"
                            fun drill(releases: Boolean) {
                                target?.let { stack = stack + it }
                                open(MusicBrowse(card.title, if (artist) "artist" else "album", AccountUuid(card.entityId), releases))
                            }
                            if (artist) TextButton(onClick = { drill(true) }) { Text(if (ru) "Альбомы" else "Albums") }
                            TextButton(onClick = { drill(false) }) { Text(if (ru) "Треки" else "Tracks") }
                        }
                        if (targetDownload != null) TextButton(onClick = { download = targetDownload }) {
                            Text(if (ru) "Загрузить" else "Download")
                        }
                    }
                })
        } }
    }
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (previous.isNotEmpty() || offset > 0) TextButton(onClick = {
            if (source == MusicSource.Vault) { cursor = previous.last(); previous = previous.dropLast(1) }
            else offset = (offset - 30).coerceAtLeast(0)
        }) { Text(if (ru) "Предыдущая страница" else "Previous page") }
        if (page?.next != null || cards?.nextOffset != null) TextButton(onClick = {
            if (source == MusicSource.Vault) { previous = previous + listOf(cursor); cursor = page?.next }
            else cards?.nextOffset?.let { offset = it }
        }) { Text(if (ru) "Далее" else "Next page") }
    }
    download?.let { (kind, id) -> CatalogDownloadDialog(service, lease, kind, id, ru) { download = null } }
}


@Composable
private fun ReleaseCover(service: DesktopOnlineMusic, lease: AccountLease, id: String) {
    val bitmap by produceState<androidx.compose.ui.graphics.ImageBitmap?>(null, service, lease, id) {
        value = try { service.cover(lease, AccountUuid(id)) }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (_: Exception) { null }
    }
    val artwork = bitmap
    val bounds = Modifier.size(48.dp).clip(MaterialTheme.shapes.small)
    if (artwork == null) app.autplay.ui.AutPlayArtworkPlaceholder(id, bounds)
    else androidx.compose.foundation.Image(artwork, null, bounds, contentScale = androidx.compose.ui.layout.ContentScale.Crop)
}

@Composable
private fun CatalogDownloadDialog(service: DesktopOnlineMusic, lease: AccountLease, kind: String,
    id: AccountUuid, ru: Boolean, close: () -> Unit) {
    val scope = rememberCoroutineScope()
    var alternatives by remember { mutableStateOf(false) }
    val prefs = remember(lease.scopeId, id, kind) { Preferences.userRoot().node("app/autplay/catalog/${lease.scopeId.value}/$kind/${id.value}") }
    val slot = if (alternatives) "all" else "standard"
    var state by remember(slot) { mutableStateOf<CatalogJob?>(null) }
    var busy by remember { mutableStateOf(false) }
    var error by remember(slot) { mutableStateOf(false) }
    fun run(start: Boolean) {
        if (busy) return
        busy = true; error = false
        scope.launch {
            try {
                val saved = prefs.get(slot, null)?.let(::AccountUuid)
                val op = saved ?: AccountUuid.random().also { prefs.put(slot, it.value); prefs.flush() }
                var job = if (saved == null || state == null) service.downloadPreview(lease, op, kind, id, alternatives)
                    else service.downloadAction(lease, op, "status")
                state = job
                if (start && job.phase == "preview") job = service.downloadAction(lease, op, "start")
                state = job
                var steps = 0
                while (job.phase in setOf("discovery", "inspect", "downloading") && steps++ < 45) {
                    if (job.phase == "downloading" && job.counts["available"] == 0) delay(4000)
                    job = service.downloadAction(lease, op, "advance")
                    state = job
                }
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { error = true }
            finally { busy = false }
        }
    }
    AlertDialog(onDismissRequest = close, title = { Text(if (ru) "Загрузка в Vault" else "Download to Vault") },
        text = {
            Column {
                if (kind == "artist") Row(verticalAlignment = Alignment.CenterVertically) {
                    Checkbox(alternatives, onCheckedChange = { alternatives = it }, enabled = !busy)
                    Text(if (ru) "Включая альтернативные версии" else "Include alternate versions")
                }
                state?.let { job ->
                    val counts = job.counts
                    Text(if (ru) "В Vault: ${counts["vault"]} · В очереди: ${counts["queued"]} · Доступно: ${counts["available"]}"
                        else "In Vault: ${counts["vault"]} · Queued: ${counts["queued"]} · Available: ${counts["available"]}")
                    Text(if (ru) "Отсутствуют или не загружены: ${counts.getValue("missing") + counts.getValue("failed")}"
                        else "Missing or failed: ${counts.getValue("missing") + counts.getValue("failed")}")
                }
                if (busy) LinearProgressIndicator()
                if (error) Text(if (ru) "Не удалось обновить загрузку" else "Could not update download")
            }
        }, confirmButton = {
            val start = state?.phase == "preview"
            TextButton(enabled = !busy, onClick = {
                if (state?.phase == "complete") { prefs.remove(slot); state = null }
                run(start)
            }) {
                Text(if (start) (if (ru) "Начать" else "Start") else (if (ru) "Проверить" else "Check"))
            }
        }, dismissButton = { TextButton(onClick = close) { Text(if (ru) "Закрыть" else "Close") } })
}

@Composable
internal fun PlayerDownload(service: DesktopOnlineMusic, lease: AccountLease, id: String, language: DesktopLanguage) {
    val ru = language == DesktopLanguage.Russian
    val scope = rememberCoroutineScope()
    var busy by remember { mutableStateOf(false) }
    var requested by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf(false) }
    OutlinedButton(enabled = !busy && !requested, onClick = {
        if (!busy) {
            busy = true
            scope.launch {
                try { service.downloadCurrent(lease, id); requested = true; error = false }
                catch (e: CancellationException) { throw e }
                catch (_: Exception) { error = true }
                finally { busy = false }
            }
        }
    }) { Text(when {
        requested -> if (ru) "В очереди загрузок" else "In downloads"
        error -> if (ru) "Повторить загрузку" else "Retry download"
        else -> if (ru) "Скачать на устройство" else "Download to device"
    }) }
}
