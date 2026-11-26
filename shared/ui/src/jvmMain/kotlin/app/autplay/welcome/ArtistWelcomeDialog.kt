package app.autplay.ui.welcome

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.autplay.application.library.WelcomeArtist
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

internal data class ArtistRow(val artists: List<WelcomeArtist>, val depth: Int)

internal fun insertRelated(rows: List<ArtistRow>, parent: String, artists: List<WelcomeArtist>): List<ArtistRow> {
    val anchor = rows.indexOfFirst { row -> row.artists.any { it.id == parent } }
    if (anchor < 0 || rows[anchor].depth >= 3) return rows
    val visible = rows.flatMap { it.artists }.mapTo(hashSetOf()) { it.id }
    val fresh = artists.filter { visible.add(it.id) }.take(2)
    if (fresh.isEmpty()) return rows
    return rows.toMutableList().apply { add(anchor + 1, ArtistRow(fresh, rows[anchor].depth + 1)) }
}

/** Returns true while the account welcome flow occupies the foreground. */
@Composable
fun showArtistWelcomeDialog(
    ru: Boolean,
    pending: suspend () -> Boolean,
    browse: suspend (String?, String, List<String>) -> List<WelcomeArtist>,
    finish: suspend (List<String>) -> Unit,
    online: Boolean = true,
    importFavorites: @Composable (() -> Unit) -> Unit,
): Boolean {
    var step by rememberSaveable { mutableStateOf("check") }
    val pendingNow by rememberUpdatedState(pending)
    LaunchedEffect(online) {
        if (online && step in setOf("check", "unavailable")) {
            try { step = if (pendingNow()) "offer" else "done" }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { step = "unavailable" }
        }
    }
    if (step in setOf("done", "check", "unavailable", "later")) return false
    if (step == "import") {
        importFavorites { step = "artists" }
        return true
    }
    if (step == "offer") {
        AlertDialog(onDismissRequest = { step = "artists" },
            title = { Text(if (ru) "Настроить Мою волну" else "Set up My Wave") },
            text = { Text(if (ru) "Перенеси любимые треки или сразу выбери исполнителей." else
                "Bring your favorite tracks or start by choosing a few artists.") },
            confirmButton = { Button(onClick = { step = "import" }) {
                Text(if (ru) "Перенести любимые" else "Import favorites") } },
            dismissButton = { TextButton(onClick = { step = "artists" }) {
                Text(if (ru) "Сразу к выбору" else "Choose artists") } })
        return true
    }

    var selected by rememberSaveable { mutableStateOf<List<String>>(emptyList()) }
    var query by rememberSaveable { mutableStateOf("") }
    var roots by remember { mutableStateOf(emptyList<ArtistRow>()) }
    var results by remember { mutableStateOf(emptyList<ArtistRow>()) }
    val searching = query.isNotBlank()
    val rows = if (searching) results else roots
    var loading by remember { mutableStateOf(false) }
    var saving by remember { mutableStateOf(false) }
    var saveFailed by remember { mutableStateOf(false) }
    var failed by remember { mutableStateOf(false) }
    var attempt by remember { mutableIntStateOf(0) }
    val expanded = remember { mutableStateListOf<String>() }
    val cached = remember(query) { mutableStateMapOf<String, List<WelcomeArtist>>() }
    val listState = rememberLazyListState()
    var retryBranch by remember { mutableStateOf<String?>(null) }
    val pendingRows = remember { mutableStateListOf<String>() }
    val scope = rememberCoroutineScope()
    val browseNow by rememberUpdatedState(browse)
    val finishNow by rememberUpdatedState(finish)
    LaunchedEffect(query, attempt) {
        failed = false
        if (!searching && roots.isNotEmpty()) { loading = false; return@LaunchedEffect }
        loading = true
        try {
            if (searching) { expanded.removeAll { it.startsWith("$query/") }; results = emptyList(); delay(250) }
            val first = browseNow(null, query.trim(), emptyList()).chunked(2).map { ArtistRow(it, 0) }
            if (searching) results = first else roots = first
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { failed = true }
        finally { loading = false }
    }
    val firstVisible by remember(rows, listState) {
        derivedStateOf { rows.getOrNull(listState.firstVisibleItemIndex) }
    }
    LaunchedEffect(firstVisible, query) {
        val visibleRow = firstVisible ?: return@LaunchedEffect
        if (visibleRow.depth >= 3) return@LaunchedEffect
        val seen = rows.flatMap { it.artists }.map { it.id }
        for (card in visibleRow.artists) {
            if (card.id in cached) continue
            try { cached[card.id] = browseNow(card.id, "", seen) }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { return@LaunchedEffect }
        }
    }
    fun expand(id: String, depth: Int) {
        val term = query
        val branch = "$term/$id"
        if (depth == 3 || branch in expanded || branch in pendingRows) return
        pendingRows.add(branch)
        scope.launch {
            try {
                val seen = rows.flatMap { it.artists }.map { it.id }
                val children = cached[id] ?: browseNow(id, "", seen)
                if (query == term) {
                    if (searching) results = insertRelated(results, id, children)
                    else roots = insertRelated(roots, id, children)
                    expanded.add(branch)
                }
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { retryBranch = id; failed = true }
            finally { pendingRows.remove(branch) }
        }
    }
    fun toggle(id: String, depth: Int) {
        if (id in selected) { selected = selected - id; return }
        if (selected.size >= 60) return
        selected = selected + id
        expand(id, depth)
    }
    fun submit(ids: List<String>) {
        if (saving) return
        saving = true
        saveFailed = false
        scope.launch {
            try { finishNow(ids); step = "done" }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { saveFailed = true }
            finally { saving = false }
        }
    }
    AlertDialog(onDismissRequest = { if (!saving) step = "later" },
        title = { Text(if (ru) "Кого ты любишь слушать?" else "Who do you like listening to?") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                OutlinedTextField(query, { query = it.take(120) }, Modifier.fillMaxWidth(), singleLine = true,
                    label = { Text(if (ru) "Найти исполнителя" else "Find an artist") })
                if (loading || pendingRows.isNotEmpty() || saving) LinearProgressIndicator(Modifier.fillMaxWidth())
                if (saveFailed) Text(if (ru) "Не удалось сохранить выбор. Попробуй ещё раз." else "Could not save your choices. Try again.")
                if (failed) TextButton(onClick = {
                    val id = retryBranch
                    retryBranch = null
                    failed = false
                    val row = rows.find { r -> r.artists.any { it.id == id } }
                    if (id != null && row != null) expand(id, row.depth) else attempt++
                }) { Text(if (ru) "Ошибка загрузки · повторить" else "Could not load · retry") }
                if (rows.isEmpty() && !loading && !failed) Text(if (ru) "Здесь пока нет исполнителей" else "No artists to show yet")
                LazyColumn(Modifier.fillMaxWidth().heightIn(max = 390.dp), state = listState, verticalArrangement = Arrangement.spacedBy(5.dp)) {
                    items(rows, key = { it.artists.first().id }) { row ->
                        Row(Modifier.fillMaxWidth().animateItem(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            row.artists.forEach { artist ->
                                FilterChip(modifier = Modifier.weight(1f), selected = artist.id in selected,
                                    enabled = !saving && (artist.id in selected || selected.size < 60),
                                    onClick = { toggle(artist.id, row.depth) },
                                    label = { Column(Modifier.padding(vertical = 4.dp)) {
                                        Text(artist.name, maxLines = 2, overflow = TextOverflow.Ellipsis)
                                        Text(artist.track, style = MaterialTheme.typography.bodySmall,
                                            maxLines = 1, overflow = TextOverflow.Ellipsis)
                                    } })
                            }
                            if (row.artists.size == 1) Spacer(Modifier.weight(1f))
                        }
                    }
                }
            }
        },
        confirmButton = { Button(enabled = !saving, onClick = { submit(selected) }) {
            Text((if (ru) "Готово" else "Done") + if (selected.isEmpty()) "" else " · ${selected.size}") } },
        dismissButton = { TextButton(enabled = !saving, onClick = { submit(emptyList()) }) {
            Text(if (ru) "Пропустить" else "Skip") } })
    return true
}
