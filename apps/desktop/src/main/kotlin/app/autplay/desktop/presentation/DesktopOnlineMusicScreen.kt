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
import app.autplay.application.desktop.DesktopWaveTrack
import app.autplay.application.desktop.account.AccountLease
import app.autplay.application.desktop.account.AccountUuid
import app.autplay.application.desktop.account.AccountLocalDomainRow
import app.autplay.application.desktop.account.AccountLocalDomainDetail
import app.autplay.application.server.InternetMetadataDiscoveryCard
import app.autplay.application.server.MusicCatalogueContextRequest
import app.autplay.application.server.InternetMetadataDiscoveryPage
import app.autplay.ui.desktop.DesktopLanguage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive

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
    val calendar = remember(lease) { WaveCalendar() }
    var ranked by remember(lease) { mutableStateOf<List<DesktopWaveTrack>>(emptyList()) }
    val weights by produceState(emptyMap<String, Double>(), service, lease, ranked) {
        if (service != null && lease != null) value = try { service.wheelWeights(lease, ranked) }
        catch (cancelled: CancellationException) { throw cancelled } catch (_: Exception) { emptyMap() }
    }
    val hour = java.time.LocalTime.now().hour
    val order = remember(hour, weights) { app.autplay.application.recommendation.WaveWheel.ring(hour, weights) }
    var choice by remember { mutableStateOf<String?>(null) }
    var tracks by remember(choice) { mutableStateOf<List<DesktopWaveTrack>?>(null) }
    var failure by remember(choice) { mutableStateOf(false) }
    var playing by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val selected = choice
    val genre = selected?.takeIf { it in setOf("pop", "rock", "hip_hop", "electronic", "jazz", "classical") }
    LaunchedEffect(selected, lease, service) {
        if (service != null && lease != null && selected != null) {
            try {
                tracks = when {
                    genre != null -> {
                        val candidates = ranked.ifEmpty { service.rankedWave(lease, "GENERAL", calendar).also { ranked = it } }
                        service.wheelMix(lease, genre, candidates)
                    }
                    selected == "focus" || selected == "sleep" -> service.rankedWave(lease, if (selected == "focus") "WORK" else "SLEEP", calendar)
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
                        try { service.playMix(lease, result, genre, calendar) } catch (cancelled: CancellationException) { throw cancelled }
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
    val revision by service.packRevision.collectAsState()
    var saved by remember(lease) { mutableStateOf<SavedHome?>(null) }
    LaunchedEffect(service, lease) {
        while (true) { service.schedulePack(lease, "GENERAL"); delay(86_400_000L) }
    }
    LaunchedEffect(service, lease, revision) {
        while (true) {
            saved = try { service.savedHome(lease) }
            catch (cancelled: CancellationException) { throw cancelled } catch (_: Exception) { null }
            val expiry = saved?.validUntilMs ?: break
            delay((expiry - System.currentTimeMillis()).coerceAtLeast(1))
        }
    }
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
        val home = saved
        if (home != null && home.items.isNotEmpty()) {
            Text(if (home.stale) (if (ru) "На устройстве из прошлой подборки" else "Downloaded from a previous mix")
                else (if (ru) "Сохранённые рекомендации" else "Saved recommendations"))
            home.items.take(6).forEach { track ->
                TextButton(enabled = !busy, onClick = {
                    if (!busy) {
                        busy = true; failed = false
                        scope.launch {
                            try { service.playSavedHomeTrack(lease, track.id) }
                            catch (cancelled: CancellationException) { throw cancelled }
                            catch (_: Exception) { failed = true } finally { busy = false }
                        }
                    }
                }) { Text(listOfNotNull(track.title, track.artist).joinToString(" · ")) }
            }
        }
        if (home != null && !home.stale && home.items.isNotEmpty()) TextButton(enabled = !busy, onClick = {
            if (!busy) {
                busy = true; failed = false
                scope.launch {
                    try { service.playSavedWave(lease, "GENERAL") }
                    catch (cancelled: CancellationException) { throw cancelled }
                    catch (_: Exception) { failed = true } finally { busy = false }
                }
            }
        }) { Text(if (ru) "Слушать офлайн" else "Play offline") }
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
private fun ColumnScope.OnlineMusicResults(service: DesktopOnlineMusic, lease: AccountLease, source: MusicSource, ru: Boolean, cache: java.nio.file.Path?,
    initial: MusicBrowse? = null, ranking: Map<String, Long> = emptyMap()) {
    val scope = rememberCoroutineScope()
    var input by remember { mutableStateOf("") }
    var target by remember { mutableStateOf(initial) }
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
    var playlistTrack by remember { mutableStateOf<PlaylistTrackTarget?>(null) }
    var rankedCards by remember(target) { mutableStateOf<List<InternetMetadataDiscoveryCard>?>(null) }
    var incomplete by remember(target) { mutableStateOf(false) }
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
        if (rankedCards != null) return@LaunchedEffect
        busy = true; error = false; vault = null; discovery = null
        try {
            if (source == MusicSource.Vault) vault = service.vault(lease, value.query, cursor)
            else {
                val (page, offline) = service.discover(lease, value, offset, cache, savedOnly)
                discovery = page
                cached = offline
                if (target == initial && initial != null && rankedCards == null) {
                    val rows = page.items.toMutableList()
                    var last = page
                    while (last.nextOffset != null) {
                        val next = checkNotNull(last.nextOffset)
                        if (next <= last.offset || next > 1000) { incomplete = true; break }
                        val following = try { service.discover(lease, value, next, cache, savedOnly).first }
                        catch (cancelled: CancellationException) { throw cancelled }
                        catch (_: Exception) { incomplete = true; break }
                        rows.addAll(following.items)
                        last = following
                    }
                    incomplete = incomplete || last.truncated
                    val order = if (value.releases) compareByDescending<InternetMetadataDiscoveryCard> { it.releaseDate.orEmpty() }
                        else compareByDescending { ranking[it.recordingId] ?: 0L }
                    rankedCards = rows.distinctBy { it.id }.sortedWith(order.thenBy(String.CASE_INSENSITIVE_ORDER) { it.title }.thenBy { it.entityId })
                }
            }
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { error = true }
        finally { busy = false }
    }
    if (initial == null) Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        OutlinedTextField(value = input, onValueChange = { if (it.length <= 200) input = it }, singleLine = true,
            modifier = Modifier.weight(1f), label = { Text(if (ru) "Поиск на сервере" else "Search server") })
        Button(enabled = input.isNotBlank() && playing == null, onClick = {
            stack = emptyList(); showSaved = false; savedOnly = false
            open(MusicBrowse(input.trim(), if (source == MusicSource.Artists) "artist" else "album"))
        }) { Text(if (ru) "Найти" else "Search") }
    }
    if (initial == null && source != MusicSource.Vault && cache != null) {
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
    val ranked = rankedCards
    if (incomplete) Text(if (ru) "Показана доступная часть дискографии" else "Showing available discography")
    if (cards != null && cached) Text(if (ru) "Сохранённая страница каталога" else "Saved catalog page")
    if (page?.items?.isEmpty() == true || cards?.items?.isEmpty() == true) Text(if (ru) "Ничего не найдено" else "No results")
    Column(Modifier.fillMaxWidth().weight(1f, false).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        page?.items?.forEach { hit -> key(hit.publication, hit.ref) {
            ListItem(headlineContent = { Text(hit.title) }, supportingContent = { Text(hit.artist) },
                trailingContent = { Row {
                    TextButton(enabled = playing == null, onClick = { playlistTrack = PlaylistTrackTarget.Vault(hit) }) {
                        Text(if (ru) "В плейлист" else "Add to playlist")
                    }
                    TextButton(enabled = playing == null && hit.available, onClick = {
                    if (playing == null) {
                        playing = hit.ref
                        scope.launch {
                            try { service.stream(lease, hit, page.items) }
                            catch (e: CancellationException) { throw e }
                            catch (_: Exception) { error = true }
                            finally { playing = null }
                        }
                    }
                }) { Text(if (ru) "Слушать" else "Play") } } })
        } }
        if (cards != null) (ranked?.drop(offset)?.take(30) ?: cards.items).forEach { card -> key(card.id) {
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
                supportingContent = {
                    val guest = initial?.entity?.value?.let { JsonPrimitive(it) in (card.rawPayload["guest_artist_ids"] as? JsonArray).orEmpty() } == true
                    Text(listOfNotNull(card.artist, card.disambiguation, card.releaseDate,
                        if (guest) (if (ru) "Приглашённый исполнитель" else "Guest appearance") else null).joinToString(" · "))
                },
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
                        if (cards.knownActionsAllowed && card.trackLookup != null) TextButton(onClick = {
                            playlistTrack = MusicCatalogueContextRequest.fromCard(card)?.let(PlaylistTrackTarget::Catalogue)
                        }) { Text(if (ru) "В плейлист" else "Add to playlist") }
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
        if (page?.next != null || (if (ranked == null) cards?.nextOffset != null else offset + 30 < ranked.size)) TextButton(onClick = {
            if (source == MusicSource.Vault) { previous = previous + listOf(cursor); cursor = page?.next }
            else if (ranked != null) offset += 30 else cards?.nextOffset?.let { offset = it }
        }) { Text(if (ru) "Далее" else "Next page") }
    }
    playlistTrack?.let { selection -> CataloguePlaylistDialog(service, lease, selection, ru) { playlistTrack = null } }
    download?.let { (kind, id) -> CatalogDownloadDialog(service, lease, kind, id, ru) { download = null } }
}


@Composable
private fun CataloguePlaylistDialog(service: DesktopOnlineMusic, lease: AccountLease, selection: PlaylistTrackTarget,
    ru: Boolean, close: () -> Unit) {
    val scope = rememberCoroutineScope()
    var after by remember { mutableStateOf<AccountUuid?>(null) }
    var previous by remember { mutableStateOf<List<AccountUuid?>>(emptyList()) }
    var next by remember { mutableStateOf<AccountUuid?>(null) }
    var rows by remember { mutableStateOf<List<AccountLocalDomainRow>>(emptyList()) }
    var loading by remember { mutableStateOf(true) }
    var saving by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf(false) }
    var saved by remember { mutableStateOf(false) }
    var attempt by remember { mutableIntStateOf(0) }
    var chosen by remember { mutableStateOf<AccountLocalDomainRow?>(null) }
    val operation = remember(chosen) { AccountUuid.random() }
    val entry = remember(chosen) { AccountUuid.random() }
    val timestamp = remember(chosen) { System.currentTimeMillis() }
    LaunchedEffect(after, attempt) {
        loading = true; error = false; rows = emptyList(); next = null
        try { val result = service.playlists(lease, after); rows = result.first; next = result.second }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (_: Exception) { error = true }
        finally { loading = false }
    }
    AlertDialog(onDismissRequest = { if (!saving) close() }, title = { Text(if (ru) "В плейлист" else "Add to playlist") },
        text = { Column(Modifier.heightIn(max = 390.dp).verticalScroll(rememberScrollState())) {
            when {
                loading || saving -> LinearProgressIndicator()
                saved -> Text(if (ru) "Трек добавлен в плейлист" else "Track added to playlist")
                else -> {
                    if (rows.isEmpty() && !error) Text(if (ru) "На этой странице нет редактируемых плейлистов" else "No editable playlists on this page")
                    rows.forEach { row ->
                        val playlist = row.detail as AccountLocalDomainDetail.Playlist
                        FilterChip(selected = row == chosen, onClick = { chosen = row; error = false },
                            label = { Text(playlist.name.orEmpty()) })
                    }
                    Row {
                        if (previous.isNotEmpty()) TextButton(onClick = {
                            chosen = null; after = previous.last(); previous = previous.dropLast(1)
                        }) { Text(if (ru) "Назад" else "Previous") }
                        next?.let { cursor -> TextButton(onClick = {
                            chosen = null; previous = previous + listOf(after); after = cursor
                        }) { Text(if (ru) "Далее" else "Next") } }
                    }
                    if (error) TextButton(onClick = { attempt++ }) {
                        Text(if (ru) "Не удалось выполнить действие. Обновить список" else "Action failed. Refresh playlists")
                    }
                }
            }
        } }, confirmButton = {
            val row = chosen
            if (!saved && row != null) TextButton(enabled = !saving && !loading, onClick = {
                if (!saving) {
                    saving = true; error = false
                    scope.launch {
                        try { service.appendPlaylistTrack(lease, selection, row, operation, entry, timestamp); saved = true }
                        catch (cancelled: CancellationException) { throw cancelled }
                        catch (_: Exception) { error = true }
                        finally { saving = false }
                    }
                }
            }) { Text(if (ru) "Добавить" else "Add") }
        }, dismissButton = { TextButton(enabled = !saving, onClick = close) { Text(if (ru) "Закрыть" else "Close") } })
}

@Composable
internal fun PlayerArtistPicker(service: DesktopOnlineMusic, lease: AccountLease, track: String,
    cache: java.nio.file.Path?, language: DesktopLanguage) {
    val ru = language == DesktopLanguage.Russian
    val credits by produceState(emptyList<app.autplay.application.desktop.account.AccountCatalogCreditMember>(), lease, track) {
        value = try { service.credits(lease, AccountUuid(track)) }
        catch (cancelled: CancellationException) { throw cancelled } catch (_: Exception) { emptyList() }
    }
    if (credits.isEmpty()) return
    var choosing by remember { mutableStateOf(false) }
    var selected by remember { mutableStateOf<app.autplay.application.desktop.account.AccountCatalogCreditMember?>(null) }
    TextButton(onClick = { if (credits.size == 1) selected = credits.single() else choosing = true }) {
        Text(if (ru) "К исполнителю" else "Go to artist")
    }
    if (choosing) AlertDialog(onDismissRequest = { choosing = false }, title = {
        Text(if (ru) "Выберите исполнителя" else "Choose artist")
    }, text = { Column(Modifier.heightIn(max = 360.dp).verticalScroll(rememberScrollState())) {
        credits.forEach { member -> TextButton(onClick = { selected = member; choosing = false }) { Text(member.creditedName) } }
    } }, confirmButton = { TextButton(onClick = { choosing = false }) { Text(if (ru) "Закрыть" else "Close") } })
    selected?.let { member -> ArtistDiscography(service, lease, member.artistId, member.creditedName, cache, ru) { selected = null } }
}

@Composable
internal fun ArtistDiscographyButton(service: DesktopOnlineMusic, lease: AccountLease, artist: String, title: String,
    cache: java.nio.file.Path?, language: DesktopLanguage) {
    val ru = language == DesktopLanguage.Russian
    var opened by remember { mutableStateOf(false) }
    TextButton(onClick = { opened = true }) { Text(if (ru) "Дискография исполнителя" else "Artist discography") }
    if (opened) ArtistDiscography(service, lease, AccountUuid(artist), title, cache, ru) { opened = false }
}

@Composable
private fun ArtistDiscography(service: DesktopOnlineMusic, lease: AccountLease, artist: AccountUuid, name: String,
    cache: java.nio.file.Path?, ru: Boolean, close: () -> Unit) {
    var catalogue by remember { mutableStateOf<ArtistCatalogue?>(null) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf(false) }
    var attempt by remember { mutableIntStateOf(0) }
    var albums by remember { mutableStateOf(false) }
    LaunchedEffect(lease, artist, attempt) {
        loading = true; error = false
        try { catalogue = service.artistCatalogue(lease, artist, cache) }
        catch (cancelled: CancellationException) { throw cancelled } catch (_: Exception) { error = true }
        finally { loading = false }
    }
    androidx.compose.ui.window.Dialog(onDismissRequest = close,
        properties = androidx.compose.ui.window.DialogProperties(usePlatformDefaultWidth = false)) {
        Surface(modifier = Modifier.widthIn(max = 860.dp).fillMaxWidth(0.94f), shape = MaterialTheme.shapes.extraLarge, tonalElevation = 6.dp) {
            Column(Modifier.fillMaxWidth().heightIn(max = 560.dp).padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(name, style = MaterialTheme.typography.titleLarge)
                val resolved = catalogue
                when {
                    loading -> LinearProgressIndicator()
                    error -> TextButton(onClick = { attempt++ }) { Text(if (ru) "Повторить загрузку" else "Retry loading") }
                    resolved == null -> Text(if (ru) "Для этого исполнителя пока нет связи с MusicBrainz" else "No MusicBrainz identity for this artist yet")
                    else -> {
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            FilterChip(selected = !albums, onClick = { albums = false }, label = { Text(if (ru) "Треки" else "Tracks") })
                            FilterChip(selected = albums, onClick = { albums = true }, label = { Text(if (ru) "Альбомы" else "Albums") })
                        }
                        key(albums) { OnlineMusicResults(service, lease, MusicSource.Artists, ru, cache,
                            MusicBrowse(name, "artist", resolved.mbid, albums), resolved.counts) }
                    }
                }
                TextButton(onClick = close) { Text(if (ru) "Закрыть" else "Close") }
            }
        }
    }
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
    var alternatives by remember { mutableStateOf(false) }
    val target = remember(kind, id, alternatives) { CatalogTarget(kind, id, alternatives) }
    val downloads = service.catalogDownloads
    val states by downloads.states.collectAsState()
    val progress = states[lease to target]
    val state = progress?.job
    val busy = progress?.running == true
    val error = progress?.error == true
    LaunchedEffect(lease, target) {
        if (progress == null && kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { downloads.hasOperation(lease, target) })
            downloads.submit(lease, target)
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
                    job.issues.take(12).forEach { (title, reason) ->
                        val label = when (reason) {
                            "source_not_found" -> if (ru) "Источник не найден" else "Source not found"
                            "catalogue_match_evidence_missing" -> if (ru) "Совпадение не подтверждено" else "Match unconfirmed"
                            else -> if (ru) "Не удалось загрузить" else "Download failed"
                        }
                        Text("$title · $label", style = MaterialTheme.typography.bodySmall)
                    }
                }
                if (busy) LinearProgressIndicator()
                if (error) Text(if (ru) "Не удалось обновить загрузку" else "Could not update download")
            }
        }, confirmButton = {
            val start = state?.phase == "preview"
            TextButton(enabled = !busy, onClick = {
                downloads.submit(lease, target, start, restart = state?.phase == "complete")
            }) {
                Text(if (start) (if (ru) "Начать" else "Start") else (if (ru) "Проверить" else "Check"))
            }
        }, dismissButton = { TextButton(onClick = close) { Text(if (ru) "Закрыть" else "Close") } })
}

@Composable
internal fun PlayerDownload(service: DesktopOnlineMusic, lease: AccountLease, id: String, language: DesktopLanguage) {
    val ru = language == DesktopLanguage.Russian
    var chooser by remember { mutableStateOf(false) }
    TextButton(onClick = { chooser = true }) { Text(if (ru) "В плейлист" else "Add to playlist") }
    if (chooser) CataloguePlaylistDialog(service, lease, PlaylistTrackTarget.Local(AccountUuid(id)), ru) { chooser = false }
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

@Composable
internal fun LocalPlayerPlaylists(playlists: List<app.autplay.application.desktop.DesktopPlaylist>, language: DesktopLanguage,
    add: (String) -> Unit) {
    if (playlists.isEmpty()) return
    val ru = language == DesktopLanguage.Russian
    var open by remember { mutableStateOf(false) }
    TextButton(onClick = { open = true }) { Text(if (ru) "В плейлист" else "Add to playlist") }
    if (open) AlertDialog(onDismissRequest = { open = false }, title = { Text(if (ru) "В плейлист" else "Add to playlist") },
        text = { Column(Modifier.heightIn(max = 360.dp).verticalScroll(rememberScrollState())) {
            playlists.forEach { playlist ->
                TextButton(onClick = { open = false; add(playlist.id) }) { Text(playlist.name) }
            }
        } }, confirmButton = { TextButton(onClick = { open = false }) { Text(if (ru) "Закрыть" else "Close") } })
}
